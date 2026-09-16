"""
Núcleo compartilhado do protótipo de detecção de sonolência do TCC:
cálculo de EAR/MAR, fontes de frame (câmera ao vivo / arquivo de vídeo),
máquina de estados com histerese, PERCLOS, frequência de bocejo e log
em SQLite.

Usado por eye_fatigue.py (app interativo) e benchmark.py (medição de
latência/FPS), pra não duplicar a mesma lógica nos dois.
"""

import sqlite3
import time
from collections import deque
from pathlib import Path

import numpy as np

MODEL_PATH = Path(__file__).parent / "face_landmarker.task"

# Índices dos pontos do face mesh (478 landmarks, com íris) usados no EAR
# (Eye Aspect Ratio, Soukupová & Čech 2016). Topologia igual à do Face Mesh clássico.
LEFT_EYE = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33, 160, 158, 133, 153, 144]

# Pontos da boca (contorno interno do lábio) usados no MAR (Mouth Aspect
# Ratio), no mesmo esquema de 6 pontos do EAR: p1/p4 = cantos (referência
# horizontal), (p2,p6) e (p3,p5) = pares verticais esquerdo/direito.
MOUTH = [61, 82, 312, 291, 317, 87]

LORES_SIZE = (640, 480)     # resolução usada pela IA (fixo, independe do sensor,
                             # não afetado pela resolução escolhida pro "main")
TARGET_FPS = 30              # só usado como fallback se o sensor_mode não informar
                              # fps (não limita mais a escolha de resolução)

EAR_THRESHOLD_DEFAULT = 0.21       # usado se não houver calibração por pessoa
EAR_ALMOST_CLOSED_DEFAULT = 0.26
CLOSED_RATIO = 0.80                # limiar "fechado" = baseline calibrado * essa fração
                                    # (era 0.75 — no primeiro teste guiado real,
                                    # 0.75 ficou mais rígido que o padrão sem
                                    # calibração e um microssono real não disparou
                                    # o alarme; MediaPipe às vezes não zera o EAR
                                    # de olho fechado de verdade, então o limiar
                                    # precisa de uma margem maior)
ALMOST_RATIO = 0.85                # limiar "quase fechado" = baseline * essa fração

CLOSED_SECONDS_ALERT = 1.5    # tempo contínuo de olho fechado para soar alerta

LONG_BLINK_SECONDS = 0.5      # piscada normal dura ~100-400ms; acima disso já é
                               # sinal de sonolência (pálpebra pesada, microssono
                               # começando), mesmo sem chegar no alerta de 1.5s
LONG_BLINK_WINDOW_SECONDS = 120   # janela deslizante p/ contar frequência de
                                    # piscadas longas
LONG_BLINK_FREQ_ALERT = 3     # nº de piscadas longas na janela que já eleva o
                               # nível pra "atencao" mesmo de olho aberto

MAR_THRESHOLD_DEFAULT = 0.6   # usado se não houver calibração de MAR por pessoa
                               # (chute inicial — ajustar com dados reais)
MAR_YAWN_RATIO = 2.2          # limiar de bocejo = MAR de boca fechada (calibrado) * essa razão
                               # (bocejo abre a boca bem mais que falar — valor alto
                               # de propósito pra não confundir com conversa)
MIN_YAWN_DURATION = 1.2       # boca aberta menos que isso é fala, não bocejo. Era
                               # 2.0s, mas um bocejo real testado ao vivo (MAR
                               # chegando a 0.79, bem acima do limiar) não sustentou
                               # 2s contínuos — o piso de amplitude (MAR_THRESHOLD_
                               # DEFAULT) já filtra a fala, então a duração não
                               # precisa ser tão rígida quanto antes
YAWN_SECONDS_ALERT = 1.5      # boca aberta contínua acima disso já eleva o nível
                               # agora, sem esperar terminar o bocejo
YAWN_GRACE_SECONDS = 0.5      # tolera até essa lacuna sem o sinal de bocejo antes
                               # de encerrar o episódio (glitch de tracking na boca
                               # bem aberta, ver SustainedSignalTracker)
YAWN_WINDOW_SECONDS = 300     # janela deslizante (5min) p/ frequência de bocejos
YAWN_FREQ_ALERT = 3           # nº de bocejos na janela que já eleva o nível

HEAD_DOWN_DELTA = 0.15        # desvio de head_drop_ratio em relação à baseline
                               # calibrada que já conta como "cabeça caindo".
                               # Chute inicial (razão adimensional, não graus)
                               # — ainda precisa validar com mais dados reais.
                               # Sem baseline calibrada por pessoa não existe
                               # limiar confiável (a proporção nariz/olhos varia
                               # de rosto pra rosto), então nesse caso a
                               # detecção de cabeça fica desativada (ver
                               # DrowsinessState.head_pitch_threshold=None).
HEAD_DOWN_SECONDS_ALERT = 1.0   # duração contínua de cabeça caída pra contar
                                  # o episódio e/ou elevar o nível agora
HEAD_DOWN_WINDOW_SECONDS = 120  # janela deslizante p/ frequência de quedas de cabeça
HEAD_DOWN_FREQ_ALERT = 2        # nº de quedas de cabeça na janela que já eleva o nível

# Óculos escuros (ou qualquer oclusão) deixam o EAR não confiável — a região
# do olho fica escura/sem contraste mesmo com o rosto bem iluminado. Em vez
# de limiar absoluto de brilho (varia com iluminação/exposição automática),
# compara o brilho da região do olho com o resto do rosto no mesmo frame.
DARK_EYES_BRIGHTNESS_RATIO = 0.70   # olho com brilho médio abaixo dessa fração
                                      # do resto do rosto = suspeito de lente escura.
                                      # Era 0.45 — testado ao vivo com óculos escuro
                                      # polarizado de verdade, a razão ficou em 0.63
                                      # (sem óculos fica ~0.95-1.0), então 0.45 nunca
                                      # cruzava. 0.70 dá margem confortável dos dois lados
