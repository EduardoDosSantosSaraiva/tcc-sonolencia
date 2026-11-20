"""
Avalia o detector no dataset público YawDD (Abtahi et al., ACM MMSys 2014):
roda o sistema, com a mesma calibração e os mesmos limiares do programa ao
vivo, em cada vídeo, e compara com o rótulo que vem no nome do arquivo.

YawDD tem dois conjuntos:
  - Mirror: câmera no retrovisor, um vídeo por comportamento
    ("...-Normal", "...-Talking", "...-Yawning"). O rótulo é por vídeo, então
    a avaliação de bocejo é por vídeo: nos de bocejo o sistema tem que contar
    pelo menos um bocejo, nos de fala/normal não pode contar nenhum.
    Falar é o caso difícil (boca abre e fecha o tempo todo).
  - Dash: câmera no painel, um vídeo por pessoa com tudo misturado (dirigir
    calado, falando e bocejando). Serve pra ver se o bocejo é achado nesse
    ângulo; sem marcação de tempo, não dá pra contar falso positivo.

Em nenhum vídeo o motorista está com sono de verdade, então todo alarme
cheio ("fechado") e todo aviso de atenção contam como alarme falso, e a
taxa por hora de direção entra no relatório.

Os primeiros segundos de cada vídeo viram calibração (como no carro, ao
ligar). Um bocejo nesse trecho é descartado da referência mas conta como
bocejo, e a calibração se estende (até CALIBRATION_MAX_SECONDS) pra juntar
CALIBRATION_SECONDS de rosto em repouso, igual ao programa ao vivo.

Duas fases: a pesada roda o MediaPipe uma vez por vídeo e salva as medidas
de cada frame (EAR, MAR, cabeça, rosto visto) em series/*.npz; a rápida
refaz calibração e detecção a partir delas. Depois de mudar um limiar ou
regra no core, --simular reavalia os 349 vídeos em segundos.

Uso:
    ./venv/bin/python avaliar_yawdd.py ~/datasets/YawDD/"YawDD dataset"
    ./venv/bin/python avaliar_yawdd.py <pasta> --limite 10              # teste rápido
    ./venv/bin/python avaliar_yawdd.py <pasta> --retomar avaliacao_yawdd/20260923_150000
    ./venv/bin/python avaliar_yawdd.py --simular avaliacao_yawdd/20260923_150000

Saída em avaliacao_yawdd/<timestamp>/: resultados.csv (um vídeo por linha,
gravado conforme vai, pra poder retomar), series/ e relatorio.md (tabelas
prontas pro Cap. 4). Nenhum frame de imagem é salvo, só números (licença do
YawDD proíbe mostrar o rosto da maioria dos participantes).
"""

import argparse
import csv
import multiprocessing as mp
import re
import time
from pathlib import Path

BASE = Path(__file__).parent
OUT_DIR = BASE / "avaliacao_yawdd"
EXTENSOES = {".avi", ".mp4", ".mov", ".mkv"}
ATTENTION_REPEAT = 10.0   # igual a eye_fatigue.ATTENTION_REPEAT_SECONDS, só pro texto do relatório

CAMPOS = [
    "arquivo", "conjunto", "rotulo", "genero", "oculos", "duracao_s", "frames",
    "fps_processamento", "pct_frames_com_rosto", "calibracao_ok", "ear_baseline",
    "mar_baseline", "mar_max", "bocejos", "t_primeiro_bocejo_s", "alarmes_fechado",
    "avisos_atencao", "piscadas_longas", "quedas_cabeca", "erro", "serie",
]


def rotular(path):
    """Tira conjunto, rótulo, gênero e óculos do caminho/nome do arquivo."""
    nome = path.stem.lower()
    caminho = str(path).lower()
    conjunto = "dash" if "dash" in caminho else "mirror" if "mirror" in caminho else "?"
    if "yawn" in nome:
        rotulo = "bocejo"
    elif "talk" in nome:
        rotulo = "fala"
    elif "normal" in nome:
        rotulo = "normal"
    elif conjunto == "dash":
        rotulo = "misto"
    else:
        rotulo = "?"
    genero = "F" if "female" in nome else "M" if "male" in nome else "?"
    if re.search(r"sun ?glass", nome):
        oculos = "escuro"
    elif re.search(r"no ?glass", nome):
        oculos = "sem"
    elif "glass" in nome:
        oculos = "grau"
    else:
        oculos = "?"
    return conjunto, rotulo, genero, oculos


