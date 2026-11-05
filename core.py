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
TARGET_FPS = 30              # taxa da câmera ao vivo: a mesma dos vídeos do YawDD,
                              # em que os limiares e as janelas (em quadros) foram
                              # validados. Ver pick_camera_mode

EAR_THRESHOLD_DEFAULT = 0.21       # usado se não houver calibração por pessoa
EAR_ALMOST_CLOSED_DEFAULT = 0.26
CLOSED_RATIO = 0.70                # limiar "fechado" = baseline calibrado * essa fração
                                    # (0.80 -> 0.70 em 30/09: no YawDD, onde ninguém
                                    # dorme, 0.80 dava alarme cheio com olho só
                                    # apertado de quem fala/sorri ou olha pra baixo,
                                    # e 0.70 cortou esses alarmes de 44 pra 18. Nas
                                    # sessões ao vivo do sonolencia.db o olho fechado
                                    # de verdade ficou entre 15% e 70% do baseline, e
                                    # os 21 episódios continuam abaixo de 0.70; 0.65
                                    # perdia 2 deles. Histórico anterior:
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

MAR_THRESHOLD_DEFAULT = 0.55  # pico que a boca tem que atingir pra ser bocejo (piso
                               # absoluto; ver MAR_YAWN_RATIO). Era 0.6 como limiar
                               # único. Ajustado no YawDD (349 vídeos): fala chega a
                               # ~0.29 (percentil 99, mediana dos vídeos), bocejo
                               # passa de 0.7, mas várias pessoas (sobretudo
                               # mulheres, boca menor na imagem) bocejam com pico
                               # entre 0.55 e 0.65
MAR_YAWN_RATIO = 2.2          # limiar de pico = MAR de boca fechada (calibrado) * essa razão
                               # (bocejo abre a boca bem mais que falar — valor alto
                               # de propósito pra não confundir com conversa)
MAR_SUSTAIN_RATIO = 0.73      # histerese: o episódio de boca aberta dura enquanto o
                               # MAR ficar acima de pico * essa fração (0.55 -> 0.40),
                               # e só vira bocejo se o pico for atingido em algum
                               # momento. Com limiar único, o bocejo de pico baixo
                               # passava só uns décimos de segundo acima de 0.6 e
                               # nunca completava a duração mínima (a causa de 35
                               # dos 62 bocejos perdidos no YawDD)
# Segundo caminho pro bocejo, pela mandíbula (blendshape jawOpen do MediaPipe,
# 0 a 1): ela desce mesmo quando os lábios ficam juntos ou atrás da mão, casos
# em que o MAR mal sobe. Fala também abre a mandíbula, mas por pouco tempo,
# então esse caminho exige uma duração bem maior que o dos lábios.
# Ajustado em 30/09 nos 20 bocejos que o MAR perdia + 30 vídeos de fala do
# YawDD: recupera 4 dos 20 sem nenhum bocejo falso novo na fala (pico 0.5
# recuperava 5, mas criava 1). A fala chegou a 0.83 de pico e a 2.4 s acima de
# 0.4; os bocejos recuperados ficam de 2.3 a 3.8 s acima de 0.4.
JAW_OPEN_THRESHOLD = 0.60     # pico que a mandíbula tem que atingir
JAW_SUSTAIN = 0.40            # o episódio dura enquanto jawOpen ficar acima disso
JAW_MIN_YAWN_DURATION = 2.0   # duração mínima do episódio pela mandíbula (lábios: 0.8 s)
YAWN_MERGE_SECONDS = 5.0      # os dois caminhos costumam ver o mesmo bocejo: dois
                               # registros com menos que isso de intervalo são um só
MIN_YAWN_DURATION = 0.8       # boca aberta (acima do limiar de sustentação) menos
                               # que isso é fala, não bocejo. Era 1.2s com limiar
                               # único; com a histerese a duração medida fica mais
                               # longa (conta a subida e a descida da boca). No
                               # YawDD, com validação cruzada por participante,
                               # fala virou bocejo em 1 de 100 vídeos
YAWN_SECONDS_ALERT = 1.5      # boca aberta contínua acima disso já eleva o nível
                               # agora, sem esperar terminar o bocejo
YAWN_GRACE_SECONDS = 0.5      # tolera até essa lacuna sem o sinal de bocejo antes
                               # de encerrar o episódio (glitch de tracking na boca
                               # bem aberta, ver SustainedSignalTracker)
YAWN_EYE_GRACE_SECONDS = 2.0  # depois que a boca fecha, o olho ainda fica fechado ou
                               # lacrimejando por um tempo (visto no YawDD: 11 alarmes
                               # cheios falsos logo após bocejos, com a boca já
                               # voltando). Nesse intervalo o olho fechado continua
                               # tratado como bocejo, e a contagem do olho fechado só
                               # começa quando ele termina
TALKING_WINDOW_SECONDS = 3.0  # janela pra decidir se a pessoa está falando agora
TALKING_MAR = 0.12            # boca "abrindo" numa sílaba: MAR cruzando isso pra cima
                               # (boca fechada fica perto de 0.01-0.05; fala oscila
                               # até ~0.3, percentil 99 dos vídeos de fala do YawDD)
TALKING_MIN_OPENINGS = 2      # nº de aberturas na janela pra contar como fala (2 cortou os
                               # alarmes falsos em vídeos de fala do YawDD de 7 pra 2;
                               # 3 só pra 4. Nenhum dos 22 alarmes reais do
                               # sonolencia.db teria fala detectada, com 2 ou 3)
CLOSED_SECONDS_ALERT_TALKING = 2.5  # quem está falando ativamente não está cochilando:
                               # olho fechado numa risada não dispara o alarme cheio
                               # com 1.5 s, espera até aqui. Calado continua 1.5 s
