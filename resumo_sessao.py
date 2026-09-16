"""
Resume uma sessão gravada no banco SQLite do eye_fatigue.py: timeline de
eventos e tempo em cada nível, pra comparar com o que você realmente fez na
frente da câmera durante o teste. É a ponte entre "testei e pareceu certo" e
uma evidência anotável na monografia.

Uso:
    ./venv/bin/python resumo_sessao.py sonolencia.db              # última sessão
    ./venv/bin/python resumo_sessao.py sonolencia.db --todas       # todas as sessões
    ./venv/bin/python resumo_sessao.py sonolencia.db --sessao 3    # uma sessão específica
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def fmt_dur(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}m{s:04.1f}s" if m else f"{s:.1f}s"


def summarize(conn, session_id, total_sessions):
    frames = conn.execute(
        "SELECT t_s, level, perclos FROM frames WHERE session = ? ORDER BY t_s",
        (session_id,),
    ).fetchall()
    eventos = conn.execute(
        "SELECT t_s, tipo, duracao_s FROM eventos WHERE session = ? ORDER BY t_s",
        (session_id,),
    ).fetchall()

    if not frames:
        print(f"\n=== Sessão {session_id}/{total_sessions} — sem frames logados ===")
        return

    t_start, t_end = frames[0][0], frames[-1][0]
    duration = t_end - t_start
    print(f"\n=== Sessão {session_id}/{total_sessions} — duração ~{fmt_dur(duration)}, {len(frames)} frames ===")

    level_time = {}
    prev_t, prev_level = None, None
    for t, level, _perclos in frames:
        if prev_t is not None:
            level_time[prev_level] = level_time.get(prev_level, 0.0) + (t - prev_t)
        prev_t, prev_level = t, level
    for level, secs in sorted(level_time.items(), key=lambda kv: -kv[1]):
        pct = 100 * secs / duration if duration > 0 else 0
        print(f"  {level or '(sem nivel)':22s} {fmt_dur(secs):>9s}  ({pct:4.1f}%)")

    perclos_vals = [row[2] for row in frames if row[2] is not None]
    if perclos_vals:
        print(f"  PERCLOS pico: {max(perclos_vals) * 100:.0f}%   médio: {sum(perclos_vals) / len(perclos_vals) * 100:.0f}%")

    if eventos:
        print(f"  Eventos ({len(eventos)}):")
        for t, tipo, dur in eventos:
            dur_txt = f" ({dur:.1f}s)" if dur is not None else ""
            print(f"    [{fmt_dur(t):>9s}] {tipo}{dur_txt}")
    else:
        print("  Nenhum evento registrado nessa sessão.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", type=Path, help="Caminho do banco SQLite (ex.: sonolencia.db)")
    ap.add_argument("--todas", action="store_true", help="Mostra todas as sessões, não só a última")
    ap.add_argument("--sessao", type=int, help="Mostra só essa sessão específica (número)")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"Banco não encontrado: {args.db}")
        sys.exit(1)

    conn = sqlite3.connect(str(args.db))
    max_session = conn.execute("SELECT MAX(session) FROM frames").fetchone()[0]

    if max_session is None:
        print("Nenhuma sessão com número de sessão registrada nesse banco "
              "(banco antigo, de antes dessa coluna existir).")
        conn.close()
        sys.exit(0)

    if args.sessao is not None:
        summarize(conn, args.sessao, max_session)
    elif args.todas:
        for s in range(1, max_session + 1):
            summarize(conn, s, max_session)
    else:
        summarize(conn, max_session, max_session)

    conn.close()


if __name__ == "__main__":
    main()