def extrair(args):
    """Fase pesada: roda o MediaPipe em todos os frames do vídeo (mesmo
    process_frame do programa ao vivo) e salva as medidas cruas de cada
    frame em <destino>.npz. Retorna (vídeo, fps de processamento, erro)."""
    path, destino = args
    try:
        return str(path), _extrair(path, destino), ""
    except Exception as e:
        return str(path), 0.0, f"{type(e).__name__}: {e}"


def _extrair(path, destino):
    import numpy as np
    import core
    import eye_fatigue as ef
    from mediapipe import Image as MpImage, ImageFormat as MpImageFormat

    source = core.VideoFileSource(path, realtime=False)
    landmarker = core.build_landmarker()   # um por vídeo: rastreamento começa do zero
    timestamp = core.MonotonicMs()
    linhas = []
    t_ini = time.perf_counter()
    try:
        while True:
            frame = source.read()
            if frame is None:
                break
            infer, _disp, iw, ih, t = frame
            pf = ef.process_frame(landmarker, timestamp, MpImage, MpImageFormat, infer, iw, ih, t)
            if pf is None:
                continue
            nan = float("nan")
            linhas.append((t,
                           nan if pf["left_ear"] is None else pf["left_ear"],
                           nan if pf["right_ear"] is None else pf["right_ear"],
                           nan if pf["mar"] is None else pf["mar"],
                           nan if pf["head_pitch"] is None else pf["head_pitch"],
                           nan,  # 5: era o modo óculos (removido em 30/10/2026); vazia pra manter as colunas
                           float(pf["face_detected"]),
                           # colunas 7 e 8: medidas de boca em estudo, fora da decisão
                           nan if pf["abertura_boca"] is None else pf["abertura_boca"],
                           nan if pf["abertura_boca_ext"] is None else pf["abertura_boca_ext"],
                           # 9: nariz-queixo; 10 em diante: blendshapes, na ordem de core.BLENDSHAPES_USADOS
                           nan if pf["queixo"] is None else pf["queixo"],
                           *(pf["blendshapes"].get(n, nan) for n in core.BLENDSHAPES_USADOS),
                           # 24 a 27, em estudo (rosto de lado): EAR esq/dir e MAR em 3D e
                           # ângulo do rosto (graus); 28 era a razão de brilho do modo óculos
                           *(nan if pf[k] is None else pf[k] for k in ("left_ear3d", "right_ear3d", "mar3d", "yaw")),
                           nan))
    finally:
        landmarker.close()
        source.close()
    dur = time.perf_counter() - t_ini
    # tamanho_original: largura e altura do vídeo (vídeo que não é 4:3 vai
    # pro MediaPipe sem esticar, ver core.tamanho_inferencia)
    np.savez_compressed(destino, serie=np.array(linhas, dtype=np.float64),
                        tamanho_original=np.array(source.tamanho_original or (0, 0)))
    return len(linhas) / dur if dur > 0 else 0.0


def _v(x):
    return None if x != x else x   # nan -> None


def _jaw_e_sorriso(serie):
    """jawOpen (coluna 10) e sorriso (média das colunas de mouthSmile) da
    série; séries antigas não têm essas colunas e voltam NaN."""
    import numpy as np
    import core
    jaw = serie[:, 10] if serie.shape[1] > 10 else np.full(len(serie), np.nan)
    c_sorriso = [10 + core.BLENDSHAPES_USADOS.index(n) for n in ("mouthSmileLeft", "mouthSmileRight")]
    sorriso = (serie[:, c_sorriso].mean(axis=1) if serie.shape[1] > max(c_sorriso)
               else np.full(len(serie), np.nan))
    return jaw, sorriso


def calibrar_serie(serie):
    """Calibração do início da série, com a mesma regra de parada do
    core.calibrate. Devolve (ear_b, mar_b, pitch_b, sorriso_b, bocejos na
    calibração, índice do 1º quadro depois da calibração)."""
    import core
    jaw, sorriso = _jaw_e_sorriso(serie)
    serie = serie[:, :7]
    t0 = serie[0, 0] if len(serie) else 0.0
    # junta CALIBRATION_SECONDS de rosto em repouso (boca aberta não conta),
    # no máximo CALIBRATION_MAX_SECONDS
    quadros = []
    i = 0
    while i < len(serie):
        t = serie[i, 0]
        if ((core.tempo_em_repouso(quadros) >= core.CALIBRATION_SECONDS and t - t0 >= core.CALIBRATION_SECONDS)
                or t - t0 >= core.CALIBRATION_MAX_SECONDS):
            break
        _t, l, r, m, p, _d, face = serie[i]
        if face:
            quadros.append((t, (l + r) / 2 if l == l and r == r else None,
                            m if m == m else None, p if p == p else None, _v(jaw[i]), _v(sorriso[i])))
        i += 1
    ear_b, mar_b, pitch_b, _aviso, bocejos_calib = core.calibrar_quadros(quadros)
    return ear_b, mar_b, pitch_b, core.sorriso_neutro(quadros), bocejos_calib, i


