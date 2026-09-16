#!/usr/bin/env python3
"""
Analisa os microdados abertos da PRF (datatran, "agrupados por ocorrência")
para quantificar os acidentes cuja causa registrada é "Condutor Dormindo".

Gera os números citados na introdução da monografia. Reprodutível: baixe os
arquivos anuais em https://www.gov.br/prf/pt-br/acesso-a-informacao/dados-abertos
(um ZIP por ano, contendo datatran<ano>.csv, codificação latin-1, separador ';')
e aponte este script para a pasta que os contém.

Uso:
    python3 analise_prf.py /caminho/com/os/datatran*.csv
"""

import collections
import csv
import glob
import sys
import unicodedata
from pathlib import Path

MADRUGADA = range(0, 6)   # 00h00 às 05h59


def sem_acento(texto):
    return (unicodedata.normalize("NFKD", texto)
            .encode("ascii", "ignore").decode().lower().strip())


def e_sono(causa):
    """A PRF usa a categoria 'Condutor Dormindo' em todo o período analisado."""
    return "dorm" in sem_acento(causa)


def main():
    base = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    arquivos = sorted(glob.glob(str(base / "**" / "datatran*.csv"), recursive=True))
    if not arquivos:
        print(f"Nenhum datatran*.csv encontrado em {base}")
        sys.exit(1)

    por_ano = {}
    hora_sono = collections.Counter()
    hora_geral = collections.Counter()

    for caminho in arquivos:
        ano = "".join(c for c in Path(caminho).stem if c.isdigit())
        t = dict(acidentes=0, mortos=0, fatais=0,
                 s_acidentes=0, s_mortos=0, s_fatais=0, s_madrugada=0, madrugada=0)
        ranking = collections.Counter()

        with open(caminho, encoding="latin-1", newline="") as f:
            for linha in csv.DictReader(f, delimiter=";"):
                causa = linha.get("causa_acidente", "").strip()
                mortos = int(linha.get("mortos") or 0)
                fatal = "fatais" in sem_acento(linha.get("classificacao_acidente", ""))
                try:
                    hora = int(linha.get("horario", "")[:2])
                except ValueError:
                    hora = -1

                t["acidentes"] += 1
                t["mortos"] += mortos
                t["fatais"] += fatal
                ranking[causa] += 1
                hora_geral[hora] += 1
                if hora in MADRUGADA:
                    t["madrugada"] += 1

                if e_sono(causa):
                    t["s_acidentes"] += 1
                    t["s_mortos"] += mortos
                    t["s_fatais"] += fatal
                    hora_sono[hora] += 1
                    if hora in MADRUGADA:
                        t["s_madrugada"] += 1

        posicoes = [i for i, (c, _) in enumerate(ranking.most_common(), 1) if e_sono(c)]
        t["posicao"] = min(posicoes) if posicoes else None
        por_ano[ano] = t

    print(f"{'Ano':<6}{'Acidentes':>11}{'Por sono':>10}{'%':>7}"
          f"{'Mortos (sono)':>15}{'Letalidade':>12}{'Posição':>9}")
    tot = collections.Counter()
    for ano, t in sorted(por_ano.items()):
        tot.update(t)
        print(f"{ano:<6}{t['acidentes']:>11,}{t['s_acidentes']:>10,}"
              f"{100 * t['s_acidentes'] / t['acidentes']:>6.2f}%"
              f"{t['s_mortos']:>15,}"
              f"{100 * t['s_mortos'] / t['s_acidentes']:>11.1f}%"
              f"{t['posicao']:>9}")

    print(f"\nPeríodo completo: {tot['acidentes']:,} acidentes, "
          f"{tot['s_acidentes']:,} por sono ({100 * tot['s_acidentes'] / tot['acidentes']:.2f}%), "
          f"{tot['s_mortos']:,} mortes.")
    print(f"Letalidade: {100 * tot['s_mortos'] / tot['s_acidentes']:.1f} mortos por 100 acidentes "
          f"(geral: {100 * tot['mortos'] / tot['acidentes']:.1f}).")
    print(f"Madrugada (00h–05h59): {100 * tot['s_madrugada'] / tot['s_acidentes']:.1f}% dos acidentes por sono "
          f"contra {100 * tot['madrugada'] / tot['acidentes']:.1f}% do total "
          f"({(tot['s_madrugada'] / tot['s_acidentes']) / (tot['madrugada'] / tot['acidentes']):.2f}x).")

    print("\nDistribuição horária (% do próprio grupo):")
    print(f"{'Hora':<6}{'Por sono':>10}{'Geral':>8}")
    for h in range(24):
        print(f"{h:02d}h{'':<3}{100 * hora_sono[h] / tot['s_acidentes']:>9.2f}%"
              f"{100 * hora_geral[h] / tot['acidentes']:>7.2f}%")


if __name__ == "__main__":
    main()
