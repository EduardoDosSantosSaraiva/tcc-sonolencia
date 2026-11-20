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
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
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
ATTENTION_REPEAT_IDLE_SECONDS = 60.0   # sem motivo novo, o aviso de atenção só
                                        # repete depois disso (ver core.AvisoLeve);
                                        # o de fadiga não repete sem bocejo novo

FATIGUE_REPEAT_SECONDS = 10.0   # aviso específico de bocejo — nunca dispara
                                  # o alarme cheio (ver core.DrowsinessState),
                                  # só esse aviso leve de fadiga/cansaço

DISTRACTION_SOUND_PATH = Path(__file__).parent / "aviso_distracao.wav"
DISTRACTION_REPEAT_SECONDS = 6.0   # rosto sumido (core.DISTRACTION_SECONDS):
                                     # repete enquanto o motorista não voltar
                                     # a olhar pra frente

BUZZER_GPIO = 26          # buzzer ativo reserva (GPIO26 = pino físico 37, GND no
                          # pino 39). Só toca quando o fone Bluetooth cai, junto
                          # com o alarme cheio ou o aviso de distração. Pinos
                          # 1-26 ficam por baixo da tela piscreen, que ainda usa
                          # o GPIO17 pro touch, 24/25 e o SPI0
BUZZER_HOLD_SECONDS = 8.0   # mesmo tempo do alerta.wav: o alarme nunca corta
                              # no meio, nem no buzzer

CALIB_TENTATIVAS = 3      # calibração que falha ou sai fora do normal é refeita
CALIB_DESVIO_MAX = 0.20   # EAR calibrado mais de 20% longe da mediana das
                          # calibrações anteriores = provavelmente olho
                          # arregalado ou postura errada (aconteceu em 23/09:
                          # 0,320 contra ~0,24 de sempre, e o autoteste caiu
                          # de 7/8 pra 4/8)
CALIB_HISTORICO_MIN = 3   # com menos calibrações boas que isso, não compara

FALA_ESPERA_MOTORISTA = ("Aguardando o motorista. A calibração começa quando o seu rosto "
                         "aparecer na frente da câmera.")
FALA_CALIB_INICIO = ("Calibração em três segundos. Sente como se estivesse dirigindo, "
                     "olhe para a frente com os olhos relaxados e a boca fechada.")
FALA_CALIB_SEM_OLHO = "Não consegui ver seus olhos. Ajuste a câmera ou a sua posição. Vou calibrar de novo."
FALA_CALIB_DIFERENTE = ("A calibração ficou diferente do normal. Olhe para a frente, "
                        "sem arregalar os olhos. Vou calibrar de novo.")
FALA_CALIB_BOCA = ("Não consegui calibrar com a boca aberta. "
                   "Fique com a boca fechada por alguns segundos. Vou calibrar de novo.")
FALA_CALIB_OK = "Calibração concluída. Monitoramento ativo."
FALA_CALIB_PADRAO = "Não consegui calibrar. Vou usar os valores padrão, mas confira a câmera."
FALA_FONE_CAIU = "Atenção: o fone desconectou. Os alarmes vão tocar na saída de som reserva."
FALA_FONE_VOLTOU = "Fone conectado de novo."
FRASES_PROGRAMA = [FALA_ESPERA_MOTORISTA, FALA_CALIB_INICIO, FALA_CALIB_SEM_OLHO, FALA_CALIB_DIFERENTE, FALA_CALIB_BOCA, FALA_CALIB_OK,
                   FALA_CALIB_PADRAO, FALA_FONE_CAIU, FALA_FONE_VOLTOU]   # pré-gravadas (gerar_vozes.py)

PIPER_BIN = Path(__file__).parent / "venv" / "bin" / "piper"
TTS_MODEL_PATH = Path(__file__).parent / "tts_model" / "pt_BR-faber-medium.onnx"

# Falas pré-gravadas com a voz Kokoro (gerar_vozes.py). Síntese na hora não
# serve: o Kokoro leva ~3x o tempo real no Pi 5. Como toda frase falada pelo
# programa é fixa, ela é gerada uma vez e tocada do arquivo; o Piper fica só
# de reserva pra alguma frase que não tenha sido gerada.
VOICE_DIR = Path(__file__).parent / "vozes"


def voice_file(text):
    return VOICE_DIR / (hashlib.sha1(text.encode("utf-8")).hexdigest()[:16] + ".wav")


def frase_resultado(passou, detalhe):
    return f"{'Certo' if passou else 'Atenção'}: {detalhe}."


def frase_conclusao(ok_count, total):
    return f"Autoteste concluído. {ok_count} de {total} cenários passaram."


