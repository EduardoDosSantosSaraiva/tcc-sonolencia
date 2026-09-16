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


def test_long_blink_is_counted_without_full_alarm():
    state = core.DrowsinessState(
        ear_threshold=0.21, ear_almost_closed=0.26, smoothing_window=1,
    )
    state.update(0.1, 0.1, None, None, None, True, 0.0)      # olho fecha
    st = state.update(0.3, 0.3, None, None, None, True, 0.9)  # reabre em 0.9s
    assert st["level"] != "fechado"
    assert st["blink_count_recent"] == 1


def test_perclos_escalates_level_even_with_eyes_open_now():
    state = core.DrowsinessState(smoothing_window=1)
    # injeta histórico de olho majoritariamente fechado direto no tracker
    state.perclos_tracker.update(0.0, True)
    state.perclos_tracker.update(50.0, True)
    st = state.update(0.35, 0.35, None, None, None, True, 50.1)  # olho aberto agora
    assert st["perclos"] >= core.PERCLOS_ALERT_RATIO
    assert st["level"] == "atencao"
    assert st["atencao_by_perclos"] is True


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
