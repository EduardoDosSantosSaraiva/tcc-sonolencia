"""
Validação cruzada por participante da detecção de bocejo no YawDD: o
número "honesto" pra monografia.

Os limiares do bocejo foram ajustados olhando os mesmos 320 vídeos do
Mirror em que o acerto é medido, o que deixa o resultado otimista. Aqui os
participantes são divididos em 5 grupos (dobras). Pra cada dobra, os
limiares são escolhidos de novo usando só as outras 4 (o treino), e o
acerto é medido nessa dobra, com pessoas que a escolha não viu. Juntando
as 5 dobras, cada vídeo é testado uma vez por uma regra que não o viu.

Participante = número + sexo no nome do arquivo (os vídeos com e sem
óculos da mesma pessoa ficam sempre na mesma dobra; separar deixaria a
pessoa no treino e no teste ao mesmo tempo).

Limiares variados (o valor em uso fica no meio de cada lista):
    MAR_THRESHOLD_DEFAULT, MAR_SUSTAIN_RATIO, MIN_YAWN_DURATION, JAW_OPEN_THRESHOLD

Também mede a regra congelada (a que está no core hoje) em cada dobra,
pra mostrar quanto o resultado varia de um grupo de pessoas pra outro.

Uso:
    ./venv/bin/python validacao_cruzada.py              # ~15 min no Pi 5 (4 processos)
    ./venv/bin/python validacao_cruzada.py --dobras 10

Saída em validacao_cruzada/<data_hora>/relatorio.md e resultados.csv.
"""

import argparse
import csv
import itertools
import multiprocessing as mp
import random
import re
import subprocess
import time
from pathlib import Path

import numpy as np

GRADE = {
    "MAR_THRESHOLD_DEFAULT": (0.50, 0.55, 0.60),
    "MAR_SUSTAIN_RATIO": (0.65, 0.73, 0.80),
    "MIN_YAWN_DURATION": (0.6, 0.8, 1.0),
    "JAW_OPEN_THRESHOLD": (0.5, 0.6, 0.7),
}
SEMENTE = 42
SAIDA = Path(__file__).parent / "validacao_cruzada"

_series = {}


def _carregar(itens):
    for arq, npz in itens:
        _series[arq] = np.load(npz)["serie"]


def _rodar(cfg):
    """Bocejos, alarmes e avisos de cada vídeo com essa configuração."""
    import core
    from avaliar_yawdd import simular
    for k, v in cfg.items():
        setattr(core, k, v)
    out = {}
    for arq, serie in _series.items():
        s = simular(serie)
        out[arq] = (int(s["bocejos"]), int(s["alarmes_fechado"]), int(s["avisos_atencao"]), float(s["duracao_s"]))
    return cfg, out


def participante(arquivo, conjunto):
    m = re.match(r"(\d+)-(Female|Male)", Path(arquivo).name)
    return f"{'M' if conjunto == 'mirror' else 'D'}{m.group(1)}{m.group(2)[0]}" if m else Path(arquivo).name


def metricas(videos, bocejos):
    """videos: [(arquivo, rótulo)]; bocejos: {arquivo: n}. Só Mirror."""
    vp = sum(1 for a, r in videos if r == "bocejo" and bocejos[a] > 0)
    fn = sum(1 for a, r in videos if r == "bocejo" and bocejos[a] == 0)
    fp = sum(1 for a, r in videos if r in ("fala", "normal") and bocejos[a] > 0)
    vn = sum(1 for a, r in videos if r in ("fala", "normal") and bocejos[a] == 0)
    rec = vp / (vp + fn) if vp + fn else float("nan")
    prec = vp / (vp + fp) if vp + fp else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if prec == prec and rec == rec and prec + rec else 0.0
    return dict(vp=vp, fn=fn, fp=fp, vn=vn, recall=rec, precisao=prec, f1=f1,
                taxa_fp=fp / (fp + vn) if fp + vn else float("nan"))