def speak_blocking(text, player):
    """Toca a frase (pré-gravada em vozes/, ou sintetizada com o Piper se não
    houver arquivo), esperando terminar antes de voltar — o autoteste guiado
    é sequencial de propósito (fala a instrução, só depois começa a contar o
    tempo do cenário), diferente do AlertPlayer (que é assíncrono pra não
    travar o loop de detecção)."""
    if player is None:
        print(f"[fala] {text}")
        return
    pre = voice_file(text)
    if pre.exists():
        try:
            subprocess.run([player, str(pre)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        except Exception as e:
            print(f"Aviso: falha ao tocar a fala ({e}).")
        return
    if not PIPER_BIN.exists() or not TTS_MODEL_PATH.exists():
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


def speak_async(text, player):
    """Toca uma fala pré-gravada sem esperar (usado de dentro da thread do
    AudioOutputMonitor, que não pode travar)."""
    pre = voice_file(text)
    if player is None or not pre.exists():
        print(f"[fala] {text}")
        return
    subprocess.Popen([player, str(pre)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class AudioOutputMonitor:
    """Vigia se a saída de som padrão continua sendo o fone Bluetooth. Se o
    fone desconectar (bateria, alcance), o PipeWire passa a tocar na saída
    seguinte (HDMI/alto-falante) sozinho; o monitor avisa por voz, marca
    fone_ok=False pro loop principal ligar o buzzer reserva e mostrar o aviso
    na tela. Vigia também quando o programa começa sem fone: no boot a caixa
    Bluetooth costuma conectar depois que o monitoramento já abriu, e aí os
    alarmes passam para ela e o aviso de fone caído sai da tela. Roda numa
    thread porque o wpctl leva alguns ms e o loop de detecção não pode esperar."""

    INTERVAL_SECONDS = 3.0

    def __init__(self, player):
        self.player = player
        self.fone_ok = self._fone_conectado()
        self._parar = threading.Event()
        if self.fone_ok is not None:
            threading.Thread(target=self._loop, daemon=True).start()
        if self.fone_ok is False:
            print("Aviso: a saída de som não é um fone Bluetooth; alarmes tocam na saída padrão"
                  " até um fone conectar.")

    @staticmethod
    def _fone_conectado():
        """True/False se deu pra consultar o PipeWire, None se não deu."""
        try:
            out = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"],
                                 capture_output=True, text=True, timeout=2).stdout
        except Exception:
            return None
        return "bluez" in out

    def _loop(self):
        while not self._parar.wait(self.INTERVAL_SECONDS):
            ok = self._fone_conectado()
            if ok is None or ok == self.fone_ok:
                continue
            self.fone_ok = ok
            print("Fone Bluetooth reconectado." if ok else "FONE BLUETOOTH DESCONECTOU: usando saída reserva e buzzer.")
            speak_async(FALA_FONE_VOLTOU if ok else FALA_FONE_CAIU, self.player)

    def stop(self):
        self._parar.set()


class BuzzerReserva:
    """Buzzer ativo no GPIO, alarme de reserva pra quando o fone cai. Sem o
    buzzer ligado fisicamente, o GPIO só muda de nível e nada acontece."""

    def __init__(self, pin=BUZZER_GPIO):
        self.buzzer = None
        self.ligado = False
        try:
            from gpiozero import Buzzer
            self.buzzer = Buzzer(pin)
        except Exception as e:
            print(f"Buzzer reserva no GPIO{pin} indisponível ({e}).")

    def set(self, ligar):
        if self.buzzer is None or ligar == self.ligado:
            return
        if ligar:
            self.buzzer.beep(on_time=0.2, off_time=0.2)
        else:
            self.buzzer.off()
        self.ligado = ligar

    def close(self):
        if self.buzzer is not None:
            self.buzzer.close()


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
    ap.add_argument("--buzzer-gpio", type=int, default=BUZZER_GPIO,
                    help=f"GPIO do buzzer reserva, usado se o fone Bluetooth cair (padrão {BUZZER_GPIO}; 0 desliga)")
    ap.add_argument("--modo-teste", action="store_true", help="Autoteste guiado por voz pelos 8 cenários do protocolo_testes.md")
    ap.add_argument("--cenario", type=int, nargs="+", metavar="N",
                    help="Com --modo-teste, roda só esses cenários (ex.: --cenario 7)")
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


def pick_screen():
    """(nome, x, y, largura, altura) da tela onde a janela abre, em
    coordenadas do layout do Wayland: a saída escolhida em TCC_TELA (ex.:
    TCC_TELA=SPI-1 para testar a telinha com o HDMI ligado) ou a maior tela
    ligada. None se não deu pra consultar (sessão X11, sem wlr-randr)."""
    try:
        out = subprocess.run(["wlr-randr", "--json"], capture_output=True, text=True, timeout=3).stdout
        telas = []
        for o in json.loads(out):
            modo = next((m for m in o.get("modes", []) if m.get("current")), None)
            if not o.get("enabled") or not modo:
                continue
            escala = o.get("scale") or 1.0
            w, h = modo["width"] / escala, modo["height"] / escala
            if "90" in str(o.get("transform", "")) or "270" in str(o.get("transform", "")):
                w, h = h, w
            telas.append((o["name"], int(o["position"]["x"]), int(o["position"]["y"]), int(w), int(h)))
    except Exception:
        return None
    pedida = os.environ.get("TCC_TELA")
    for t in telas:
        if t[0] == pedida:
            return t
    return max(telas, key=lambda t: t[3] * t[4]) if telas else None


def fit_to_screen(cv2, img, w, h):
    """Reduz a imagem para caber em w x h sem distorcer e completa com preto.
    INTER_LINEAR: 0,85 ms por quadro de 1296x972 no Pi 5, contra 4,2 ms do
    INTER_AREA (medido em 01/11/2026), num laço que já usa ~25 de 33 ms."""
    ih, iw = img.shape[:2]
    if (iw, ih) == (w, h):   # a interface já desenha no tamanho da telinha
        return img
    f = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * f)), max(1, int(ih * f))
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    top, left = (h - nh) // 2, (w - nw) // 2
    return cv2.copyMakeBorder(img, top, h - nh - top, left, w - nw - left, cv2.BORDER_CONSTANT, value=0)


_window_placed = False
_small_screen = None   # (w, h) quando a tela é menor que a imagem
UI = None              # interface.Interface: as telas do programa (None sem tela)


def tecla(cv2):
    """cv2.waitKey(1) que também trata as teclas da interface: D liga e
    desliga os dados técnicos (no carro, um toque na tela faz o mesmo)."""
    k = cv2.waitKey(1) & 0xFF
    if k in (ord("d"), ord("D")) and UI is not None:
        UI.tecnico = not UI.tecnico
    return k


def _clique(evento, _x, _y, _flags, _param):
    if evento == 4 and UI is not None:   # cv2.EVENT_LBUTTONUP; o toque na telinha vira clique
        UI.tecnico = not UI.tecnico


def mostrar_mensagem(cv2, headless, titulo, texto, tipo="info", etapa=1):
    """Põe um cartão na tela antes de uma fala: a fala trava o laço, e sem
    isso a janela ficava parada no último quadro, sem dizer o que acontecia."""
    if headless or UI is None:
        return
    show_frame(cv2, UI.mensagem(titulo, texto, tipo, etapa))
    cv2.waitKey(1)


def show_frame(cv2, img):
    """cv2.imshow na janela do programa, aberta na tela certa. Sem posição, o
    labwc abria a janela na tela SPI de 3,5" (480x320, à esquerda do monitor
    HDMI), espremida, e no boot parecia que o programa não tinha aberto.
    - Tela que comporta a imagem (monitor HDMI, nos testes): janela no
      tamanho natural, centralizada.
    - Tela menor que a imagem (a telinha, no carro): a imagem é reduzida para
      caber e a janela ocupa a tela inteira, sem barras."""
    global _window_placed, _small_screen
    if _window_placed:
        cv2.imshow(WINDOW_NAME, fit_to_screen(cv2, img, *_small_screen) if _small_screen else img)
        return
    _window_placed = True
    tela = pick_screen()
    if not tela:
        cv2.imshow(WINDOW_NAME, img)
        return
    _nome, x, y, w, h = tela
    ih, iw = img.shape[:2]
    extra = 40   # barra de título (sem a barra de ferramentas do Qt: WINDOW_GUI_NORMAL)
    if iw <= w and ih + extra <= h:
        # AUTOSIZE com GUI_NORMAL encolhia a janela para 98x55 ao mover
        # (medido): NORMAL e o tamanho pedido depois de mover
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO | cv2.WINDOW_GUI_NORMAL)
        cv2.imshow(WINDOW_NAME, img)
        _move_to_screen(cv2, x + (w - iw) // 2, y + (h - ih - extra) // 2, tela)
        cv2.resizeWindow(WINDOW_NAME, iw, ih)
    else:
        _small_screen = (w, h)
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO | cv2.WINDOW_GUI_NORMAL)
        cv2.imshow(WINDOW_NAME, fit_to_screen(cv2, img, w, h))
        # a tela cheia vai para a tela onde a janela está: só depois de ela
        # chegar na telinha
        _move_to_screen(cv2, x, y, tela)
        cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    try:
        cv2.setMouseCallback(WINDOW_NAME, _clique)
    except Exception:
        pass


