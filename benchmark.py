"""
Benchmark de latência/FPS do pipeline de detecção, pro capítulo de
resultados do TCC. Roda sem GUI e sem calibração — só mede o custo real
de cada frame passando pelo MediaPipe.

Uso:
    ./venv/bin/python benchmark.py --video clip.mp4
    ./venv/bin/python benchmark.py --seconds 30          # câmera ao vivo
    ./venv/bin/python benchmark.py --video clip.mp4 --csv latencias.csv
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

import core


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path, help="Vídeo pra usar como entrada; se omitido, usa a câmera ao vivo")
    ap.add_argument("--seconds", type=float, default=30.0, help="Duração do benchmark com câmera ao vivo (padrão 30s)")
    ap.add_argument("--csv", type=Path, help="Salva a latência de cada frame nesse CSV")
    return ap.parse_args()


def main():
    args = parse_args()

    if args.video:
        try:
            source = core.VideoFileSource(args.video, realtime=False)
        except Exception as e:
            print(f"Não consegui abrir o vídeo '{args.video}': {e}")
            sys.exit(1)
    else:
        try:
            source = core.PicameraSource(want_display=False)
        except Exception as e:
            print(f"Não consegui abrir a câmera: {e}")
            sys.exit(1)

    try:
        landmarker = core.build_landmarker()
    except Exception as e:
        print(f"Erro ao carregar o modelo do MediaPipe: {e}")
        source.close()
        sys.exit(1)

    from mediapipe import Image as MpImage, ImageFormat as MpImageFormat

    latencies_ms = []
    frame_count = 0
    t_wall_start = time.perf_counter()
    timestamp = core.MonotonicMs()

    print("Rodando benchmark...")
    try:
        while True:
            if args.video is None and (time.perf_counter() - t_wall_start) >= args.seconds:
                break
            try:
                frame = source.read()
            except core.CaptureError as e:
                print(f"Erro de captura, parando: {e}")
                break
            if frame is None:
                break

            infer_frame, _display, iw, ih, t = frame
            mp_image = MpImage(image_format=MpImageFormat.SRGB, data=infer_frame)

            t_start = time.perf_counter()
            try:
                landmarker.detect_for_video(mp_image, timestamp.next(t))
            except Exception as e:
                print(f"Erro de inferência, pulando frame ({e}).")
                continue
            latencies_ms.append((time.perf_counter() - t_start) * 1000)
            frame_count += 1
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
    finally:
        elapsed = time.perf_counter() - t_wall_start
        landmarker.close()
        source.close()

    if not latencies_ms:
        print("Nenhum frame processado.")
        sys.exit(1)

    latencies_ms.sort()

    def pct(p):
        idx = min(len(latencies_ms) - 1, int(len(latencies_ms) * p))
        return latencies_ms[idx]

    print(f"\nFrames processados: {frame_count}")
    print(f"Tempo total: {elapsed:.1f}s  |  FPS médio: {frame_count / elapsed:.1f}")
    print(
        f"Latência de inferência por frame (ms) — "
        f"média: {statistics.mean(latencies_ms):.1f}  "
        f"mediana: {statistics.median(latencies_ms):.1f}  "
        f"p95: {pct(0.95):.1f}  max: {max(latencies_ms):.1f}"
    )

    if args.csv:
        import csv
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["frame", "latencia_ms"])
            for i, lat in enumerate(latencies_ms):
                w.writerow([i, f"{lat:.3f}"])
        print(f"Latências por frame salvas em {args.csv}")


if __name__ == "__main__":
    main()
