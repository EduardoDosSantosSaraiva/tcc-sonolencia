"""
Testes automatizados da lógica pura do TCC (core.py): EAR/MAR, timestamp
monotônico, trackers de duração/frequência e a máquina de estados de
sonolência. Tudo aqui roda sem câmera, sem MediaPipe e sem hardware — só a
matemática e as transições de estado, por isso é rápido e determinístico.

Uso:
    ./venv/bin/python -m pytest test_core.py -v
"""

from types import SimpleNamespace

import pytest

import core


def make_landmarks(overrides, n=500):
    """Lista de landmarks fake (só .x/.y, como os do MediaPipe) com todos os
    pontos em (0, 0) exceto os índices passados em overrides={idx: (x, y)}."""
    pts = [SimpleNamespace(x=0.0, y=0.0) for _ in range(n)]
    for idx, (x, y) in overrides.items():
        pts[idx] = SimpleNamespace(x=x, y=y)
    return pts


# ---------------------------------------------------------------------------
# EAR / MAR
# ---------------------------------------------------------------------------

def test_eye_aspect_ratio_known_geometry():
    p1, p2, p3, p4, p5, p6 = core.LEFT_EYE
    landmarks = make_landmarks({
        p1: (0.0, 0.0), p4: (0.4, 0.0),      # cantos: horizontal = 0.4
        p2: (0.1, 0.1), p6: (0.1, -0.1),     # par vertical esquerdo: 0.2
        p3: (0.3, 0.1), p5: (0.3, -0.1),     # par vertical direito: 0.2
    })
    ear = core.eye_aspect_ratio(landmarks, core.LEFT_EYE, w=1.0, h=1.0)
    assert ear == 0.5  # (0.2 + 0.2) / (2 * 0.4)


def test_eye_aspect_ratio_degenerate_returns_none():
    p1, p2, p3, p4, p5, p6 = core.LEFT_EYE
    landmarks = make_landmarks({p1: (0.5, 0.5), p4: (0.5, 0.5)})  # cantos colapsados
    assert core.eye_aspect_ratio(landmarks, core.LEFT_EYE, w=1.0, h=1.0) is None


def test_mouth_aspect_ratio_uses_same_geometry_as_eye():
    p1, p2, p3, p4, p5, p6 = core.MOUTH
    landmarks = make_landmarks({
        p1: (0.0, 0.0), p4: (0.4, 0.0),
        p2: (0.1, 0.1), p6: (0.1, -0.1),
        p3: (0.3, 0.1), p5: (0.3, -0.1),
    })
    mar = core.mouth_aspect_ratio(landmarks, core.MOUTH, w=1.0, h=1.0)
    assert mar == 0.5


def _landmarks_for_nose_test(nose_y):
    overrides = {i: (0.6, 0.3) for i in core.LEFT_EYE}
    overrides.update({i: (0.4, 0.3) for i in core.RIGHT_EYE})
    overrides[core.NOSE_TIP] = (0.5, nose_y)
    return make_landmarks(overrides)


def test_head_drop_ratio_increases_when_nose_drops_below_eye_line():
    neutral = core.head_drop_ratio(_landmarks_for_nose_test(0.4), w=1.0, h=1.0)
    dropped = core.head_drop_ratio(_landmarks_for_nose_test(0.5), w=1.0, h=1.0)
    assert neutral == pytest.approx(0.5)   # (0.4 - 0.3) / 0.2
    assert dropped == pytest.approx(1.0)   # (0.5 - 0.3) / 0.2
    assert dropped > neutral


def test_head_drop_ratio_degenerate_returns_none():
    overrides = {i: (0.5, 0.3) for i in core.LEFT_EYE + core.RIGHT_EYE}  # olhos colapsados no mesmo x
    overrides[core.NOSE_TIP] = (0.5, 0.4)
    landmarks = make_landmarks(overrides)
    assert core.head_drop_ratio(landmarks, w=1.0, h=1.0) is None