def _move_to_screen(cv2, px, py, tela, timeout=3.0):
    """Move a janela e confere onde ela foi parar, repetindo até ela estar na
    tela pedida. Medido (01/11/2026): logo depois do 1º imshow, o labwc
    ignora o pedido de ir para a telinha (a janela ficava no HDMI); com 0,1 s
    de espera já obedece. O getWindowImageRect devolve a posição real."""
    _nome, x, y, w, h = tela
    fim = time.monotonic() + timeout
    while True:
        cv2.moveWindow(WINDOW_NAME, px, py)
        cv2.waitKey(100)
        try:
            rx, ry, rw, rh = cv2.getWindowImageRect(WINDOW_NAME)
        except Exception:
            return   # backend sem essa consulta: fica com o pedido feito
        cx, cy = rx + rw / 2, ry + rh / 2
        if (x <= cx < x + w and y <= cy < y + h) or time.monotonic() > fim:
            return


PREP_SECONDS = 3   # aviso antes da calibração de verdade começar, pra pessoa
                     # se posicionar sem que esse tempo conte como amostra

DRIVER_READY_SECONDS = 1.0   # rosto com os dois olhos medidos por esse tempo
                             # seguido = tem gente sentada, pode calibrar
DRIVER_SPEAK_AFTER = 2.0     # sem rosto depois disso, avisa por voz (uma vez);
                             # com o motorista já sentado não fala nada a mais


