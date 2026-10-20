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
    ../venv/bin/python processar_uta.py Fold1_part1            # um pacote (6 pessoas)
    ../venv/bin/python processar_uta.py Fold1_part1 Fold1_part2
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
    import zip_remoto
    pacotes = sys.argv[1:] or ["Fold1_part1"]
    VIDEOS.mkdir(exist_ok=True)
    SERIES.mkdir(exist_ok=True)
    feitos = set()
    if PROGRESSO.exists():
        feitos = {r["serie"] for r in csv.DictReader(open(PROGRESSO)) if not r["erro"]}
    novo = not PROGRESSO.exists()
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

        for pacote in pacotes:
            print(f"== {pacote}: lendo o índice do zip remoto", flush=True)
            z = zip_remoto.abrir(PACOTES[pacote])
            for info in z.infolist():
                partes = Path(info.filename).parts
                if info.is_dir() or len(partes) != 3:
                    continue
                pessoa, nome = partes[1], partes[2]
                rotulo = ROTULOS.get(Path(nome).stem)
                if rotulo is None:
                    continue
                serie = f"{pacote}_{pessoa}_{rotulo}.npz"
                if serie in feitos:
                    continue
                while len(pendentes) >= NO_DISCO_MAX:
                    recolher()
                    time.sleep(5)
                video = VIDEOS / f"{pacote}_{pessoa}_{rotulo}{Path(nome).suffix.lower()}"
                print(f"baixando {info.filename} ({info.file_size / 1e6:.0f} MB)...", flush=True)
                t0 = time.time()
                with z.open(info) as src, open(video, "wb") as dst:   # zipfile confere o CRC no fim
                    shutil.copyfileobj(src, dst, 4 << 20)
                linha = dict(pacote=pacote, pessoa=pessoa, rotulo=rotulo, arquivo_zip=info.filename,
                             serie=serie, mb=f"{info.file_size / 1e6:.0f}", baixado_s=f"{time.time() - t0:.0f}")
                pendentes.append((pool.apply_async(extrair, ((video, SERIES / serie),)), linha))
                recolher()
        while pendentes:
            recolher()
            time.sleep(5)
    print("Fim.")


if __name__ == "__main__":
    main()