def simular(serie, eventos=None, calibracao=None):
    """Fase rápida: refaz calibração e detecção a partir das medidas salvas,
    com a mesma lógica do programa ao vivo (core.calibrar_quadros e
    eye_fatigue.build_state). Como só usa o core, qualquer ajuste de limiar
    ou regra pode ser reavaliado nos 349 vídeos em segundos.

    eventos: lista opcional que recebe (t, tipo) de cada alarme, aviso,
    bocejo, piscada longa e queda de cabeça, pra avaliar por trecho de vídeo
    (ver avaliar_angulos.py).

    calibracao: (ear_b, mar_b, pitch_b, sorriso_b) vinda de OUTRA série (ex.:
    calibra no vídeo da pessoa alerta e monitora o vídeo dela com sono, como
    o motorista que liga o sistema descansado). Aí o vídeo inteiro é
    monitorado, sem trecho de calibração."""
    import contextlib, io
    import core
    import eye_fatigue as ef

    jaw, sorriso = _jaw_e_sorriso(serie)
    if calibracao is None:
        ear_b, mar_b, pitch_b, sorriso_b, bocejos_calib, i = calibrar_serie(serie)
    else:
        (ear_b, mar_b, pitch_b, sorriso_b), bocejos_calib, i = calibracao, [], 0
    serie = serie[:, :7]
    t0 = serie[0, 0] if len(serie) else 0.0
    with contextlib.redirect_stdout(io.StringIO()):   # build_state imprime os limiares
        state = ef.build_state(ear_b, mar_b, pitch_b, sorriso_b)
    for t_b in bocejos_calib:
        state.registrar_bocejo(t_b)
        if eventos is not None:
            eventos.append((t_b, "bocejo"))

    frames = com_rosto = alarmes = avisos = bocejos = piscadas = quedas = 0
    t_bocejo = None
    aviso = core.AvisoLeve(ef.ATTENTION_REPEAT_SECONDS, ef.ATTENTION_REPEAT_IDLE_SECONDS)
    mar_max = 0.0
    prev = {"fechado": False, "bocejos": 0, "piscadas": 0, "cabeca": 0}
    t = t0
    for (t, l, r, m, p, _d, face), j, sr in zip(serie[i:], jaw[i:], sorriso[i:]):
        frames += 1
        com_rosto += bool(face)
        st = state.update(_v(l), _v(r), _v(m), _v(p), bool(face), t,
                          jaw_open=_v(j), smile=_v(sr))
        if st["mar"] is not None:
            mar_max = max(mar_max, st["mar"])
        fechado = st["level"] == "fechado"
        alarmes += fechado and not prev["fechado"]
        if eventos is not None and fechado and not prev["fechado"]:
            eventos.append((t, "alarme"))
        # aviso contado como o programa toca (mesma regra, core.AvisoLeve)
        atencao = st["atencao_by_blinks"] or st["atencao_by_head"] or st["atencao_by_perclos"]
        if aviso.tocar(atencao, st["marcador_atencao"], t):
            avisos += 1
            if eventos is not None:
                eventos.append((t, "aviso"))
        # contagens das janelas deslizantes: soma só os aumentos (evento novo)
        if st["yawn_count_recent"] > prev["bocejos"]:
            bocejos += st["yawn_count_recent"] - prev["bocejos"]
            if t_bocejo is None:
                t_bocejo = t
        piscadas += max(0, st["blink_count_recent"] - prev["piscadas"])
        quedas += max(0, st["head_down_count_recent"] - prev["cabeca"])
        if eventos is not None:
            for tipo, chave, atual in (("bocejo", "bocejos", st["yawn_count_recent"]),
                                       ("piscada_longa", "piscadas", st["blink_count_recent"]),
                                       ("queda_cabeca", "cabeca", st["head_down_count_recent"])):
                eventos.extend([(t, tipo)] * max(0, atual - prev[chave]))
        prev = {"fechado": fechado, "bocejos": st["yawn_count_recent"],
                "piscadas": st["blink_count_recent"], "cabeca": st["head_down_count_recent"]}

    # vídeo mais curto que a calibração: o laço acima nem roda, mas o bocejo
    # visto na calibração já está registrado no estado
    bocejos = max(bocejos, len(bocejos_calib))
    return dict(
        duracao_s=f"{t - t0:.1f}", frames=frames,
        pct_frames_com_rosto=f"{100 * com_rosto / frames:.1f}" if frames else "0",
        calibracao_ok=ear_b is not None,
        ear_baseline=f"{ear_b:.3f}" if ear_b else "", mar_baseline=f"{mar_b:.3f}" if mar_b else "",
        mar_max=f"{mar_max:.3f}", bocejos=bocejos,
        t_primeiro_bocejo_s=f"{t_bocejo - t0:.1f}" if t_bocejo is not None else "",
        alarmes_fechado=alarmes, avisos_atencao=avisos, piscadas_longas=piscadas, quedas_cabeca=quedas,
    )