def wait_for_driver(source, landmarker, headless, cv2, player=None):
    """Espera o motorista aparecer antes de calibrar. O início automático liga
    o programa com o Pi, muitas vezes sem ninguém na frente (01/11/2026: três
    tentativas de calibração no vazio e a sessão inteira com limiar padrão,
    cabeça e sorriso desligados). Espera sem limite de tempo; 'q' ou fechar a
    janela levanta KeyboardInterrupt, tratado no main como saída normal."""
    import mediapipe as mp

    ts = core.MonotonicMs()
    t0 = desde = None
    falou = False
    while True:
        try:
            frame = source.read()
        except core.CaptureError:
            return
        if frame is None:
            return
        infer_frame, display_frame, iw, ih, t = frame
        if t0 is None:
            t0 = t
        try:
            result = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=infer_frame), ts.next(t))
        except Exception:
            result = None
        idx = core.select_driver_index(result.face_landmarks) if result else None
        olhos = False
        landmarks = None
        if idx is not None:
            landmarks = result.face_landmarks[idx]
            olhos = (len(landmarks) > max(core.LEFT_EYE + core.RIGHT_EYE)
                     and core.eye_aspect_ratio(landmarks, core.LEFT_EYE, iw, ih) is not None
                     and core.eye_aspect_ratio(landmarks, core.RIGHT_EYE, iw, ih) is not None)
        if not olhos:
            desde = None
        elif desde is None:
            desde = t
        if desde is not None and t - desde >= DRIVER_READY_SECONDS:
            return
        if not falou and desde is None and t - t0 >= DRIVER_SPEAK_AFTER:
            print("Ninguém na frente da câmera: aguardando o motorista para calibrar.")
            speak_blocking(FALA_ESPERA_MOTORISTA, player)
            falou = True
        if not headless and display_frame is not None:
            show_frame(cv2, UI.espera(display_frame, rosto_ok=olhos, landmarks=landmarks))
            if tecla(cv2) == ord("q"):
                raise KeyboardInterrupt
            try:
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    raise KeyboardInterrupt
            except cv2.error:
                raise KeyboardInterrupt


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
            show_frame(cv2, UI.preparo(display_frame, remaining))
            tecla(cv2)


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

    left_ear = right_ear = mar = head_pitch = None
    abertura_boca = abertura_boca_ext = queixo = None
    left_ear3d = right_ear3d = mar3d = yaw = None   # em estudo (rosto de lado)
    idx = core.select_driver_index(result.face_landmarks)
    landmarks = result.face_landmarks[idx] if idx is not None else None
    face_detected = landmarks is not None
    if landmarks is not None and len(landmarks) > max(core.LEFT_EYE + core.RIGHT_EYE + core.MOUTH):
        left_ear = core.eye_aspect_ratio(landmarks, core.LEFT_EYE, iw, ih)
        right_ear = core.eye_aspect_ratio(landmarks, core.RIGHT_EYE, iw, ih)
        mar = core.mouth_aspect_ratio(landmarks, core.MOUTH, iw, ih)
        abertura_boca = core.mouth_opening_ratio(landmarks, iw, ih)
        abertura_boca_ext = core.mouth_opening_ratio(landmarks, iw, ih, core.OUTER_LIPS)
        queixo = core.jaw_drop_ratio(landmarks, iw, ih)
        head_pitch = core.head_drop_ratio(landmarks, iw, ih)
        left_ear3d = core.aspect_ratio_3d(landmarks, core.LEFT_EYE, iw, ih)
        right_ear3d = core.aspect_ratio_3d(landmarks, core.RIGHT_EYE, iw, ih)
        mar3d = core.aspect_ratio_3d(landmarks, core.MOUTH, iw, ih)
        yaw = core.head_yaw_deg(landmarks, iw, ih)

    return {
        "left_ear": left_ear, "right_ear": right_ear, "mar": mar,
        "head_pitch": head_pitch,
        "face_detected": face_detected, "landmarks": landmarks,
        "abertura_boca": abertura_boca, "abertura_boca_ext": abertura_boca_ext,
        "queixo": queixo, "blendshapes": core.blendshapes_do_rosto(result, idx),
        "left_ear3d": left_ear3d, "right_ear3d": right_ear3d, "mar3d": mar3d, "yaw": yaw,
        "latency_ms": latency_ms,
    }


def historico_ear(n=10):
    """Mediana do EAR das últimas n calibrações que deram certo, ou None se
    ainda não há calibrações suficientes pra comparar."""
    try:
        linhas = CALIBRATION_LOG_PATH.read_text().splitlines()
    except OSError:
        return None
    valores = []
    for linha in linhas:
        for campo in linha.split():
            if campo.startswith("EAR=") and campo != "EAR=FALHOU":
                try:
                    valores.append(float(campo[4:]))
                except ValueError:
                    pass
    valores = sorted(valores[-n:])
    if len(valores) < CALIB_HISTORICO_MIN:
        return None
    meio = len(valores) // 2
    return valores[meio] if len(valores) % 2 else (valores[meio - 1] + valores[meio]) / 2


