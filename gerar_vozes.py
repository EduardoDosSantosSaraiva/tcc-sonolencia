"""
Gera todas as falas do programa com a voz Kokoro (pt-BR) e salva em arquivo.

Por que pré-gravar em vez de sintetizar na hora: o Kokoro soa bem mais
natural que o Piper (e pronuncia "sonolência" e "veículo" certo, o que a voz
Piper faber não fazia), mas leva ~3x o tempo real no Pi 5. Como toda frase
que o programa fala é fixa, basta gerar uma vez. Na execução o sistema só
toca arquivos, continua 100% offline e não gasta CPU com síntese.

Gera:
  - alerta.wav, aviso_atencao.wav, aviso_fadiga.wav, aviso_distracao.wav
    (alarmes; o alerta cheio leva a sirene.wav por baixo, a 30%, como antes)
  - vozes/<hash>.wav  (falas da calibração, do aviso de fone e do autoteste
    guiado, achadas pelo texto via eye_fatigue.voice_file)

Uso (precisa dos modelos do Kokoro em tts_model/, ver baixar_modelos.sh):
    ./venv/bin/python gerar_vozes.py
    ./venv/bin/python gerar_vozes.py --voz pm_alex   # outra voz
"""

import argparse
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

import eye_fatigue as ef

BASE = Path(__file__).parent
KOKORO_MODEL = BASE / "tts_model" / "kokoro-v1.0.int8.onnx"
KOKORO_VOICES = BASE / "tts_model" / "voices-v1.0.bin"
SIREN_PATH = BASE / "sirene.wav"
SAMPLE_RATE = 44100      # mesma taxa da sirene.wav
SIREN_GAIN = 0.3         # sirene por baixo da voz, medido do alerta.wav antigo
PEAK = 0.95

ALARMES = {
    ef.ALERT_SOUND_PATH: ("Acorde! Sinais de sonolência detectados! Pare o veículo!", True),
    ef.ATTENTION_SOUND_PATH: ("Atenção! Sinais de sono detectados.", False),
    ef.FATIGUE_SOUND_PATH: ("Sinal de fadiga ou cansaço.", False),
    ef.DISTRACTION_SOUND_PATH: ("Atenção! Olhe para a estrada.", False),
}


def frases_autoteste():
    frases = list(ef.FRASES_PROGRAMA) + [ef.SELF_TEST_INTRO]
    for _nome, instrucao, _dur, _avaliar, msg_ok, msg_falha in ef.SELF_TEST_SCENARIOS:
        frases += [instrucao, ef.frase_resultado(True, msg_ok), ef.frase_resultado(False, msg_falha)]
    total = len(ef.SELF_TEST_SCENARIOS)
    # total < 8 só acontece se o autoteste for interrompido no meio
    frases += [ef.frase_conclusao(ok, n) for n in range(total + 1) for ok in range(n + 1)]
    return frases


def reamostrar(x, sr_in, sr_out):
    """Reamostragem limitada em banda via FFT (o Kokoro sai a 24 kHz, a
    sirene é 44,1 kHz). O clipe inteiro cabe numa FFT só, são poucos segundos."""
    n_out = round(len(x) * sr_out / sr_in)
    X = np.fft.rfft(x)
    Y = np.zeros(n_out // 2 + 1, dtype=complex)
    m = min(len(X), len(Y))
    Y[:m] = X[:m]
    return (np.fft.irfft(Y, n_out) * (n_out / len(x))).astype(np.float32)


def sintetizar(kokoro, texto, voz):
    audio, sr = kokoro.create(texto, voice=voz, speed=1.0, lang="pt-br")
    audio = reamostrar(np.asarray(audio, dtype=np.float64), sr, SAMPLE_RATE)
    return audio * (PEAK / max(np.abs(audio).max(), 1e-9))


def ler_sirene():
    with wave.open(str(SIREN_PATH)) as w:
        assert w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def salvar(path, audio):
    sf.write(path, np.clip(audio, -1.0, 1.0), SAMPLE_RATE, subtype="PCM_16")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voz", default="pf_dora", help="Voz pt-BR do Kokoro: pf_dora, pm_alex ou pm_santa")
    args = ap.parse_args()

    kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))

    for path, (texto, com_sirene) in ALARMES.items():
        voz = sintetizar(kokoro, texto, args.voz)
        if com_sirene:
            sirene = ler_sirene() * SIREN_GAIN
            n = max(len(voz), len(sirene))
            mix = np.zeros(n, dtype=np.float32)
            mix[:len(sirene)] += sirene
            mix[:len(voz)] += voz
            voz = mix * (1.0 / max(np.abs(mix).max(), 1.0))   # sem clipar a soma
        salvar(path, voz)
        print(f"{path.name}: {len(voz) / SAMPLE_RATE:.1f}s")

    ef.VOICE_DIR.mkdir(exist_ok=True)
    marca_voz = ef.VOICE_DIR / "VOZ.txt"
    if not marca_voz.exists() or marca_voz.read_text().strip() != args.voz:
        for antigo in ef.VOICE_DIR.glob("*.wav"):
            antigo.unlink()   # trocou de voz: regrava tudo
        marca_voz.write_text(args.voz + "\n")
    frases = frases_autoteste()
    validos = {ef.voice_file(f) for f in frases}
    for antigo in ef.VOICE_DIR.glob("*.wav"):
        if antigo not in validos:
            antigo.unlink()   # frase que mudou/saiu do código
    for i, frase in enumerate(frases, 1):
        destino = ef.voice_file(frase)
        if destino.exists():
            continue
        salvar(destino, sintetizar(kokoro, frase, args.voz))
        print(f"[{i}/{len(frases)}] {frase}")
    print(f"Pronto: {len(frases)} falas do autoteste em {ef.VOICE_DIR.name}/")


if __name__ == "__main__":
    main()
