"""Números da Seção 4.5 / 3.7 / Cap. 5 / Resumo a partir do por_video.csv da UTA-RLDD.
Uso (da pasta do projeto): venv/bin/python uta_rldd/extras_uta.py uta_rldd/resultados/<data>/por_video.csv"""
import csv
import statistics as st
import sys

sys.path[:0] = ["uta_rldd", "."]
from analisar_uta import wilcoxon_exato  # noqa: E402


def f(x):
    return float(x) if x not in ("", None) else float("nan")


def b(x):
    return x == "True"


linhas = list(csv.DictReader(open(sys.argv[1])))
pp = {}
for r in linhas:
    pp.setdefault(r["pessoa_id"], {})[r["rotulo"]] = r
pares = {p: v for p, v in sorted(pp.items()) if "alerta" in v and "sonolento" in v}
A = {p: v["alerta"] for p, v in pares.items()}
S = {p: v["sonolento"] for p, v in pares.items()}
N = len(pares)
print(f"== {N} pares; pessoas: {', '.join(p.split('-')[1] for p in pares)}")

# --- Seção 3.7: vídeos usados
vids = list(A.values()) + list(S.values())
horas = sum(f(v["duracao_min"]) for v in vids) / 60
tams = sorted({v["tamanho"] for v in vids}, key=lambda t: [int(x) for x in t.split("x")] if "x" in t else [0])
fps = [f(v["fps"]) for v in vids]
area = lambda t: int(t.split("x")[0]) * int(t.split("x")[1]) if "x" in t else 0  # noqa: E731
print(f"vídeos {len(vids)}, horas {horas:.1f}, menor {min(tams, key=area)}, maior {max(tams, key=area)}, "
      f"fps {min(fps):.1f} a {max(fps):.1f}")

# --- 4.5 parágrafo 1
falhou = [(p, r) for p, v in pares.items() for r, x in v.items() if r in ("alerta", "sonolento") and not b(x["proprio_calib_ok"])]
print(f"calibração própria falhou: {falhou}")
print(f"rosto mínimo: {min(f(v['pct_rosto']) for v in vids):.1f}%  (vídeo: "
      f"{min(vids, key=lambda v: f(v['pct_rosto']))['arquivo']})")

# --- Quadro 6
med = [("dur_piscada_ms", "Duração média da piscada"), ("dur_piscada_p90_ms", "p90"),
       ("pct_piscadas_longas", "piscadas >= 0,5 s %"), ("fech_1s_h", "fechamentos >=1s /h"),
       ("perclos_pct", "PERCLOS %"), ("piscadas_min", "piscadas/min")]
print("\n== Quadro 6")
for k, nome in med:
    a = [f(A[p][k]) for p in pares]
    s = [f(S[p][k]) for p in pares]
    sub = sum(y > x for x, y in zip(a, s)); cai = sum(y < x for x, y in zip(a, s))
    n, pv = wilcoxon_exato([y - x for x, y in zip(a, s)])
    print(f"{nome:28s} alerta {st.median(a):7.1f}  sono {st.median(s):7.1f}  subiu {sub} caiu {cai} "
          f"igual {N - sub - cai}  p={pv:.2e}")

d = {p: (f(A[p]["dur_piscada_ms"]), f(S[p]["dur_piscada_ms"])) for p in pares}
print("piscada caiu/igual:", {p: (round(a), round(s)) for p, (a, s) in d.items() if s <= a})
al = [a for a, _ in d.values()]
print(f"piscada alerta: min {min(al):.0f} máx {max(al):.0f} ms")
mais_curta = sum(1 for p, (a, s) in d.items() if any(s < a2 for q, (a2, _) in d.items() if q != p))
print(f"participantes cuja piscada com sono < piscada alerta de algum outro: {mais_curta}")
f_a = [f(A[p]["fech_1s_h"]) for p in pares]
print(f"vídeos alerta sem fechamento >=1s: {sum(x == 0 for x in f_a)} de {N}")
print("fechamento >=1s caiu:", [(p, round(f(A[p]['fech_1s_h']), 1), round(f(S[p]['fech_1s_h']), 1))
                                for p in pares if f(S[p]["fech_1s_h"]) < f(A[p]["fech_1s_h"])])
lentos = [(p, round(f(A[p]["dur_piscada_ms"])), round(f(A[p]["perclos_pct"]), 1),
           round(f(S[p]["dur_piscada_ms"])), round(f(S[p]["perclos_pct"]), 1))
          for p in pares if f(A[p]["dur_piscada_ms"]) >= 230]