def run_calibration(source, landmarker, headless, cv2, player=None, conferir=True):
    """Calibra, falando as instruções. Se não achar o olho, ou se o EAR sair
    muito longe do histórico (conferir=True), explica por voz e refaz, até
    CALIB_TENTATIVAS vezes. Na última tentativa aceita o que tiver: EAR
    diferente pode ser só outra pessoa dirigindo. conferir=False (vídeo
    gravado) calibra uma vez só, sem comparar com o histórico do motorista."""
    referencia = historico_ear() if conferir else None
    tentativas = CALIB_TENTATIVAS if conferir else 1

    def on_frame(display_frame, remaining):
        if headless or display_frame is None:
            return
        progresso = 1.0 - max(0.0, remaining) / core.CALIBRATION_SECONDS
        show_frame(cv2, UI.calibrando(display_frame, progresso, remaining))
        tecla(cv2)

    # o que a tela mostra enquanto cada fala toca
    cartoes = {
        FALA_CALIB_SEM_OLHO: ("Não encontrei seus olhos", "Ajuste a câmera ou a sua posição. Vou calibrar de novo."),
        FALA_CALIB_DIFERENTE: ("Calibração fora do normal",
                               "Olhe para a frente, sem arregalar os olhos. Vou calibrar de novo."),
        FALA_CALIB_BOCA: ("Boca aberta na calibração", "Fique com a boca fechada. Vou calibrar de novo."),
    }

    bocejos_total = []   # bocejo numa tentativa que foi refeita também conta
    for tentativa in range(1, tentativas + 1):
        if conferir:
            wait_for_driver(source, landmarker, headless, cv2, player)
            mostrar_mensagem(cv2, headless, "Calibração em 3 segundos",
                             "Sente como se estivesse dirigindo: olhe para a frente, olhos relaxados e boca fechada.")
            speak_blocking(FALA_CALIB_INICIO, player)
        wait_for_ready(source, headless, cv2)
        print(f"Calibrando pra valer (tentativa {tentativa}/{tentativas})... olhos abertos e relaxados, "
              f"boca fechada, cabeça reta, por {core.CALIBRATION_SECONDS:.0f}s.")
        ear_baseline, mar_baseline, pitch_baseline, warning, bocejos, sorriso_baseline = core.calibrate(
            source, landmarker, on_frame=on_frame)
        if bocejos:
            print(f"{len(bocejos)} bocejo(s) durante a calibração: entram na contagem de bocejos.")
            bocejos_total += bocejos
        problema = None
        if ear_baseline is None:
            # mesmo estendendo a calibração até CALIBRATION_MAX_SECONDS não
            # sobrou rosto em repouso suficiente: ou a boca ficou aberta, ou
            # o olho não foi achado
            problema = FALA_CALIB_BOCA if warning and "maior parte" in warning else FALA_CALIB_SEM_OLHO
        elif referencia and abs(ear_baseline - referencia) / referencia > CALIB_DESVIO_MAX:
            problema = FALA_CALIB_DIFERENTE
            warning = (f"EAR {ear_baseline:.3f} a {100 * abs(ear_baseline - referencia) / referencia:.0f}% "
                       f"da mediana das calibrações anteriores ({referencia:.3f})")
        if warning:
            print(f"Aviso na calibração: {warning}.")
        if conferir:   # vídeo gravado é de outra pessoa: não entra no histórico do motorista
            log_calibration(ear_baseline, mar_baseline, pitch_baseline, warning)
        if problema is None or tentativa == tentativas:
            break
        mostrar_mensagem(cv2, headless, *cartoes[problema], tipo="aviso")
        speak_blocking(problema, player)

    if ear_baseline is None:
        print("Usando limiares padrão (sem calibração válida de olho).")
        if conferir:
            mostrar_mensagem(cv2, headless, "Calibração não concluída",
                             "Vou usar os valores padrão. Confira a posição da câmera.", tipo="aviso", etapa=2)
            speak_blocking(FALA_CALIB_PADRAO, player)
        return None, None, None, None, bocejos_total
    print(f"Calibração concluída. EAR olho aberto: {ear_baseline:.3f}"
          + (f"  MAR boca fechada: {mar_baseline:.3f}" if mar_baseline else "  MAR: sem amostras suficientes")
          + (f"  pitch neutro: {pitch_baseline:.3f}" if pitch_baseline is not None else "  pitch: sem amostras suficientes"))
    if conferir:
        mostrar_mensagem(cv2, headless, "Calibração concluída", "Monitoramento ativo.", tipo="ok", etapa=2)
        speak_blocking(FALA_CALIB_OK, player)
    return ear_baseline, mar_baseline, pitch_baseline, sorriso_baseline, bocejos_total


# ---------------------------------------------------------------------------
# Modo de autoteste guiado por voz: espelha os cenários de protocolo_testes.md,
# falando a instrução de cada um, esperando a pessoa executar, e avaliando se
# o sinal esperado foi detectado — sem precisar preencher planilha na mão.
# ---------------------------------------------------------------------------

SELF_TEST_INTRO = (
    "Modo de autoteste. Vou guiar você por oito cenários, um de cada vez. "
    "Escute a instrução e faça o que eu pedir."
)