def nome_serie(path, raiz):
    """Nome do .npz a partir do caminho relativo (há nomes repetidos entre
    Mirror e Dash, e alguns com espaço)."""
    return str(Path(path).relative_to(raiz)).replace("/", "__").replace(" ", "_") + ".npz"


def linha_csv(path, serie, fps=""):
    conjunto, rotulo, genero, oculos = rotular(Path(path))
    r = dict.fromkeys(CAMPOS, "")
    r.update(arquivo=str(path), conjunto=conjunto, rotulo=rotulo, genero=genero, oculos=oculos,
             fps_processamento=f"{fps:.1f}" if fps else "")
    r.update(simular(serie))
    return r


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------

def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/d"


def metricas_bocejo(linhas):
    """Matriz de confusão por vídeo, só no Mirror (rótulo por vídeo)."""
    vp = sum(1 for r in linhas if r["rotulo"] == "bocejo" and int(r["bocejos"]) > 0)
    fn = sum(1 for r in linhas if r["rotulo"] == "bocejo" and int(r["bocejos"]) == 0)
    fp = sum(1 for r in linhas if r["rotulo"] in ("fala", "normal") and int(r["bocejos"]) > 0)
    vn = sum(1 for r in linhas if r["rotulo"] in ("fala", "normal") and int(r["bocejos"]) == 0)
    prec = vp / (vp + fp) if vp + fp else None
    rec = vp / (vp + fn) if vp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else None
    return vp, fn, fp, vn, prec, rec, f1


def fmt(x):
    return f"{100 * x:.1f}%" if x is not None else "n/d"