# ---------------------------------------------------------------------------
# MonotonicMs
# ---------------------------------------------------------------------------

def test_monotonic_ms_strictly_increasing_even_with_duplicate_or_backward_t():
    ts = core.MonotonicMs()
    a = ts.next(1.0)
    b = ts.next(1.0)      # mesmo t de novo
    c = ts.next(0.5)      # t voltou pra trás
    assert a < b < c


# ---------------------------------------------------------------------------
# Trackers
# ---------------------------------------------------------------------------

def test_sustained_signal_tracker_counts_long_episode():
    tracker = core.SustainedSignalTracker(min_duration=1.0, window_seconds=60)
    dur, count = tracker.update(True, 0.0)
    assert dur == 0.0 and count == 0
    dur, count = tracker.update(True, 1.2)
    assert dur == 1.2 and count == 0  # ainda em andamento, não contou ainda
    dur, count = tracker.update(False, 1.3)
    assert dur is None and count == 1  # episódio de 1.3s >= min_duration


def test_sustained_signal_tracker_ignores_short_episode():
    tracker = core.SustainedSignalTracker(min_duration=1.0, window_seconds=60)
    tracker.update(True, 0.0)
    dur, count = tracker.update(False, 0.3)  # episódio de só 0.3s
    assert dur is None and count == 0


def test_sustained_signal_tracker_tolerates_short_gap_within_grace():
    tracker = core.SustainedSignalTracker(min_duration=1.0, window_seconds=60, grace_seconds=0.3)
    tracker.update(True, 0.0)
    tracker.update(True, 0.5)
    dur, count = tracker.update(False, 0.6)   # lacuna de 0.1s, dentro da tolerância
    assert dur == pytest.approx(0.5) and count == 0   # episódio segue "vivo"
    dur, count = tracker.update(True, 0.8)    # sinal volta antes de estourar a tolerância
    assert dur == pytest.approx(0.8) and count == 0
    dur, count = tracker.update(True, 1.2)
    assert dur == pytest.approx(1.2)
    dur, count = tracker.update(False, 1.9)   # lacuna de 0.7s, além da tolerância — encerra
    assert dur is None and count == 1         # episódio de 1.2s >= min_duration, contou


def test_sustained_signal_tracker_peak_hysteresis():
    """Bocejo de pico baixo: fica pouco tempo acima do pico, mas bastante
    acima do limiar de sustentação. Com histerese conta; sem atingir o pico
    (fala com a boca meio aberta), não conta nem informa duração."""
    tracker = core.SustainedSignalTracker(min_duration=0.8, window_seconds=60, peak_threshold=0.55)
    dur, _ = tracker.update(True, 0.0, value=0.45)
    assert dur is None                       # pico ainda não atingido
    dur, _ = tracker.update(True, 0.5, value=0.60)
    assert dur == pytest.approx(0.5)         # atingiu o pico: duração conta desde o início
    tracker.update(True, 1.0, value=0.42)
    dur, count = tracker.update(False, 2.0)
    assert dur is None and count == 1

    fala = core.SustainedSignalTracker(min_duration=0.8, window_seconds=60, peak_threshold=0.55)
    for i in range(20):
        dur, _ = fala.update(True, i * 0.1, value=0.45)
        assert dur is None
    _, count = fala.update(False, 3.0)
    assert count == 0


def test_low_peak_yawn_is_counted_by_state():
    state = core.DrowsinessState(ear_threshold=0.21, mar_threshold=0.55)
    t = 0.0
    for mar in [0.05] * 10 + [0.45] * 5 + [0.6] * 8 + [0.45] * 10 + [0.05] * 20:
        st = state.update(0.3, 0.3, mar, None, None, True, t)
        t += 0.1
    assert st["yawn_count_recent"] == 1


def test_event_frequency_tracker_trims_old_events():
    tracker = core.EventFrequencyTracker(window_seconds=10)
    tracker.record(0.0)
    tracker.record(5.0)
    assert tracker.count(5.0) == 2
    tracker.record(20.0)  # cutoff = 20 - 10 = 10, derruba os eventos de 0 e 5
    assert tracker.count(20.0) == 1