SELF_TEST_SCENARIOS = [
    (
        "Baseline",
        "Cenário um, linha de base. Fique parado, olhando para a frente como se estivesse dirigindo, por quinze segundos.",
        15,
        lambda b, a: not a["reached_fechado"] and a["blink_count"] == b["blink_count"],
        "sem alarme nem piscada longa à toa",
        "disparou alarme ou piscada longa à toa",
    ),
    (
        "Piscada normal",
        "Cenário dois, piscada normal. Pisque algumas vezes, normalmente, por dez segundos.",
        10,
        lambda b, a: a["blink_count"] == b["blink_count"],
        "nenhuma piscada longa contada",
        "piscada normal foi contada como longa",
    ),
    (
        "Piscada longa",
        "Cenário três, piscada longa. Feche o olho por quase um segundo, e abra.",
        6,
        lambda b, a: a["blink_count"] > b["blink_count"] and not a["reached_fechado"],
        "piscada longa contada, sem disparar o alarme cheio",
        "não contou a piscada longa, ou disparou o alarme cheio",
    ),
    (
        "Microssono",
        "Cenário quatro, microssono. Feche o olho e segure por dois segundos ou mais.",
        8,
        lambda b, a: a["reached_fechado"],
        "alarme cheio disparou",
        "alarme cheio não disparou",
    ),
    (
        "Fala normal",
        "Cenário cinco, fala normal. Fale alguma coisa, naturalmente, olhando para a frente.",
        15,
        lambda b, a: a["yawn_count"] == b["yawn_count"],
        "nenhum bocejo contado à toa",
        "fala foi confundida com bocejo",
    ),
    (
        "Bocejo real",
        "Cenário seis, bocejo real. Boceje de verdade, pode fechar o olho.",
        8,
        lambda b, a: a["yawn_count"] > b["yawn_count"] and not a["reached_fechado"],
        "bocejo contado, sem disparar o alarme cheio",
        "não contou o bocejo, ou disparou o alarme cheio",
    ),
    (
        "Cabeça caindo",
        "Cenário sete, cochilo com a cabeça caindo. Feche os olhos e deixe a cabeça pender pra frente por dois segundos.",
        8,
        # passa se a queda foi contada OU se ainda está em andamento mas já
        # durou o suficiente (a contagem só fecha quando a cabeça sobe, e a
        # pessoa pode ainda estar de cabeça baixa quando a janela acaba)
        lambda b, a: a["head_down_count"] > b["head_down_count"] or a["reached_head_down"],
        "queda de cabeça contada",
        "não contou a queda de cabeça (confira se a calibração de pitch rodou)",
    ),
    (
        # De óculos o microssono tem que disparar igual: a luz infravermelha
        # da câmera atravessa a lente (escura ou polarizada) e o EAR enxerga o
        # olho. Havia um modo óculos que, com a região do olho escura, parava
        # de olhar o olho; saiu em 30/10/2026, porque com infravermelho a lente
        # não esconde o olho e o modo quase ligou num olho fechado sem óculos
        # (razão de brilho 0,71 contra o limiar de 0,70), o que desligaria o
        # alarme principal no meio de um cochilo.
        "Óculos",
        "Cenário oito, óculos. Coloque os óculos, escuro ou polarizado, e olhe para a frente por cinco segundos. "
        "Depois feche o olho e segure por três segundos.",
        12,
        lambda b, a: a["reached_fechado"],
        "de óculos, o olho fechado foi detectado e o alarme disparou",
        "de óculos, o olho fechado não foi detectado",
    ),
]


