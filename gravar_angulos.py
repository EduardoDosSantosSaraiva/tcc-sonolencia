#!/usr/bin/env python3
"""
Grava vídeos de teste do próprio motorista em várias posições da câmera
(de frente e cada vez mais de lado), guiando por voz, como o autoteste.

Em cada posição o roteiro é o mesmo: olhar para a frente, piscar, fechar os
olhos, falar, bocejar e deixar a cabeça cair. Cada trecho fica anotado com
o tempo de início e fim (rotulos.csv), então depois avaliar_angulos.py mede
o sistema trecho por trecho, quantas vezes quiser, sem precisar repetir a
gravação: dá pra comparar regras e medidas (2D x 3D) nos mesmos vídeos.

Grava a imagem de 640 x 480 a 30 fps, exatamente a que vai para a IA no
programa ao vivo. Os vídeos mostram o rosto: ficam só no Pi (pasta
videos_proprios/, fora do Git).

Uso:
    ./venv/bin/python gravar_angulos.py                     # todas as posições
    ./venv/bin/python gravar_angulos.py --posicoes frente lado_45
"""

import argparse
import csv
import threading
import time
from pathlib import Path

import core
import eye_fatigue as ef

PASTA = Path(__file__).parent / "videos_proprios"

POSICOES = {
    "frente": "Posição de frente. Deixe a câmera bem na sua frente, na altura do rosto.",
    "lado_30": "Agora um pouco de lado: vire a câmera uns trinta graus, como uma câmera no meio do painel.",
    "lado_45": "Agora mais de lado: uns quarenta e cinco graus, como uma câmera perto do retrovisor.",
    "lado_60": "Agora bem de lado: uns sessenta graus, como uma câmera perto da porta.",
}

# (rótulo do trecho, instrução falada, duração em segundos depois da fala)
ROTEIRO = [
    ("neutro", "Olhe para a frente, como se estivesse dirigindo, por dez segundos.", 10),
    ("piscar", "Pisque normalmente por oito segundos.", 8),
    ("olho_fechado", "Feche os olhos e segure por três segundos.", 4),
    ("neutro", "Abra os olhos e olhe para a frente.", 4),
    ("fala", "Fale alguma coisa, naturalmente, por dez segundos.", 10),
    ("bocejo", "Agora boceje de verdade.", 8),
    ("neutro", "Olhe para a frente de novo.", 4),
    # distração com o rosto ainda visível (hoje só o rosto sumido gera aviso)
    ("olhar_lado", "Agora vire a cabeça e olhe para a janela do seu lado por cinco segundos.", 5),
    ("neutro", "Volte a olhar para a frente.", 4),
    ("cabeca", "Feche os olhos e deixe a cabeça pender para a frente por dois segundos.", 6),
]
FIM_POSICAO = "Posição concluída."
FPS = core.TARGET_FPS


def gravar_posicao(nome, destino, cv2, voz, rotulos):
    source = core.PicameraSource(want_display=False)
    escritor = cv2.VideoWriter(str(destino), cv2.VideoWriter_fourcc(*"MJPG"), FPS, core.LORES_SIZE)
    quadros = 0

    def grava_ate(fim_condicao, rotulo):
        nonlocal quadros
        ini = quadros / FPS
        while not fim_condicao():
            infer, _disp, _w, _h, _t = source.read()
            escritor.write(cv2.cvtColor(infer, cv2.COLOR_RGB2BGR))
            quadros += 1
            prev = cv2.cvtColor(infer, cv2.COLOR_RGB2BGR)
            cv2.putText(prev, f"{nome}: {rotulo}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.imshow("Gravacao de teste", prev)
            cv2.waitKey(1)
        rotulos.append((nome, rotulo, f"{ini:.3f}", f"{quadros / FPS:.3f}"))

    def falando(texto):
        th = threading.Thread(target=ef.speak_blocking, args=(texto, voz))
        th.start()
        return lambda: not th.is_alive()

    try:
        # o tempo da fala também é gravado, como "instrucao" (fica fora da avaliação)
        grava_ate(falando(POSICOES[nome]), "instrucao")
        for rotulo, fala, segundos in ROTEIRO:
            grava_ate(falando(fala), "instrucao")
            alvo = quadros + int(segundos * FPS)
            grava_ate(lambda: quadros >= alvo, rotulo)
        grava_ate(falando(FIM_POSICAO), "instrucao")
    finally:
        escritor.release()
        source.close()
    return quadros


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--posicoes", nargs="+", choices=list(POSICOES), default=list(POSICOES))
    args = ap.parse_args()
    import cv2
    import shutil

    voz = shutil.which("pw-play") or shutil.which("paplay") or shutil.which("ffplay")
    pasta = PASTA / time.strftime("%Y%m%d_%H%M")
    pasta.mkdir(parents=True, exist_ok=True)
    rotulos = []
    try:
        for i, nome in enumerate(args.posicoes):
            if i:
                ef.speak_blocking("Mude a posição da câmera e aperte Enter no teclado quando estiver pronto.", voz)
                input(f"\nPróxima posição: {nome}. Ajuste a câmera e aperte Enter... ")
            print(f"Gravando {nome}...")
            n = gravar_posicao(nome, pasta / f"{nome}.avi", cv2, voz, rotulos)
            print(f"  {n} quadros ({n / FPS:.0f} s)")
    finally:
        with open(pasta / "rotulos.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["posicao", "trecho", "inicio_s", "fim_s"])
            w.writerows(rotulos)
        cv2.destroyAllWindows()
    ef.speak_blocking("Gravação terminada. Obrigado.", voz)
    print(f"\nVídeos e rótulos em {pasta}")


if __name__ == "__main__":
    main()
