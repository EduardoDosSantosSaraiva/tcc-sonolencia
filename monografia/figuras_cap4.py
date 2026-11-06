#!/usr/bin/env python3
"""
Gera as figuras do Capítulo 4 a partir dos arquivos de resultado, em SVG
(vetorial, embutido no HTML e no PDF pelo gerar_html.py):

    figuras/figura2_autoteste.svg      cenários aprovados em cada rodada do autoteste
    figuras/figura3_bocejo_grupos.svg  sensibilidade do bocejo por grupo, versão
                                       inicial (26/09) x versão final, no YawDD
    figuras/figura4_uta_piscada.svg    duração média da piscada de cada participante,
                                       alerta x sonolento, na UTA-RLDD
    figuras/figura4_benchmark.svg      FPS e temperatura no teste de resistência
                                       (Figura 5 no texto; o nome do arquivo ficou)

Sempre pega o resultado mais recente de cada medição: depois de uma rodada
nova, é só rodar de novo.

Uso:
    ../venv/bin/python figuras_cap4.py
"""

import csv
import re
from pathlib import Path

import matplotlib

matplotlib.use("svg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

# vírgula decimal nos eixos, como no texto
VIRGULA = FuncFormatter(lambda v, _: f"{v:g}".replace(".", ","))

AQUI = Path(__file__).parent
PROJETO = AQUI.parent
FIGURAS = AQUI / "figuras"

LARGURA_POL = 6.3          # 16 cm: a largura útil da página do TCC-modelo
AZUL = "#2a78d6"           # cor principal (um tom só: imprime como cinza médio)
AZUL_CLARO = "#86b6ef"     # passo mais claro da mesma rampa (versão inicial)
AZUL_ESCURO = "#1c5cab"    # passo mais escuro (versão final)
GRADE = "#e4e3df"
TEXTO = "#0b0b0b"
TEXTO_2 = "#52514e"

plt.rcParams.update({
    "font.family": "Liberation Sans",   # clone métrico da Arial, instalado no Pi
    "font.size": 9,
    "svg.fonttype": "none",          # texto continua texto no SVG
    "axes.edgecolor": GRADE,
    "axes.linewidth": 0.8,
    "axes.labelcolor": TEXTO_2,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.color": TEXTO_2,
    "ytick.color": TEXTO_2,
    "xtick.major.size": 0,
    "ytick.major.size": 0,
    "axes.grid": True,
    "grid.color": GRADE,
    "grid.linewidth": 0.8,
    "grid.linestyle": "-",
    "axes.axisbelow": True,
    "legend.frameon": False,
})


def salvar(fig, nome):
    FIGURAS.mkdir(exist_ok=True)
    destino = FIGURAS / nome
    fig.savefig(destino, bbox_inches="tight", pad_inches=0.04)
    plt.close(fig)
    print(f"  {destino.relative_to(AQUI)}")


# ---------------------------------------------------------------------------
# Figura 2: autoteste
# ---------------------------------------------------------------------------

def rodadas_autoteste():
    """[(rótulo 'dd/mm hh:mm', aprovados)] das rodadas completas (8 cenários)."""
    rodadas = []
    for arq in sorted((PROJETO / "autoteste_resultados").glob("*.md")):
        m = re.search(r"\*\*(\d+)/(\d+) cenários passaram", arq.read_text())
        d = re.match(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})", arq.stem)
        if m and d and m.group(2) == "8":
            rodadas.append((f"{d.group(3)}/{d.group(2)}\n{d.group(4)}:{d.group(5)}", int(m.group(1))))
    return rodadas


def figura_autoteste():
    rodadas = rodadas_autoteste()
    if not rodadas:
        print("  (sem relatórios do autoteste)")
        return
    rotulos, valores = zip(*rodadas)
    fig, ax = plt.subplots(figsize=(LARGURA_POL, 2.3))
    x = range(len(valores))
    ax.bar(x, valores, width=0.45, color=AZUL)
    ax.set_xticks(list(x), rotulos, fontsize=7.5)
    ax.set_ylim(0, 8.9)
    ax.set_yticks(range(0, 9, 2))
    ax.set_ylabel("Cenários aprovados (de 8)")
    ax.grid(axis="x", visible=False)
    # rótulo só na última rodada, a que vale como resultado
    ax.annotate(f"{valores[-1]}/8", (len(valores) - 1, valores[-1]), xytext=(0, 3),
                textcoords="offset points", ha="center", va="bottom", color=TEXTO, fontsize=9)
    salvar(fig, "figura2_autoteste.svg")


# ---------------------------------------------------------------------------
# Figura 3: bocejo por grupo, antes x depois
# ---------------------------------------------------------------------------