DARK_EYES_MIN_FACE_BRIGHTNESS = 25  # abaixo disso o rosto inteiro já tá escuro
                                      # demais (ambiente, não óculos) — não mede
DARK_EYES_ENTER_SECONDS = 3.0   # tempo contínuo escuro pra confirmar "modo óculos
                                  # escuros" (evita entrar por causa de 1 piscada)
DARK_EYES_EXIT_SECONDS = 2.0    # tempo contínuo claro pra sair do modo

SMOOTHING_WINDOW = 3          # mediana de N frames p/ ignorar ruído de 1 frame só
                               # (reflexo de óculos, blink parcial, glitch de tracking)
MAR_SMOOTHING_WINDOW = 9      # boca bem aberta (bocejo) é mais instável pro tracking
                               # dos pontos internos do lábio do que o olho — testado
                               # ao vivo, MAR oscilou de 0.003 a 0.82 na mesma janela
                               # de 8s durante um bocejo real mesmo já com janela de 5;
                               # subiu pra 9 (~0.6s a 15fps) pra aguentar uma lacuna de
                               # tracking maior sem perder o bocejo
NO_FACE_GRACE_SECONDS = 0.5   # tolerância antes de considerar "sem rosto" de vez
                               # (tracking falha por 1-2 frames com frequência, mesmo
                               # com rosto presente; sem isso perde o progresso do
                               # alerta à toa)
MAX_CONSECUTIVE_ERRORS = 30   # frames ruins seguidos antes de desistir da câmera

PERCLOS_WINDOW_SECONDS = 60   # janela deslizante da métrica PERCLOS
PERCLOS_ALERT_RATIO = 0.15    # PERCLOS >= 15% em 60s já eleva o nível — pega
                               # sonolência que se manifesta em várias piscadas
                               # curtas ao longo do tempo, não uma fechada isolada

CALIBRATION_SECONDS = 5       # duração da calibração de baseline por pessoa
MIN_CALIB_SAMPLES = 20        # amostras mínimas pra confiar na calibração

# Faixa plausível de EAR/MAR de calibração — um valor fora disso normalmente
# significa que a calibração pegou lixo (rosto mal detectado, olho não
# aberto de verdade, ângulo ruim) e não deve ser usado silenciosamente: é
# melhor cair pro limiar padrão com aviso do que confiar num número absurdo
# (já aconteceu: uma calibração rendeu EAR baseline de 0.027, bem fora do
# normal, e o sistema usou sem perceber).
EAR_BASELINE_MIN = 0.15
EAR_BASELINE_MAX = 0.45
MAR_BASELINE_MAX = 0.5        # boca fechada não devia já parecer "bem aberta"


class CaptureError(Exception):
    """Falha transitória ao capturar um frame (ex.: câmera desconectou)."""


class MonotonicMs:
    """Gera o timestamp em milissegundos pro detect_for_video do MediaPipe.
    O modo VIDEO do MediaPipe exige timestamps estritamente crescentes entre
    chamadas — se dois frames caírem no mesmo milissegundo (fonte rápida
    demais, ou glitch de relógio) ele levanta erro nesse frame. Em vez de
    deixar isso acontecer, empurra 1ms pra frente quando precisar, garantindo
    a invariante sem descartar o frame."""

    def __init__(self):
        self.last_ms = -1

    def next(self, t):
        ms = int(t * 1000)
        if ms <= self.last_ms:
            ms = self.last_ms + 1
        self.last_ms = ms
        return ms


def _aspect_ratio(landmarks, idx, w, h):
    """Razão vertical/horizontal genérica de 6 pontos (Soukupová & Čech
    2016): serve tanto pro EAR (olho) quanto pro MAR (boca), só muda o
    conjunto de landmarks. Retorna None se a geometria não é confiável
    nesse frame (rosto de perfil extremo, landmark colapsado etc.) em vez
    de estourar ZeroDivisionError ou devolver um valor absurdo."""
    pts = np.array([(landmarks[i].x * w, landmarks[i].y * h) for i in idx])
    p1, p2, p3, p4, p5, p6 = pts
    horizontal = np.linalg.norm(p1 - p4)
    if horizontal < 1e-3:
        return None
    vertical = np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)
    return vertical / (2.0 * horizontal)


def eye_aspect_ratio(landmarks, eye_idx, w, h):
    return _aspect_ratio(landmarks, eye_idx, w, h)


def mouth_aspect_ratio(landmarks, mouth_idx, w, h):
    return _aspect_ratio(landmarks, mouth_idx, w, h)


MAX_FACES_DETECTED = 3   # detecta até esse nº de rostos no frame (motorista +
                          # possíveis passageiros); a escolha de qual é o
                          # motorista fica pra select_driver_face()


def build_landmarker(model_path=MODEL_PATH, num_faces=MAX_FACES_DETECTED):
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    if not model_path.exists():
        raise FileNotFoundError(
            f"Modelo não encontrado em {model_path}. Baixe com:\n"
            "curl -L -o face_landmarker.task "
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/latest/face_landmarker.task"
        )

    base_options = mp_python.BaseOptions(model_asset_path=str(model_path))
    options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.VIDEO,
        num_faces=num_faces,
        min_face_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return mp_vision.FaceLandmarker.create_from_options(options)


NOSE_TIP = 1  # ponta do nariz no face mesh do MediaPipe