SMILE_DELTA = 0.25            # sorriso/riso: mouthSmile (média esq/dir) acima do neutro
                               # da calibração. Sorrir é músculo contraído, o oposto do
                               # rosto que relaxa no cochilo. Nos 43 vídeos-problema do
                               # YawDD: piscada normal +0.00 (mediana), alarmes cheios
                               # falsos +0.20, 5 dos 9 sorrindo. Entre 0.15 e 0.30 o
                               # resultado é o mesmo (6 alarmes), 0.25 fica no meio
SMILE_GRACE_SECONDS = 1.0     # o sorriso vale por mais esse tempo depois de sumir: no
                               # riso o sorriso oscila enquanto o olho segue fechado
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
HEAD_DOWN_GRACE_SECONDS = 0.5   # tolera oscilação curta sem encerrar a queda: a regra
                                  # exige cabeça baixa E olho fechando ao mesmo tempo, e
                                  # com a cabeça baixa o olho é visto de cima e o EAR
                                  # oscila; sem tolerância, um frame fora do limite
                                  # recomeçava o episódio e ele nunca chegava a 1 s
                                  # (autoteste de 30/09: pitch foi a 0.82 e não contou)
HEAD_DROP_FACE_LOSS_SECONDS = 1.0  # rosto que some até esse tempo depois de um frame de
                                    # cabeça baixa com olho fechando = a cabeça caiu pra
                                    # fora do quadro: conta como queda mesmo sem ter
                                    # durado HEAD_DOWN_SECONDS_ALERT visível (autoteste de
                                    # 30/09: rosto sumiu 4,7 s, queda visível só 0,3 s)
HEAD_DOWN_SECONDS_ALERT = 1.0   # duração contínua de cabeça caída pra contar
                                  # o episódio e/ou elevar o nível agora
HEAD_DOWN_WINDOW_SECONDS = 120  # janela deslizante p/ frequência de quedas de cabeça
HEAD_DOWN_REQUIRES_EYES = True  # queda de cabeça só conta com o olho semicerrado ou
                                 # fechado junto
HEAD_DOWN_FREQ_ALERT = 2        # nº de quedas de cabeça na janela que já eleva o nível

SMOOTHING_WINDOW = 3          # mediana de N frames p/ ignorar ruído de 1 frame só
                               # (reflexo de óculos, blink parcial, glitch de tracking)
MAR_SMOOTHING_WINDOW = 9      # boca bem aberta (bocejo) é mais instável pro tracking
                               # dos pontos internos do lábio do que o olho — testado
                               # ao vivo, MAR oscilou de 0.003 a 0.82 na mesma janela
                               # de 8s durante um bocejo real mesmo já com janela de 5;
                               # subiu pra 9 (~0.6s a 15fps, ~0.3s a 30fps como no YawDD) pra aguentar uma lacuna de
                               # tracking maior sem perder o bocejo
NO_FACE_GRACE_SECONDS = 0.5   # tolerância antes de considerar "sem rosto" de vez
                               # (tracking falha por 1-2 frames com frequência, mesmo
                               # com rosto presente; sem isso perde o progresso do
                               # alerta à toa)
DISTRACTION_SECONDS = 3.0     # rosto sumido por mais que isso (motorista olhando o
                               # celular, virado pro banco de trás) gera aviso de
                               # distração. A 80 km/h, 3 s são ~67 m sem olhar a via
DISTRACTION_MAX_SECONDS = 60.0  # depois disso sem rosto, assume que o motorista saiu
                                 # do banco (carro parado) e para de avisar
MAX_CONSECUTIVE_ERRORS = 30   # frames ruins seguidos antes de desistir da câmera

PERCLOS_WINDOW_SECONDS = 60   # janela deslizante da métrica PERCLOS
PERCLOS_CLOSED_RATIO = 0.50   # pro PERCLOS, "fechado" = EAR abaixo dessa fração do
                               # baseline. A literatura usa P80 (pálpebra cobrindo
                               # 80% ou mais do olho, Dinges & Grace 1998); o limiar
                               # do alarme (CLOSED_RATIO) é bem mais frouxo porque
                               # precisa pegar o microssono cedo, mas no PERCLOS ele
                               # somava olho só apertado (câmera no painel, olhando
                               # pra baixo) e dava aviso falso
PERCLOS_ALERT_RATIO = 0.15    # PERCLOS >= 15% em 60s já eleva o nível — pega
                               # sonolência que se manifesta em várias piscadas
                               # curtas ao longo do tempo, não uma fechada isolada
PERCLOS_MIN_COVERAGE_SECONDS = 30   # só alerta por PERCLOS com pelo menos isso de
                                     # histórico na janela. Sem esse mínimo, logo
                                     # depois da calibração uma piscada normal de
                                     # 0,3 s em 2 s de dados já dava "15%" e o aviso
                                     # de atenção tocava à toa (visto em quase todo
                                     # vídeo "normal" do YawDD)

CALIBRATION_SECONDS = 5       # duração da calibração de baseline por pessoa
MIN_CALIB_SAMPLES = 20        # amostras mínimas pra confiar na calibração
CALIBRATION_MAX_SECONDS = 10  # se a pessoa bocejar ou falar na calibração, os frames
                               # de boca aberta são descartados e a calibração se
                               # estende sozinha até juntar CALIBRATION_SECONDS de
                               # rosto em repouso, no máximo até aqui

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


def _ponto3d(lm, w, h):
    # o MediaPipe dá a profundidade z na mesma escala do x (fração da largura)
    return np.array((lm.x * w, lm.y * h, getattr(lm, "z", 0.0) * w))


def aspect_ratio_3d(landmarks, idx, w, h):
    """Mesma razão de 6 pontos do EAR/MAR, mas com distâncias em 3D. Em 2D,
    com o rosto visto de lado (câmera fora do eixo ou cabeça virada), a
    largura do olho/boca encolhe na imagem pelo cosseno do ângulo e a razão
    sobe: a 45° o MAR de quem só fala sobe ~40%. Com a profundidade do
    MediaPipe a largura volta ao tamanho real. EM ESTUDO: gravada nas séries
    do YawDD, ainda fora da decisão."""
    p1, p2, p3, p4, p5, p6 = (_ponto3d(landmarks[i], w, h) for i in idx)
    horizontal = np.linalg.norm(p1 - p4)
    if horizontal < 1e-3:
        return None
    return float((np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)) / (2.0 * horizontal))


