"""
Protótipo do TCC: detecção de sonolência do motorista via câmera do Pi 5 ou
vídeo gravado — olhos (EAR/PERCLOS/piscadas longas), boca (MAR/bocejo) e
inclinação da cabeça, com alarme sonoro (voz + sirene) no ThinkPlus.

Uso:
    ./venv/bin/python eye_fatigue.py                  # câmera ao vivo — calibra
                                                        # de novo toda vez que roda
    ./venv/bin/python eye_fatigue.py --video clip.mp4 # vídeo gravado (testes)
    ./venv/bin/python eye_fatigue.py --skip-calibration  # limiares padrão, sem calibrar
    ./venv/bin/python eye_fatigue.py --no-gui          # só terminal

A calibração por pessoa (EAR/MAR/pitch neutros) roda toda vez que o programa
inicia — dá um aviso de alguns segundos antes de começar a valer, pra dar
tempo de se posicionar (ver PREP_SECONDS/wait_for_ready).

Usa a Tasks API do MediaPipe 1.0 (mp.solutions foi removido nessa versão).
Requer o modelo face_landmarker.task na mesma pasta (ver core.build_landmarker).
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import core

WINDOW_NAME = "TCC - Deteccao de cansaco nos olhos"

SNAPSHOT_DIR = Path(__file__).parent / "alarme_snapshots"  # evidência do que
                                                              # disparou cada
                                                              # alarme cheio,
                                                              # útil pra defesa

RESULTS_DIR = Path(__file__).parent / "autoteste_resultados"  # relatório de
                                                                 # cada rodada
                                                                 # do modo teste,
                                                                 # gravado sempre
                                                                 # (não só na tela)

CALIBRATION_LOG_PATH = Path(__file__).parent / "calibracao_log.txt"  # histórico
                                                                        # de toda
                                                                        # calibração
                                                                        # rodada,
                                                                        # não só a
                                                                        # tela do
                                                                        # terminal


def log_calibration(ear_baseline, mar_baseline, pitch_baseline, warning):
    """Registra o resultado de cada calibração num arquivo, independente de
    como o programa foi iniciado (atalho, terminal, etc.) — sem isso, uma
    calibração ruim só aparece na tela de quem estava vendo na hora."""
    ear_txt = f"{ear_baseline:.3f}" if ear_baseline is not None else "FALHOU"
    linha = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  EAR={ear_txt}"
    linha += f"  MAR={mar_baseline:.3f}" if mar_baseline is not None else "  MAR=None"
    linha += f"  pitch={pitch_baseline:.3f}" if pitch_baseline is not None else "  pitch=None"
    if warning:
        linha += f"  aviso=\"{warning}\""
    with open(CALIBRATION_LOG_PATH, "a") as f:
        f.write(linha + "\n")


ALERT_SOUND_PATH = Path(__file__).parent / "alerta.wav"
ALERT_REPEAT_SECONDS = 2.5   # intervalo mínimo entre repetições do alarme
                              # enquanto o estado "fechado" persistir

ATTENTION_SOUND_PATH = Path(__file__).parent / "aviso_atencao.wav"
ATTENTION_REPEAT_SECONDS = 10.0   # aviso mais leve (piscadas longas frequentes,
                                    # cabeça caindo), repete bem mais espaçado
                                    # que o alarme cheio pra não ficar irritante

FATIGUE_SOUND_PATH = Path(__file__).parent / "aviso_fadiga.wav"
FATIGUE_REPEAT_SECONDS = 10.0   # aviso específico de bocejo — nunca dispara
                                  # o alarme cheio (ver core.DrowsinessState),
                                  # só esse aviso leve de fadiga/cansaço

PIPER_BIN = Path(__file__).parent / "venv" / "bin" / "piper"
TTS_MODEL_PATH = Path(__file__).parent / "tts_model" / "pt_BR-faber-medium.onnx"


def speak_blocking(text, player):
    """Sintetiza a frase com o Piper e toca, esperando terminar antes de
    voltar — o autoteste guiado é sequencial de propósito (fala a instrução,
    só depois começa a contar o tempo do cenário), diferente do AlertPlayer
    (que é assíncrono pra não travar o loop de detecção)."""
    if player is None or not PIPER_BIN.exists() or not TTS_MODEL_PATH.exists():
        print(f"[fala] {text}")
        return
    wav_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        subprocess.run(
            [str(PIPER_BIN), "--model", str(TTS_MODEL_PATH), "--output_file", wav_path],
            input=text, text=True, capture_output=True, timeout=15,
        )
        subprocess.run([player, wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except Exception as e:
        print(f"Aviso: falha ao tocar a fala ({e}).")
    finally:
        if wav_path:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


class AlertPlayer:
    """Toca o som de alarme no dispositivo de áudio padrão do sistema (no
    setup atual, o fone Bluetooth ThinkPlus via PipeWire) de forma não
    bloqueante — spawna um processo tocador em vez de travar o loop
    principal de captura/inferência enquanto o som toca.

    Repete a cada ALERT_REPEAT_SECONDS enquanto is_alert continuar True, sem
    empilhar processos (só dispara de novo quando o anterior já terminou).
    Uma vez disparado, o clipe toca até o fim mesmo que is_alert vire False
    no meio (olho reabriu por um instante) — o alarme existe pra acordar o
    motorista de verdade, não só reagir ao estado momentâneo; cortar na hora
    do olho reabrir seria fácil demais de "silenciar" com um piscar rápido
    sem realmente estar acordado. A voz já toca primeiro no áudio (ver
    alerta.wav), então mesmo um alerta que termina de tocar sozinho já deu o
    aviso principal logo no início."""

    def __init__(self, sound_path=ALERT_SOUND_PATH, repeat_seconds=ALERT_REPEAT_SECONDS):
        self.sound_path = sound_path
        self.repeat_seconds = repeat_seconds
        self.player = shutil.which("pw-play") or shutil.which("paplay") or shutil.which("ffplay")
        self._proc = None
        self._last_play_t = None
        if self.player is None:
            print("Aviso: nenhum tocador de áudio encontrado (pw-play/paplay/ffplay); alarme sonoro desativado.")
        elif not self.sound_path.exists():
            print(f"Aviso: som de alerta não encontrado em {self.sound_path}; alarme sonoro desativado.")

    @property
    def enabled(self):
        return self.player is not None and self.sound_path.exists()

    def maybe_play(self, is_alert, t):
        if not self.enabled or not is_alert:
            return
        finished = self._proc is None or self._proc.poll() is not None
        due = self._last_play_t is None or (t - self._last_play_t) >= self.repeat_seconds
        if finished and due:
            args = [self.player]
            if self.player.endswith("ffplay"):
                args += ["-nodisp", "-autoexit", "-loglevel", "quiet"]
            args.append(str(self.sound_path))
            self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._last_play_t = t

    def stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path, help="Roda sobre um arquivo de vídeo em vez da câmera ao vivo")
    ap.add_argument("--fast", action="store_true", help="Com --video, processa o mais rápido possível (sem pacing em tempo real)")
    ap.add_argument("--skip-calibration", action="store_true", help="Usa limiares padrão em vez de calibrar por pessoa")
    ap.add_argument("--no-gui", action="store_true", help="Não abre janela de vídeo, só imprime no terminal")
    ap.add_argument("--db", type=Path, default=Path(__file__).parent / "sonolencia.db", help="Banco SQLite pra log da sessão")
    ap.add_argument("--no-log", action="store_true", help="Não grava log da sessão")
    ap.add_argument("--no-sound", action="store_true", help="Não toca o alarme sonoro no estado 'fechado'")
    ap.add_argument("--modo-teste", action="store_true", help="Autoteste guiado por voz pelos 8 cenários do protocolo_testes.md")
    return ap.parse_args()


def build_source(args):
    if args.video:
        try:
            return core.VideoFileSource(args.video, realtime=not args.fast)
        except Exception as e:
            print(f"Não consegui abrir o vídeo '{args.video}': {e}")
            sys.exit(1)

    try:
        return core.PicameraSource(want_display=not args.no_gui)
    except ImportError:
        print("Picamera2 não está instalado. No Pi OS: sudo apt install python3-picamera2")
        sys.exit(1)
    except Exception as e:
        print(f"Não consegui abrir/configurar a câmera: {e}")
        print("Verifique se ela está conectada, o cabo CSI encaixado e se nenhum outro")
        print("programa (outro rpicam-hello, outra instância deste script) está usando-a.")
        sys.exit(1)


PREP_SECONDS = 3   # aviso antes da calibração de verdade começar, pra pessoa
                     # se posicionar sem que esse tempo conte como amostra


def wait_for_ready(source, headless, cv2, seconds=PREP_SECONDS):
    """Contagem regressiva de aviso antes da calibração começar a valer —
    dá tempo da pessoa sentar na posição de dirigir, sem que esse ajuste
    inicial vire amostra ruim da calibração de verdade."""
    print(f"Calibração começa em {seconds}s — prepare-se: sente na posição de "
          f"dirigir, olhos abertos, boca fechada, cabeça reta.")
    t0 = None
    while True:
        try:
            frame = source.read()
        except core.CaptureError:
            break
        if frame is None:
            break
        _infer_frame, display_frame, _iw, _ih, t = frame
        if t0 is None:
            t0 = t
        remaining = seconds - (t - t0)
        if remaining <= 0:
            break
        if not headless and display_frame is not None:
            disp = cv2.cvtColor(display_frame, cv2.COLOR_RGB2BGR)
            cv2.putText(
                disp, f"Prepare-se... calibrando em {remaining:.0f}s",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 165, 255), 2,
            )
            cv2.imshow(WINDOW_NAME, disp)
            cv2.waitKey(1)


def process_frame(landmarker, timestamp, MpImage, MpImageFormat, infer_frame, iw, ih, t):
    """Roda a inferência do MediaPipe num frame e extrai EAR/MAR/pitch/
    contraste dos olhos do rosto do motorista. Retorna None se a inferência
    falhar nesse frame (quem chama deve pular pro próximo). Compartilhado
    entre o loop principal e o modo de autoteste guiado, pra não duplicar
    essa extração nos dois."""
    mp_image = MpImage(image_format=MpImageFormat.SRGB, data=infer_frame)
    t_infer_start = time.perf_counter()
    try:
        result = landmarker.detect_for_video(mp_image, timestamp.next(t))
    except Exception as e:
        print(f"Erro na inferência do MediaPipe nesse frame, pulando ({e}).")
        return None
    latency_ms = (time.perf_counter() - t_infer_start) * 1000

    left_ear = right_ear = mar = head_pitch = eyes_dark = None
    eye_bright = face_bright = None
    idx = core.select_driver_index(result.face_landmarks)
    landmarks = result.face_landmarks[idx] if idx is not None else None
    face_detected = landmarks is not None
    if landmarks is not None and len(landmarks) > max(core.LEFT_EYE + core.RIGHT_EYE + core.MOUTH):
        left_ear = core.eye_aspect_ratio(landmarks, core.LEFT_EYE, iw, ih)
        right_ear = core.eye_aspect_ratio(landmarks, core.RIGHT_EYE, iw, ih)
        mar = core.mouth_aspect_ratio(landmarks, core.MOUTH, iw, ih)
        head_pitch = core.head_drop_ratio(landmarks, iw, ih)
        eye_bright, face_bright = core.eye_region_darkness(infer_frame, landmarks, iw, ih)
        if eye_bright is not None and face_bright is not None and face_bright > core.DARK_EYES_MIN_FACE_BRIGHTNESS:
            eyes_dark = eye_bright < face_bright * core.DARK_EYES_BRIGHTNESS_RATIO

    return {
        "left_ear": left_ear, "right_ear": right_ear, "mar": mar,
        "head_pitch": head_pitch, "eyes_dark": eyes_dark,
        "eye_bright": eye_bright, "face_bright": face_bright,
        "face_detected": face_detected, "landmarks": landmarks,
        "latency_ms": latency_ms,
    }


def run_calibration(source, landmarker, headless, cv2):
    wait_for_ready(source, headless, cv2)
    print(f"Calibrando pra valer... olhos e boca fechados, cabeça reta, "
          f"por {core.CALIBRATION_SECONDS:.0f}s.")

    def on_frame(display_frame, remaining):
        if headless or display_frame is None:
            return
        disp = cv2.cvtColor(display_frame, cv2.COLOR_RGB2BGR)
        cv2.putText(
            disp, f"Calibrando... olhos/boca fechados, cabeca reta ({remaining:.1f}s)",
            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2,
        )
        cv2.imshow(WINDOW_NAME, disp)
        cv2.waitKey(1)

    ear_baseline, mar_baseline, pitch_baseline, warning = core.calibrate(source, landmarker, on_frame=on_frame)
    if warning:
        print(f"Aviso na calibração: {warning}.")
    if ear_baseline is None:
        print("Usando limiares padrão (sem calibração válida de olho).")
        log_calibration(None, mar_baseline, pitch_baseline, warning)
        return None, None, None
    print(f"Calibração concluída. EAR olho aberto: {ear_baseline:.3f}"
          + (f"  MAR boca fechada: {mar_baseline:.3f}" if mar_baseline else "  MAR: sem amostras suficientes")
          + (f"  pitch neutro: {pitch_baseline:.3f}" if pitch_baseline is not None else "  pitch: sem amostras suficientes"))
    log_calibration(ear_baseline, mar_baseline, pitch_baseline, warning)
    return ear_baseline, mar_baseline, pitch_baseline


# ---------------------------------------------------------------------------
# Modo de autoteste guiado por voz: espelha os cenários de protocolo_testes.md,
# falando a instrução de cada um, esperando a pessoa executar, e avaliando se
# o sinal esperado foi detectado — sem precisar preencher planilha na mão.
# ---------------------------------------------------------------------------

SELF_TEST_SCENARIOS = [
    (
        "Baseline",
        "Cenário um, linha de base. Fique parado, olhando pra câmera normalmente, por quinze segundos.",
        15,
        lambda b, a: (
            not a["reached_fechado"] and a["blink_count"] == b["blink_count"],
            "sem alarme nem piscada longa à toa"
            if not a["reached_fechado"] and a["blink_count"] == b["blink_count"]
            else "disparou alarme ou piscada longa à toa",
        ),
    ),
    (
        "Piscada normal",
        "Cenário dois, piscada normal. Pisque algumas vezes, normalmente, por dez segundos.",
        10,
        lambda b, a: (
            a["blink_count"] == b["blink_count"],
            "nenhuma piscada longa contada"
            if a["blink_count"] == b["blink_count"]
            else "piscada normal foi contada como longa",
        ),
    ),
    (
        "Piscada longa",
        "Cenário três, piscada longa. Feche o olho por quase um segundo, e abra.",
        6,
        lambda b, a: (
            a["blink_count"] > b["blink_count"] and not a["reached_fechado"],
            "piscada longa contada, sem disparar o alarme cheio"
            if a["blink_count"] > b["blink_count"] and not a["reached_fechado"]
            else "não contou a piscada longa, ou disparou o alarme cheio",
        ),
    ),
    (
        "Microssono",
        "Cenário quatro, microssono. Feche o olho e segure por dois segundos ou mais.",
        8,
        lambda b, a: (
            a["reached_fechado"],
            "alarme cheio disparou" if a["reached_fechado"] else "alarme cheio não disparou",
        ),
    ),
    (
        "Fala normal",
        "Cenário cinco, fala normal. Fale alguma coisa, naturalmente, olhando pra câmera.",
        15,
        lambda b, a: (
            a["yawn_count"] == b["yawn_count"],
            "nenhum bocejo contado à toa"
            if a["yawn_count"] == b["yawn_count"]
            else "fala foi confundida com bocejo",
        ),
    ),
    (
        "Bocejo real",
        "Cenário seis, bocejo real. Boceje de verdade, pode fechar o olho.",
        8,
        lambda b, a: (
            a["yawn_count"] > b["yawn_count"] and not a["reached_fechado"],
            "bocejo contado, sem disparar o alarme cheio"
            if a["yawn_count"] > b["yawn_count"] and not a["reached_fechado"]
            else "não contou o bocejo, ou disparou o alarme cheio",
        ),
    ),
    (
        "Cabeça caindo",
        "Cenário sete, cabeça caindo. Deixe a cabeça pender pra frente por um segundo.",
        6,
        lambda b, a: (
            a["head_down_count"] > b["head_down_count"],
            "queda de cabeça contada"
            if a["head_down_count"] > b["head_down_count"]
            else "não contou a queda de cabeça (confira se a calibração de pitch rodou)",
        ),
    ),
    (
        "Óculos escuros",
        "Cenário oito, óculos escuros. Coloque um óculos escuro de verdade, com lente escura, por cinco segundos. "
        "Cobrir com a mão não funciona, a detecção depende do escurecimento óptico da lente.",
        8,
        lambda b, a: (
            a["reached_glasses"],
            "modo óculos escuros ativou" if a["reached_glasses"] else "modo óculos escuros não ativou",
        ),
    ),
]


def run_guided_self_test(source, landmarker, state, cv2, MpImage, MpImageFormat, timestamp, headless, player):
    print("\n=== MODO AUTOTESTE GUIADO (ver protocolo_testes.md) ===")
    speak_blocking(
        "Modo de autoteste. Vou guiar você por oito cenários, um de cada vez. "
        "Escute a instrução e faça o que eu pedir.",
        player,
    )

    print(f"(limiares em uso: bocejo>{state.mar_threshold:.3f}  "
          + (f"cabeca-caida>{state.head_pitch_threshold:.3f}" if state.head_pitch_threshold is not None
             else "cabeca-caida: desativado"))

    resultados = []
    try:
        for nome, instrucao, duracao, avaliar in SELF_TEST_SCENARIOS:
            speak_blocking(instrucao, player)
            print(f"\n[{nome}] {instrucao}")

            before = after = None
            # telemetria bruta observada no cenário, pra diagnosticar sem
            # precisar rodar de novo se algo não bater com o esperado
            ear_min = ear_max = None
            mar_min = mar_max = None
            pitch_min = pitch_max = None
            eyes_dark_seen = False
            dark_ratio_min = None  # menor eye_bright/face_bright visto (quanto
                                     # menor, mais escura a região do olho ficou)
            t0 = None
            while True:
                try:
                    frame = source.read()
                except core.CaptureError:
                    break
                if frame is None:
                    break
                infer_frame, display_frame, iw, ih, t = frame
                if t0 is None:
                    t0 = t
                remaining = duracao - (t - t0)

                pf = process_frame(landmarker, timestamp, MpImage, MpImageFormat, infer_frame, iw, ih, t)
                if pf is not None:
                    st = state.update(
                        pf["left_ear"], pf["right_ear"], pf["mar"], pf["head_pitch"],
                        pf["eyes_dark"], pf["face_detected"], t,
                    )
                    snapshot = {
                        "blink_count": st["blink_count_recent"],
                        "yawn_count": st["yawn_count_recent"],
                        "head_down_count": st["head_down_count_recent"],
                        "reached_fechado": (after or {}).get("reached_fechado", False) or st["level"] == "fechado",
                        "reached_glasses": (after or {}).get("reached_glasses", False) or st["modo_oculos_escuros"],
                    }
                    if before is None:
                        before = dict(snapshot)
                    after = snapshot

                    if st["ear_avg"] is not None:
                        ear_min = st["ear_avg"] if ear_min is None else min(ear_min, st["ear_avg"])
                        ear_max = st["ear_avg"] if ear_max is None else max(ear_max, st["ear_avg"])
                    if st["mar"] is not None:
                        mar_min = st["mar"] if mar_min is None else min(mar_min, st["mar"])
                        mar_max = st["mar"] if mar_max is None else max(mar_max, st["mar"])
                    if st["head_pitch"] is not None:
                        pitch_min = st["head_pitch"] if pitch_min is None else min(pitch_min, st["head_pitch"])
                        pitch_max = st["head_pitch"] if pitch_max is None else max(pitch_max, st["head_pitch"])
                    if pf["eyes_dark"]:
                        eyes_dark_seen = True
                    if pf["eye_bright"] is not None and pf["face_bright"] is not None and pf["face_bright"] > 0:
                        ratio = pf["eye_bright"] / pf["face_bright"]
                        dark_ratio_min = ratio if dark_ratio_min is None else min(dark_ratio_min, ratio)

                    if not headless and display_frame is not None:
                        disp = cv2.cvtColor(display_frame, cv2.COLOR_RGB2BGR)
                        cv2.putText(disp, f"[{nome}] {remaining:.0f}s restantes", (10, 30),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                        cv2.putText(disp, st["status"], (10, 60),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, st["color"], 2)
                        cv2.imshow(WINDOW_NAME, disp)
                        cv2.waitKey(1)

                if remaining <= 0:
                    break

            telemetria = (
                f"MAR min/max: {mar_min:.3f}/{mar_max:.3f}" if mar_min is not None else "MAR: sem amostras"
            ) + "; " + (
                f"pitch min/max: {pitch_min:.3f}/{pitch_max:.3f}" if pitch_min is not None else "pitch: sem amostras"
            ) + f"; olho escuro visto: {'sim' if eyes_dark_seen else 'não'}" + (
                f" (razão brilho olho/rosto mín: {dark_ratio_min:.2f}, limiar<{core.DARK_EYES_BRIGHTNESS_RATIO})"
                if dark_ratio_min is not None else ""
            )

            if before is None or after is None:
                print("  -> pulado: sem frames processados nesse cenário.")
                resultados.append((nome, False, "sem frames processados", telemetria))
                continue

            passou, detalhe = avaliar(before, after)
            print(f"  -> {'passou' if passou else 'nao passou'}: {detalhe}  [{telemetria}]")
            speak_blocking(f"{'Certo' if passou else 'Atenção'}: {detalhe}.", player)
            resultados.append((nome, passou, detalhe, telemetria))
    except KeyboardInterrupt:
        print("\nAutoteste interrompido pelo usuário.")

    print("\n=== RESUMO DO AUTOTESTE ===")
    ok_count = sum(1 for r in resultados if r[1])
    for nome, passou, detalhe, telemetria in resultados:
        print(f"  [{'OK' if passou else 'FALHOU'}] {nome}: {detalhe}  [{telemetria}]")
    print(f"\n{ok_count}/{len(resultados)} cenários passaram.")
    speak_blocking(f"Autoteste concluído. {ok_count} de {len(resultados)} cenários passaram.", player)

    RESULTS_DIR.mkdir(exist_ok=True)
    fname = RESULTS_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}.md"
    linhas = [
        f"# Autoteste guiado — {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"**{ok_count}/{len(resultados)} cenários passaram.**",
        "",
        "| Cenário | Resultado | Detalhe | Telemetria |",
        "|---|---|---|---|",
    ]
    for nome, passou, detalhe, telemetria in resultados:
        linhas.append(f"| {nome} | {'OK' if passou else 'FALHOU'} | {detalhe} | {telemetria} |")
    fname.write_text("\n".join(linhas) + "\n")
    print(f"Relatório salvo em {fname}")


def run_live_monitoring(source, landmarker, state, cv2, MpImage, MpImageFormat, timestamp, headless,
                         logger, alert_player, attention_player, fatigue_player):
    """Loop principal de monitoramento ao vivo: captura, detecta, decide o
    nível de sonolência, dispara os alarmes e desenha a janela. Retorna
    (frame_count, perclos_value) pro resumo final impresso no main()."""
    consecutive_errors = 0
    prev_wall = time.time()
    prev_level = None
    prev_blink_count = 0
    prev_yawn_count = 0
    prev_head_down_count = 0
    last_printed_status = None
    frame_count = 0
    perclos_value = 0.0

    print("Rodando. " + ("Pressione 'q' na janela de vídeo para sair." if not headless else "Ctrl+C para sair."))

    try:
        while True:
            try:
                frame = source.read()
            except core.CaptureError as e:
                consecutive_errors += 1
                print(f"Falha ao capturar frame ({e}); tentando de novo...")
                if consecutive_errors >= core.MAX_CONSECUTIVE_ERRORS:
                    print("Câmera parou de responder (foi desconectada?). Encerrando.")
                    break
                time.sleep(0.05)
                continue
            consecutive_errors = 0

            if frame is None:
                print("Fim do vídeo.")
                break

            infer_frame, display_frame, iw, ih, t = frame
            frame_count += 1

            pf = process_frame(landmarker, timestamp, MpImage, MpImageFormat, infer_frame, iw, ih, t)
            if pf is None:
                continue
            landmarks = pf["landmarks"]
            face_detected = pf["face_detected"]
            latency_ms = pf["latency_ms"]

            st = state.update(
                pf["left_ear"], pf["right_ear"], pf["mar"], pf["head_pitch"],
                pf["eyes_dark"], face_detected, t,
            )
            perclos_value = st["perclos"]

            if alert_player:
                alert_player.maybe_play(st["level"] == "fechado", t)
            if attention_player:
                attention_player.maybe_play(
                    st["atencao_by_blinks"] or st["atencao_by_head"] or st["atencao_by_perclos"], t
                )
            if fatigue_player:
                fatigue_player.maybe_play(st["atencao_by_yawn"], t)

            level_changed = st["level"] != prev_level
            if level_changed and st["level"] == "fechado" and display_frame is not None:
                SNAPSHOT_DIR.mkdir(exist_ok=True)
                fname = SNAPSHOT_DIR / f"alarme_{time.strftime('%Y%m%d_%H%M%S')}_t{t:.1f}s.jpg"
                cv2.imwrite(str(fname), cv2.cvtColor(display_frame, cv2.COLOR_RGB2BGR))
                print(f"Print do alarme salvo em {fname}")

            if logger:
                logger.log_frame(t, st["ear_left"], st["ear_right"], st["ear_avg"],
                                  st["mar"], st["head_pitch"], perclos_value,
                                  st["yawn_count_recent"], st["head_down_count_recent"],
                                  st["level"], latency_ms)
                if level_changed:
                    logger.log_evento(t, st["level"], st["closed_dur"])
                if st["blink_count_recent"] > prev_blink_count:
                    logger.log_evento(t, "piscada_longa")
                prev_blink_count = st["blink_count_recent"]
                if st["yawn_count_recent"] > prev_yawn_count:
                    logger.log_evento(t, "bocejo")
                prev_yawn_count = st["yawn_count_recent"]
                if st["head_down_count_recent"] > prev_head_down_count:
                    logger.log_evento(t, "cabeca_caindo")
                prev_head_down_count = st["head_down_count_recent"]
            prev_level = st["level"]

            now_wall = time.time()
            fps = 1.0 / max(now_wall - prev_wall, 1e-6)
            prev_wall = now_wall

            if headless:
                if st["status"] != last_printed_status:
                    print(f"[{t:7.1f}s] {st['status']}  PERCLOS(60s): {perclos_value*100:.0f}%")
                    last_printed_status = st["status"]
                continue

            display = cv2.cvtColor(display_frame, cv2.COLOR_RGB2BGR)
            dh, dw = display.shape[:2]

            if face_detected:
                # landmarks são normalizados (0..1), então valem tanto pro frame
                # lores (iw x ih) quanto pro main (dw x dh) sem precisar escalar
                for pt_idx in core.LEFT_EYE + core.RIGHT_EYE:
                    x = int(landmarks[pt_idx].x * dw)
                    y = int(landmarks[pt_idx].y * dh)
                    cv2.circle(display, (x, y), max(2, dw // 320), (0, 255, 0), -1)
                for pt_idx in core.MOUTH:
                    x = int(landmarks[pt_idx].x * dw)
                    y = int(landmarks[pt_idx].y * dh)
                    cv2.circle(display, (x, y), max(2, dw // 320), (255, 200, 0), -1)

            if st["ear_avg"] is not None:
                cv2.putText(
                    display, f"EAR med:{st['ear_avg']:.3f}  L:{st['ear_left']:.3f}  R:{st['ear_right']:.3f}",
                    (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2,
                )

            cv2.putText(display, st["status"], (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, st["color"], 2)
            cv2.putText(
                display,
                f"PERCLOS(60s): {perclos_value*100:.0f}%  "
                f"Piscadas longas(2min): {st['blink_count_recent']}"
                + ("   [OCULOS ESCUROS]" if st["modo_oculos_escuros"] else ""),
                (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
            )
            mar_txt = f"{st['mar']:.3f}" if st["mar"] is not None else "-"
            pitch_txt = f"{st['head_pitch']:.3f}" if st["head_pitch"] is not None else "-"
            cv2.putText(
                display,
                f"MAR: {mar_txt}  Bocejos(5min): {st['yawn_count_recent']}  "
                f"Pitch: {pitch_txt}  Cabeca-caida(2min): {st['head_down_count_recent']}",
                (10, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1,
            )
            cv2.putText(
                display, f"FPS: {fps:.1f}  {dw}x{dh}  latencia IA: {latency_ms:.0f}ms", (10, dh - 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
            )

            cv2.imshow(WINDOW_NAME, display)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            # usuário fechou a janela no X em vez de apertar 'q'
            try:
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except cv2.error:
                break
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")

    return frame_count, perclos_value


def main():
    args = parse_args()

    source = build_source(args)

    try:
        landmarker = core.build_landmarker()
    except Exception as e:
        print(f"Não consegui carregar o modelo do MediaPipe: {e}")
        source.close()
        sys.exit(1)

    try:
        from mediapipe import Image as MpImage, ImageFormat as MpImageFormat
        import cv2
    except ImportError as e:
        print(f"Dependência faltando ({e}). Rode dentro do venv: ./venv/bin/python eye_fatigue.py")
        landmarker.close()
        source.close()
        sys.exit(1)

    # Sessão sem servidor gráfico (ex.: SSH sem X forwarding) ou --no-gui:
    # roda em modo texto, sem tentar abrir janela e crashar no Qt.
    headless = args.no_gui or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if headless and not args.no_gui:
        print("Nenhum display gráfico detectado (sem $DISPLAY/$WAYLAND_DISPLAY). Modo texto.")

    logger = None if args.no_log else core.SessionLogger(args.db)

    if args.skip_calibration:
        ear_baseline = mar_baseline = pitch_baseline = None
    else:
        ear_baseline, mar_baseline, pitch_baseline = run_calibration(source, landmarker, headless, cv2)

    ear_threshold = ear_baseline * core.CLOSED_RATIO if ear_baseline else core.EAR_THRESHOLD_DEFAULT
    ear_almost_closed = ear_baseline * core.ALMOST_RATIO if ear_baseline else core.EAR_ALMOST_CLOSED_DEFAULT
    # max(...) com MAR_THRESHOLD_DEFAULT como piso: a razão sozinha quebra
    # quando a baseline calibrada é muito perto de zero (boca fechada com
    # esses landmarks mede quase 0 pra algumas pessoas/câmeras — já vimos
    # baseline de 0.006, o que daria limiar de bocejo de 0.013, disparando
    # com qualquer movimento de boca).
    mar_threshold = max(mar_baseline * core.MAR_YAWN_RATIO, core.MAR_THRESHOLD_DEFAULT) if mar_baseline else core.MAR_THRESHOLD_DEFAULT
    # Pitch depende do ângulo de montagem da câmera — sem calibração por
    # pessoa não existe "neutro" confiável (um valor absoluto fixo erra
    # dependendo de como a câmera está apontada), então desativa a detecção
    # de cabeça em vez de usar um padrão que pode disparar à toa.
    head_pitch_threshold = pitch_baseline + core.HEAD_DOWN_DELTA if pitch_baseline is not None else None
    print(f"Limiares em uso: fechado<{ear_threshold:.3f}  quase-fechado<{ear_almost_closed:.3f}  "
          f"bocejo>{mar_threshold:.3f}  "
          + (f"cabeca-caida>{head_pitch_threshold:.3f}" if head_pitch_threshold is not None else "cabeca-caida: desativado (sem calibracao)")
          + ("" if ear_baseline else "  (EAR padrão, sem calibração)"))

    state = core.DrowsinessState(
        ear_threshold=ear_threshold, ear_almost_closed=ear_almost_closed,
        mar_threshold=mar_threshold, head_pitch_threshold=head_pitch_threshold,
    )
    alert_player = None if args.no_sound else AlertPlayer()
    attention_player = None if args.no_sound else AlertPlayer(
        sound_path=ATTENTION_SOUND_PATH, repeat_seconds=ATTENTION_REPEAT_SECONDS
    )
    fatigue_player = None if args.no_sound else AlertPlayer(
        sound_path=FATIGUE_SOUND_PATH, repeat_seconds=FATIGUE_REPEAT_SECONDS
    )

    timestamp = core.MonotonicMs()
    player = shutil.which("pw-play") or shutil.which("paplay") or shutil.which("ffplay")

    try:
        if args.modo_teste:
            run_guided_self_test(source, landmarker, state, cv2, MpImage, MpImageFormat, timestamp, headless, player)
            frame_count, perclos_value = 0, 0.0
        else:
            frame_count, perclos_value = run_live_monitoring(
                source, landmarker, state, cv2, MpImage, MpImageFormat, timestamp, headless,
                logger, alert_player, attention_player, fatigue_player,
            )
    finally:
        landmarker.close()
        source.close()
        if logger:
            logger.close()
        if alert_player:
            alert_player.stop()
        if attention_player:
            attention_player.stop()
        if fatigue_player:
            fatigue_player.stop()
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass

    if not args.modo_teste:
        print(f"\nFrames processados: {frame_count}  |  PERCLOS final (60s): {perclos_value*100:.0f}%")
        if logger and not args.no_log:
            print(f"Log da sessão em: {args.db}")


if __name__ == "__main__":
    main()