def head_drop_ratio(landmarks, w, h):
    """Proxy 2D de queda de cabeça: altura da ponta do nariz abaixo da linha
    dos olhos, normalizada pela distância interocular (fica ~constante
    independente da distância da câmera, já que os dois escalam junto com o
    tamanho do rosto na imagem). Quando a cabeça pende pra frente, o nariz
    (que fica à frente do plano dos olhos no rosto) desce proporcionalmente
    mais que os olhos na projeção 2D — sinal direto, fácil de depurar
    olhando o número.

    Substituiu uma primeira versão baseada na matriz de rotação 3D do
    MediaPipe: testada ao vivo, aquela mal reagia a um aceno de cabeça real
    mas disparava alto por ruído/oclusão (mão perto do rosto). Essa aqui é
    literalmente uma posição de landmark, sem decomposição de matriz —
    menos sofisticada, mas dá pra confirmar visualmente que faz sentido."""
    eye_idx = LEFT_EYE + RIGHT_EYE
    eye_line_y = sum(landmarks[i].y for i in eye_idx) / len(eye_idx) * h
    left_eye_x = sum(landmarks[i].x for i in LEFT_EYE) / len(LEFT_EYE) * w
    right_eye_x = sum(landmarks[i].x for i in RIGHT_EYE) / len(RIGHT_EYE) * w
    interocular = abs(left_eye_x - right_eye_x)
    if interocular < 1e-3:
        return None
    nose_y = landmarks[NOSE_TIP].y * h
    return (nose_y - eye_line_y) / interocular


def _face_bbox_area(landmarks):
    xs = [lm.x for lm in landmarks]
    ys = [lm.y for lm in landmarks]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def eye_region_darkness(frame, landmarks, w, h):
    """Retorna (brilho_medio_olhos, brilho_medio_rosto), 0-255, ou (None,
    None) se alguma das regiões não puder ser recortada. Usado pra estimar
    se os olhos estão cobertos por lente escura: compara o brilho da região
    ao redor dos olhos com o resto do rosto no mesmo frame, em vez de um
    limiar absoluto (que variaria com iluminação ambiente e exposição
    automática da câmera)."""
    import cv2

    xs_face = [lm.x for lm in landmarks]
    ys_face = [lm.y for lm in landmarks]
    fx1, fy1 = max(0, int(min(xs_face) * w)), max(0, int(min(ys_face) * h))
    fx2, fy2 = int(max(xs_face) * w), int(max(ys_face) * h)
    face_roi = frame[fy1:fy2, fx1:fx2]

    eye_idx = LEFT_EYE + RIGHT_EYE
    xs_eye = [landmarks[i].x for i in eye_idx]
    ys_eye = [landmarks[i].y for i in eye_idx]
    pad = int(0.02 * w)  # expande um pouco além dos pontos pra cobrir a lente
    ex1, ey1 = max(0, int(min(xs_eye) * w) - pad), max(0, int(min(ys_eye) * h) - pad)
    ex2, ey2 = int(max(xs_eye) * w) + pad, int(max(ys_eye) * h) + pad
    eye_roi = frame[ey1:ey2, ex1:ex2]

    if face_roi.size == 0 or eye_roi.size == 0:
        return None, None

    face_gray = cv2.cvtColor(face_roi, cv2.COLOR_RGB2GRAY) if face_roi.ndim == 3 else face_roi
    eye_gray = cv2.cvtColor(eye_roi, cv2.COLOR_RGB2GRAY) if eye_roi.ndim == 3 else eye_roi
    return float(np.mean(eye_gray)), float(np.mean(face_gray))


class DarkEyesTracker:
    """Detecta, com histerese, se a região dos olhos está consistentemente
    escura/sem contraste em relação ao resto do rosto (indício de óculos
    escuros). Exige DARK_EYES_ENTER_SECONDS contínuos escuro pra ativar o
    modo, e DARK_EYES_EXIT_SECONDS contínuos claro pra desativar — evita
    ficar entrando/saindo a cada frame por ruído momentâneo (sombra,
    piscada, ângulo)."""

    def __init__(self, enter_seconds, exit_seconds):
        self.enter_seconds = enter_seconds
        self.exit_seconds = exit_seconds
        self.dark_since = None
        self.bright_since = None
        self.active = False

    def update(self, is_dark, t):
        if is_dark:
            self.bright_since = None
            if self.dark_since is None:
                self.dark_since = t
            if not self.active and (t - self.dark_since) >= self.enter_seconds:
                self.active = True
        else:
            self.dark_since = None
            if self.bright_since is None:
                self.bright_since = t
            if self.active and (t - self.bright_since) >= self.exit_seconds:
                self.active = False
        return self.active


def select_driver_index(face_landmarks_list):
    """Entre os rostos detectados no frame, escolhe o índice do que
    provavelmente é o motorista. A câmera fica montada de frente pro
    motorista a curta distância, então o rosto dele ocupa uma área maior do
    frame do que o de alguém mais afastado (banco de trás, carona) — usa a
    bounding box normalizada dos landmarks como proxy de proximidade.

    Heurística simples, não infalível (um passageiro bem próximo da câmera
    também ficaria grande), mas resolve o caso comum de pegar alguém atrás.
    Retorna o índice em vez dos landmarks pra dar pra buscar a matriz de
    transformação facial correspondente, que vem numa lista paralela.
    Retorna None se a lista estiver vazia."""
    if not face_landmarks_list:
        return None
    areas = [_face_bbox_area(lm) for lm in face_landmarks_list]
    return max(range(len(areas)), key=lambda i: areas[i])


def select_driver_face(face_landmarks_list):
    idx = select_driver_index(face_landmarks_list)
    return None if idx is None else face_landmarks_list[idx]


# ---------------------------------------------------------------------------
# Fontes de frame: câmera ao vivo (Picamera2) ou arquivo de vídeo gravado.
# Ambas expõem .read() -> (infer_frame RGB, display_frame RGB|None, iw, ih, t)
# ou None no fim do stream, e .close(). "t" é o tempo do stream em segundos:
# relógio de parede pra câmera, posição do vídeo pro arquivo — isso importa
# porque os limiares de duração (CLOSED_SECONDS_ALERT, PERCLOS) precisam do
# tempo "de conteúdo", não de quão rápido o Pi processa o arquivo.
# ---------------------------------------------------------------------------

