#!/usr/bin/env python3
"""
Análise da UTA-RLDD: a mesma pessoa filmada alerta, pouco vigilante e com
sono de verdade (autoavaliação pela escala KSS). Pergunta: o que o sistema
mede e decide muda quando a pessoa está com sono?

Duas partes, para cada vídeo (séries de processar_uta.py):

1. Medidas do olho, sem as regras de decisão. Referência de olho aberto =
   mediana do EAR do próprio vídeo (cada vídeo foi gravado num dia, às vezes
   com outro aparelho/posição, então comparar o EAR cru entre vídeos não
   vale). "Fechado" = EAR abaixo de CLOSED_RATIO dessa referência, como no
   sistema; PERCLOS = tempo com EAR abaixo de PERCLOS_CLOSED_RATIO dela.
2. O sistema inteiro (avaliar_yawdd.simular, mesma lógica do programa ao
   vivo): alarmes cheios, avisos de atenção, piscadas longas, bocejos e
   quedas de cabeça por hora. Em dois modos:
     - "calibra no próprio vídeo": liga o sistema já naquele estado;
     - "calibra alerta": calibração tirada do vídeo da pessoa alerta e usada
       no vídeo com sono (motorista que liga o sistema descansado). Só vale
       se a câmera não mudou de lugar entre os vídeos: o relatório mostra o
       EAR de olho aberto de cada vídeo pra conferir.

Comparação pareada (mesma pessoa, alerta x sonolento): em quantas pessoas a
medida subiu e teste exato de postos sinalizados de Wilcoxon (bilateral).

Uso (da pasta do projeto):
    ./venv/bin/python uta_rldd/analisar_uta.py                 # séries em uta_rldd/series
    ./venv/bin/python uta_rldd/analisar_uta.py --series DIR
Saída: uta_rldd/resultados/<data_hora>/por_video.csv e relatorio.md
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI.parent))

import core  # noqa: E402
from avaliar_yawdd import calibrar_serie, simular  # noqa: E402

ROTULOS = ("alerta", "pouco_vigilante", "sonolento")
NOME = {"alerta": "alerta", "pouco_vigilante": "pouco vigilante", "sonolento": "sonolento"}


# ------------------------------------------------------------------ medidas

def mediana_movel(x, n=core.SMOOTHING_WINDOW):
    """Mediana de n quadros centrada (como a suavização do sistema); NaN
    (sem rosto) continua NaN."""
    if len(x) < n:
        return x.copy()
    pad = n // 2
    xp = np.concatenate([np.full(pad, np.nan), x, np.full(pad, np.nan)])
    janelas = np.lib.stride_tricks.sliding_window_view(xp, n)
    with np.errstate(all="ignore"), _sem_aviso():
        m = np.nanmedian(janelas, axis=1)
    m[np.isnan(x)] = np.nan
    return m


class _sem_aviso:
    def __enter__(self):
        import warnings
        self._w = warnings.catch_warnings()
        self._w.__enter__()
        warnings.simplefilter("ignore", RuntimeWarning)

    def __exit__(self, *a):
        self._w.__exit__(*a)


def trechos(mask):
    """(início, fim exclusivo) de cada trecho seguido de True."""
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def medidas_olho(serie):
    t = serie[:, 0]
    with _sem_aviso():
        ear = np.nanmean(serie[:, 1:3], axis=1)
    ear[serie[:, 6] <= 0] = np.nan
    ear = mediana_movel(ear)
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 1 / 30
    com_rosto = ~np.isnan(ear)
    min_rosto = com_rosto.sum() * dt / 60
    ref = float(np.nanmedian(ear)) if com_rosto.any() else float("nan")
    fechado = ear < core.CLOSED_RATIO * ref          # NaN -> False: rosto sumido corta o trecho
    duracoes = np.array([(f - i) * dt for i, f in trechos(fechado)])
    perclos = float(np.mean(ear[com_rosto] < core.PERCLOS_CLOSED_RATIO * ref)) if com_rosto.any() else float("nan")
    n = len(duracoes)
    return dict(
        fps=1 / dt, duracao_min=(t[-1] - t[0] + dt) / 60, pct_rosto=100 * com_rosto.mean(),
        ear_aberto=ref,
        piscadas_min=n / min_rosto if min_rosto else float("nan"),
        dur_piscada_ms=1000 * float(np.mean(duracoes)) if n else float("nan"),
        dur_piscada_p90_ms=1000 * float(np.percentile(duracoes, 90)) if n else float("nan"),
        pct_piscadas_longas=100 * float(np.mean(duracoes >= core.LONG_BLINK_SECONDS)) if n else float("nan"),
        fech_1s_h=float(np.sum(duracoes >= 1.0)) / (min_rosto / 60) if min_rosto else float("nan"),
        perclos_pct=100 * perclos,
    )


def medidas_sistema(serie, calibracao=None):
    s = simular(serie, calibracao=calibracao)
    h = float(s["duracao_s"]) / 3600 or float("nan")
    return dict(calib_ok=bool(s["calibracao_ok"]), ear_calib=s["ear_baseline"],
                alarmes_h=s["alarmes_fechado"] / h, avisos_h=s["avisos_atencao"] / h,
                pisc_longas_h=s["piscadas_longas"] / h, bocejos_h=s["bocejos"] / h,
                quedas_h=s["quedas_cabeca"] / h,
                sinalizou=bool(s["alarmes_fechado"] or s["avisos_atencao"]))


# --------------------------------------------------------------- estatística

def wilcoxon_exato(d):
    """Teste de postos sinalizados de Wilcoxon, exato, bilateral. Zeros saem;
    empates de |d| levam o posto médio. Devolve (n usado, p)."""
    d = np.asarray([x for x in d if x == x and x != 0], dtype=float)
    n = len(d)
    if n == 0:
        return 0, float("nan")
    ordem = np.argsort(np.abs(d))
    postos = np.empty(n)
    a = np.abs(d)[ordem]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and a[j + 1] == a[i]:
            j += 1
        postos[ordem[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    # Distribuição exata de W sob H0 por programação dinâmica (cada posto entra
    # com sinal + ou - com chance 1/2). Postos médios são múltiplos de 0,5, então
    # trabalho com o dobro deles, que é inteiro. Enumerar as 2^n combinações
    # (versão antiga) não termina com 52 pares.
    dobro = np.rint(postos * 2).astype(int)
    contagem = [1] + [0] * int(dobro.sum())   # contagem[k] = nº de subconjuntos com soma k
    for r in dobro:
        for k in range(len(contagem) - 1, r - 1, -1):
            contagem[k] += contagem[k - r]
    w2 = int(dobro[d > 0].sum())
    centro2 = int(dobro.sum())                # 2 * centro, com centro = soma/2
    # |2W - soma| >= |2w - soma|, tudo em inteiros (dobro): |2k - centro2| >= |2w2 - centro2|
    extremos = sum(c for k, c in enumerate(contagem) if abs(2 * k - centro2) >= abs(2 * w2 - centro2))
    p = extremos / 2 ** n
    return n, float(p)


# ------------------------------------------------------------------ relatório

def fmt(x, casas=1):
    if isinstance(x, bool):
        return "sim" if x else "não"
    if isinstance(x, str):
        return x
    return "–" if x != x else f"{x:.{casas}f}".replace(".", ",")


MEDIDAS_PAREADAS = [
    # (chave, texto, casas, "sono deveria" subir/descer)
    ("dur_piscada_ms", "Duração média da piscada (ms)", 0, "sobe"),
    ("dur_piscada_p90_ms", "Duração da piscada, percentil 90 (ms)", 0, "sobe"),
    ("pct_piscadas_longas", f"Piscadas ≥ {fmt(core.LONG_BLINK_SECONDS)} s (% das piscadas)", 1, "sobe"),
    ("fech_1s_h", "Olho fechado ≥ 1 s (por hora)", 1, "sobe"),
    ("perclos_pct", "PERCLOS do vídeo inteiro (%)", 1, "sobe"),
    ("piscadas_min", "Piscadas por minuto", 1, "?"),
    ("pisc_longas_h", "Sistema: piscadas longas por hora", 1, "sobe"),
    ("avisos_h", "Sistema: avisos de atenção por hora", 1, "sobe"),
    ("alarmes_h", "Sistema: alarmes cheios por hora", 1, "sobe"),
    ("bocejos_h", "Sistema: bocejos por hora", 1, "sobe"),
    ("quedas_h", "Sistema: quedas de cabeça por hora", 1, "sobe"),
]


def comparar(linhas, chave_sistema="proprio"):
    """Pares alerta x sonolento por pessoa, para cada medida."""
    por_pessoa = {}
    for ln in linhas:
        por_pessoa.setdefault(ln["pessoa_id"], {})[ln["rotulo"]] = ln
    pares = [(p, v["alerta"], v["sonolento"]) for p, v in sorted(por_pessoa.items())
             if "alerta" in v and "sonolento" in v]
    saida = []
    if not pares:
        return pares, saida
    for chave, texto, casas, espera in MEDIDAS_PAREADAS:
        k = chave if chave in pares[0][1] else f"{chave_sistema}_{chave}" if pares else chave
        a = np.array([pa[k] for _p, pa, _ps in pares], dtype=float)
        s = np.array([ps[k] for _p, _pa, ps in pares], dtype=float)
        ok = ~np.isnan(a) & ~np.isnan(s)
        if k != chave:
            # calibração que falhou deixa o sistema nos limiares padrão (no
            # carro ele pediria para refazer): número sem sentido, fica fora
            ok &= np.array([pa[f"{chave_sistema}_calib_ok"] and ps[f"{chave_sistema}_calib_ok"]
                            for _p, pa, ps in pares])
        subiu = int(np.sum(s[ok] > a[ok]))
        desceu = int(np.sum(s[ok] < a[ok]))
        n, p = wilcoxon_exato(s[ok] - a[ok])
        saida.append(dict(texto=texto, casas=casas, espera=espera,
                          med_a=float(np.median(a[ok])) if ok.any() else float("nan"),
                          med_s=float(np.median(s[ok])) if ok.any() else float("nan"),
                          subiu=subiu, desceu=desceu, n=int(ok.sum()), p=p))
    return pares, saida


def tabela_comparacao(saida):
    out = ["| Medida | Pessoas | Alerta (mediana) | Sonolento (mediana) | Subiu / caiu / igual | p (Wilcoxon) |",
           "|---|---:|---:|---:|:---:|---:|"]
    for r in saida:
        iguais = r["n"] - r["subiu"] - r["desceu"]
        out.append(f"| {r['texto']} | {r['n']} | {fmt(r['med_a'], r['casas'])} | {fmt(r['med_s'], r['casas'])} | "
                   f"{r['subiu']} / {r['desceu']} / {iguais} | {fmt(r['p'], 3)} |")
    return out


def gerar_relatorio(linhas, pasta, origem):
    pares, prop = comparar(linhas, "proprio")
    com_cruz = [ln for ln in linhas if "cruz_alarmes_h" in ln]
    _, cruz = comparar(com_cruz, "cruz") if com_cruz else ([], [])
    pessoas = sorted({ln["pessoa_id"] for ln in linhas})
    md = [f"# UTA-RLDD: alerta x sono real ({time.strftime('%d/%m/%Y %H:%M')})", "",
          f"Séries: `{origem}`. {len(linhas)} vídeos de {len(pessoas)} pessoas; "
          f"{len(pares)} pessoas com o par alerta e sonolento.", "",
          "Rótulos da base (autoavaliação pela escala KSS): alerta, pouco vigilante, sonolento. "
          "Cada vídeo tem ~10 min.", ""]
    if len(pares) < 6:
        md += [f"**Atenção: só {len(pares)} pares.** Com menos de 6 pessoas o teste de Wilcoxon "
               "bilateral não chega a p < 0,05 nem com todas indo no mesmo sentido.", ""]

    md += ["## 1. Vídeos", "",
           "| Pessoa | Estado | Tamanho | fps | Rosto (%) | EAR aberto | Calibração (EAR) | "
           "Piscadas/min | Piscada média (ms) | PERCLOS (%) | Alarmes/h | Avisos/h |",
           "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for ln in sorted(linhas, key=lambda x: (x["pessoa_id"], ROTULOS.index(x["rotulo"]))):
        md.append(f"| {ln['pessoa_id']} | {NOME[ln['rotulo']]} | {ln['tamanho']} | {fmt(ln['fps'], 0)} | "
                  f"{fmt(ln['pct_rosto'])} | {fmt(ln['ear_aberto'], 3)} | "
                  f"{ln['proprio_ear_calib'].replace('.', ',') or 'falhou'} | {fmt(ln['piscadas_min'])} | "
                  f"{fmt(ln['dur_piscada_ms'], 0)} | {fmt(ln['perclos_pct'])} | "
                  f"{fmt(ln['proprio_alarmes_h'])} | {fmt(ln['proprio_avisos_h'])} |")
    md += ["", "EAR aberto = mediana do EAR do vídeo. Se o da mesma pessoa muda muito entre os vídeos, "
           "a câmera mudou de lugar ou de aparelho (o modo \"calibra alerta\" da seção 3 fica "
           "menos confiável para ela).", ""]

    md += ["## 2. Alerta x sonolento, mesma pessoa (sistema calibrado no próprio vídeo)", ""]
    md += tabela_comparacao(prop)
    validos = [(pa, ps) for _p, pa, ps in pares if pa["proprio_calib_ok"] and ps["proprio_calib_ok"]]
    sin_a = sum(pa["proprio_sinalizou"] for pa, _s in validos)
    sin_s = sum(ps["proprio_sinalizou"] for _a, ps in validos)
    md += ["", f"Medidas do sistema só nas pessoas com calibração boa nos dois vídeos ({len(validos)} de "
           f"{len(pares)}). Vídeos com pelo menos um alarme ou aviso: alerta {sin_a} de {len(validos)}, "
           f"sonolento {sin_s} de {len(validos)}.", ""]

    if cruz:
        md += ["## 3. Sono com a calibração do vídeo alerta (motorista que liga o sistema descansado)", "",
               "Só as medidas do sistema mudam (as do olho usam a referência do próprio vídeo).", ""]
        md += tabela_comparacao([r for r in cruz if r["texto"].startswith("Sistema")])
        val_c = [ps for _p, pa, ps in pares if pa.get("cruz_calib_ok") and ps.get("cruz_calib_ok")]
        sin_c = sum(ps["cruz_sinalizou"] for ps in val_c)
        md += ["", f"Vídeos sonolentos com pelo menos um alarme ou aviso: {sin_c} de {len(val_c)}.", ""]

    md += ["## Como ler", "",
           "- \"Subiu / caiu / igual\": em quantas pessoas o vídeo com sono deu número maior, menor "
           "ou igual ao vídeo alerta da mesma pessoa.",
           "- p: teste exato de postos sinalizados de Wilcoxon, bilateral (pares iguais saem). "
           "Com poucas pessoas é pouco poder: p alto não prova que não há diferença.",
           "- Os vídeos alerta não têm cochilo marcado; alarme num vídeo alerta é provável alarme "
           "falso, mas a base não marca os instantes, então isto não é medida de acurácia por evento.",
           ""]
    (pasta / "relatorio.md").write_text("\n".join(md), encoding="utf-8")


# ----------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--series", type=Path, default=AQUI / "series")
    ap.add_argument("--saida", type=Path, default=AQUI / "resultados")
    args = ap.parse_args()

    arquivos = sorted(args.series.glob("*.npz"))
    if not arquivos:
        sys.exit(f"Nenhuma série em {args.series}")
    linhas, calib_alerta = [], {}
    for f in arquivos:
        partes = f.stem.split("_")       # Fold1_part1_06_pouco_vigilante
        pacote, pessoa, rotulo = "_".join(partes[:2]), partes[2], "_".join(partes[3:])
        dados = np.load(f)
        serie = dados["serie"]
        tam = dados["tamanho_original"] if "tamanho_original" in dados.files else None
        ln = dict(arquivo=f.name, pacote=pacote, pessoa_id=f"{pacote.split('_')[0]}-{pessoa}",
                  rotulo=rotulo, tamanho=f"{tam[0]}x{tam[1]}" if tam is not None and tam[0] else "?")
        ln.update(medidas_olho(serie))
        ln.update({f"proprio_{k}": v for k, v in medidas_sistema(serie).items()})
        if rotulo == "alerta":
            ear_b, mar_b, pitch_b, sorriso_b, _boc, _i = calibrar_serie(serie)
            if ear_b is not None:
                calib_alerta[ln["pessoa_id"]] = (ear_b, mar_b, pitch_b, sorriso_b)
        linhas.append((ln, serie))
        print(f"  {f.stem}: rosto {ln['pct_rosto']:.0f}%, EAR aberto {ln['ear_aberto']:.3f}", flush=True)

    # sono e pouco vigilante monitorados com a calibração do vídeo alerta
    for ln, serie in linhas:
        cal = calib_alerta.get(ln["pessoa_id"])
        if cal is not None:
            ln.update({f"cruz_{k}": v for k, v in medidas_sistema(serie, calibracao=cal).items()})
    linhas = [ln for ln, _s in linhas]

    pasta = args.saida / time.strftime("%Y%m%d_%H%M%S")
    pasta.mkdir(parents=True)
    campos = sorted({k for ln in linhas for k in ln}, key=lambda k: (k.startswith(("proprio_", "cruz_")), k))
    with open(pasta / "por_video.csv", "w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=campos)
        w.writeheader()
        w.writerows(linhas)
    gerar_relatorio(linhas, pasta, args.series)
    print(f"\nRelatório: {pasta / 'relatorio.md'}")


if __name__ == "__main__":
    main()
