"""
Benchmark de velocidade, atraso e temperatura do detector, pro capítulo de
resultados do TCC. Roda sem janela e sem calibração.

Mede, quadro a quadro:
  - inferência: só o MediaPipe (rosto, 478 pontos e blendshapes);
  - quadro completo: do quadro pronto até a decisão (MediaPipe + medidas +
    regras), que é o atraso que o programa soma ao tempo das regras;
  - espera da câmera (ao vivo): quanto o quadro demora pra chegar.
E a cada 10 s: FPS, uso de CPU, temperatura, frequência do processador,
ventoinha e o registro de subtensão/redução de velocidade do Pi
(vcgencmd get_throttled).

Uso:
    ./venv/bin/python benchmark.py --video clip.avi                  # vazão máxima, até o vídeo acabar
    ./venv/bin/python benchmark.py --seconds 60                      # câmera ao vivo, 60 s
    ./venv/bin/python benchmark.py --video clip.avi --minutos 60     # resistência: repete o vídeo por 1 h
    ./venv/bin/python benchmark.py --video clip.avi --tempo-real     # vídeo no ritmo da câmera (30 fps)

Saída em benchmark_resultados/<data_hora>/: resumo.md, intervalos.csv e,
com --csv, a latência de cada quadro.
"""

import argparse
import contextlib
import csv
import io
import statistics
import subprocess
import sys
import time
from pathlib import Path

import core
import eye_fatigue as ef

INTERVALO_S = 10.0
SAIDA = Path(__file__).parent / "benchmark_resultados"

# bits do vcgencmd get_throttled (documentação do Raspberry Pi)
BITS_THROTTLED = {
    0: "subtensão agora", 1: "frequência limitada agora", 2: "velocidade reduzida agora",
    3: "limite de temperatura agora", 16: "subtensão desde que ligou",
    17: "frequência limitada desde que ligou", 18: "velocidade reduzida desde que ligou",
    19: "limite de temperatura desde que ligou",
}


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path, help="Vídeo pra usar como entrada; se omitido, usa a câmera ao vivo")
    ap.add_argument("--seconds", type=float, default=30.0, help="Duração com câmera ao vivo (padrão 30 s)")
    ap.add_argument("--minutos", type=float, help="Teste de resistência: roda por N minutos (repete o vídeo)")
    ap.add_argument("--tempo-real", action="store_true", help="Vídeo no ritmo em que foi gravado, como a câmera")
    ap.add_argument("--csv", action="store_true", help="Salva também a latência de cada quadro")
    return ap.parse_args()


def ler(caminho, escala=1.0):
    try:
        return float(Path(caminho).read_text().strip()) / escala
    except (OSError, ValueError):
        return None


def throttled():
    try:
        saida = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2).stdout
        return int(saida.strip().split("=")[1], 16)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def descrever_throttled(valor):
    if valor is None:
        return "não disponível"
    if valor == 0:
        return "0x0 (nenhum problema)"
    return f"0x{valor:x}: " + ", ".join(t for b, t in BITS_THROTTLED.items() if valor & (1 << b))


class Cpu:
    """Uso de CPU entre duas leituras de /proc/stat."""

    def __init__(self):
        self.ant = self._ler()

    @staticmethod
    def _ler():
        campos = [int(x) for x in Path("/proc/stat").read_text().split("\n")[0].split()[1:]]
        return sum(campos), campos[3] + campos[4]   # total, ocioso (idle + iowait)

    def uso(self):
        total, ocioso = self._ler()
        dt, di = total - self.ant[0], ocioso - self.ant[1]
        self.ant = (total, ocioso)
        return 100 * (1 - di / dt) if dt else None


def pct(valores, p):
    v = sorted(valores)
    return v[min(len(v) - 1, int(len(v) * p))] if v else float("nan")


def abrir_fonte(args):
    if args.video:
        return core.VideoFileSource(args.video, realtime=args.tempo_real)
    return core.PicameraSource(want_display=False)