def run_guided_self_test(source, landmarker, state, cv2, MpImage, MpImageFormat, timestamp, headless, player,
                         cenarios=None):
    """cenarios: números (1 a 8) pra rodar só esses; None roda todos."""
    print("\n=== MODO AUTOTESTE GUIADO (ver protocolo_testes.md) ===")
    escolhidos = [c for i, c in enumerate(SELF_TEST_SCENARIOS, 1) if not cenarios or i in cenarios]
    nomes = [c[0] for c in escolhidos]
    tela = UI if not headless else None
    if not cenarios:
        if tela:
            show_frame(cv2, tela.mensagem_autoteste(
                "Autoteste guiado", f"Vou guiar você por {len(escolhidos)} cenários, um de cada vez. "
                "Escute a instrução e faça o que eu pedir.", nomes))
            cv2.waitKey(1)
        speak_blocking(SELF_TEST_INTRO, player)

    print(f"(limiares em uso: bocejo>{state.mar_threshold:.3f}  "
          + (f"cabeca-caida>{state.head_pitch_threshold:.3f}" if state.head_pitch_threshold is not None
             else "cabeca-caida: desativado"))

    resultados = []
    try:
        for indice, (nome, instrucao, duracao, avaliar, msg_ok, msg_falha) in enumerate(escolhidos, 1):
            feitos = [(r[0], r[1]) for r in resultados]
            if tela:
                show_frame(cv2, tela.autoteste(None, None, None, 0.0, indice, len(escolhidos), nome, instrucao,
                                               duracao, duracao, feitos, nomes))
                cv2.waitKey(1)
            speak_blocking(instrucao, player)
            print(f"\n[{nome}] {instrucao}")

            before = after = None
            # telemetria bruta observada no cenário, pra diagnosticar sem
            # precisar rodar de novo se algo não bater com o esperado
            ear_min = ear_max = None
            mar_min = mar_max = None
            pitch_min = pitch_max = None
            # cabeça: tempo com a cabeça baixa, com cabeça baixa E olho
            # fechando (o que conta), maior episódio e tempo sem rosto
            cab_baixa_s = cab_conta_s = sem_rosto_s = cab_maior_s = 0.0
            t_ant = None
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
                        pf["face_detected"], t,
                        jaw_open=pf["blendshapes"].get("jawOpen"), smile=core.sorriso(pf["blendshapes"]),
                    )
                    snapshot = {
                        "blink_count": st["blink_count_recent"],
                        "yawn_count": st["yawn_count_recent"],
                        "head_down_count": st["head_down_count_recent"],
                        "reached_fechado": (after or {}).get("reached_fechado", False) or st["level"] == "fechado",
                        "reached_head_down": (after or {}).get("reached_head_down", False)
                        or (st.get("head_down_dur") or 0) >= core.HEAD_DOWN_SECONDS_ALERT,
                    }
                    if before is None:
                        before = dict(snapshot)
                    after = snapshot

                    dt = t - t_ant if t_ant is not None else 0.0
                    t_ant = t
                    cab_baixa_s += dt if st.get("head_pitch_down") else 0.0
                    cab_conta_s += dt if st.get("head_down") else 0.0
                    sem_rosto_s += dt if not pf["face_detected"] else 0.0
                    cab_maior_s = max(cab_maior_s, st.get("head_down_dur") or 0.0)
                    if st["ear_avg"] is not None:
                        ear_min = st["ear_avg"] if ear_min is None else min(ear_min, st["ear_avg"])
                        ear_max = st["ear_avg"] if ear_max is None else max(ear_max, st["ear_avg"])
                    if st["mar"] is not None:
                        mar_min = st["mar"] if mar_min is None else min(mar_min, st["mar"])
                        mar_max = st["mar"] if mar_max is None else max(mar_max, st["mar"])
                    if st["head_pitch"] is not None:
                        pitch_min = st["head_pitch"] if pitch_min is None else min(pitch_min, st["head_pitch"])
                        pitch_max = st["head_pitch"] if pitch_max is None else max(pitch_max, st["head_pitch"])

                    if tela and display_frame is not None:
                        show_frame(cv2, tela.autoteste(display_frame, st, pf["landmarks"], t, indice,
                                                       len(escolhidos), nome, instrucao, remaining, duracao,
                                                       feitos, nomes))
                        tecla(cv2)

                if remaining <= 0:
                    break

            telemetria = (
                f"EAR min/max: {ear_min:.3f}/{ear_max:.3f}" if ear_min is not None else "EAR: sem amostras"
            ) + "; " + (
                f"MAR min/max: {mar_min:.3f}/{mar_max:.3f}" if mar_min is not None else "MAR: sem amostras"
            ) + "; " + (
                f"pitch min/max: {pitch_min:.3f}/{pitch_max:.3f}" if pitch_min is not None else "pitch: sem amostras"
            ) + (
                f"; cabeça baixa {cab_baixa_s:.1f}s, baixa com olho fechando {cab_conta_s:.1f}s, "
                f"maior episódio {cab_maior_s:.1f}s, sem rosto {sem_rosto_s:.1f}s"
            )

            if before is None or after is None:
                print("  -> pulado: sem frames processados nesse cenário.")
                resultados.append((nome, False, "sem frames processados", telemetria))
                continue

            passou = bool(avaliar(before, after))
            detalhe = msg_ok if passou else msg_falha
            print(f"  -> {'passou' if passou else 'nao passou'}: {detalhe}  [{telemetria}]")
            resultados.append((nome, passou, detalhe, telemetria))
            if tela:
                show_frame(cv2, tela.resultado_cenario(nome, passou, detalhe, nomes,
                                                       [(r[0], r[1]) for r in resultados]))
                cv2.waitKey(1)
            speak_blocking(frase_resultado(passou, detalhe), player)
    except KeyboardInterrupt:
        print("\nAutoteste interrompido pelo usuário.")

    print("\n=== RESUMO DO AUTOTESTE ===")
    ok_count = sum(1 for r in resultados if r[1])
    for nome, passou, detalhe, telemetria in resultados:
        print(f"  [{'OK' if passou else 'FALHOU'}] {nome}: {detalhe}  [{telemetria}]")
    print(f"\n{ok_count}/{len(resultados)} cenários passaram.")
    if tela and resultados:
        show_frame(cv2, tela.resumo_autoteste(resultados))
        cv2.waitKey(1)
    speak_blocking(frase_conclusao(ok_count, len(resultados)), player)

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
                         logger, alert_player, attention_player, fatigue_player,
                         distraction_player=None, audio_monitor=None, buzzer=None):
    """Loop principal de monitoramento ao vivo: captura, detecta, decide o
    nível de sonolência, dispara os alarmes e desenha a janela. Retorna
    (frame_count, perclos_value) pro resumo final impresso no main()."""
    consecutive_errors = 0
    prev_wall = time.time()
    prev_level = None
    # quando cada aviso leve toca (motivo novo, não a cada 10 s; ver core.AvisoLeve)
    aviso_atencao = core.AvisoLeve(ATTENTION_REPEAT_SECONDS, ATTENTION_REPEAT_IDLE_SECONDS)
    aviso_fadiga = core.AvisoLeve(FATIGUE_REPEAT_SECONDS)
    prev_blink_count = 0
    prev_yawn_count = 0
    prev_head_down_count = 0
    prev_distraido = False
    buzzer_ate = None
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
                face_detected, t,
                jaw_open=pf["blendshapes"].get("jawOpen"), smile=core.sorriso(pf["blendshapes"]),
            )
            perclos_value = st["perclos"]

            if alert_player:
                alert_player.maybe_play(st["level"] == "fechado", t)
            tocar_atencao = aviso_atencao.tocar(
                st["atencao_by_blinks"] or st["atencao_by_head"] or st["atencao_by_perclos"],
                st["marcador_atencao"], t)
            tocar_fadiga = aviso_fadiga.tocar(st["atencao_by_yawn"], st["marcador_fadiga"], t)
            if attention_player:
                attention_player.maybe_play(tocar_atencao, t)
            if fatigue_player:
                fatigue_player.maybe_play(tocar_fadiga, t)
            if distraction_player:
                distraction_player.maybe_play(st["distraido"], t)
            fone_caiu = audio_monitor is not None and audio_monitor.fone_ok is False
            if buzzer:
                if fone_caiu and (st["level"] == "fechado" or st["distraido"]):
                    buzzer_ate = t + BUZZER_HOLD_SECONDS
                buzzer.set(buzzer_ate is not None and t < buzzer_ate)

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
                if st["distraido"] and not prev_distraido:
                    logger.log_evento(t, "distracao")
            prev_distraido = st["distraido"]
            prev_level = st["level"]

            now_wall = time.time()
            fps = 1.0 / max(now_wall - prev_wall, 1e-6)
            prev_wall = now_wall

            if headless:
                if st["status"] != last_printed_status:
                    print(f"[{t:7.1f}s] {st['status']}  PERCLOS(60s): {perclos_value*100:.0f}%")
                    last_printed_status = st["status"]
                continue

            show_frame(cv2, UI.monitor(
                display_frame, st, landmarks if face_detected else None, t, perclos=perclos_value,
                fone=audio_monitor.fone_ok if audio_monitor is not None else None,
                fps=fps, latencia=latency_ms, limiares=state))

            key = tecla(cv2)
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