def pick_max_mode(picam2):
    """Maior resolução que o sensor conectado oferece, sem concessão de fps
    — prioridade é o detalhe da imagem (gravação/prints da monografia), o
    fps que o hardware entregar nessa resolução é o que for. Não fixamos no
    hardware da OV5647 porque o plano do TCC prevê trocar pra uma câmera
    NoIR mais pra frente; a lógica de "maior resolução do sensor" vale pra
    qualquer sensor conectado."""
    modes = picam2.sensor_modes
    return max(modes, key=lambda m: m["size"][0] * m["size"][1])


def _build_camera_config(picam2):
    """Tenta a configuração ideal (main na resolução máxima do sensor +
    lores em baixa resolução pra IA) e vai degradando se o hardware/driver
    recusar, em vez de travar o programa por causa de uma combinação não
    suportada."""
    mode = pick_max_mode(picam2)
    main_size = mode["size"]
    # Trava no fps nativo do sensor nessa resolução (não um alvo artificial
    # menor) — sem isso o Picamera2 às vezes negocia uma taxa mais baixa que
    # o necessário. É o teto físico do sensor, não uma limitação nossa.
    native_fps = mode.get("fps") or TARGET_FPS
    frame_duration_us = int(1_000_000 / native_fps)
    controls = {"FrameDurationLimits": (frame_duration_us, frame_duration_us)}

    attempts = [
        lambda: picam2.create_video_configuration(
            main={"size": main_size, "format": "RGB888"},
            lores={"size": LORES_SIZE, "format": "RGB888"},
            controls=controls,
        ),
        lambda: picam2.create_video_configuration(
            main={"size": main_size, "format": "RGB888"},
            lores={"size": LORES_SIZE, "format": "YUV420"},
            controls=controls,
        ),
        lambda: picam2.create_preview_configuration(
            main={"size": LORES_SIZE, "format": "RGB888"}, controls=controls
        ),
    ]

    last_error = None
    for build in attempts:
        try:
            config = build()
            picam2.configure(config)
            has_lores = "lores" in config
            return has_lores, config["lores"]["format"] if has_lores else None
        except Exception as e:  # driver/ISP recusou a combinação de streams
            last_error = e
            continue
    raise RuntimeError(f"Câmera não aceitou nenhuma configuração testada: {last_error}")


class PicameraSource:
    def __init__(self, want_display=True):
        from picamera2 import Picamera2

        self.picam2 = Picamera2()
        try:
            self.has_lores, self.lores_format = _build_camera_config(self.picam2)
            self.picam2.start()
        except Exception:
            self.picam2.close()
            raise
        time.sleep(1)  # deixa o AE/AWB estabilizar
        self.want_display = want_display
        self.start_t = time.time()

    def read(self):
        import cv2

        try:
            if self.has_lores:
                infer = self.picam2.capture_array("lores")
                if self.lores_format == "YUV420":
                    infer = cv2.cvtColor(infer, cv2.COLOR_YUV2RGB_I420)
                disp = self.picam2.capture_array("main") if self.want_display else None
            else:
                infer = self.picam2.capture_array("main")
                disp = infer if self.want_display else None
            if infer is None or infer.size == 0:
                raise ValueError("frame vazio")
        except Exception as e:
            raise CaptureError(str(e)) from e

        ih, iw = infer.shape[:2]
        t = time.time() - self.start_t
        return infer, disp, iw, ih, t

    def close(self):
        self.picam2.stop()
        self.picam2.close()


class VideoFileSource:
    """Lê de um arquivo de vídeo em vez da câmera — pra testar de forma
    repetível os casos difíceis (óculos, escuro, rosto virado) sem precisar
    ficar na frente da câmera toda hora, e pra servir de entrada do
    benchmark."""

    def __init__(self, path, realtime=False):
        import cv2

        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise RuntimeError(f"Não consegui abrir o vídeo: {path}")
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.native_fps = fps if fps and fps > 0 else 30.0
        self.frame_idx = 0
        self.realtime = realtime
        self._t0_wall = None

    def read(self):
        import cv2

        ret, frame_bgr = self.cap.read()
        if not ret:
            return None

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        ih, iw = frame_rgb.shape[:2]
        if (iw, ih) != LORES_SIZE:
            infer = cv2.resize(frame_rgb, LORES_SIZE)
        else:
            infer = frame_rgb

        t = self.frame_idx / self.native_fps
        self.frame_idx += 1

        if self.realtime:
            if self._t0_wall is None:
                self._t0_wall = time.time()
            delay = (self._t0_wall + t) - time.time()
            if delay > 0:
                time.sleep(delay)

        return infer, frame_rgb, infer.shape[1], infer.shape[0], t

    def close(self):
        self.cap.release()


# ---------------------------------------------------------------------------
# Calibração por pessoa: mede o EAR de olho aberto no início da sessão e
# deriva os limiares a partir dele, em vez de usar números mágicos fixos que
# variam de rosto pra rosto (formato do olho, óculos).
# ---------------------------------------------------------------------------