def test_perclos_tracker_ratio():
    tracker = core.PerclosTracker(window_seconds=60)
    tracker.update(0, False)
    tracker.update(5, True)
    tracker.update(10, True)
    # intervalo [0,5) conta o estado do ponto inicial (False) = aberto
    # intervalo [5,10) conta o estado do ponto em t=5 (True) = fechado
    assert tracker.value() == 0.5


# ---------------------------------------------------------------------------
# DrowsinessState — máquina de estados
# ---------------------------------------------------------------------------

def test_closed_eyes_trigger_full_alarm_after_threshold():
    state = core.DrowsinessState(ear_threshold=0.21, ear_almost_closed=0.26)
    state.update(0.1, 0.1, None, None, None, True, 0.0)
    st = state.update(0.1, 0.1, None, None, None, True, 2.0)
    assert st["level"] == "fechado"


def test_yawn_with_closed_eyes_never_triggers_full_alarm():
    """Bocejar fecha os olhos por reflexo — isso NUNCA pode disparar o
    alarme cheio de sono (só o aviso leve de fadiga)."""
    state = core.DrowsinessState(ear_threshold=0.21, mar_threshold=0.6)
    state.update(0.1, 0.1, 0.8, None, None, True, 0.0)
    st = state.update(0.1, 0.1, 0.8, None, None, True, 3.0)
    assert st["level"] != "fechado"
    assert st["level"] == "atencao"
    assert st["atencao_by_yawn"] is True


def test_eyes_closed_right_after_yawn_do_not_trigger_full_alarm():
    """Boca já fechando e olho ainda fechado logo depois do bocejo: não é
    sono. Só se o olho continuar fechado além da tolerância o alarme conta."""
    state = core.DrowsinessState(ear_threshold=0.21, mar_threshold=0.55)
    t = 0.0
    for _ in range(20):                      # bocejo de 2 s, olho fechado
        state.update(0.1, 0.1, 0.8, None, None, True, t); t += 0.1
    for _ in range(20):                      # boca fechou, olho segue fechado 2 s
        st = state.update(0.1, 0.1, 0.05, None, None, True, t); t += 0.1
        assert st["level"] != "fechado"
    for _ in range(30):                      # continua fechado: agora é sono
        st = state.update(0.1, 0.1, 0.05, None, None, True, t); t += 0.1
    assert st["level"] == "fechado"


def test_long_blink_is_counted_without_full_alarm():
    state = core.DrowsinessState(
        ear_threshold=0.21, ear_almost_closed=0.26, smoothing_window=1,
    )
    state.update(0.1, 0.1, None, None, None, True, 0.0)      # olho fecha
    st = state.update(0.3, 0.3, None, None, None, True, 0.9)  # reabre em 0.9s
    assert st["level"] != "fechado"
    assert st["blink_count_recent"] == 1


def test_face_missing_triggers_distraction_only_after_threshold():
    state = core.DrowsinessState()
    state.update(0.3, 0.3, None, None, None, True, 0.0)   # rosto visto
    st = state.update(None, None, None, None, None, False, 1.0)
    assert st["distraido"] is False                        # sumiu há pouco
    st = state.update(None, None, None, None, None, False, 0.0 + core.DISTRACTION_SECONDS + 0.1)
    assert st["distraido"] is True
    st = state.update(0.3, 0.3, None, None, None, True, 5.0)
    assert st["distraido"] is False                        # voltou a olhar


def test_face_never_seen_or_gone_too_long_is_not_distraction():
    state = core.DrowsinessState()
    st = state.update(None, None, None, None, None, False, 10.0)
    assert st["distraido"] is False                        # câmera ainda sem ninguém
    state.update(0.3, 0.3, None, None, None, True, 20.0)
    st = state.update(None, None, None, None, None, False, 20.0 + core.DISTRACTION_MAX_SECONDS + 1)
    assert st["distraido"] is False                        # motorista saiu do carro