def build_state(ear_baseline, mar_baseline, pitch_baseline, smile_baseline=None):
    """Converte a calibração em limiares e monta o estado de decisão.
    Compartilhado com avaliar_yawdd.py, pra avaliação no dataset usar
    exatamente os mesmos limiares do programa ao vivo."""
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
          f"bocejo>{mar_threshold:.3f} (sustenta>{mar_threshold * core.MAR_SUSTAIN_RATIO:.3f})  "
          + (f"cabeca-caida>{head_pitch_threshold:.3f}" if head_pitch_threshold is not None else "cabeca-caida: desativado (sem calibracao)")
          + (f"  sorriso>{smile_baseline + core.SMILE_DELTA:.3f}" if smile_baseline is not None else "  sorriso: desativado")
          + ("" if ear_baseline else "  (EAR padrão, sem calibração)"))

    return core.DrowsinessState(
        ear_threshold=ear_threshold, ear_almost_closed=ear_almost_closed,
        perclos_ear_threshold=ear_baseline * core.PERCLOS_CLOSED_RATIO if ear_baseline else None,
        mar_threshold=mar_threshold, head_pitch_threshold=head_pitch_threshold,
        smile_baseline=smile_baseline, ear_baseline=ear_baseline,
    )


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
    if not headless:
        global UI
        import interface
        UI = interface.Interface.para_tela(pick_screen())

    logger = None if args.no_log else core.SessionLogger(args.db)

    bocejos_calib = []
    if args.skip_calibration:
        ear_baseline = mar_baseline = pitch_baseline = smile_baseline = None
    else:
        voz = None if args.no_sound else (shutil.which("pw-play") or shutil.which("paplay") or shutil.which("ffplay"))
        try:
            ear_baseline, mar_baseline, pitch_baseline, smile_baseline, bocejos_calib = run_calibration(
                source, landmarker, headless, cv2, player=voz, conferir=not args.video)
        except KeyboardInterrupt:
            # 'q' esperando o motorista: saída normal (código 0), o início
            # automático entende como "o usuário fechou" e não reinicia
            print("\nEncerrado antes da calibração.")
            landmarker.close()
            source.close()
            if logger:
                logger.close()
            return

    state = build_state(ear_baseline, mar_baseline, pitch_baseline, smile_baseline)
    for t_bocejo in bocejos_calib:   # entra na janela de frequência e no log (1º frame)
        state.registrar_bocejo(t_bocejo)
    alert_player = None if args.no_sound else AlertPlayer()
    attention_player = None if args.no_sound else AlertPlayer(
        sound_path=ATTENTION_SOUND_PATH, repeat_seconds=ATTENTION_REPEAT_SECONDS
    )
    fatigue_player = None if args.no_sound else AlertPlayer(
        sound_path=FATIGUE_SOUND_PATH, repeat_seconds=FATIGUE_REPEAT_SECONDS
    )
    distraction_player = None if args.no_sound else AlertPlayer(
        sound_path=DISTRACTION_SOUND_PATH, repeat_seconds=DISTRACTION_REPEAT_SECONDS
    )
    ao_vivo = not args.video and not args.no_sound
    audio_monitor = AudioOutputMonitor(alert_player.player) if ao_vivo else None
    buzzer = BuzzerReserva(args.buzzer_gpio) if ao_vivo and args.buzzer_gpio else None

    timestamp = core.MonotonicMs()
    player = shutil.which("pw-play") or shutil.which("paplay") or shutil.which("ffplay")

    try:
        if args.modo_teste:
            run_guided_self_test(source, landmarker, state, cv2, MpImage, MpImageFormat, timestamp, headless, player,
                                 cenarios=args.cenario)
            frame_count, perclos_value = 0, 0.0
        else:
            frame_count, perclos_value = run_live_monitoring(
                source, landmarker, state, cv2, MpImage, MpImageFormat, timestamp, headless,
                logger, alert_player, attention_player, fatigue_player,
                distraction_player, audio_monitor, buzzer,
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
        if distraction_player:
            distraction_player.stop()
        if audio_monitor:
            audio_monitor.stop()
        if buzzer:
            buzzer.close()
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