def calibrate(source, landmarker, seconds=CALIBRATION_SECONDS, on_frame=None):
    """on_frame(display_frame, remaining_s) é chamado a cada frame, se dado
    (pra desenhar uma contagem regressiva na tela). Retorna
    (ear_baseline, mar_baseline, pitch_baseline, warning). ear_baseline é
    None se não conseguiu amostras suficientes OU se o valor calibrado caiu
    fora de uma faixa plausível (ver EAR_BASELINE_MIN/MAX) — quem chama deve
    cair pros limiares padrão nesse caso. mar_baseline/pitch_baseline podem
    ser None independentemente (cada um cai pro próprio padrão) mesmo com o
    EAR ok. warning é uma mensagem pra mostrar ao usuário quando algo foi
    descartado por implausibilidade, ou None se não houve problema."""
    import mediapipe as mp

    ear_samples = []
    mar_samples = []
    pitch_samples = []
    t0 = None
    ts = MonotonicMs()

    while True:
        try:
            frame = source.read()
        except CaptureError:
            break
        if frame is None:
            break

        infer_frame, display_frame, iw, ih, t = frame
        if t0 is None:
            t0 = t
        remaining = seconds - (t - t0)
        if remaining <= 0:
            break

        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=infer_frame)
        try:
            result = landmarker.detect_for_video(mp_image, ts.next(t))
        except Exception:
            result = None

        if result and result.face_landmarks:
            idx = select_driver_index(result.face_landmarks)
            if idx is not None:
                landmarks = result.face_landmarks[idx]
                if len(landmarks) > max(LEFT_EYE + RIGHT_EYE + MOUTH):
                    l = eye_aspect_ratio(landmarks, LEFT_EYE, iw, ih)
                    r = eye_aspect_ratio(landmarks, RIGHT_EYE, iw, ih)
                    if l is not None and r is not None:
                        ear_samples.append((l + r) / 2)
                    m = mouth_aspect_ratio(landmarks, MOUTH, iw, ih)
                    if m is not None:
                        mar_samples.append(m)
                    drop = head_drop_ratio(landmarks, iw, ih)
                    if drop is not None:
                        pitch_samples.append(drop)

        if on_frame:
            on_frame(display_frame, remaining)

    # EAR/MAR/pitch são validados de forma independente — uma calibração de
    # olho ruim (rosto mal detectado num trecho do vídeo, ângulo ruim etc.)
    # não pode derrubar a calibração de boca/cabeça se elas tiveram amostras
    # suficientes por conta própria. Cada sinal cai pro próprio padrão só se
    # o problema for dele mesmo.
    warning = None
    if len(ear_samples) >= MIN_CALIB_SAMPLES:
        ear_baseline = float(np.median(ear_samples))
        if not (EAR_BASELINE_MIN <= ear_baseline <= EAR_BASELINE_MAX):
            # calibração pegou lixo (rosto mal detectado, olho não realmente
            # aberto, ângulo ruim) — não confia nesse número silenciosamente
            warning = (
                f"EAR calibrado ({ear_baseline:.3f}) fora da faixa plausível "
                f"[{EAR_BASELINE_MIN:.2f}, {EAR_BASELINE_MAX:.2f}]; descartando e usando limiar padrão"
            )
            ear_baseline = None
    else:
        ear_baseline = None
        warning = "poucas amostras de olho detectadas na calibração"

    mar_baseline = float(np.median(mar_samples)) if len(mar_samples) >= MIN_CALIB_SAMPLES else None
    if mar_baseline is not None and mar_baseline > MAR_BASELINE_MAX:
        mar_baseline = None  # mesma lógica do EAR, sem gerar um segundo aviso

    pitch_baseline = float(np.median(pitch_samples)) if len(pitch_samples) >= MIN_CALIB_SAMPLES else None
    return ear_baseline, mar_baseline, pitch_baseline, warning


# ---------------------------------------------------------------------------
# Máquina de estados: aberto -> atencao (quase fechado) -> fechando -> fechado
# ---------------------------------------------------------------------------

