"""
Teste de regressão do detector: confere se uma mudança nas regras piorou
algum número que já estava bom, antes de ela ir pro programa.

Mede duas coisas, sem câmera e sem rodar o MediaPipe de novo:
  - YawDD (349 vídeos): refaz calibração e detecção a partir das séries já
    salvas em avaliacao_yawdd/ (a mesma simulação do avaliar_yawdd.py) e
    calcula bocejos achados, bocejos inventados e alarmes falsos.
  - Sessões ao vivo (sonolencia.db): repassa cada sessão gravada pelas regras
    atuais e conta quantos episódios de olho fechado que deram alarme cheio
    na hora continuam dando. É a proteção contra uma regra nova que corta
    alarme falso escondendo um cochilo de verdade.

Compara com regressao_referencia.json e sai com código 1 se algum número
piorou, listando os vídeos que mudaram.

Uso:
    ./venv/bin/python verificar_regressao.py            # compara com a referência
    ./venv/bin/python verificar_regressao.py --salvar   # grava os números atuais como referência

Depois de uma rodada nova do MediaPipe no YawDD (séries novas), os números
mudam sem o código mudar: confira o relatório e rode --salvar.
"""

import argparse
import contextlib
import csv
import io
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

import core
import eye_fatigue as ef
from avaliar_yawdd import simular

AQUI = Path(__file__).parent
PASTA_YAWDD = AQUI / "avaliacao_yawdd"
DB = AQUI / "sonolencia.db"
REFERENCIA = AQUI / "regressao_referencia.json"

# quanto cada número pode andar pro lado ruim antes de acusar regressão
# (bocejo em pontos percentuais; os outros em contagem)
TOLERANCIA = {"recall_bocejo": 0.0, "bocejos_falsos": 0, "dash_com_bocejo": 0,
              "alarmes_falsos": 0, "avisos_falsos": 0, "alarmes_reais_mantidos": 0}
PIOR_SE_SOBE = {"bocejos_falsos", "alarmes_falsos", "avisos_falsos"}


def escolher_series():
    """Pra cada vídeo, a série mais completa (mais colunas) e, no empate, a
    mais nova. As rodadas antigas não têm mandíbula nem sorriso. Retorna
    {arquivo: (linha do resultados.csv, série, caminho do .npz)}."""
    escolhidas = {}
    for csv_path in sorted(PASTA_YAWDD.glob("*/resultados.csv")):
        for r in csv.DictReader(open(csv_path)):
            npz = csv_path.parent / "series" / (r.get("serie") or "")
            if r["erro"] or not npz.is_file():
                continue
            serie = np.load(npz)["serie"]
            chave = (serie.shape[1], csv_path.parent.name)
            if r["arquivo"] not in escolhidas or chave > escolhidas[r["arquivo"]][0]:
                escolhidas[r["arquivo"]] = (chave, r, serie, npz)
    return {a: (r, s, npz) for a, (_k, r, s, npz) in escolhidas.items()}


def medir_yawdd():
    series = escolher_series()
    por_video = {}
    vp = fn = falsos = dash = alarmes = avisos = 0
    for arq, (r, serie, _npz) in sorted(series.items()):
        sim = simular(serie)
        b, al, av = int(sim["bocejos"]), int(sim["alarmes_fechado"]), int(sim["avisos_atencao"])
        nome = str(Path(arq).relative_to(Path(arq).parents[2]))
        por_video[nome] = {"bocejos": b, "alarmes": al, "avisos": av}
        if r["conjunto"] == "mirror" and r["rotulo"] == "bocejo":
            vp += b > 0
            fn += b == 0
        elif r["conjunto"] == "mirror" and r["rotulo"] in ("fala", "normal"):
            falsos += b > 0
        elif r["conjunto"] == "dash":
            dash += b > 0
        alarmes += al
        avisos += av
    return {
        "videos": len(series),
        "recall_bocejo": round(100 * vp / (vp + fn), 1) if vp + fn else None,
        "bocejos_falsos": falsos,
        "dash_com_bocejo": dash,
        "alarmes_falsos": alarmes,
        "avisos_falsos": avisos,
    }, por_video