print("piscada alerta >= 230 ms (alerta ms, perclos | sono ms, perclos):", lentos)


# --- Quadro 7
def contagem(v, modo, k):          # nº de eventos do vídeo
    return round(f(v[f"{modo}_{k}_h"]) * f(v["duracao_min"]) / 60)


def bloco(nome, vs, modo):
    vs = [v for v in vs if b(v[f"{modo}_calib_ok"])]
    h = sum(f(v["duracao_min"]) for v in vs) / 60
    alm = sum(contagem(v, modo, "alarmes") for v in vs)
    av = sum(contagem(v, modo, "avisos") for v in vs)
    print(f"{nome:26s} n={len(vs)}  algum alarme/aviso {sum(b(v[f'{modo}_sinalizou']) for v in vs)}  "
          f"com alarme {sum(f(v[f'{modo}_alarmes_h']) > 0 for v in vs)}  alarmes {alm:.0f} ({alm / h:.1f}/h)  "
          f"avisos {av:.0f} ({av / h:.1f}/h)  em {h:.1f} h  pisc.longas/h mediana "
          f"{st.median(f(v[f'{modo}_pisc_longas_h']) for v in vs):.1f}  "
          f"bocejo em {sum(f(v[f'{modo}_bocejos_h']) > 0 for v in vs)}  queda em {sum(f(v[f'{modo}_quedas_h']) > 0 for v in vs)}")


print("\n== Quadro 7")
bloco("alerta (próprio)", A.values(), "proprio")
bloco("sonolento (próprio)", S.values(), "proprio")
bloco("sonolento (calib. alerta)", S.values(), "cruz")
bloco("alerta (cruz, conferência)", A.values(), "cruz")

print("\n== p por participante (sistema)")
for modo in ("proprio", "cruz"):
    for k in ("pisc_longas", "avisos", "alarmes", "bocejos", "quedas"):
        ok = [p for p in pares if b(A[p][f"{modo}_calib_ok"]) and b(S[p][f"{modo}_calib_ok"])]
        if modo == "cruz":     # comparação do relatório: alerta próprio x sono cruz
            dif = [f(S[p]["cruz_" + k + "_h"]) - f(A[p]["proprio_" + k + "_h"]) for p in pares if b(S[p]["cruz_calib_ok"])]
        else:
            dif = [f(S[p][f"{modo}_{k}_h"]) - f(A[p][f"{modo}_{k}_h"]) for p in ok]
        n, pv = wilcoxon_exato(dif)
        print(f"{modo:8s} {k:12s} sobe {sum(x > 0 for x in dif)} cai {sum(x < 0 for x in dif)} n={n} p={pv:.2e}")

# EAR de referência
ear = [(p, f(S[p]["proprio_ear_calib"]), f(A[p]["proprio_ear_calib"])) for p in pares
       if b(S[p]["proprio_calib_ok"]) and b(A[p]["proprio_calib_ok"])]
print(f"\nEAR de referência menor no sonolento: {sum(s < a for _, s, a in ear)} de {len(ear)}")

# --- sonolentos sem alerta (calibração do alerta)
print("\n== sonolentos x fechamentos >= 1 s (calibração do alerta)")
com = [p for p in pares if f(S[p]["fech_1s_h"]) > 0]
sem = [p for p in pares if f(S[p]["fech_1s_h"]) == 0]
print(f"com fechamento: {len(com)}, alertou {sum(b(S[p]['cruz_sinalizou']) for p in com)}")
print(f"sem fechamento: {len(sem)}, alertou {sum(b(S[p]['cruz_sinalizou']) for p in sem)} "
      f"(com alarme: {sum(f(S[p]['cruz_alarmes_h']) > 0 for p in sem)})")
mudos = [p for p in pares if not b(S[p]["cruz_sinalizou"])]
print(f"sem alerta: {len(mudos)}:")
for p in mudos:
    v = S[p]
    nf = f(v["fech_1s_h"]) * f(v["duracao_min"]) / 60
    print(f"   {p}: fechamentos>=1s {nf:.0f} em {f(v['duracao_min']):.1f} min, piscada {f(v['dur_piscada_ms']):.0f} ms "
          f"(alerta {f(A[p]['dur_piscada_ms']):.0f}), perclos {f(v['perclos_pct']):.1f}%")
print("com fechamento e sem alerta:", [p for p in com if not b(S[p]["cruz_sinalizou"])])
