"""
Exporta as tabelas do banco de sessão (SQLite, gerado por eye_fatigue.py)
pra CSV, pra abrir no Excel/pandas e gerar os gráficos da monografia.

Uso:
    ./venv/bin/python export_csv.py sonolencia.db
    ./venv/bin/python export_csv.py sonolencia.db --out-dir resultados/
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

TABLES = ("frames", "eventos")


def export_table(conn, table, out_path):
    cur = conn.execute(f"SELECT * FROM {table}")  # nome vem de TABLES, fixo no código
    cols = [d[0] for d in cur.description]
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(cur.fetchall())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", type=Path, help="Caminho do banco SQLite (ex.: sonolencia.db)")
    ap.add_argument("--out-dir", type=Path, default=Path("."), help="Pasta onde salvar os CSVs")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"Banco não encontrado: {args.db}")
        sys.exit(1)

    conn = sqlite3.connect(str(args.db))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for table in TABLES:
        out_path = args.out_dir / f"{table}.csv"
        try:
            export_table(conn, table, out_path)
            print(f"{table} -> {out_path}")
        except sqlite3.OperationalError as e:
            print(f"Não consegui exportar '{table}': {e}")

    conn.close()


if __name__ == "__main__":
    main()
