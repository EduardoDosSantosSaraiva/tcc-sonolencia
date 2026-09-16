"""
Auxiliar de foco da câmera do TCC: mostra em tempo real uma medida de
nitidez (variância do Laplaciano — quanto maior, mais nítido) enquanto você
gira o anel de foco da lente manualmente. Não faz nada de detecção de
sonolência, só ajuda a resolver a câmera desfocada antes de confiar em
qualquer dado.

Uso:
    ./venv/bin/python focus_check.py             # janela com o valor ao vivo
    ./venv/bin/python focus_check.py --no-gui     # só imprime no terminal

Aponte a câmera pra um alvo com bastante detalhe/textura (ex.: uma folha
impressa, um rosto real) a ~40-60cm — a mesma distância de uso real — e gire
a lente devagar até o número parar de subir. "Melhor até agora" marca o pico
já visto na sessão, útil porque é fácil passar do ponto ideal sem perceber.
Aperte 'r' (ou Ctrl+C no modo texto) pra zerar o pico e recomeçar.
"""

import argparse
import os
import sys
import time

import core


WINDOW_NAME = "TCC - Ajuste de foco"


def sharpness(gray_frame, cv2):
    return cv2.Laplacian(gray_frame, cv2.CV_64F).var()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-gui", action="store_true", help="Não abre janela, só imprime no terminal")
    args = ap.parse_args()

    headless = args.no_gui or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if headless and not args.no_gui:
        print("Nenhum display gráfico detectado (sem $DISPLAY/$WAYLAND_DISPLAY). Modo texto.")

    try:
        source = core.PicameraSource(want_display=not headless)
    except ImportError:
        print("Picamera2 não está instalado. No Pi OS: sudo apt install python3-picamera2")
        sys.exit(1)
    except Exception as e:
        print(f"Não consegui abrir/configurar a câmera: {e}")
        sys.exit(1)

    import cv2

    best = 0.0
    last_printed = None
    print("Rodando. " + ("Pressione 'q' pra sair, 'r' pra zerar o pico." if not headless else "Ctrl+C pra sair."))

    try:
        while True:
            try:
                frame = source.read()
            except core.CaptureError as e:
                print(f"Falha ao capturar frame ({e}); tentando de novo...")
                time.sleep(0.05)
                continue

            infer_frame, display_frame, iw, ih, t = frame
            # usa o frame de maior resolução disponível (main) pra medir
            # nitidez — o lores da IA é baixo demais pra essa avaliação
            eval_frame = display_frame if display_frame is not None else infer_frame
            gray = cv2.cvtColor(eval_frame, cv2.COLOR_RGB2GRAY)
            score = sharpness(gray, cv2)
            best = max(best, score)

            if headless:
                label = f"[{t:6.1f}s] nitidez: {score:8.1f}   melhor ate agora: {best:8.1f}"
                if label != last_printed:
                    print(label)
                    last_printed = label
                continue

            display = cv2.cvtColor(eval_frame, cv2.COLOR_RGB2BGR)
            dh, dw = display.shape[:2]
            cv2.putText(display, f"Nitidez: {score:.1f}", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 0), 3)
            cv2.putText(display, f"Melhor ate agora: {best:.1f}", (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.putText(
                display, "Gire a lente ate estabilizar no maximo. 'r' zera o pico, 'q' sai.",
                (10, dh - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
            )
            # barra simples proporcional ao score atual vs melhor já visto
            bar_w = int(dw * 0.9)
            filled = int(bar_w * min(1.0, score / best)) if best > 0 else 0
            cv2.rectangle(display, (10, 100), (10 + bar_w, 120), (80, 80, 80), 2)
            cv2.rectangle(display, (10, 100), (10 + filled, 120), (0, 255, 0), -1)

            cv2.imshow(WINDOW_NAME, display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("r"):
                best = 0.0
            try:
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except cv2.error:
                break
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
    finally:
        source.close()
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass

    print(f"\nMelhor nitidez vista na sessão: {best:.1f}")


if __name__ == "__main__":
    main()