def head_yaw_deg(landmarks, w, h):
    """Quanto o rosto está virado de lado, em graus (0 = de frente pra
    câmera; o sinal diz o lado). Ângulo da linha entre os cantos externos
    dos olhos no plano horizontal (x, z): de frente ela é paralela à imagem,
    virando ela ganha profundidade. EM ESTUDO."""
    a = _ponto3d(landmarks[EYE_CORNERS_OUTER[0]], w, h)
    b = _ponto3d(landmarks[EYE_CORNERS_OUTER[1]], w, h)
    dx, dz = b[0] - a[0], b[2] - a[2]
    if abs(dx) < 1e-3 and abs(dz) < 1e-3:
        return None
    return float(np.degrees(np.arctan2(dz, dx)))


INNER_LIPS = (13, 14)        # meio do lábio de cima / de baixo, por dentro
OUTER_LIPS = (0, 17)         # idem, por fora (pega a boca aberta atrás da mão?)
EYE_CORNERS_OUTER = (33, 263)  # cantos externos dos olhos: régua do tamanho do rosto


def mouth_opening_ratio(landmarks, w, h, lips=INNER_LIPS):
    """Abertura vertical da boca dividida pela distância entre os cantos dos
    olhos. Alternativa ao MAR em estudo (avaliar_yawdd.py grava as duas): o
    MAR divide pela largura da boca, que muda com o formato do bocejo e do
    rosto; a distância entre os olhos quase não muda com a expressão."""
    a, b = (np.array((landmarks[i].x * w, landmarks[i].y * h)) for i in lips)
    o1, o2 = (np.array((landmarks[i].x * w, landmarks[i].y * h)) for i in EYE_CORNERS_OUTER)
    regua = np.linalg.norm(o1 - o2)
    if regua < 1e-3:
        return None
    return float(np.linalg.norm(a - b) / regua)


NOSE_TIP, CHIN = 1, 152
BLENDSHAPES_USADOS = ("jawOpen", "mouthClose", "eyeBlinkLeft", "eyeBlinkRight",
                      # olhar pra baixo: estudado e descartado (a pálpebra fechando
                      # já sobe o eyeLookDown numa piscada normal, +0.32 contra +0.15
                      # nos alarmes falsos). cheekSquint: o modelo devolve sempre ~0.
                      # Ficam gravados nas séries do YawDD pra referência
                      "eyeLookDownLeft", "eyeLookDownRight", "eyeLookUpLeft", "eyeLookUpRight",
                      "eyeSquintLeft", "eyeSquintRight",
                      # sorriso: usado (SMILE_DELTA)
                      "mouthSmileLeft", "mouthSmileRight",
                      "cheekSquintLeft", "cheekSquintRight")


def sorriso(blendshapes):
    """Sorriso de 0 a 1 (média de mouthSmileLeft/Right), ou None."""
    l, r = blendshapes.get("mouthSmileLeft"), blendshapes.get("mouthSmileRight")
    return (l + r) / 2 if l is not None and r is not None else None


def jaw_drop_ratio(landmarks, w, h):
    """Distância ponta do nariz -> queixo dividida pela distância entre os
    cantos dos olhos. No bocejo a mandíbula desce mesmo com a boca fechada
    ou coberta pela mão, e essa distância cresce."""
    pts = {i: np.array((landmarks[i].x * w, landmarks[i].y * h)) for i in (NOSE_TIP, CHIN) + EYE_CORNERS_OUTER}
    regua = np.linalg.norm(pts[EYE_CORNERS_OUTER[0]] - pts[EYE_CORNERS_OUTER[1]])
    if regua < 1e-3:
        return None
    return float(np.linalg.norm(pts[NOSE_TIP] - pts[CHIN]) / regua)


def blendshapes_do_rosto(result, idx):
    """{nome: score} dos BLENDSHAPES_USADOS do rosto idx, ou {} se o modelo
    não devolveu blendshapes."""
    todas = getattr(result, "face_blendshapes", None)
    if not todas or idx is None or idx >= len(todas):
        return {}
    return {c.category_name: float(c.score) for c in todas[idx] if c.category_name in BLENDSHAPES_USADOS}


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
        # coeficientes de expressão (52, padrão ARKit) que o mesmo modelo já
        # traz; jawOpen mede a mandíbula mesmo quando os lábios ficam juntos
        # (bocejo de boca fechada) ou somem atrás da mão
        output_face_blendshapes=True,
    )
    return mp_vision.FaceLandmarker.create_from_options(options)


LOW_LIGHT_MEAN = 70   # brilho médio (0-255) abaixo do qual o frame é realçado
                       # antes de ir pro MediaPipe (noite, túnel, garagem)


def realcar_pouca_luz(frame_rgb):
    """Em frame escuro, equaliza o contraste local (CLAHE) só no canal de
    luminância, pra o detector de rosto achar o rosto que ficou perdido no
    escuro. Frame claro volta sem mudança (e sem custo além de medir o
    brilho). Serve só pra entrada do MediaPipe."""
    import cv2

    if cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY).mean() >= LOW_LIGHT_MEAN:
        return frame_rgb
    lab = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


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
    # Distância entre os olhos em 3D (o MediaPipe dá a profundidade z na
    # mesma escala do x). A versão 2D usava só |dx|, que encolhe quando a
    # câmera fica de lado (rosto visto em ângulo) ou a cabeça inclina pro
    # ombro: o índice inflava e a queda disparava com menos movimento. De
    # frente as duas dão o mesmo valor.
    centro = lambda idx: np.array([
        sum(landmarks[i].x for i in idx) / len(idx) * w,
        sum(landmarks[i].y for i in idx) / len(idx) * h,
        sum(getattr(landmarks[i], "z", 0.0) for i in idx) / len(idx) * w,
    ])
    interocular = float(np.linalg.norm(centro(LEFT_EYE) - centro(RIGHT_EYE)))
    if interocular < 1e-3:
        return None
    nose_y = landmarks[NOSE_TIP].y * h
    return (nose_y - eye_line_y) / interocular