def sessoes_ao_vivo():
    """Frames do sonolencia.db separados por sessão (coluna session, ou o
    tempo voltando pra trás nas sessões antigas, gravadas sem ela)."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    linhas = con.execute("SELECT session, t_s, level, ear_left, ear_right, ear_avg, mar, head_pitch "
                         "FROM frames ORDER BY rowid").fetchall()
    con.close()
    sessoes, atual = [], []
    for r in linhas:
        if atual and (r[0] != atual[-1][0] or r[1] < atual[-1][1]):
            sessoes.append(atual)
            atual = []
        atual.append(r)
    if atual:
        sessoes.append(atual)
    return sessoes


def medir_alarmes_reais():
    """Episódios de alarme cheio gravados ao vivo que as regras atuais ainda
    disparam. A calibração daquela sessão não foi gravada: usa a mediana do
    olho aberto (nível "aberto") como referência, que fica bem perto dela."""
    if not DB.is_file():
        return {"alarmes_reais": 0, "alarmes_reais_mantidos": 0}
    total = mantidos = 0
    for sessao in sessoes_ao_vivo():
        abertos = [r for r in sessao if r[2] == "aberto"]
        mediana = lambda i: (float(np.median([r[i] for r in abertos if r[i] is not None]))
                             if any(r[i] is not None for r in abertos) else None)
        with contextlib.redirect_stdout(io.StringIO()):
            state = ef.build_state(mediana(5), mediana(6), mediana(7))
        episodios, inicio = [], None
        disparos = []
        for i, (_s, t, nivel, l, r, _e, m, p) in enumerate(sessao):
            if nivel == "fechado" and inicio is None:
                inicio = t
            elif nivel != "fechado" and inicio is not None:
                episodios.append((inicio, t))
                inicio = None
            rosto = l is not None and r is not None
            st = state.update(l, r, m, p, None, rosto, t)
            if st["level"] == "fechado":
                disparos.append(t)
        if inicio is not None:
            episodios.append((inicio, sessao[-1][1]))
        for ini, fim in episodios:
            total += 1
            # a regra nova pode atrasar o alarme (fala, sorriso): vale até
            # a diferença entre 2.5 s e 1.5 s depois do fim do episódio
            folga = core.CLOSED_SECONDS_ALERT_TALKING - core.CLOSED_SECONDS_ALERT
            mantidos += any(ini - 0.5 <= t <= fim + folga for t in disparos)
    return {"alarmes_reais": total, "alarmes_reais_mantidos": mantidos}


def comparar(atual, ref):
    piorou = []
    for k, tol in TOLERANCIA.items():
        a, b = atual.get(k), ref.get(k)
        if a is None or b is None:
            continue
        ruim = (a - b > tol) if k in PIOR_SE_SOBE else (b - a > tol)
        seta = "igual" if a == b else ("PIOROU" if ruim else "melhorou")
        print(f"  {k:24} {b!s:>7} -> {a!s:>7}  {seta}")
        if ruim:
            piorou.append(k)
    return piorou


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salvar", action="store_true", help="Grava os números atuais como nova referência")
    args = ap.parse_args()

    print("Simulando o YawDD...", flush=True)
    numeros, por_video = medir_yawdd()
    print("Repassando as sessões ao vivo do sonolencia.db...", flush=True)
    numeros.update(medir_alarmes_reais())

    if args.salvar or not REFERENCIA.is_file():
        REFERENCIA.write_text(json.dumps({"numeros": numeros, "por_video": por_video},
                                         indent=1, ensure_ascii=False) + "\n")
        print(json.dumps(numeros, indent=1, ensure_ascii=False))
        print(f"Referência gravada em {REFERENCIA.name}.")
        return

    ref = json.loads(REFERENCIA.read_text())
    print("\nComparando com a referência:")
    piorou = comparar(numeros, ref["numeros"])
    mudaram = [(v, ref["por_video"].get(v), d) for v, d in por_video.items() if ref["por_video"].get(v) != d]
    if mudaram:
        print(f"\n{len(mudaram)} vídeo(s) mudaram (antes -> agora):")
        for v, antes, agora in mudaram:
            print(f"  {v}: {antes} -> {agora}")
    if piorou:
        print(f"\nREGRESSÃO: piorou {', '.join(piorou)}.")
        sys.exit(1)
    print("\nNenhum número piorou.")


if __name__ == "__main__":
    main()
