#!/usr/bin/env python3
"""
Processa a base UTA-RLDD (Ghoddoosian, Galib e Athitsos, CVPR Workshops 2019):
60 pessoas filmadas três vezes cada, alerta (0), pouco vigilante (5) e com
sono de verdade (10), autoavaliadas pela escala KSS. É a única base aberta
com sonolência real, que falta na avaliação do TCC.

Os vídeos vêm em zips de ~13 GB no Google Drive, mais que o espaço livre do
cartão do Pi. Por isso cada vídeo é tirado de dentro do zip remoto
(zip_remoto.py), passa pelo MediaPipe (o mesmo _extrair do YawDD, mesmas
colunas) e é APAGADO: fica só a série de números em series/. O progresso vai
para progresso.csv, então dá pra parar e continuar.

Uso (da pasta uta_rldd/):
    ../venv/bin/python processar_uta.py                        # os 10 pacotes (60 pessoas), Fold1 primeiro
    ../venv/bin/python processar_uta.py Fold1_part1 Fold1_part2
Quando o Google bloqueia um pacote (cota por arquivo), passa para o próximo.
"""

import csv
import multiprocessing as mp
import shutil
import sys
import time
from pathlib import Path

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI.parent))

PACOTES = {
    "Fold1_part1": "16x8afSOwfbdGAjUbfRFvwGj1ie8Lf5Do", "Fold1_part2": "1qPoJ06jeznytP_BGJ25hxdQy7eYTTjyk",
    "Fold2_part1": "12uVMVLv_kRXzQb4QDdru9RK3G9hzfpPQ", "Fold2_part2": "1nYGUpqE3x5ZeRLgDeMlUN4K_ccPjM2pe",
    "Fold3_part1": "1-JhE5TfkNyr4UbQk6PSkkpI7z-SChkWS", "Fold3_part2": "1LZU5KfJkFMj2pIkGwb3RjHDazoUfxSYQ",
    "Fold4_part1": "1IYKdrjvawf90bG_cfeoWA4rZSymllFbD", "Fold4_part2": "1zJlASBwABBgVSULQLiia0R_Eqar8_B3D",
    "Fold5_part1": "16w30wEgcAbaUr4gx9o4sl_2TbIunD5yA", "Fold5_part2": "1loOQE1-hoNM5dIOjXHI2Ss5NjjoPZcX6",
}
ROTULOS = {"0": "alerta", "5": "pouco_vigilante", "10": "sonolento"}
VIDEOS = AQUI / "videos"
SERIES = AQUI / "series"
PROGRESSO = AQUI / "progresso.csv"
CAMPOS = ["pacote", "pessoa", "rotulo", "arquivo_zip", "serie", "mb", "fps_proc", "baixado_s", "erro"]
NO_DISCO_MAX = 3      # vídeos esperando processamento ao mesmo tempo (espaço do cartão)
PROCESSOS = 3         # o Pi tem 4 núcleos; um fica pro download e pro sistema
ESPERA_COTA = 30 * 60  # pacote que bateu na cota só é tentado de novo depois disso


def extrair(args):
    from avaliar_yawdd import _extrair
    video, destino = args
    try:
        fps = _extrair(video, destino)
        return str(video), destino, fps, ""
    except Exception as e:
        return str(video), destino, 0.0, f"{type(e).__name__}: {e}"
    finally:
        Path(video).unlink(missing_ok=True)   # o vídeo nunca fica guardado