def main():
    args = parse_args()
    duracao = args.minutos * 60 if args.minutos else (None if args.video else args.seconds)

    try:
        source = abrir_fonte(args)
    except Exception as e:
        print(f"Não consegui abrir a {'entrada ' + str(args.video) if args.video else 'câmera'}: {e}")
        sys.exit(1)
    try:
        landmarker = core.build_landmarker()
    except Exception as e:
        print(f"Erro ao carregar o modelo do MediaPipe: {e}")
        source.close()
        sys.exit(1)
    from mediapipe import Image as MpImage, ImageFormat as MpImageFormat
    with contextlib.redirect_stdout(io.StringIO()):
        state = ef.build_state(None, None, None)

    pasta = SAIDA / time.strftime("%Y%m%d_%H%M%S")
    pasta.mkdir(parents=True, exist_ok=True)
    inferencia, completo, espera, com_rosto = [], [], [], 0
    intervalos = []
    cpu = Cpu()
    throttled_inicio = throttled()
    t_ini = t_int = time.perf_counter()
    n_int, lat_int = 0, []
    timestamp = core.MonotonicMs()
    t_video = 0.0      # tempo contínuo mesmo repetindo o vídeo
    offset = 0.0

    print(f"Rodando benchmark{' de resistência' if args.minutos else ''}... saída em {pasta}")
    try:
        while True:
            agora = time.perf_counter()
            if duracao is not None and agora - t_ini >= duracao:
                break
            t_espera = time.perf_counter()
            try:
                frame = source.read()
            except core.CaptureError as e:
                print(f"Erro de captura, parando: {e}")
                break
            if frame is None:
                if args.video and args.minutos:
                    source.close()
                    source = abrir_fonte(args)
                    offset = t_video + 1 / 30
                    continue
                break
            espera.append((time.perf_counter() - t_espera) * 1000)

            infer_frame, _disp, iw, ih, t = frame
            t_video = offset + t
            t0 = time.perf_counter()
            pf = ef.process_frame(landmarker, timestamp, MpImage, MpImageFormat, infer_frame, iw, ih, t_video)
            if pf is None:
                continue
            state.update(pf["left_ear"], pf["right_ear"], pf["mar"], pf["head_pitch"], pf["eyes_dark"],
                         pf["face_detected"], t_video, jaw_open=pf["blendshapes"].get("jawOpen"),
                         smile=core.sorriso(pf["blendshapes"]))
            total_ms = (time.perf_counter() - t0) * 1000
            inferencia.append(pf["latency_ms"])
            completo.append(total_ms)
            lat_int.append(total_ms)
            com_rosto += pf["face_detected"]
            n_int += 1

            if time.perf_counter() - t_int >= INTERVALO_S:
                dt = time.perf_counter() - t_int
                linha = {
                    "min": round((time.perf_counter() - t_ini) / 60, 2),
                    "fps": round(n_int / dt, 1),
                    "quadro_p50_ms": round(pct(lat_int, 0.50), 1),
                    "quadro_p95_ms": round(pct(lat_int, 0.95), 1),
                    "cpu_pct": round(cpu.uso() or 0, 0),
                    "temp_c": ler("/sys/class/thermal/thermal_zone0/temp", 1000),
                    "freq_mhz": ler("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", 1000),
                    "ventoinha": ler("/sys/class/thermal/cooling_device0/cur_state"),
                }
                intervalos.append(linha)
                print(f"  {linha['min']:6.1f} min  {linha['fps']:5.1f} fps  quadro p95 {linha['quadro_p95_ms']:5.1f} ms  "
                      f"CPU {linha['cpu_pct']:3.0f}%  {linha['temp_c']:.1f} °C  {linha['freq_mhz']:.0f} MHz", flush=True)
                t_int, n_int, lat_int = time.perf_counter(), 0, []
    except KeyboardInterrupt:
        print("\nInterrompido.")
    finally:
        elapsed = time.perf_counter() - t_ini
        landmarker.close()
        source.close()

    if not completo:
        print("Nenhum quadro processado.")
        sys.exit(1)

    throttled_fim = throttled()
    if intervalos:
        with open(pasta / "intervalos.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(intervalos[0]))
            w.writeheader()
            w.writerows(intervalos)
    if args.csv:
        with open(pasta / "quadros.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["quadro", "inferencia_ms", "quadro_completo_ms"])
            for i, (a, b) in enumerate(zip(inferencia, completo)):
                w.writerow([i, f"{a:.3f}", f"{b:.3f}"])

    def linha_lat(nome, v):
        return (f"| {nome} | {statistics.mean(v):.1f} | {statistics.median(v):.1f} | "
                f"{pct(v, 0.95):.1f} | {pct(v, 0.99):.1f} | {max(v):.1f} |")

    temps = [i["temp_c"] for i in intervalos if i["temp_c"] is not None]
    freqs = [i["freq_mhz"] for i in intervalos if i["freq_mhz"] is not None]
    fps_int = [i["fps"] for i in intervalos]
    entrada = (f"vídeo `{args.video.name}`" + (" no ritmo original" if args.tempo_real else " na velocidade máxima")
               if args.video else "câmera ao vivo")
    p95 = pct(completo, 0.95)
    resumo = [
        f"# Benchmark — {time.strftime('%d/%m/%Y %H:%M')}",
        "",
        f"Entrada: {entrada}. Duração: {elapsed / 60:.1f} min. Quadros: {len(completo)} "
        f"({100 * com_rosto / len(completo):.1f}% com rosto).",
        "",
        f"**FPS médio: {len(completo) / elapsed:.1f}**"
        + (f" (por intervalo de 10 s: mín {min(fps_int):.1f}, máx {max(fps_int):.1f})" if fps_int else ""),
        "",
        "| Tempo por quadro (ms) | média | mediana | p95 | p99 | máx |",
        "|---|---|---|---|---|---|",
        linha_lat("Só o MediaPipe", inferencia),
        linha_lat("Quadro completo (até a decisão)", completo),
    ]
    if not args.video:
        resumo.append(linha_lat("Espera pela câmera", espera))
    resumo += [
        "",
        "## Temperatura e energia",
        "",
        (f"Temperatura: início {temps[0]:.1f} °C, máxima {max(temps):.1f} °C, fim {temps[-1]:.1f} °C. "
         f"Frequência do processador: mínima {min(freqs):.0f} MHz, máxima {max(freqs):.0f} MHz."
         if temps and freqs else "Sem leituras de temperatura (teste curto demais)."),
        "",
        f"Registro do Pi no início: {descrever_throttled(throttled_inicio)}.",
        f"Registro do Pi no fim: {descrever_throttled(throttled_fim)}.",
        "",
        "## Tempo de reação ao olho fechado",
        "",
        f"O alarme cheio dispara quando o olho fica fechado por {core.CLOSED_SECONDS_ALERT:.1f} s "
        f"({core.CLOSED_SECONDS_ALERT_TALKING:.1f} s falando ou sorrindo). O processamento soma no pior caso "
        f"comum (p95) {p95:.0f} ms por quadro, mais o intervalo entre quadros. O atraso do áudio Bluetooth "
        "não entra aqui: é do fone, não do programa.",
        "",
    ]
    (pasta / "resumo.md").write_text("\n".join(resumo))
    print("\n" + "\n".join(resumo))
    print(f"Salvo em {pasta}")


if __name__ == "__main__":
    main()