def _face_bbox_area(landmarks):
    xs = [lm.x for lm in landmarks]
    ys = [lm.y for lm in landmarks]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


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

def _campo_inteiro(mode, sensor_size):
    """O modo enxerga (quase) o sensor inteiro, sem cortar as bordas? Modos
    que recortam o centro (ex.: 1920x1080 da OV5647) estreitam o campo de
    visão e podem deixar parte do rosto de fora com a câmera de lado."""
    _x, _y, w, h = mode.get("crop_limits", (0, 0, *sensor_size))
    return w >= 0.95 * sensor_size[0] and h >= 0.95 * sensor_size[1]


def pick_camera_mode(picam2):
    """Maior resolução que entrega TARGET_FPS (30) com o campo de visão
    inteiro; sem nenhum assim, o modo mais rápido de campo inteiro.

    Era a maior resolução do sensor sem olhar o fps: na OV5647 isso dava
    2592x1944 a só 15,6 fps, metade da taxa dos vídeos do YawDD, em que os
    limiares foram validados (as janelas de suavização contam quadros: 9
    quadros eram 0,58 s ao vivo contra 0,3 s na avaliação). Na OV5647 a
    escolha agora é 1296x972 (binning 2x2 do sensor inteiro, até 46 fps)
    travado em 30. Vale pra qualquer sensor (o plano prevê trocar pra uma
    câmera NoIR)."""
    modes = picam2.sensor_modes
    sensor_size = picam2.sensor_resolution
    inteiros = [m for m in modes if _campo_inteiro(m, sensor_size)] or modes
    rapidos = [m for m in inteiros if (m.get("fps") or 0) >= TARGET_FPS]
    if rapidos:
        return max(rapidos, key=lambda m: m["size"][0] * m["size"][1])
    return max(inteiros, key=lambda m: m.get("fps") or 0)