def main():
    """Fila única com todos os pacotes. A cota do Google vale por arquivo:
    quando um zip bloqueia, em vez de ficar parado esperando (chegou a 12,8 h
    em 04-05/11), passa para o próximo pacote e tenta o bloqueado de novo
    depois de ESPERA_COTA. Ordem: alerta e sonolento de todos os pacotes
    primeiro (pareados por pessoa), pouco vigilante só no fim; dentro disso,
    sempre o primeiro pacote liberado na ordem pedida (Fold1 antes de Fold2...)."""
    import zip_remoto
    pacotes = sys.argv[1:] or list(PACOTES)
    VIDEOS.mkdir(exist_ok=True)
    SERIES.mkdir(exist_ok=True)
    feitos = set()
    if PROGRESSO.exists():
        feitos = {r["serie"] for r in csv.DictReader(open(PROGRESSO)) if not r["erro"]}
    novo = not PROGRESSO.exists()

    filas = {p: None for p in pacotes}     # None = índice do zip ainda não lido
    zips = {}                               # zips abertos (fecham na cota)
    bloqueado_ate = {p: 0.0 for p in pacotes}

    def fase(rotulo):
        return 1 if rotulo == "pouco_vigilante" else 0

    def ler_indice(pacote, z):
        fila = []
        for info in z.infolist():
            partes = Path(info.filename).parts
            if info.is_dir() or len(partes) != 3:
                continue
            pessoa, nome = partes[1], partes[2]
            rotulo = ROTULOS.get(Path(nome).stem)
            if rotulo is None or f"{pacote}_{pessoa}_{rotulo}.npz" in feitos:
                continue
            fila.append((fase(rotulo), pessoa, rotulo, info))
        fila.sort(key=lambda x: (x[0], x[1], x[2]))
        return fila

    with open(PROGRESSO, "a", newline="") as fprog, mp.get_context("spawn").Pool(PROCESSOS) as pool:
        w = csv.DictWriter(fprog, fieldnames=CAMPOS)
        if novo:
            w.writeheader()
        pendentes = []   # (AsyncResult, linha do progresso)

        def recolher():
            """Anota no progresso os vídeos que já terminaram de processar."""
            for res, linha in [x for x in pendentes if x[0].ready()]:
                _v, _d, fps, erro = res.get()
                linha.update(fps_proc=f"{fps:.1f}", erro=erro)
                w.writerow(linha)
                fprog.flush()
                pendentes.remove((res, linha))
                print(f"  pronto: {linha['serie']} ({fps:.1f} fps){' ERRO ' + erro if erro else ''}", flush=True)

        def falta(p, f):
            """O pacote p ainda tem vídeo da fase f (índice não lido conta como sim)."""
            return filas[p] is None or any(x[0] == f for x in filas[p])

        for f in (0, 1):
            while True:
                candidatos = [p for p in pacotes if falta(p, f)]
                if not candidatos:
                    break
                agora = time.time()
                livres = [p for p in candidatos if bloqueado_ate[p] <= agora]
                if not livres:
                    volta = min(bloqueado_ate[p] for p in candidatos)
                    print(f"  [cota] todos os {len(candidatos)} pacotes bloqueados; próxima tentativa "
                          f"às {time.strftime('%H:%M', time.localtime(volta))}", flush=True)
                    while time.time() < volta:
                        recolher()
                        time.sleep(min(30, max(1, volta - time.time())))
                    continue
                pacote = livres[0]
                video = None
                try:
                    if pacote not in zips:
                        print(f"== {pacote}: lendo o índice do zip remoto", flush=True)
                        zips[pacote] = zip_remoto.abrir(PACOTES[pacote], desistir_na_cota=True)
                        if filas[pacote] is None:
                            filas[pacote] = ler_indice(pacote, zips[pacote])
                            print(f"   {len(filas[pacote])} vídeos a medir", flush=True)
                        continue
                    item = next(x for x in filas[pacote] if x[0] == f)
                    _f, pessoa, rotulo, info = item
                    serie = f"{pacote}_{pessoa}_{rotulo}.npz"
                    while len(pendentes) >= NO_DISCO_MAX:
                        recolher()
                        time.sleep(5)
                    video = VIDEOS / f"{pacote}_{pessoa}_{rotulo}{Path(info.filename).suffix.lower()}"
                    print(f"baixando {info.filename} ({info.file_size / 1e6:.0f} MB)...", flush=True)
                    t0 = time.time()
                    with zips[pacote].open(info) as src, open(video, "wb") as dst:   # zipfile confere o CRC no fim
                        shutil.copyfileobj(src, dst, 4 << 20)
                    filas[pacote].remove(item)
                    linha = dict(pacote=pacote, pessoa=pessoa, rotulo=rotulo, arquivo_zip=info.filename,
                                 serie=serie, mb=f"{info.file_size / 1e6:.0f}", baixado_s=f"{time.time() - t0:.0f}")
                    pendentes.append((pool.apply_async(extrair, ((video, SERIES / serie),)), linha))
                    recolher()
                except zip_remoto.CotaExcedida:
                    bloqueado_ate[pacote] = time.time() + ESPERA_COTA
                    zips.pop(pacote, None)        # reabre do zero na volta
                    if video is not None:
                        video.unlink(missing_ok=True)
                    print(f"  [cota] {pacote} bloqueado; passando para o próximo pacote "
                          f"(volta às {time.strftime('%H:%M', time.localtime(bloqueado_ate[pacote]))})", flush=True)
        while pendentes:
            recolher()
            time.sleep(5)
    print("Fim.")


if __name__ == "__main__":
    main()