def fmt(x):
    return f"{100 * x:.1f}%" if x == x else "n/d"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dobras", type=int, default=5)
    ap.add_argument("--processos", type=int, default=4)
    args = ap.parse_args()

    import core
    from verificar_regressao import escolher_series
    congelada = {k: getattr(core, k) for k in GRADE}
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                cwd=Path(__file__).parent).stdout.strip()
        sujo = subprocess.run(["git", "status", "--porcelain", "core.py", "avaliar_yawdd.py", "eye_fatigue.py"],
                              capture_output=True, text=True, cwd=Path(__file__).parent).stdout.strip()
    except OSError:
        commit, sujo = "?", ""

    series = escolher_series()
    info = {a: (r["conjunto"], r["rotulo"]) for a, (r, _s, _n) in series.items()}
    itens = [(a, npz) for a, (_r, _s, npz) in series.items()]
    del series

    configs = [dict(zip(GRADE, vals)) for vals in itertools.product(*GRADE.values())]
    print(f"{len(configs)} configurações x {len(itens)} vídeos, {args.processos} processos...", flush=True)
    t0 = time.time()
    resultados = {}
    with mp.get_context("spawn").Pool(args.processos, initializer=_carregar, initargs=(itens,)) as pool:
        for i, (cfg, out) in enumerate(pool.imap_unordered(_rodar, configs), 1):
            resultados[tuple(cfg.values())] = out
            if i % 9 == 0 or i == len(configs):
                print(f"  {i}/{len(configs)} ({(time.time() - t0) / 60:.1f} min)", flush=True)
    chave_congelada = tuple(congelada.values())

    # dobras por participante (só Mirror entra na conta do bocejo)
    mirror = [(a, rot) for a, (conj, rot) in info.items() if conj == "mirror" and rot in ("bocejo", "fala", "normal")]
    pessoas = sorted({participante(a, "mirror") for a, _ in mirror})
    random.Random(SEMENTE).shuffle(pessoas)
    dobra_de = {p: i % args.dobras for i, p in enumerate(pessoas)}

    def bocejos_de(chave):
        return {a: v[0] for a, v in resultados[chave].items()}

    linhas_dobra = []
    teste_total = {}
    for d in range(args.dobras):
        treino = [(a, r) for a, r in mirror if dobra_de[participante(a, "mirror")] != d]
        teste = [(a, r) for a, r in mirror if dobra_de[participante(a, "mirror")] == d]
        # melhor F1 no treino; empate: a mais perto da regra congelada
        def nota(chave):
            dist = sum(abs(GRADE[k].index(v) - GRADE[k].index(congelada[k])) for k, v in zip(GRADE, chave))
            return (round(metricas(treino, bocejos_de(chave))["f1"], 6), -dist)
        escolhida = max(resultados, key=nota)
        bt = bocejos_de(escolhida)
        teste_total.update({a: bt[a] for a, _ in teste})
        m_esc = metricas(teste, bt)
        m_cong = metricas(teste, bocejos_de(chave_congelada))
        linhas_dobra.append((d + 1, len({participante(a, "mirror") for a, _ in teste}), len(teste),
                             escolhida, m_esc, m_cong))

    em_todos = metricas(mirror, bocejos_de(chave_congelada))
    cruzada = metricas(mirror, teste_total)

    # alarmes falsos da regra congelada, por dobra (Mirror + Dash, cada um no seu grupo)
    todos = [(a, conj) for a, (conj, _r) in info.items()]
    pessoas_dash = sorted({participante(a, c) for a, c in todos if c == "dash"})
    random.Random(SEMENTE).shuffle(pessoas_dash)
    dobra_de.update({p: i % args.dobras for i, p in enumerate(pessoas_dash)})
    alarmes_dobra = []
    for d in range(args.dobras):
        vids = [a for a, c in todos if dobra_de[participante(a, c)] == d]
        horas = sum(resultados[chave_congelada][a][3] for a in vids) / 3600
        al = sum(resultados[chave_congelada][a][1] for a in vids)
        av = sum(resultados[chave_congelada][a][2] for a in vids)
        alarmes_dobra.append((d + 1, len(vids), horas, al, av))

    pasta = SAIDA / time.strftime("%Y%m%d_%H%M%S")
    pasta.mkdir(parents=True, exist_ok=True)
    with open(pasta / "resultados.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([*GRADE, "arquivo", "conjunto", "rotulo", "participante", "dobra",
                    "bocejos", "alarmes", "avisos", "duracao_s"])
        for chave, out in sorted(resultados.items()):
            for a, (b, al, av, dur) in sorted(out.items()):
                conj, rot = info[a]
                p = participante(a, conj)
                w.writerow([*chave, a, conj, rot, p, dobra_de.get(p, -1) + 1, b, al, av, f"{dur:.1f}"])

    nomes = {"MAR_THRESHOLD_DEFAULT": "pico MAR", "MAR_SUSTAIN_RATIO": "sustenta",
             "MIN_YAWN_DURATION": "duração", "JAW_OPEN_THRESHOLD": "pico mandíbula"}
    desc = lambda chave: ", ".join(f"{nomes[k]} {v}" for k, v in zip(GRADE, chave))
    rel = [
        f"# Validação cruzada por participante — {time.strftime('%d/%m/%Y %H:%M')}",
        "",
        f"Código: commit `{commit}`" + (" (com mudanças não commitadas no core!)" if sujo else "") + ". "
        f"{len(mirror)} vídeos do Mirror, {len(pessoas)} participantes, {args.dobras} dobras "
        f"(semente {SEMENTE}). {len(configs)} combinações de limiares testadas.",
        "",
        "## Resultado",
        "",
        "| Bocejo (Mirror) | Regra ajustada em todos os vídeos | Validação cruzada |",
        "|---|---|---|",
        f"| Sensibilidade (recall) | {fmt(em_todos['recall'])} | **{fmt(cruzada['recall'])}** |",
        f"| Precisão | {fmt(em_todos['precisao'])} | **{fmt(cruzada['precisao'])}** |",
        f"| F1 | {fmt(em_todos['f1'])} | **{fmt(cruzada['f1'])}** |",
        f"| Vídeos de fala/normal com bocejo inventado | {em_todos['fp']} de {em_todos['fp'] + em_todos['vn']} "
        f"({fmt(em_todos['taxa_fp'])}) | {cruzada['fp']} de {cruzada['fp'] + cruzada['vn']} ({fmt(cruzada['taxa_fp'])}) |",
        "",
        "A coluna da validação cruzada é o número pra monografia: cada vídeo foi julgado por limiares "
        "escolhidos sem ver aquela pessoa. A diferença entre as duas colunas mostra o quanto o ajuste "
        "no conjunto inteiro deixava o resultado otimista.",
        "",
        "## Por dobra",
        "",
        "| Dobra | Pessoas | Vídeos | Limiares escolhidos no treino | Recall (teste) | Precisão (teste) | Recall da regra congelada |",
        "|---|---|---|---|---|---|---|",
    ]
    for d, np_, nv, esc, m_esc, m_cong in linhas_dobra:
        rel.append(f"| {d} | {np_} | {nv} | {desc(esc)} | {fmt(m_esc['recall'])} | {fmt(m_esc['precisao'])} | "
                   f"{fmt(m_cong['recall'])} |")
    recs = [m_cong["recall"] for *_x, m_cong in linhas_dobra]
    rel += [
        "",
        f"Regra congelada ({desc(chave_congelada)}): recall entre {fmt(min(recs))} e {fmt(max(recs))} "
        "conforme o grupo de pessoas.",
        "",
        "## Alarmes falsos da regra congelada por dobra (ninguém dorme no YawDD)",
        "",
        "| Dobra | Vídeos | Horas | Alarmes cheios | Por hora | Avisos de atenção | Por hora |",
        "|---|---|---|---|---|---|---|",
    ]
    for d, nv, h, al, av in alarmes_dobra:
        rel.append(f"| {d} | {nv} | {h:.2f} | {al} | {al / h:.1f} | {av} | {av / h:.1f} |")
    rel.append("")
    (pasta / "relatorio.md").write_text("\n".join(rel))
    print("\n".join(rel))
    print(f"Salvo em {pasta}")


if __name__ == "__main__":
    main()