GRUPOS = [  # (rótulo, filtro sobre a linha do resultados.csv, nome no relatório antigo)
    ("Todos", lambda r: True, None),
    ("Sem óculos", lambda r: r["oculos"] == "sem", "Sem óculos"),
    ("Óculos de grau", lambda r: r["oculos"] == "grau", "Óculos de grau"),
    ("Óculos escuros", lambda r: r["oculos"] == "escuro", "Óculos escuros"),
    ("Mulheres", lambda r: r["genero"] == "F", "Mulheres"),
    ("Homens", lambda r: r["genero"] == "M", "Homens"),
]


def avaliacao_final():
    """resultados.csv da rodada completa mais recente do YawDD (349 vídeos)."""
    for csv_path in sorted((PROJETO / "avaliacao_yawdd").glob("*/resultados.csv"), reverse=True):
        linhas = [r for r in csv.DictReader(open(csv_path)) if not r["erro"]]
        if len(linhas) >= 349:
            return csv_path, linhas
    return None, []


def recall_antes():
    """Sensibilidade por grupo da versão inicial (relatório de 26/09)."""
    arq = PROJETO / "avaliacao_yawdd" / "20260923_150005" / "relatorio_antes_30-09.md"
    texto = arq.read_text()
    valores = {"Todos": float(re.search(r"Sensibilidade \(recall\): ([\d.]+)%", texto).group(1))}
    for nome, _, _, recall, _ in re.findall(r"^\| ([^|]+?) \| (\d+) \| ([\d.]+)% \| ([\d.]+)% \| ([\d.]+)% \|$",
                                            texto, re.M):
        valores[nome] = float(recall)
    return valores


def figura_bocejo():
    csv_path, linhas = avaliacao_final()
    if not linhas:
        print("  (rodada completa do YawDD ainda não terminou)")
        return
    antes = recall_antes()
    mirror_bocejo = [r for r in linhas if r["conjunto"] == "mirror" and r["rotulo"] == "bocejo"]
    depois = {}
    for rotulo, filtro, _ in GRUPOS:
        grupo = [r for r in mirror_bocejo if filtro(r)]
        depois[rotulo] = 100 * sum(int(r["bocejos"]) > 0 for r in grupo) / len(grupo)

    fig, ax = plt.subplots(figsize=(LARGURA_POL, 2.6))
    for i, (rotulo, _, nome_antigo) in enumerate(GRUPOS):
        a = antes[nome_antigo or rotulo]
        d = depois[rotulo]
        ax.plot([a, d], [i, i], color=GRADE, linewidth=2, solid_capstyle="round", zorder=1)
        ax.scatter([a], [i], s=42, color=AZUL_CLARO, edgecolors="white", linewidths=1.5, zorder=2)
        ax.scatter([d], [i], s=42, color=AZUL_ESCURO, edgecolors="white", linewidths=1.5, zorder=3)
        ax.annotate(f"{d:.1f}%".replace(".", ","), (d, i), xytext=(7, 0), textcoords="offset points",
                    va="center", ha="left", color=TEXTO, fontsize=8.5)
    ax.set_yticks(range(len(GRUPOS)), [g[0] for g in GRUPOS])
    ax.invert_yaxis()
    ax.set_xlim(0, 108)
    ax.set_xticks(range(0, 101, 20), [f"{v}%" for v in range(0, 101, 20)])
    ax.set_xlabel("Sensibilidade na detecção de bocejo (vídeos de bocejo do conjunto Mirror)")
    ax.grid(axis="y", visible=False)
    ax.spines["left"].set_visible(False)
    marcas = [plt.Line2D([], [], marker="o", linestyle="", markersize=6.5, color=c, markeredgecolor="white")
              for c in (AZUL_CLARO, AZUL_ESCURO)]
    ax.legend(marcas, ["Versão inicial (26/09/2026)", "Versão final"], loc="lower center",
              bbox_to_anchor=(0.5, 1.0), ncol=2, fontsize=8.5, handletextpad=0.3, columnspacing=1.6)
    salvar(fig, "figura3_bocejo_grupos.svg")
    print(f"    (dados: {csv_path.parent.name})")


# ---------------------------------------------------------------------------
# Figura 4: UTA-RLDD, duração da piscada alerta x sonolento
# ---------------------------------------------------------------------------

def uta_por_video():
    """por_video.csv da análise mais recente da UTA-RLDD."""
    pastas = sorted((PROJETO / "uta_rldd" / "resultados").glob("*/por_video.csv"))
    if not pastas:
        return None, []
    return pastas[-1], list(csv.DictReader(open(pastas[-1])))