def test_perclos_escalates_level_even_with_eyes_open_now():
    state = core.DrowsinessState(smoothing_window=1)
    # injeta histórico de olho majoritariamente fechado direto no tracker
    state.perclos_tracker.update(0.0, True)
    state.perclos_tracker.update(50.0, True)
    st = state.update(0.35, 0.35, None, None, None, True, 50.1)  # olho aberto agora
    assert st["perclos"] >= core.PERCLOS_ALERT_RATIO
    assert st["level"] == "atencao"
    assert st["atencao_by_perclos"] is True


def test_perclos_needs_minimum_history_before_alerting():
    """Logo depois da calibração, uma piscada em poucos segundos de dados
    não pode virar 'PERCLOS alto'."""
    state = core.DrowsinessState(smoothing_window=1)
    state.update(0.35, 0.35, None, None, None, True, 0.0)
    state.update(0.10, 0.10, None, None, None, True, 1.0)    # piscada
    st = state.update(0.35, 0.35, None, None, None, True, 1.3)
    assert st["perclos"] >= core.PERCLOS_ALERT_RATIO       # a conta dá alto...
    assert st["atencao_by_perclos"] is False               # ...mas não alerta ainda


def test_glasses_mode_hysteresis_enter_and_exit():
    state = core.DrowsinessState(smoothing_window=1)

    # óculos escuros: olho lido como "fechado" pelo EAR bruto, mas o sinal
    # de contraste diz que é lente escura, não sono
    state.update(0.05, 0.05, None, None, True, True, 0.0)
    st = state.update(0.05, 0.05, None, None, True, True, 3.5)  # >= 3s escuro
    assert st["modo_oculos_escuros"] is True
    assert st["level"] != "fechado"

    # volta a enxergar o olho: precisa de 2s contínuos claros pra sair do modo
    st = state.update(0.3, 0.3, None, None, False, True, 4.0)
    assert st["modo_oculos_escuros"] is True  # ainda não passou os 2s
    st = state.update(0.3, 0.3, None, None, False, True, 6.5)  # >= 2s claro
    assert st["modo_oculos_escuros"] is False


def test_calibration_discards_open_mouth_frames_and_keeps_the_yawn():
    """Bocejo durante a calibração: sai da referência (fecharia o olho e
    subiria o MAR), mas conta como bocejo em vez de ser jogado fora."""
    repouso = [(i / 30, 0.30, 0.02, 0.5, 0.01) for i in range(90)]           # 3 s
    bocejo = [(3 + i / 30, 0.10, 0.90, 0.7, 0.80) for i in range(60)]        # 2 s boca aberta
    depois = [(5 + i / 30, 0.30, 0.02, 0.5, 0.01) for i in range(60)]
    ear_b, mar_b, _p, warning, bocejos = core.calibrar_quadros(repouso + bocejo + depois)
    assert ear_b == pytest.approx(0.30) and mar_b == pytest.approx(0.02)
    assert "descartados" in warning
    assert len(bocejos) == 1
    assert core.tempo_em_repouso(repouso + bocejo + depois) == pytest.approx(5.0, abs=0.1)
    *_, bocejos = core.calibrar_quadros(repouso)
    assert bocejos == []


def test_calibration_yawn_is_counted_by_state():
    state = core.DrowsinessState()
    state.registrar_bocejo(4.0)
    st = state.update(0.3, 0.3, 0.02, None, None, True, 11.0)
    assert st["yawn_count_recent"] == 1


def test_head_down_with_eyes_open_is_not_drowsiness():
    """Cabeça baixa de olho aberto é olhar o painel; só conta com o olho
    fechando junto (cochilo)."""
    olhando = core.DrowsinessState(ear_threshold=0.21, ear_almost_closed=0.26, head_pitch_threshold=0.6)
    cochilo = core.DrowsinessState(ear_threshold=0.21, ear_almost_closed=0.26, head_pitch_threshold=0.6)
    t = 0.0
    for _ in range(20):
        a = olhando.update(0.3, 0.3, 0.02, 0.8, None, True, t)
        b = cochilo.update(0.15, 0.15, 0.02, 0.8, None, True, t)
        t += 0.1
    assert a["head_down"] is False and a["atencao_by_head"] is False
    assert b["head_down"] is True