class DrowsinessState:
    def __init__(
        self,
        ear_threshold=EAR_THRESHOLD_DEFAULT,
        ear_almost_closed=EAR_ALMOST_CLOSED_DEFAULT,
        mar_threshold=MAR_THRESHOLD_DEFAULT,
        head_pitch_threshold=None,
        closed_seconds_alert=CLOSED_SECONDS_ALERT,
        smoothing_window=SMOOTHING_WINDOW,
        mar_smoothing_window=MAR_SMOOTHING_WINDOW,
        no_face_grace=NO_FACE_GRACE_SECONDS,
    ):
        self.ear_threshold = ear_threshold
        self.ear_almost_closed = ear_almost_closed
        self.mar_threshold = mar_threshold
        self.head_pitch_threshold = head_pitch_threshold
        self.closed_seconds_alert = closed_seconds_alert
        self.no_face_grace = no_face_grace
        self.ear_smooth = deque(maxlen=smoothing_window)
        self.mar_smooth = deque(maxlen=mar_smoothing_window)
        self.closed_since = None
        self.last_face_seen = None
        self.long_blink_tracker = EventFrequencyTracker(LONG_BLINK_WINDOW_SECONDS)
        self.yawn_tracker = SustainedSignalTracker(MIN_YAWN_DURATION, YAWN_WINDOW_SECONDS, grace_seconds=YAWN_GRACE_SECONDS)
        self.head_down_tracker = SustainedSignalTracker(HEAD_DOWN_SECONDS_ALERT, HEAD_DOWN_WINDOW_SECONDS)
        self.dark_eyes_tracker = DarkEyesTracker(DARK_EYES_ENTER_SECONDS, DARK_EYES_EXIT_SECONDS)
        self.perclos_tracker = PerclosTracker()

    def update(self, left_ear, right_ear, mar, head_pitch, eyes_dark, face_detected, t):
        """t: tempo do stream em segundos. eyes_dark: True/False se foi
        possível medir o contraste da região do olho nesse frame, None se
        não (ex.: sem frame de imagem disponível). Retorna um dict com
        status/cor/nível/métricas pro chamador desenhar e logar."""
        if face_detected and left_ear is not None and right_ear is not None:
            self.last_face_seen = t
            self.ear_smooth.append((left_ear, right_ear))
            # mediana das últimas N leituras: ignora um frame ruim isolado
            # (reflexo de óculos, blink parcial captado no meio do gesto)
            l_med = float(np.median([v[0] for v in self.ear_smooth]))
            r_med = float(np.median([v[1] for v in self.ear_smooth]))

            both_closed = l_med < self.ear_threshold and r_med < self.ear_threshold
            both_almost = l_med < self.ear_almost_closed and r_med < self.ear_almost_closed
            closed_dur = None

            # Calculado antes do estado do olho de propósito: bocejar fecha
            # os olhos por reflexo (várias pessoas bocejam de olho fechado),
            # e isso sozinho não pode disparar o alarme cheio de sono — é
            # fadiga/cansaço, não microssono. Ver uso logo abaixo.
            # Suavizado igual o EAR: um frame de tracking ruim no meio do
            # bocejo (boca aberta grande mexe mais a geometria) não pode
            # resetar a contagem de duração sozinho.
            if mar is not None:
                self.mar_smooth.append(mar)
            mar_med = float(np.median(self.mar_smooth)) if self.mar_smooth else None
            is_yawning = mar_med is not None and mar_med > self.mar_threshold

            # Óculos escuros (ou qualquer oclusão) deixam o EAR não confiável
            # — se a região do olho está consistentemente escura/sem
            # contraste em relação ao rosto, para de confiar nele de vez e
            # passa a depender só de bocejo/cabeça (ver eye_region_darkness).
            glasses_mode = (
                self.dark_eyes_tracker.update(eyes_dark, t)
                if eyes_dark is not None else self.dark_eyes_tracker.active
            )

            # PERCLOS conta com o EAR bruto (both_closed), não com o glasses_mode
            # aplicado — mas em modo óculos escuros o EAR não é confiável, então
            # trata como "não fechado" pra não viciar a métrica com leitura ruim.
            self.perclos_tracker.update(t, both_closed and not glasses_mode)
            perclos_value = self.perclos_tracker.value()

            if glasses_mode:
                self.closed_since = None
                level = "aberto"
                status = "MODO OCULOS ESCUROS: EAR nao confiavel, monitorando boca/cabeca"
                color = (180, 180, 0)
            elif both_closed:
                if self.closed_since is None:
                    self.closed_since = t
                closed_dur = t - self.closed_since
                if is_yawning:
                    # olho fechado por causa do bocejo, não de sono — nunca
                    # vira "fechado" (alarme cheio), só sinaliza fadiga
                    level = "atencao"
                    status = f"ATENCAO: bocejando, olhos fechados ({closed_dur:.1f}s)"
                    color = (0, 255, 255)
                elif closed_dur >= self.closed_seconds_alert:
                    level = "fechado"
                    status = f"ALERTA: OLHOS FECHADOS ({closed_dur:.1f}s)"
                    color = (0, 0, 255)
                else:
                    level = "fechando"
                    status = f"olhos fechando... ({closed_dur:.1f}s)"
                    color = (0, 165, 255)
            else:
                # Piscada terminando: se o olho ficou fechado por mais que
                # uma piscada normal (mas sem chegar no alerta de 1.5s),
                # conta como "piscada longa" — sinal de pálpebra pesada /
                # microssono começando, mesmo já com o olho reaberto.
                # Ignora se foi bocejo (mesmo motivo do "fechado" acima).
                if self.closed_since is not None:
                    blink_dur = t - self.closed_since
                    if LONG_BLINK_SECONDS <= blink_dur < self.closed_seconds_alert and not is_yawning:
                        self.long_blink_tracker.record(t)
                self.closed_since = None
                if both_almost:
                    level = "atencao"
                    status = "ATENCAO: olhos semicerrados"
                    color = (0, 255, 255)
                else:
                    level = "aberto"
                    status = "alerta (olhos abertos)"
                    color = (0, 200, 0)

            blink_count_recent = self.long_blink_tracker.count(t)

            yawn_dur, yawn_count_recent = self.yawn_tracker.update(is_yawning, t)

            is_head_down = (
                head_pitch is not None
                and self.head_pitch_threshold is not None
                and head_pitch > self.head_pitch_threshold
            )
            head_down_dur, head_down_count_recent = self.head_down_tracker.update(is_head_down, t)

            atencao_by_blinks = False
            atencao_by_yawn = both_closed and is_yawning
            atencao_by_head = False
            atencao_by_perclos = False
            if level == "aberto":
                # sinais secundários só sobem o nível a partir de "aberto" —
                # olho já semicerrado/fechando continua tendo prioridade.
                # Ordem: sinal sustentado agora > frequência/janela recente.
                if yawn_dur is not None and yawn_dur >= YAWN_SECONDS_ALERT:
                    level = "atencao"
                    atencao_by_yawn = True
                    status = f"ATENCAO: bocejando ({yawn_dur:.1f}s)"
                    color = (0, 255, 255)
                elif head_down_dur is not None and head_down_dur >= HEAD_DOWN_SECONDS_ALERT:
                    level = "atencao"
                    atencao_by_head = True
                    status = f"ATENCAO: cabeca caindo ({head_down_dur:.1f}s)"
                    color = (0, 255, 255)
                elif perclos_value >= PERCLOS_ALERT_RATIO:
                    # nenhuma piscada isolada foi longa o bastante, mas o
                    # tempo total de olho fechado no último minuto já é alto
                    # — clássico da literatura (PERCLOS), pega sonolência
                    # espalhada em várias piscadas curtas
                    level = "atencao"
                    atencao_by_perclos = True
                    status = f"ATENCAO: PERCLOS alto ({perclos_value * 100:.0f}% em 60s)"
                    color = (0, 255, 255)
                elif blink_count_recent >= LONG_BLINK_FREQ_ALERT:
                    level = "atencao"
                    atencao_by_blinks = True
                    status = f"ATENCAO: piscadas longas frequentes ({blink_count_recent} em {LONG_BLINK_WINDOW_SECONDS // 60}min)"
                    color = (0, 255, 255)
                elif yawn_count_recent >= YAWN_FREQ_ALERT:
                    level = "atencao"
                    atencao_by_yawn = True
                    status = f"ATENCAO: bocejos frequentes ({yawn_count_recent} em {YAWN_WINDOW_SECONDS // 60}min)"
                    color = (0, 255, 255)
                elif head_down_count_recent >= HEAD_DOWN_FREQ_ALERT:
                    level = "atencao"
                    atencao_by_head = True
                    status = f"ATENCAO: cabeca caindo com frequencia ({head_down_count_recent} em {HEAD_DOWN_WINDOW_SECONDS // 60}min)"
                    color = (0, 255, 255)

            return {
                "status": status, "color": color, "level": level,
                "atencao_by_blinks": atencao_by_blinks,
                "atencao_by_yawn": atencao_by_yawn,
                "atencao_by_head": atencao_by_head,
                "atencao_by_perclos": atencao_by_perclos,
                "modo_oculos_escuros": glasses_mode,
                "ear_left": l_med, "ear_right": r_med,
                "ear_avg": (l_med + r_med) / 2.0,
                "mar": mar_med, "yawning": is_yawning, "yawn_count_recent": yawn_count_recent,
                "head_pitch": head_pitch, "head_down": is_head_down,
                "head_down_count_recent": head_down_count_recent,
                "both_closed": both_closed, "closed_dur": closed_dur,
                "blink_count_recent": blink_count_recent,
                "perclos": perclos_value,
                "face_detected": True,
            }

        if face_detected:
            # rosto ali, mas geometria do olho não confiável nesse frame
            # específico (ângulo ruim) — não conta como fechado nem reseta
            # o histórico de suavização, só não atualiza o estado agora.
            return {
                "status": "geometria do olho não confiável", "color": (128, 128, 128),
                "level": "geometria_invalida", "ear_left": None, "ear_right": None,
                "ear_avg": None, "both_closed": False, "closed_dur": None,
                "blink_count_recent": self.long_blink_tracker.count(t),
                "atencao_by_blinks": False, "atencao_by_yawn": False, "atencao_by_head": False,
                "atencao_by_perclos": False,
                "modo_oculos_escuros": self.dark_eyes_tracker.active,
                "mar": None, "yawning": False, "yawn_count_recent": self.yawn_tracker.freq.count(t),
                "head_pitch": None, "head_down": False,
                "head_down_count_recent": self.head_down_tracker.freq.count(t),
                "perclos": self.perclos_tracker.value(),
                "face_detected": True,
            }

        # Sem rosto: só reseta o estado depois do período de tolerância
        # (histerese), pra não perder o progresso do alerta por causa de
        # 1-2 frames perdidos de tracking — comum mesmo com rosto presente.
        if self.last_face_seen is not None and (t - self.last_face_seen) < self.no_face_grace:
            return {
                "status": "rastreando...", "color": (128, 128, 128),
                "level": "sem_rosto_tolerancia", "ear_left": None, "ear_right": None,
                "ear_avg": None, "both_closed": False, "closed_dur": None,
                "blink_count_recent": self.long_blink_tracker.count(t),
                "atencao_by_blinks": False, "atencao_by_yawn": False, "atencao_by_head": False,
                "atencao_by_perclos": False,
                "modo_oculos_escuros": self.dark_eyes_tracker.active,
                "mar": None, "yawning": False, "yawn_count_recent": self.yawn_tracker.freq.count(t),
                "head_pitch": None, "head_down": False,
                "head_down_count_recent": self.head_down_tracker.freq.count(t),
                "perclos": self.perclos_tracker.value(),
                "face_detected": False,
            }

        self.closed_since = None
        self.ear_smooth.clear()
        self.mar_smooth.clear()
        self.yawn_tracker.since = None
        self.head_down_tracker.since = None
        self.dark_eyes_tracker.dark_since = None
        self.dark_eyes_tracker.bright_since = None
        return {
            "status": "SEM ROSTO", "color": (128, 128, 128), "level": "sem_rosto",
            "ear_left": None, "ear_right": None, "ear_avg": None,
            "both_closed": False, "closed_dur": None,
            "blink_count_recent": self.long_blink_tracker.count(t),
            "atencao_by_blinks": False, "atencao_by_yawn": False, "atencao_by_head": False,
            "atencao_by_perclos": False,
            "modo_oculos_escuros": self.dark_eyes_tracker.active,
            "mar": None, "yawning": False, "yawn_count_recent": self.yawn_tracker.freq.count(t),
            "head_pitch": None, "head_down": False,
            "head_down_count_recent": self.head_down_tracker.freq.count(t),
            "perclos": self.perclos_tracker.value(),
            "face_detected": False,
        }