def figura_uta():
    arq, linhas = uta_por_video()
    if not linhas:
        print("  (análise da UTA-RLDD ainda não rodou)")
        return
    por_pessoa = {}
    for r in linhas:
        por_pessoa.setdefault(r["pessoa_id"], {})[r["rotulo"]] = float(r["dur_piscada_ms"])
    pares = [(p, v["alerta"], v["sonolento"]) for p, v in por_pessoa.items()
             if "alerta" in v and "sonolento" in v]
    pares.sort(key=lambda x: x[2] / x[1], reverse=True)     # maior aumento no topo

    fig, ax = plt.subplots(figsize=(LARGURA_POL, 0.2 * len(pares) + 0.9))
    for i, (_p, a, d) in enumerate(pares):
        ax.plot([a, d], [i, i], color=GRADE, linewidth=2, solid_capstyle="round", zorder=1)
        ax.scatter([a], [i], s=36, color=AZUL_CLARO, edgecolors="white", linewidths=1.2, zorder=2)
        ax.scatter([d], [i], s=36, color=AZUL_ESCURO, edgecolors="white", linewidths=1.2, zorder=3)
    # numeração da própria base (Fold1-01 -> 1), para casar com o texto
    ax.set_yticks(range(len(pares)), [str(int(p.split("-")[1])) for p, _, _ in pares], fontsize=8)
    ax.set_ylabel("Participante (numeração da base)")
    ax.invert_yaxis()
    ax.set_xscale("log")
    marcas = [50, 100, 200, 500, 1000, 2000]
    ax.set_xticks(marcas, [f"{m:,}".replace(",", ".") for m in marcas])   # 1.000, como no texto
    ax.minorticks_off()
    ax.set_xlim(70, 2000)
    ax.set_xlabel("Duração média da piscada (ms, escala logarítmica)")
    ax.grid(axis="y", visible=False)
    ax.spines["left"].set_visible(False)
    leg = [plt.Line2D([], [], marker="o", linestyle="", markersize=6, color=c, markeredgecolor="white")
           for c in (AZUL_CLARO, AZUL_ESCURO)]
    ax.legend(leg, ["Vídeo alerta", "Vídeo sonolento"], loc="lower center",
              bbox_to_anchor=(0.5, 1.0), ncol=2, fontsize=8.5, handletextpad=0.3, columnspacing=1.6)
    salvar(fig, "figura4_uta_piscada.svg")
    print(f"    (dados: {arq.parent.name}, {len(pares)} participantes)")


# ---------------------------------------------------------------------------
# Figura 5: teste de resistência
# ---------------------------------------------------------------------------

def benchmark_resistencia():
    """intervalos.csv do teste mais longo e mais recente (de preferência 15 min ou mais)."""
    candidatos = []
    for arq in (PROJETO / "benchmark_resultados").glob("*/intervalos.csv"):
        if (arq.parent / "NAO_USAR.txt").exists():   # medição contaminada
            continue
        linhas = list(csv.DictReader(open(arq)))
        if linhas:
            candidatos.append((float(linhas[-1]["min"]) >= 15, arq.parent.name, arq, linhas))
    if not candidatos:
        return None, []
    _, _, arq, linhas = max(candidatos)
    return arq, linhas


def figura_benchmark():
    arq, linhas = benchmark_resistencia()
    if not linhas:
        print("  (sem benchmark)")
        return
    minutos = [float(r["min"]) for r in linhas]
    fps = [float(r["fps"]) for r in linhas]
    temp = [float(r["temp_c"]) for r in linhas]
    # duas medidas de escala diferente: dois gráficos com o mesmo eixo de tempo,
    # nunca um gráfico com dois eixos verticais
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(LARGURA_POL, 3.4), sharex=True)
    ax1.plot(minutos, fps, color=AZUL, linewidth=1.6, solid_joinstyle="round", solid_capstyle="round")
    ax1.set_ylim(0, max(fps) * 1.2)
    ax1.set_ylabel("Quadros por segundo")
    ax2.plot(minutos, temp, color=AZUL, linewidth=1.6, solid_joinstyle="round", solid_capstyle="round")
    ax2.set_ylim(min(30, min(temp) - 5), max(90, max(temp) + 5))
    ax2.set_ylabel("Temperatura (°C)")
    ax2.set_xlabel("Tempo de execução (min)")
    ax2.set_xlim(0, max(minutos))
    for ax, serie, fmt in ((ax1, fps, "{:.1f} fps"), (ax2, temp, "{:.1f} °C")):
        ax.grid(axis="x", visible=False)
        ax.annotate(fmt.format(serie[-1]).replace(".", ","), (minutos[-1], serie[-1]), xytext=(0, 5),
                    textcoords="offset points", ha="right", va="bottom", color=TEXTO, fontsize=8.5)
    for ax in (ax1, ax2):
        ax.xaxis.set_major_formatter(VIRGULA)
        ax.yaxis.set_major_formatter(VIRGULA)
    fig.align_ylabels((ax1, ax2))
    salvar(fig, "figura4_benchmark.svg")
    print(f"    (dados: {arq.parent.name}, {minutos[-1]:.0f} min"
          + ("" if minutos[-1] >= 15 else "; ainda não é o teste de resistência") + ")")


if __name__ == "__main__":
    print("Figuras do Capítulo 4:")
    figura_autoteste()
    figura_bocejo()
    figura_uta()
    figura_benchmark()