def _fecha_olho(state, t, segundos, mar_de):
    """Olho fechado por `segundos` a 10 fps; mar_de(i) dá o MAR de cada frame."""
    st = None
    for i in range(int(segundos * 10)):
        st = state.update(0.1, 0.1, mar_de(i), None, None, True, t + i / 10)
    return st


def test_eyes_closed_while_talking_waits_longer_before_full_alarm():
    """Risada de olho fechado: quem fala ativamente não está cochilando,
    então o alarme cheio espera CLOSED_SECONDS_ALERT_TALKING."""
    fala = lambda i: 0.25 if i % 4 < 2 else 0.03     # sílaba a cada 0,4 s
    state = core.DrowsinessState(ear_threshold=0.21, mar_threshold=0.55)
    st = _fecha_olho(state, 0.0, 2.0, fala)
    assert st["level"] != "fechado"
    st = _fecha_olho(state, 2.0, 1.0, fala)
    assert st["level"] == "fechado"


def test_eyes_closed_in_silence_keeps_normal_alarm_time():
    state = core.DrowsinessState(ear_threshold=0.21, mar_threshold=0.55)
    st = _fecha_olho(state, 0.0, 1.7, lambda i: 0.02)
    assert st["level"] == "fechado"


def test_yawn_with_lips_together_is_counted_by_jaw():
    """Bocejo de boca fechada ou com a mão na frente: o MAR quase não sobe,
    mas a mandíbula desce (jawOpen alto). Tem que contar como bocejo."""
    state = core.DrowsinessState(ear_threshold=0.21, mar_threshold=0.55)
    t = 0.0
    for jaw in [0.01] * 10 + [0.45] * 5 + [0.8] * 20 + [0.45] * 5 + [0.01] * 20:   # 3 s de mandíbula aberta
        st = state.update(0.3, 0.3, 0.05, None, None, True, t, jaw_open=jaw)
        t += 0.1
    assert st["yawn_count_recent"] == 1


def test_short_jaw_openings_of_speech_are_not_yawns():
    """Fala abre a mandíbula, até alto, mas por pouco tempo."""
    state = core.DrowsinessState(ear_threshold=0.21, mar_threshold=0.55)
    t = 0.0
    for _ in range(8):
        for jaw in [0.05] * 5 + [0.7] * 12 + [0.05] * 5:   # 1,2 s aberta por frase
            st = state.update(0.3, 0.3, 0.15, None, None, True, t, jaw_open=jaw)
            t += 0.1
    assert st["yawn_count_recent"] == 0


def test_same_yawn_seen_by_lips_and_jaw_counts_once():
    state = core.DrowsinessState(ear_threshold=0.21, mar_threshold=0.55)
    t = 0.0
    for mar, jaw in [(0.05, 0.01)] * 10 + [(0.9, 0.9)] * 35 + [(0.05, 0.01)] * 30:
        st = state.update(0.3, 0.3, mar, None, None, True, t, jaw_open=jaw)
        t += 0.1
    assert st["yawn_count_recent"] == 1


def test_calibration_counts_jaw_only_yawn():
    repouso = [(i / 30, 0.30, 0.02, 0.5, 0.01) for i in range(90)]
    bocejo = [(3 + i / 30, 0.20, 0.10, 0.6, 0.85) for i in range(90)]   # 3 s: lábios juntos, mandíbula desce
    depois = [(6 + i / 30, 0.30, 0.02, 0.5, 0.01) for i in range(60)]
    ear_b, _m, _p, _w, bocejos = core.calibrar_quadros(repouso + bocejo + depois)
    assert ear_b == pytest.approx(0.30)
    assert len(bocejos) == 1