def gerar_relatorio(csv_path, md_path):
    with open(csv_path) as f:
        todas = list(csv.DictReader(f))
    ok = [r for r in todas if not r["erro"]]
    erros = [r for r in todas if r["erro"]]
    mirror = [r for r in ok if r["conjunto"] == "mirror" and r["rotulo"] in ("bocejo", "fala", "normal")]
    dash = [r for r in ok if r["conjunto"] == "dash"]

    L = [f"# Avaliação no YawDD ({time.strftime('%d/%m/%Y %H:%M')})", ""]
    L.append(f"Vídeos processados: {len(ok)} (Mirror: {len(mirror)}, Dash: {len(dash)})"
             + (f", com erro: {len(erros)}" if erros else ""))
    horas = sum(float(r["duracao_s"]) for r in ok) / 3600
    L.append(f"Tempo total de vídeo: {horas * 60:.1f} min")
    calib = sum(r["calibracao_ok"] == "True" for r in ok)
    L.append(f"Calibração de olho válida: {calib}/{len(ok)} ({pct(calib, len(ok))})")
    rosto = [float(r["pct_frames_com_rosto"]) for r in ok]
    if rosto:
        L.append(f"Frames com rosto detectado (média por vídeo): {sum(rosto) / len(rosto):.1f}%")
    fps = [float(r["fps_processamento"]) for r in ok if r["fps_processamento"]]
    if fps:
        L.append(f"Velocidade de processamento (média): {sum(fps) / len(fps):.1f} fps")
    L.append("")

    if mirror:
        vp, fn, fp, vn, prec, rec, f1 = metricas_bocejo(mirror)
        L += ["## Bocejo (Mirror, avaliação por vídeo)", "",
              "| | Rótulo: bocejo | Rótulo: fala ou normal |", "|---|---|---|",
              f"| Sistema contou bocejo | {vp} (VP) | {fp} (FP) |",
              f"| Sistema não contou | {fn} (FN) | {vn} (VN) |", "",
              f"- Precisão: {fmt(prec)}", f"- Sensibilidade (recall): {fmt(rec)}",
              f"- F1: {fmt(f1)}", f"- Acurácia: {pct(vp + vn, vp + fn + fp + vn)}", ""]

        L += ["### Por grupo", "", "| Grupo | Vídeos | Precisão | Recall | F1 |", "|---|---|---|---|---|"]
        grupos = [("Sem óculos", lambda r: r["oculos"] == "sem"),
                  ("Óculos de grau", lambda r: r["oculos"] == "grau"),
                  ("Óculos escuros", lambda r: r["oculos"] == "escuro"),
                  ("Mulheres", lambda r: r["genero"] == "F"),
                  ("Homens", lambda r: r["genero"] == "M")]
        for nome, filtro in grupos:
            sub = [r for r in mirror if filtro(r)]
            if sub:
                _, _, _, _, p, rc, f = metricas_bocejo(sub)
                L.append(f"| {nome} | {len(sub)} | {fmt(p)} | {fmt(rc)} | {fmt(f)} |")
        L.append("")

        L += ["### Falso positivo por tipo de vídeo", "", "| Tipo | Vídeos | Com bocejo contado à toa |", "|---|---|---|"]
        for rot in ("normal", "fala"):
            sub = [r for r in mirror if r["rotulo"] == rot]
            n = sum(int(r["bocejos"]) > 0 for r in sub)
            L.append(f"| {rot.capitalize()} | {len(sub)} | {n} ({pct(n, len(sub))}) |")
        L.append("")

    if dash:
        achou = sum(int(r["bocejos"]) > 0 for r in dash)
        L += ["## Bocejo (Dash, câmera no painel)", "",
              f"Vídeos em que pelo menos um bocejo foi contado: {achou}/{len(dash)} ({pct(achou, len(dash))}).",
              "Cada vídeo do Dash tem bocejo, mas sem marcação de tempo, então só a detecção entra aqui.", ""]

    L += ["## Alarmes falsos (ninguém está com sono no YawDD)", "",
          "| Tipo | Ocorrências | Vídeos afetados | Por hora de vídeo |", "|---|---|---|---|"]
    for nome, campo in (("Alarme cheio (olho fechado)", "alarmes_fechado"), ("Aviso de atenção", "avisos_atencao")):
        total = sum(int(r[campo]) for r in ok)
        vids = sum(int(r[campo]) > 0 for r in ok)
        L.append(f"| {nome} | {total} | {vids}/{len(ok)} | {total / horas:.1f} |" if horas else f"| {nome} | {total} | {vids} | n/d |")
    L.append("")
    L.append("Obs.: os vídeos do YawDD são curtos (até 1 a 2 min), então as regras de "
             "frequência (piscadas longas em 2 min, PERCLOS em 60 s) quase não têm tempo de agir. "
             "Aviso de atenção contado como o programa toca (no máximo um a cada "
             f"{ATTENTION_REPEAT:.0f} s).")
    L.append("")

    piores = sorted((r for r in ok if int(r["alarmes_fechado"]) or
                     (r["rotulo"] in ("fala", "normal") and int(r["bocejos"])) or
                     (r["rotulo"] == "bocejo" and not int(r["bocejos"]))),
                    key=lambda r: r["arquivo"])
    if piores:
        L += ["## Vídeos com erro de detecção (pra olhar um por um)", "",
              "| Vídeo | Rótulo | Óculos | Bocejos | Alarmes | MAR máx | Calibração |", "|---|---|---|---|---|---|---|"]
        for r in piores:
            L.append(f"| {Path(r['arquivo']).name} | {r['rotulo']} | {r['oculos']} | {r['bocejos']} | "
                     f"{r['alarmes_fechado']} | {r['mar_max']} | {'ok' if r['calibracao_ok'] == 'True' else 'FALHOU'} |")
        L.append("")
    if erros:
        L += ["## Vídeos que não abriram", ""] + [f"- {Path(r['arquivo']).name}: {r['erro']}" for r in erros] + [""]

    L += ["---", "Dataset: Abtahi, S.; Omidyeganeh, M.; Shirmohammadi, S.; Hariri, B. "
          "YawDD: A Yawning Detection Dataset. ACM MMSys 2014. DOI 10.1145/2557642.2563678"]
    md_path.write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pasta", type=Path, nargs="?", help="Pasta do YawDD descompactado (procura vídeos em subpastas)")
    ap.add_argument("--limite", type=int, help="Processa só os N primeiros vídeos (teste rápido)")
    ap.add_argument("--lista", type=Path,
                    help="Arquivo com um nome de vídeo por linha: processa só esses (ex.: os que falharam)")
    ap.add_argument("--workers", type=int, default=3, help="Vídeos em paralelo (padrão 3; o Pi 5 tem 4 núcleos)")
    ap.add_argument("--retomar", type=Path, help="Pasta de uma avaliação interrompida, pra continuar de onde parou")
    ap.add_argument("--simular", type=Path, metavar="PASTA",
                    help="Só refaz calibração+detecção a partir das medidas já salvas nessa pasta "
                         "(segundos, sem MediaPipe). Usar depois de mudar limiares/regras no core.")
    args = ap.parse_args()
    import numpy as np

    if args.simular:
        saida = args.simular
        with open(saida / "resultados.csv") as f:
            antigas = {r["arquivo"]: r for r in csv.DictReader(f)}
        linhas = []
        for arq, velha in antigas.items():
            npz = saida / "series" / velha["serie"]
            if npz.exists():
                r = linha_csv(arq, np.load(npz)["serie"], float(velha["fps_processamento"] or 0))
                r["serie"] = velha["serie"]
                linhas.append(r)
        with open(saida / "resultados.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS)
            w.writeheader()
            w.writerows(linhas)
        gerar_relatorio(saida / "resultados.csv", saida / "relatorio.md")
        print(f"{len(linhas)} vídeos resimulados. Relatório: {saida / 'relatorio.md'}")
        return

    if args.pasta is None:
        ap.error("informe a pasta do YawDD (ou --simular PASTA)")
    raiz = args.pasta.expanduser()
    videos = sorted(p for p in raiz.rglob("*") if p.suffix.lower() in EXTENSOES)
    if not videos:
        raise SystemExit(f"Nenhum vídeo encontrado em {raiz}")
    if args.lista:
        # caminho relativo à pasta do YawDD, pra não confundir nomes repetidos entre Mirror e Dash
        pedidos = {l.strip() for l in args.lista.read_text().splitlines() if l.strip()}
        videos = [v for v in videos if str(v.relative_to(raiz)) in pedidos]
    if args.limite:
        videos = videos[:args.limite]

    saida = args.retomar or OUT_DIR / time.strftime("%Y%m%d_%H%M%S")
    (saida / "series").mkdir(parents=True, exist_ok=True)
    csv_path = saida / "resultados.csv"
    feitos = set()
    if csv_path.exists():
        with open(csv_path) as f:
            feitos = {r["arquivo"] for r in csv.DictReader(f)}
    pendentes = [v for v in videos if str(v) not in feitos]

    sem_rotulo = [v.name for v in pendentes if rotular(v)[1] == "?"]
    if sem_rotulo:
        print(f"Aviso: {len(sem_rotulo)} vídeos sem rótulo reconhecido no nome (ex.: {sem_rotulo[0]}). "
              "Eles são processados, mas ficam fora da matriz de confusão.")
    print(f"{len(videos)} vídeos, {len(feitos)} já feitos, {len(pendentes)} a processar. Saída: {saida}")

    novo = not csv_path.exists()
    tarefas = [(v, saida / "series" / nome_serie(v, raiz)) for v in pendentes]
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if novo:
            w.writeheader()
        # spawn: o MediaPipe não se dá bem com fork depois de carregado
        with mp.get_context("spawn").Pool(args.workers) as pool:
            for i, (arq, fps, erro) in enumerate(pool.imap_unordered(extrair, tarefas), 1):
                npz = saida / "series" / nome_serie(arq, raiz)
                if erro:
                    r = dict.fromkeys(CAMPOS, "")
                    r.update(arquivo=arq, erro=erro)
                    w.writerow(r)
                    print(f"[{i}/{len(pendentes)}] {Path(arq).name}: {erro}", flush=True)
                    continue
                r = linha_csv(arq, np.load(npz)["serie"], fps)
                r["serie"] = npz.name
                w.writerow(r)
                f.flush()
                print(f"[{i}/{len(pendentes)}] {Path(arq).name}: {r['rotulo']}: {r['bocejos']} bocejo(s), "
                      f"{r['alarmes_fechado']} alarme(s), rosto {r['pct_frames_com_rosto']}%", flush=True)

    gerar_relatorio(csv_path, saida / "relatorio.md")
    print(f"\nRelatório: {saida / 'relatorio.md'}")


if __name__ == "__main__":
    main()
