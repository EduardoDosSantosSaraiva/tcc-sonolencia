#!/usr/bin/env python3
"""
Avalia o sistema, trecho por trecho, nos vídeos gravados por
gravar_angulos.py (o próprio motorista, de frente e de lado), comparando
variantes das medidas sem precisar gravar de novo:

    2d             o sistema como está (EAR e MAR medidos na imagem)
    3d             EAR e MAR com a profundidade do MediaPipe (não inflam de lado)
    3d_olho_perto  3d e, com o rosto virado mais que OLHO_PERTO_GRAUS, decide
                   pelo olho mais perto da câmera (o de longe fica escondido)

Critérios por trecho (os mesmos do autoteste):
    neutro        nenhum alarme, piscada longa ou bocejo
    piscar        nenhuma piscada longa nem alarme
    olho_fechado  alarme de sono
    fala          nenhum bocejo
    bocejo        bocejo contado, sem alarme de sono
    cabeca        queda de cabeça contada

Uso:
    ./venv/bin/python avaliar_angulos.py                    # gravação mais recente
    ./venv/bin/python avaliar_angulos.py videos_proprios/20261020_1600
"""

import argparse
import csv
from pathlib import Path

import numpy as np

import core
from avaliar_yawdd import _extrair, simular

PASTA = Path(__file__).parent / "videos_proprios"
OLHO_PERTO_GRAUS = 25.0
FOLGA_S = 1.5   # reação do sistema depois do fim do trecho (o alarme leva 1,5 s)

# colunas das séries (ver avaliar_yawdd._extrair)
C_EAR_E, C_EAR_D, C_MAR = 1, 2, 3
C_EAR3D_E, C_EAR3D_D, C_MAR3D, C_YAW = 24, 25, 26, 27


def variante(serie, nome):
    s = serie.copy()
    if nome == "2d":
        return s
    s[:, [C_EAR_E, C_EAR_D, C_MAR]] = serie[:, [C_EAR3D_E, C_EAR3D_D, C_MAR3D]]
    if nome == "3d_olho_perto":
        yaw = serie[:, C_YAW]
        # z maior = mais longe da câmera: yaw > 0 afasta o olho esquerdo (263),
        # então o perto é o direito; os dois passam a valer o olho de perto
        perto_dir = yaw > OLHO_PERTO_GRAUS
        perto_esq = yaw < -OLHO_PERTO_GRAUS
        s[perto_dir, C_EAR_E] = s[perto_dir, C_EAR_D]
        s[perto_esq, C_EAR_D] = s[perto_esq, C_EAR_E]
    return s


def julgar(trecho, ini, fim, eventos):
    def houve(tipo, ate=FOLGA_S):
        return any(ini <= t <= fim + ate and e == tipo for t, e in eventos)

    if trecho == "neutro":
        return not (houve("alarme") or houve("piscada_longa") or houve("bocejo"))
    if trecho == "piscar":
        return not (houve("piscada_longa") or houve("alarme"))
    if trecho == "olho_fechado":
        return houve("alarme")
    if trecho == "fala":
        return not houve("bocejo")
    if trecho == "bocejo":
        return houve("bocejo", ate=3.0) and not houve("alarme")
    if trecho == "cabeca":
        return houve("queda_cabeca", ate=2.0)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pasta", type=Path, nargs="?")
    args = ap.parse_args()
    pasta = args.pasta or max(PASTA.glob("2*"))
    rotulos = list(csv.DictReader(open(pasta / "rotulos.csv")))
    (pasta / "series").mkdir(exist_ok=True)

    linhas = ["# Avaliação por ângulo da câmera", "", f"Gravação: `{pasta.name}`", ""]
    tabela = {}
    for posicao in dict.fromkeys(r["posicao"] for r in rotulos):
        npz = pasta / "series" / f"{posicao}.npz"
        if not npz.exists():
            print(f"Rodando o MediaPipe em {posicao}.avi...")
            _extrair(pasta / f"{posicao}.avi", npz)
        serie = np.load(npz)["serie"]
        trechos = [r for r in rotulos if r["posicao"] == posicao and r["trecho"] != "instrucao"]

        yaw = np.nanmedian(np.abs(serie[:, C_YAW]))
        rosto = 100 * np.nanmean(serie[:, 6])
        fala = [r for r in trechos if r["trecho"] == "fala"]
        no_trecho = lambda r: (serie[:, 0] >= float(r["inicio_s"])) & (serie[:, 0] <= float(r["fim_s"]))
        mar_fala = {c: np.nanpercentile(serie[no_trecho(fala[0]), c], 99) if fala else float("nan")
                    for c in (C_MAR, C_MAR3D)}
        linhas += [f"## {posicao}", "",
                   f"Rosto virado (mediana): {yaw:.0f}°. Rosto detectado em {rosto:.1f}% dos quadros. "
                   f"MAR falando (percentil 99): 2D {mar_fala[C_MAR]:.3f}, 3D {mar_fala[C_MAR3D]:.3f}.", "",
                   "| Trecho | início (s) | 2d | 3d | 3d_olho_perto |", "|---|---|---|---|---|"]
        resultados = {}
        for nome in ("2d", "3d", "3d_olho_perto"):
            eventos = []
            simular(variante(serie, nome), eventos=eventos)
            resultados[nome] = [julgar(r["trecho"], float(r["inicio_s"]), float(r["fim_s"]), eventos) for r in trechos]
            tabela.setdefault(nome, []).extend(v for v in resultados[nome] if v is not None)
        for k, r in enumerate(trechos):
            marca = lambda v: "ok" if v else ("FALHOU" if v is not None else "-")
            linhas.append(f"| {r['trecho']} | {float(r['inicio_s']):.0f} | "
                          + " | ".join(marca(resultados[n][k]) for n in ("2d", "3d", "3d_olho_perto")) + " |")
        linhas.append("")

    linhas += ["## Total", "", "| Variante | Trechos certos |", "|---|---|"]
    for nome, vs in tabela.items():
        linhas.append(f"| {nome} | {sum(vs)}/{len(vs)} |")
    (pasta / "relatorio.md").write_text("\n".join(linhas) + "\n")
    print("\n".join(linhas))
    print(f"\nRelatório: {pasta / 'relatorio.md'}")


if __name__ == "__main__":
    main()