def test_yawn_that_takes_the_face_out_of_frame_still_counts():
    """Cabeça vai pra trás no bocejo e o rosto some: o episódio que já tinha
    duração e pico conta, em vez de ser descartado."""
    state = core.DrowsinessState(ear_threshold=0.21, mar_threshold=0.55)
    t = 0.0
    for _ in range(10):
        state.update(0.3, 0.3, 0.05, None, None, True, t); t += 0.1
    for _ in range(15):                                   # 1,5 s de bocejo
        state.update(0.3, 0.3, 0.9, None, None, True, t); t += 0.1
    for _ in range(10):                                   # rosto some 1 s
        st = state.update(None, None, None, None, None, False, t); t += 0.1
    assert st["yawn_count_recent"] == 1


def test_head_drop_survives_flicker_of_the_closed_eye():
    """Com a cabeça baixa o olho é visto de cima e o EAR oscila: um frame
    isolado acima do limiar não pode recomeçar a queda (autoteste 30/09)."""
    state = core.DrowsinessState(ear_threshold=0.21, ear_almost_closed=0.26, head_pitch_threshold=0.6)
    t = 0.0
    for i in range(20):                                   # 2 s, olho "pisca" a cada 5 frames
        ear = 0.30 if i % 5 == 4 else 0.10
        state.update(ear, ear, 0.02, 0.8, None, True, t); t += 0.1
    for _ in range(10):                                   # cabeça sobe, olho abre
        st = state.update(0.30, 0.30, 0.02, 0.4, None, True, t); t += 0.1
    assert st["head_down_count_recent"] == 1


def test_head_drop_that_takes_the_face_out_of_frame_still_counts():
    state = core.DrowsinessState(ear_threshold=0.21, ear_almost_closed=0.26, head_pitch_threshold=0.6)
    t = 0.0
    for _ in range(15):                                   # 1,5 s caindo, olho fechado
        state.update(0.10, 0.10, 0.02, 0.8, None, True, t); t += 0.1
    for _ in range(10):                                   # rosto some
        st = state.update(None, None, None, None, None, False, t); t += 0.1
    assert st["head_down_count_recent"] == 1


def test_head_that_falls_out_of_frame_counts_even_if_visible_part_is_short():
    """Autoteste 30/09: a cabeça baixou com o olho fechando por 0,3 s e o
    rosto sumiu. Foi a queda que tirou o rosto do quadro."""
    state = core.DrowsinessState(ear_threshold=0.21, ear_almost_closed=0.26, head_pitch_threshold=0.6)
    t = 0.0
    for _ in range(10):
        state.update(0.30, 0.30, 0.02, 0.4, None, True, t); t += 0.1
    for _ in range(3):                                    # 0,3 s caindo de olho fechado
        state.update(0.10, 0.10, 0.02, 0.8, None, True, t); t += 0.1
    for _ in range(10):
        st = state.update(None, None, None, None, None, False, t); t += 0.1
    assert st["head_down_count_recent"] == 1


def test_face_lost_without_head_drop_is_not_a_head_drop():
    """Rosto sumindo com a cabeça normal (virou pra trás, câmera tapada)
    é distração, não queda de cabeça."""
    state = core.DrowsinessState(ear_threshold=0.21, ear_almost_closed=0.26, head_pitch_threshold=0.6)
    t = 0.0
    for _ in range(10):
        state.update(0.30, 0.30, 0.02, 0.4, None, True, t); t += 0.1
    for _ in range(10):
        st = state.update(None, None, None, None, None, False, t); t += 0.1
    assert st["head_down_count_recent"] == 0