def _build_camera_config(picam2):
    """Tenta a configuração ideal (main no modo escolhido do sensor + lores
    em baixa resolução pra IA) e vai degradando se o hardware/driver
    recusar, em vez de travar o programa por causa de uma combinação não
    suportada."""
    mode = pick_camera_mode(picam2)
    main_size = mode["size"]
    # Trava em TARGET_FPS (ou no máximo do modo, se ele não chegar lá): sem
    # isso o Picamera2 às vezes negocia uma taxa mais baixa que o necessário
    fps = min(TARGET_FPS, mode.get("fps") or TARGET_FPS)
    frame_duration_us = int(1_000_000 / fps)
    controls = {"FrameDurationLimits": (frame_duration_us, frame_duration_us)}
    # pede o modo do sensor explicitamente: só pelo tamanho da saída o
    # Picamera2 poderia escolher um modo recortado
    sensor = {"output_size": mode["size"], "bit_depth": mode.get("bit_depth", 10)}
    # No Picamera2 o nome do formato segue a convenção DRM (little-endian):
    # "RGB888" chega no numpy na ordem [B, G, R] e "BGR888" na ordem
    # [R, G, B]. O resto do programa trata o quadro como RGB (é o que o
    # MediaPipe espera), então o certo é BGR888. Com RGB888 o MediaPipe
    # recebia vermelho e azul trocados: medido no YawDD, o jawOpen passa de
    # 0,60 em bem menos quadros de bocejo (30 -> 6 num vídeo) e o EAR varia
    # até 13% (efeito_cor, 30/10/2026)
    PIXEL_FORMAT = "BGR888"

    attempts = [
        lambda: picam2.create_video_configuration(
            main={"size": main_size, "format": PIXEL_FORMAT},
            lores={"size": LORES_SIZE, "format": PIXEL_FORMAT},
            sensor=sensor, controls=controls,
        ),
        lambda: picam2.create_video_configuration(
            main={"size": main_size, "format": PIXEL_FORMAT},
            lores={"size": LORES_SIZE, "format": "YUV420"},
            sensor=sensor, controls=controls,
        ),
        lambda: picam2.create_video_configuration(
            main={"size": main_size, "format": PIXEL_FORMAT},
            lores={"size": LORES_SIZE, "format": "YUV420"},
            controls=controls,
        ),
        lambda: picam2.create_preview_configuration(
            main={"size": LORES_SIZE, "format": PIXEL_FORMAT}, controls=controls
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
                # os dois fluxos do MESMO quadro: duas capture_array seguidas
                # esperavam um quadro novo cada uma e jogavam metade fora
                # (30 fps do sensor viravam ~14 no programa)
                if self.want_display:
                    (infer, disp), _meta = self.picam2.capture_arrays(["lores", "main"])
                else:
                    infer, disp = self.picam2.capture_array("lores"), None
                if self.lores_format == "YUV420":
                    infer = cv2.cvtColor(infer, cv2.COLOR_YUV2RGB_I420)
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


def tamanho_inferencia(iw, ih):
    """Tamanho do quadro entregue ao MediaPipe a partir de um vídeo iw x ih.
    Vídeo 4:3 (câmera ao vivo, YawDD) vai para LORES_SIZE como sempre foi.
    Outra proporção (celular deitado 16:9 ou em pé 9:16, caso da UTA-RLDD)
    é reduzida SEM esticar, para a mesma área de 640x480: esticar para
    640x480 distorce o rosto. Medido em 04/11/2026 com um vídeo do YawDD
    emoldurado em 16:9 e em 9:16: esticado, o EAR mediano ia de 0,228 para
    0,316 (+39%) e 0,107 (-53%), e o rosto sumia em 18% dos quadros do 9:16;
    sem esticar, 0,244 e 0,233."""
    lw, lh = LORES_SIZE
    if iw * lh == ih * lw:
        return LORES_SIZE
    escala = min(1.0, (lw * lh / (iw * ih)) ** 0.5)
    return max(1, round(iw * escala)), max(1, round(ih * escala))


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
        self.tamanho_original = None   # (largura, altura) do 1º quadro, já girado

    def read(self):
        import cv2

        ret, frame_bgr = self.cap.read()
        if not ret:
            return None

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        ih, iw = frame_rgb.shape[:2]
        if self.tamanho_original is None:
            self.tamanho_original = (iw, ih)
        alvo = tamanho_inferencia(iw, ih)
        if (iw, ih) != alvo:
            infer = cv2.resize(frame_rgb, alvo)
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
    (ear_baseline, mar_baseline, pitch_baseline, warning, bocejos,
    sorriso_baseline), ver calibrar_quadros() e sorriso_neutro(). ear_baseline é
    None se não conseguiu amostras suficientes OU se o valor calibrado caiu
    fora de uma faixa plausível (ver EAR_BASELINE_MIN/MAX) — quem chama deve
    cair pros limiares padrão nesse caso. mar_baseline/pitch_baseline podem
    ser None independentemente (cada um cai pro próprio padrão) mesmo com o
    EAR ok. warning é uma mensagem pra mostrar ao usuário quando algo foi
    descartado por implausibilidade, ou None se não houve problema."""
    import mediapipe as mp

    quadros = []   # (t, ear, mar, pitch, jaw, sorriso) por frame, None onde não deu pra medir
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
        limpo = tempo_em_repouso(quadros)
        remaining = max(seconds - limpo, 0.0)
        if (limpo >= seconds and t - t0 >= seconds) or t - t0 >= CALIBRATION_MAX_SECONDS:
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
                    ear = (l + r) / 2 if l is not None and r is not None else None
                    bs = blendshapes_do_rosto(result, idx)
                    quadros.append((t, ear, mouth_aspect_ratio(landmarks, MOUTH, iw, ih),
                                    head_drop_ratio(landmarks, iw, ih), bs.get("jawOpen"), sorriso(bs)))

        if on_frame:
            on_frame(display_frame, remaining)

    return (*calibrar_quadros(quadros), sorriso_neutro(quadros))


def _boca_aberta(mar, jaw=None):
    """Frame que não serve de referência: lábios ou mandíbula abertos."""
    return (mar is not None and mar > MAR_THRESHOLD_DEFAULT * MAR_SUSTAIN_RATIO) or (
        jaw is not None and jaw > JAW_SUSTAIN)


def tempo_em_repouso(quadros):
    """Segundos de calibração com a boca fechada (os frames que valem pra
    calibrar), somando o intervalo até cada frame em repouso."""
    total = 0.0
    for (t_ant, *_), (t, _e, m, _p, j, *_) in zip(quadros, quadros[1:]):
        if not _boca_aberta(m, j):
            total += t - t_ant
    return total


def bocejos_na_calibracao(quadros):
    """Instantes em que terminou um bocejo durante a calibração, com os
    mesmos trackers do monitoramento (lábios e mandíbula, mediana de
    MAR_SMOOTHING_WINDOW frames: sem ela, as sílabas de quem fala durante a
    calibração emendavam num "bocejo", visto no YawDD). Usa os limiares
    padrão porque a referência da pessoa ainda não existe."""
    eventos = EventFrequencyTracker(YAWN_WINDOW_SECONDS, min_interval=YAWN_MERGE_SECONDS)
    labios = SustainedSignalTracker(MIN_YAWN_DURATION, YAWN_WINDOW_SECONDS, YAWN_GRACE_SECONDS,
                                    peak_threshold=MAR_THRESHOLD_DEFAULT, freq=eventos)
    mandibula = SustainedSignalTracker(JAW_MIN_YAWN_DURATION, YAWN_WINDOW_SECONDS, YAWN_GRACE_SECONDS,
                                       peak_threshold=JAW_OPEN_THRESHOLD, freq=eventos)
    jan_mar = deque(maxlen=MAR_SMOOTHING_WINDOW)
    jan_jaw = deque(maxlen=MAR_SMOOTHING_WINDOW)
    t = None
    for t, _e, m, _p, j, *_ in quadros:
        if m is not None:
            jan_mar.append(m)
        if j is not None:
            jan_jaw.append(j)
        mar = float(np.median(jan_mar)) if jan_mar else None
        jaw = float(np.median(jan_jaw)) if jan_jaw else None
        labios.update(mar is not None and mar > MAR_THRESHOLD_DEFAULT * MAR_SUSTAIN_RATIO, t, value=mar)
        mandibula.update(jaw is not None and jaw > JAW_SUSTAIN, t, value=jaw)
    if t is not None:
        # bocejo ainda em andamento quando a calibração acabou: fecha o episódio
        fim = t + YAWN_GRACE_SECONDS + 1e-3
        labios.update(False, fim)
        mandibula.update(False, fim)
    return list(eventos.events)


def calibrar_quadros(quadros):
    """Calibra só com os frames de boca fechada. Bocejar fecha o olho e mexe
    a cabeça, então esses frames puxariam pra baixo o EAR e pra cima o
    MAR/pitch de referência (no YawDD, 21 dos 58 vídeos de bocejo de
    mulheres tinham o bocejo dentro dos 5 s de calibração). O bocejo em si
    não é descartado: quem boceja ao ligar o sistema já está cansado, então
    ele volta em bocejos (instantes de fim) pra entrar na contagem.
    quadros: (t, ear, mar, pitch, jaw[, sorriso]) por frame. Retorna o mesmo
    que calibrar_amostras() mais bocejos."""
    fechados = [q for q in quadros if not _boca_aberta(q[2], q[4])]
    ear_b, mar_b, pitch_b, warning = calibrar_amostras(
        [q[1] for q in fechados if q[1] is not None],
        [q[2] for q in fechados if q[2] is not None],
        [q[3] for q in fechados if q[3] is not None],
    )
    abertos = len(quadros) - len(fechados)
    if abertos:
        aviso = (f"boca aberta na maior parte da calibração ({abertos} de {len(quadros)} frames)"
                 if 2 * abertos >= len(quadros)
                 else f"{abertos} frames de boca aberta descartados da calibração (bocejo ou fala)")
        warning = f"{warning}; {aviso}" if warning else aviso
    return ear_b, mar_b, pitch_b, warning, bocejos_na_calibracao(quadros)


def sorriso_neutro(quadros):
    """Sorriso da boca em repouso (mediana nos frames de boca fechada da
    calibração), ou None sem amostras. Tem gente com o canto da boca
    naturalmente pra cima: sem essa referência o rosto sério já pareceria
    sorriso. Fica só na memória, como o EAR."""
    amostras = [q[5] for q in quadros
                if len(q) > 5 and q[5] is not None and not _boca_aberta(q[2], q[4])]
    return float(np.median(amostras)) if len(amostras) >= MIN_CALIB_SAMPLES else None


def calibrar_amostras(ear_samples, mar_samples, pitch_samples):
    """Parte pura de calibrate(): transforma as amostras coletadas nos
    baselines. Separada pra avaliação offline (avaliar_yawdd.py) refazer a
    calibração a partir de medidas salvas, sem rodar o MediaPipe de novo."""
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
        perclos_ear_threshold=None,
        mar_threshold=MAR_THRESHOLD_DEFAULT,
        mar_sustain_threshold=None,
        head_pitch_threshold=None,
        closed_seconds_alert=CLOSED_SECONDS_ALERT,
        smoothing_window=SMOOTHING_WINDOW,
        mar_smoothing_window=MAR_SMOOTHING_WINDOW,
        no_face_grace=NO_FACE_GRACE_SECONDS,
        smile_baseline=None,
    ):
        self.ear_threshold = ear_threshold
        self.ear_almost_closed = ear_almost_closed
        self.perclos_ear_threshold = (
            perclos_ear_threshold if perclos_ear_threshold is not None
            else ear_threshold * PERCLOS_CLOSED_RATIO / CLOSED_RATIO
        )
        self.mar_threshold = mar_threshold
        self.mar_sustain_threshold = (
            mar_sustain_threshold if mar_sustain_threshold is not None
            else mar_threshold * MAR_SUSTAIN_RATIO
        )
        self.head_pitch_threshold = head_pitch_threshold
        self.closed_seconds_alert = closed_seconds_alert
        self.no_face_grace = no_face_grace
        self.ear_smooth = deque(maxlen=smoothing_window)
        self.mar_smooth = deque(maxlen=mar_smoothing_window)
        self.jaw_smooth = deque(maxlen=mar_smoothing_window)
        self.closed_since = None
        self.last_face_seen = None
        self.last_yawn_t = None
        self.last_head_down_t = None   # último frame com cabeça baixa E olho fechando
        self.long_blink_tracker = EventFrequencyTracker(LONG_BLINK_WINDOW_SECONDS)
        # bocejo por dois caminhos (lábios e mandíbula), mesma contagem
        self.yawn_events = EventFrequencyTracker(YAWN_WINDOW_SECONDS, min_interval=YAWN_MERGE_SECONDS)
        self.yawn_tracker = SustainedSignalTracker(
            MIN_YAWN_DURATION, YAWN_WINDOW_SECONDS, grace_seconds=YAWN_GRACE_SECONDS,
            peak_threshold=mar_threshold, freq=self.yawn_events,
        )
        self.jaw_yawn_tracker = SustainedSignalTracker(
            JAW_MIN_YAWN_DURATION, YAWN_WINDOW_SECONDS, grace_seconds=YAWN_GRACE_SECONDS,
            peak_threshold=JAW_OPEN_THRESHOLD, freq=self.yawn_events,
        )
        self.head_down_tracker = SustainedSignalTracker(
            HEAD_DOWN_SECONDS_ALERT, HEAD_DOWN_WINDOW_SECONDS, grace_seconds=HEAD_DOWN_GRACE_SECONDS)
        self.perclos_tracker = PerclosTracker()
        self.mouth_openings = deque()   # instantes em que o MAR cruzou TALKING_MAR subindo
        self.last_mar_raw = None
        self.smile_baseline = smile_baseline   # None: regra do sorriso desligada
        self.last_smile_t = None

    def registrar_bocejo(self, t):
        """Conta um bocejo visto fora do monitoramento (durante a calibração)."""
        self.yawn_events.record(t)

    def update(self, left_ear, right_ear, mar, head_pitch, face_detected, t, jaw_open=None,
               smile=None):
        """Igual a _update, mais a chave "distraido": rosto sumido há mais de
        DISTRACTION_SECONDS depois de já ter sido visto (sem rosto desde o
        início não conta: pode ser a câmera ainda sendo posicionada).
        jaw_open: blendshape jawOpen do MediaPipe (0 a 1), se disponível.
        smile: sorriso de 0 a 1 (ver sorriso()), se disponível."""
        st = self._update(left_ear, right_ear, mar, head_pitch, face_detected, t, jaw_open, smile)
        sem_rosto = None if st["face_detected"] or self.last_face_seen is None else t - self.last_face_seen
        st["distraido"] = sem_rosto is not None and DISTRACTION_SECONDS <= sem_rosto < DISTRACTION_MAX_SECONDS
        if st["distraido"]:
            st["status"] = f"SEM ROSTO ha {sem_rosto:.0f}s: OLHE PARA A ESTRADA"
        return st

    def _update(self, left_ear, right_ear, mar, head_pitch, face_detected, t, jaw_open=None,
                smile=None):
        """t: tempo do stream em segundos. Retorna um dict com
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
                # fala = boca abrindo e fechando várias vezes; usa o MAR bruto
                # porque a mediana apaga justamente as sílabas
                if self.last_mar_raw is not None and self.last_mar_raw <= TALKING_MAR < mar:
                    self.mouth_openings.append(t)
                self.last_mar_raw = mar
            while self.mouth_openings and t - self.mouth_openings[0] > TALKING_WINDOW_SECONDS:
                self.mouth_openings.popleft()
            talking = len(self.mouth_openings) >= TALKING_MIN_OPENINGS
            # sorrindo/rindo: mesma folga da fala (olho fechado numa risada não
            # é cochilo). Só com o neutro da calibração: sem ele, desligado
            if smile is not None and self.smile_baseline is not None and smile - self.smile_baseline > SMILE_DELTA:
                self.last_smile_t = t
            sorrindo = self.last_smile_t is not None and t - self.last_smile_t <= SMILE_GRACE_SECONDS
            closed_alert_s = (max(self.closed_seconds_alert, CLOSED_SECONDS_ALERT_TALKING)
                              if talking or sorrindo else self.closed_seconds_alert)
            mar_med = float(np.median(self.mar_smooth)) if self.mar_smooth else None
            if jaw_open is not None:
                self.jaw_smooth.append(jaw_open)
            jaw_med = float(np.median(self.jaw_smooth)) if self.jaw_smooth else None
            # Boca bem aberta (acima do limiar de sustentação). Serve tanto pro
            # episódio de bocejo (que só conta se atingir o pico, ver
            # yawn_tracker) quanto pra proteger o olho fechado de virar alarme
            # cheio: aqui errar pro lado de "é bocejo" é o lado seguro.
            mouth_open = mar_med is not None and mar_med > self.mar_sustain_threshold
            jaw_down = jaw_med is not None and jaw_med > JAW_SUSTAIN
            # pra proteger o olho, a mandíbula só vale no nível de pico: no
            # nível de sustentação ela também aparece em quem está só falando
            is_yawning = mouth_open or (jaw_med is not None and jaw_med > JAW_OPEN_THRESHOLD)
            if is_yawning:
                self.last_yawn_t = t
            yawn_related = self.last_yawn_t is not None and t - self.last_yawn_t <= YAWN_EYE_GRACE_SECONDS

            # Olho fechado no bocejo não entra no PERCLOS: é fadiga, que tem aviso
            # próprio, não sono (nos vídeos do painel do YawDD, a maior parte
            # dos avisos de PERCLOS era olho fechado bocejando)
            perclos_closed = l_med < self.perclos_ear_threshold and r_med < self.perclos_ear_threshold
            self.perclos_tracker.update(t, perclos_closed and not yawn_related)
            perclos_value = self.perclos_tracker.value()

            if both_closed:
                if self.closed_since is None:
                    self.closed_since = t
                if yawn_related:
                    # olho fechado por causa do bocejo, não de sono — nunca
                    # vira "fechado" (alarme cheio), só sinaliza fadiga. A
                    # contagem recomeça daqui: se o olho continuar fechado
                    # depois de YAWN_EYE_GRACE_SECONDS sem bocejo, aí sim conta
                    self.closed_since = t
                closed_dur = t - self.closed_since
                if yawn_related:
                    level = "atencao"
                    status = f"ATENCAO: bocejando, olhos fechados ({closed_dur:.1f}s)"
                    color = (0, 255, 255)
                elif closed_dur >= closed_alert_s:
                    level = "fechado"
                    status = f"ALERTA: OLHOS FECHADOS ({closed_dur:.1f}s)"
                    color = (0, 0, 255)
                else:
                    level = "fechando"
                    status = (f"olhos fechados sorrindo ({closed_dur:.1f}s)" if sorrindo
                              else f"olhos fechando... ({closed_dur:.1f}s)")
                    color = (0, 165, 255)
            else:
                # Piscada terminando: se o olho ficou fechado por mais que
                # uma piscada normal (mas sem chegar no alerta de 1.5s),
                # conta como "piscada longa" — sinal de pálpebra pesada /
                # microssono começando, mesmo já com o olho reaberto.
                # Ignora se foi bocejo (mesmo motivo do "fechado" acima).
                if self.closed_since is not None:
                    blink_dur = t - self.closed_since
                    if LONG_BLINK_SECONDS <= blink_dur < closed_alert_s and not yawn_related and not sorrindo:
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

            mar_dur, _ = self.yawn_tracker.update(mouth_open, t, value=mar_med)
            jaw_dur, yawn_count_recent = self.jaw_yawn_tracker.update(jaw_down, t, value=jaw_med)
            # bocejo acontecendo agora: pelos lábios a partir de YAWN_SECONDS_ALERT,
            # pela mandíbula só depois da duração mínima dela
            yawn_dur = max(
                mar_dur if mar_dur is not None and mar_dur >= YAWN_SECONDS_ALERT else -1,
                jaw_dur if jaw_dur is not None and jaw_dur >= JAW_MIN_YAWN_DURATION else -1,
            )
            yawn_dur = yawn_dur if yawn_dur >= 0 else None

            head_pitch_down = (
                head_pitch is not None
                and self.head_pitch_threshold is not None
                and head_pitch > self.head_pitch_threshold
            )
            # Cabeça baixa de olho aberto é o motorista olhando o painel, o
            # rádio ou o retrovisor (no YawDD isso dava 103 dos 160 avisos
            # falsos); cochilo é a cabeça pendendo com o olho fechando
            is_head_down = head_pitch_down and (not HEAD_DOWN_REQUIRES_EYES or both_almost)
            if is_head_down:
                self.last_head_down_t = t
            head_down_dur, head_down_count_recent = self.head_down_tracker.update(is_head_down, t)

            atencao_by_blinks = False
            atencao_by_yawn = both_closed and yawn_related
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
                elif (perclos_value >= PERCLOS_ALERT_RATIO
                      and self.perclos_tracker.coverage() >= PERCLOS_MIN_COVERAGE_SECONDS):
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
                "ear_left": l_med, "ear_right": r_med,
                "ear_avg": (l_med + r_med) / 2.0,
                "mar": mar_med, "jaw_open": jaw_med, "yawning": is_yawning, "yawn_count_recent": yawn_count_recent,
                "head_pitch": head_pitch, "head_down": is_head_down,
                "head_pitch_down": head_pitch_down, "head_down_dur": head_down_dur,
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
                "mar": None, "yawning": False, "yawn_count_recent": self.yawn_events.count(t),
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
                "mar": None, "yawning": False, "yawn_count_recent": self.yawn_events.count(t),
                "head_pitch": None, "head_down": False,
                "head_down_count_recent": self.head_down_tracker.freq.count(t),
                "perclos": self.perclos_tracker.value(),
                "face_detected": False,
            }

        self.closed_since = None
        self.ear_smooth.clear()
        self.mar_smooth.clear()
        self.jaw_smooth.clear()
        # bocejo grande às vezes tira o rosto do quadro (cabeça vai pra trás):
        # o episódio que já tinha duração e pico conta, em vez de sumir
        self.yawn_tracker.encerrar(t)
        self.jaw_yawn_tracker.encerrar(t)
        # idem pra queda de cabeça. E se o rosto sumiu logo depois da cabeça
        # baixar com o olho fechando, foi a própria queda que o tirou do
        # quadro: conta mesmo que a parte visível tenha sido curta
        antes = self.head_down_tracker.freq.count(t)
        self.head_down_tracker.encerrar(t)
        caiu_pra_fora = (self.last_head_down_t is not None
                         and t - self.last_head_down_t <= self.no_face_grace + HEAD_DROP_FACE_LOSS_SECONDS)
        if caiu_pra_fora and self.head_down_tracker.freq.count(t) == antes:
            self.head_down_tracker.freq.record(t)
        self.last_head_down_t = None
        return {
            "status": "SEM ROSTO", "color": (128, 128, 128), "level": "sem_rosto",
            "ear_left": None, "ear_right": None, "ear_avg": None,
            "both_closed": False, "closed_dur": None,
            "blink_count_recent": self.long_blink_tracker.count(t),
            "atencao_by_blinks": False, "atencao_by_yawn": False, "atencao_by_head": False,
            "atencao_by_perclos": False,
            "mar": None, "yawning": False, "yawn_count_recent": self.yawn_events.count(t),
            "head_pitch": None, "head_down": False,
            "head_down_count_recent": self.head_down_tracker.freq.count(t),
            "perclos": self.perclos_tracker.value(),
            "face_detected": False,
        }


class EventFrequencyTracker:
    """Conta eventos discretos (ex.: piscadas longas, bocejos) numa janela
    deslizante — usado quando a frequência de um evento importa mais do que
    a duração de uma ocorrência isolada."""

    def __init__(self, window_seconds, min_interval=0.0):
        self.window_seconds = window_seconds
        self.min_interval = min_interval   # registro mais perto que isso do último é o mesmo evento
        self.events = deque()  # timestamps dos eventos

    def record(self, t):
        if self.events and t - self.events[-1] < self.min_interval:
            return
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

    def __init__(self, min_duration, window_seconds, grace_seconds=0.0, peak_threshold=None, freq=None):
        self.min_duration = min_duration
        self.grace_seconds = grace_seconds
        # Opcional (histerese): o sinal "active" é o limiar baixo, de
        # sustentação; o episódio só vale se o valor passar de peak_threshold
        # em algum momento. Antes disso a duração não é informada (None),
        # pra não subir o nível de alerta com boca meio aberta de fala.
        self.peak_threshold = peak_threshold
        self.since = None
        self.last_active_t = None
        self.peak = None
        # freq pode ser compartilhado entre trackers que contam o mesmo evento
        # por caminhos diferentes (bocejo pelos lábios e pela mandíbula)
        self.freq = freq if freq is not None else EventFrequencyTracker(window_seconds)

    def _peak_ok(self):
        return self.peak_threshold is None or (self.peak is not None and self.peak > self.peak_threshold)

    def encerrar(self, t):
        """Fecha o episódio em andamento agora (ex.: rosto sumiu), contando-o
        se já tinha a duração mínima e o pico. Sem episódio, não faz nada."""
        if self.since is not None and self.last_active_t is not None:
            if self.last_active_t - self.since >= self.min_duration and self._peak_ok():
                self.freq.record(t)
        self.since = None
        self.last_active_t = None
        self.peak = None

    def update(self, active, t, value=None):
        """Retorna (duracao_do_episodio_em_andamento_ou_None, contagem_recente)."""
        if active:
            if self.since is None:
                self.since = t
                self.peak = None
            self.last_active_t = t
            if value is not None:
                self.peak = value if self.peak is None else max(self.peak, value)
            dur = t - self.since if self._peak_ok() else None
        else:
            if self.since is not None:
                gap = t - self.last_active_t if self.last_active_t is not None else None
                if gap is not None and gap <= self.grace_seconds:
                    # lacuna curta (glitch de tracking): mantém o episódio
                    # "vivo" sem avançar a duração enquanto o sinal some
                    dur = self.last_active_t - self.since if self._peak_ok() else None
                    return dur, self.freq.count(t)
                finished_dur = (self.last_active_t - self.since) if self.last_active_t is not None else 0.0
                if finished_dur >= self.min_duration and self._peak_ok():
                    self.freq.record(t)
                self.since = None
                self.last_active_t = None
                self.peak = None
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

    def coverage(self):
        """Segundos de histórico que a janela tem de fato."""
        return self.samples[-1][0] - self.samples[0][0] if len(self.samples) >= 2 else 0.0

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