class EventFrequencyTracker:
    """Conta eventos discretos (ex.: piscadas longas, bocejos) numa janela
    deslizante — usado quando a frequência de um evento importa mais do que
    a duração de uma ocorrência isolada."""

    def __init__(self, window_seconds):
        self.window_seconds = window_seconds
        self.events = deque()  # timestamps dos eventos

    def record(self, t):
        self.events.append(t)
        self._trim(t)

    def count(self, t):
        self._trim(t)
        return len(self.events)

    def _trim(self, t):
        cutoff = t - self.window_seconds
        while self.events and self.events[0] < cutoff:
            self.events.popleft()


class SustainedSignalTracker:
    """Rastreia um sinal binário por frame (ex.: boca aberta, cabeça caída):
    mede a duração do episódio contínuo atual e conta, numa janela
    deslizante, quantos episódios "longos o bastante" (>= min_duration) já
    aconteceram — mesma lógica usada pro bocejo e pra queda de cabeça, só
    muda o sinal de entrada e os limiares de cada chamador.

    grace_seconds tolera uma lacuna curta com o sinal ausente sem encerrar o
    episódio — testado ao vivo com bocejo real: o tracking da boca bem
    aberta cai pra quase zero por instantes no meio do bocejo (não é o
    bocejo terminando, é glitch de landmark), e sem essa tolerância a
    contagem de duração nunca chegava no mínimo."""

    def __init__(self, min_duration, window_seconds, grace_seconds=0.0):
        self.min_duration = min_duration
        self.grace_seconds = grace_seconds
        self.since = None
        self.last_active_t = None
        self.freq = EventFrequencyTracker(window_seconds)

    def update(self, active, t):
        """Retorna (duracao_do_episodio_em_andamento_ou_None, contagem_recente)."""
        if active:
            if self.since is None:
                self.since = t
            self.last_active_t = t
            dur = t - self.since
        else:
            if self.since is not None:
                gap = t - self.last_active_t if self.last_active_t is not None else None
                if gap is not None and gap <= self.grace_seconds:
                    # lacuna curta (glitch de tracking): mantém o episódio
                    # "vivo" sem avançar a duração enquanto o sinal some
                    return self.last_active_t - self.since, self.freq.count(t)
                finished_dur = (self.last_active_t - self.since) if self.last_active_t is not None else 0.0
                if finished_dur >= self.min_duration:
                    self.freq.record(t)
                self.since = None
                self.last_active_t = None
            dur = None
        return dur, self.freq.count(t)


