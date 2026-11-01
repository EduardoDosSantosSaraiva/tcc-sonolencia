"""Testes da interface: toda tela desenha sem erro nos dois layouts, com os
estados reais do DrowsinessState, e a classificação mostra o que o core
decidiu. Rodar: ./venv/bin/python -m pytest -q test_interface.py"""
from collections import namedtuple

import numpy as np
import pytest

import core
import interface
from eye_fatigue import build_state

Ponto = namedtuple("Ponto", "x y")
ROSTO = [Ponto(0.5 + 0.1 * np.cos(i), 0.45 + 0.15 * np.sin(i)) for i in range(478)]
QUADRO = np.random.default_rng(0).integers(0, 255, (972, 1296, 3), dtype=np.uint8)


def estados():
    """Um st de cada situação, tirado do DrowsinessState de verdade."""
    state = build_state(0.30, 0.05, 0.40, 0.1)
    t = [0.0]

    def rodar(n, l, r, mar, rosto=True):
        st = None
        for _ in range(n):
            t[0] += 1 / 30
            st = state.update(l, r, mar, 0.40 if rosto else None, rosto, t[0])
        return st

    out = {"aberto": rodar(60, 0.30, 0.30, 0.05)}
    out["piscando"] = rodar(5, 0.10, 0.10, 0.05)          # 0,17 s: piscada normal
    out["fechando"] = rodar(15, 0.10, 0.10, 0.05)          # 0,67 s
    out["fechado"] = rodar(40, 0.10, 0.10, 0.05)           # 2 s
    rodar(60, 0.30, 0.30, 0.05)
    out["bocejo"] = rodar(75, 0.30, 0.30, 0.90)
    rodar(60, 0.30, 0.30, 0.05)
    out["sem_rosto_curto"] = rodar(5, None, None, None, rosto=False)
    out["distraido"] = rodar(150, None, None, None, rosto=False)
    return state, out


STATE, ESTADOS = estados()


def test_classificacao_segue_o_core():
    titulos = {k: interface.classificar(v)[0] for k, v in ESTADOS.items()}
    assert ESTADOS["fechado"]["level"] == "fechado" and titulos["fechado"] == "SONO DETECTADO"
    assert titulos["aberto"] == "ATENTO"
    assert titulos["piscando"] == "ATENTO"            # piscada normal não acende o cartão
    assert titulos["fechando"] == "OLHOS FECHANDO"
    assert titulos["bocejo"] == "FADIGA"              # bocejo nunca vira sono
    assert ESTADOS["distraido"]["distraido"] and titulos["distraido"] == "OLHE PARA A ESTRADA"


@pytest.mark.parametrize("compacto", [False, True])
def test_todas_as_telas_desenham(compacto):
    ui = interface.Interface(480, 320, compacto=True) if compacto else interface.Interface()
    tam = (ui.H, ui.W, 3)
    telas = [
        ui.espera(QUADRO, rosto_ok=True, landmarks=ROSTO),
        ui.espera(None),
        ui.preparo(QUADRO, 2.2),
        ui.calibrando(QUADRO, 0.5, 2.5),
        ui.calibrando(QUADRO, 1.2, -0.3),
        ui.mensagem("Calibração concluída", "Monitoramento ativo.", "ok"),
        ui.autoteste(None, None, None, 0.0, 1, 8, "Baseline", "Fique parado.", 15, 15, [], ["Baseline"] * 8),
        ui.autoteste(QUADRO, ESTADOS["fechado"], ROSTO, 3.0, 4, 8, "Microssono", "Feche os olhos.", 2.5, 12,
                     [("a", True), ("b", True), ("c", False)], [f"c{i}" for i in range(8)]),
        ui.resultado_cenario("Microssono", True, "alarme de sono disparado", ["a", "b"], [("a", True)]),
        ui.mensagem_autoteste("Autoteste guiado", "Vou guiar você.", ["a", "b"]),
        ui.resumo_autoteste([("a", True, "ok", ""), ("b", False, "falhou feio", "")]),
    ]
    for i, (k, st) in enumerate(ESTADOS.items()):
        for tecnico in (False, True):
            ui.tecnico = tecnico
            telas.append(ui.monitor(QUADRO, st, ROSTO if st["face_detected"] else None, 10.0 + i,
                                    perclos=0.2, fone=[None, True, False][i % 3], fps=30.0, latencia=24,
                                    limiares=STATE))
    for img in telas:
        assert img.shape == tam and img.dtype == np.uint8


def test_layout_pela_tela():
    assert not interface.Interface.para_tela(("HDMI-A-1", 480, 0, 1920, 1080)).compacto
    assert interface.Interface.para_tela(None).W == 1280
    telinha = interface.Interface.para_tela(("SPI-1", 0, 0, 480, 320))
    assert telinha.compacto and (telinha.W, telinha.H) == (480, 320)


def test_linha_do_tempo_guarda_o_pior_estado():
    linha = interface.LinhaDoTempo(janela_s=60, colunas=30)   # 2 s por coluna
    linha.registrar(0.0, 1)
    linha.registrar(0.5, 4)
    linha.registrar(1.0, 1)
    linha.registrar(10.0, 2)
    serie = linha.serie()
    assert serie[-1] == 2 and serie[-6] == 4 and serie[-2] == -1   # colunas sem dado ficam vazias