def test_head_drop_ratio_ignores_side_camera_angle():
    """Mesma cabeça, câmera de frente e de lado (rosto visto a ~40 graus):
    o índice não pode inflar só por causa do ângulo."""
    import math
    class P:
        def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z
    def rosto(yaw):
        pts = [P(0.5, 0.5, 0.0) for _ in range(478)]
        c, s_ = math.cos(yaw), math.sin(yaw)
        def gira(x, y, z):   # gira em torno do eixo vertical que passa pelo centro do rosto
            return P(0.5 + x * c + z * s_, y, -x * s_ + z * c)
        for i in core.LEFT_EYE:
            pts[i] = gira(-0.05, 0.45, 0.0)
        for i in core.RIGHT_EYE:
            pts[i] = gira(0.05, 0.45, 0.0)
        pts[core.NOSE_TIP] = gira(0.0, 0.50, -0.04)
        return pts
    de_frente = core.head_drop_ratio(rosto(0.0), 640, 640)
    de_lado = core.head_drop_ratio(rosto(math.radians(40)), 640, 640)
    assert de_lado == pytest.approx(de_frente, rel=0.02)


# ---------------------------------------------------------------------------
# Sorriso/riso e PERCLOS com bocejo
# ---------------------------------------------------------------------------

def test_smile_gives_closed_eyes_the_same_extra_time_as_talking():
    """Olho fechado rindo não é cochilo: o alarme cheio espera 2.5 s em vez
    de 1.5 s. Sério (sorriso no neutro), continua 1.5 s."""
    def alarme_em(smile):
        state = core.DrowsinessState(ear_threshold=0.21, smile_baseline=0.05)
        t = 0.0
        while t < 4.0:
            st = state.update(0.1, 0.1, 0.02, None, None, True, t, smile=smile)
            if st["level"] == "fechado":
                return t
            t += 0.1
        return None
    assert alarme_em(0.05) == pytest.approx(core.CLOSED_SECONDS_ALERT, abs=0.11)
    assert alarme_em(0.6) == pytest.approx(core.CLOSED_SECONDS_ALERT_TALKING, abs=0.11)


def test_smile_rule_is_off_without_calibrated_neutral():
    """Sem o neutro da calibração não dá pra saber se é sorriso ou o formato
    da boca da pessoa: a regra fica desligada."""
    state = core.DrowsinessState(ear_threshold=0.21)
    state.update(0.1, 0.1, 0.02, None, None, True, 0.0, smile=0.9)
    st = state.update(0.1, 0.1, 0.02, None, None, True, 2.0, smile=0.9)
    assert st["level"] == "fechado"


def test_long_blink_while_laughing_is_not_counted():
    state = core.DrowsinessState(ear_threshold=0.21, smoothing_window=1, smile_baseline=0.05)
    state.update(0.1, 0.1, 0.02, None, None, True, 0.0, smile=0.6)
    st = state.update(0.3, 0.3, 0.02, None, None, True, 0.9, smile=0.6)
    assert st["blink_count_recent"] == 0


def test_neutral_smile_comes_from_closed_mouth_calibration_frames():
    repouso = [(i / 10, 0.3, 0.02, 0.4, 0.05, 0.10) for i in range(50)]
    rindo = [(5 + i / 10, 0.3, 0.02, 0.4, 0.05, 0.70) for i in range(10)]
    bocejo = [(6 + i / 10, 0.2, 0.9, 0.4, 0.8, 0.90) for i in range(20)]
    assert core.sorriso_neutro(repouso + bocejo) == pytest.approx(0.10)
    assert core.sorriso_neutro(repouso + rindo) == pytest.approx(0.10)
    assert core.sorriso_neutro(repouso[:5]) is None
    assert core.sorriso_neutro([q[:5] for q in repouso]) is None   # série sem sorriso


def test_closed_eyes_while_yawning_do_not_raise_perclos():
    """Olho fechado no bocejo é fadiga (aviso próprio), não entra no PERCLOS."""
    state = core.DrowsinessState(ear_threshold=0.21, mar_threshold=0.55)
    t = 0.0
    while t < 5.0:
        st = state.update(0.05, 0.05, 0.8, None, None, True, t); t += 0.1
    assert st["perclos"] == 0.0
    while t < 10.0:
        st = state.update(0.05, 0.05, 0.02, None, None, True, t); t += 0.1
    assert st["perclos"] > 0.0