class PerclosTracker:
    """% do tempo com os dois olhos fechados numa janela deslizante de
    PERCLOS_WINDOW_SECONDS. Pega microssonos que não duram os
    CLOSED_SECONDS_ALERT contínuos do alerta mas se repetem com frequência."""

    def __init__(self, window_seconds=PERCLOS_WINDOW_SECONDS):
        self.window_seconds = window_seconds
        self.samples = deque()  # (t, closed_bool)

    def update(self, t, is_closed):
        self.samples.append((t, is_closed))
        cutoff = t - self.window_seconds
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

    def value(self):
        if len(self.samples) < 2:
            return 0.0
        pts = list(self.samples)
        total = pts[-1][0] - pts[0][0]
        if total <= 0:
            return 0.0
        closed_time = sum(
            pts[i + 1][0] - pts[i][0] for i in range(len(pts) - 1) if pts[i][1]
        )
        return closed_time / total


# ---------------------------------------------------------------------------
# Log em SQLite (pros gráficos/benchmarks da monografia). Bufferiza em
# memória e grava em lote — inserir a cada frame individualmente a 30fps
# custaria I/O de disco toda hora.
# ---------------------------------------------------------------------------

MAX_LOGGED_FRAMES = 500_000   # ~5-9h de log a 15-30fps — o banco é reaproveitado
                                # entre sessões (mesmo arquivo por padrão) e cresceria
                                # sem limite; ao passar disso, poda os frames mais
                                # antigos no início da sessão seguinte. t_s é relativo
                                # à sessão (não wall-clock), então poda por contagem de
                                # linha, não por data.


class SessionLogger:
    def __init__(self, db_path, flush_every=30, max_frames=MAX_LOGGED_FRAMES):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS frames (
                t_s REAL, ear_left REAL, ear_right REAL, ear_avg REAL,
                mar REAL, head_pitch REAL,
                perclos REAL, yawn_count REAL, head_down_count REAL,
                level TEXT, latency_ms REAL
            )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS eventos (
                t_s REAL, tipo TEXT, duracao_s REAL
            )"""
        )
        # migração leve: bancos criados antes do MAR/cabeça/sessão não têm
        # essas colunas — ALTER TABLE não quebra os dados já gravados.
        existing_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(frames)")}
        for col in ("mar", "head_pitch", "yawn_count", "head_down_count", "session"):
            if col not in existing_cols:
                coltype = "INTEGER" if col == "session" else "REAL"
                self.conn.execute(f"ALTER TABLE frames ADD COLUMN {col} {coltype}")
        existing_evento_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(eventos)")}
        if "session" not in existing_evento_cols:
            self.conn.execute("ALTER TABLE eventos ADD COLUMN session INTEGER")
        self.conn.commit()

        # t_s é relativo ao início de cada execução (reinicia do zero toda
        # vez), não wall-clock — sem um número de sessão explícito não dá
        # pra separar testes de dias diferentes no mesmo banco depois.
        last_session = self.conn.execute("SELECT COALESCE(MAX(session), 0) FROM frames").fetchone()[0]
        self.session_id = last_session + 1

        row_count = self.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
        if row_count > max_frames:
            self.conn.execute(
                "DELETE FROM frames WHERE rowid NOT IN "
                "(SELECT rowid FROM frames ORDER BY rowid DESC LIMIT ?)",
                (max_frames,),
            )
            self.conn.commit()
            self.conn.execute("VACUUM")

        self.flush_every = flush_every
        self._buffer = []

    def log_frame(self, t_s, ear_left, ear_right, ear_avg, mar, head_pitch,
                   perclos, yawn_count, head_down_count, level, latency_ms):
        self._buffer.append((t_s, ear_left, ear_right, ear_avg, mar, head_pitch,
                              perclos, yawn_count, head_down_count, level, latency_ms,
                              self.session_id))
        if len(self._buffer) >= self.flush_every:
            self.flush()

    def log_evento(self, t_s, tipo, duracao_s=None):
        self.conn.execute(
            "INSERT INTO eventos (t_s, tipo, duracao_s, session) VALUES (?,?,?,?)",
            (t_s, tipo, duracao_s, self.session_id),
        )
        self.conn.commit()

    def flush(self):
        if self._buffer:
            # colunas explícitas: bancos antigos (migrados via ALTER TABLE)
            # têm mar/head_pitch/yawn_count/head_down_count/session no fim
            # da tabela, não na ordem "natural" — INSERT posicional quebraria.
            self.conn.executemany(
                """INSERT INTO frames
                   (t_s, ear_left, ear_right, ear_avg, mar, head_pitch,
                    perclos, yawn_count, head_down_count, level, latency_ms, session)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                self._buffer,
            )
            self.conn.commit()
            self._buffer.clear()

    def close(self):
        self.flush()
        self.conn.close()
