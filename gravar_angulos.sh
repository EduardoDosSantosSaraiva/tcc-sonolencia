#!/bin/bash
# Grava os vídeos de teste em várias posições da câmera (gravar_angulos.py),
# com o Pi livre: medições pesadas pausadas e o monitoramento fechado. Com o
# processador ocupado a gravação perderia quadros e os tempos anotados de
# cada trecho deixariam de bater com o vídeo.
cd "$(dirname "$0")" || exit 1
source ./preparar_camera.sh

./venv/bin/python gravar_angulos.py "$@"
status=$?
if [ $status -ne 0 ]; then
    echo
    echo "O programa fechou com erro (codigo $status). Veja a mensagem acima."
    read -r -p "Pressione Enter para fechar esta janela..." _
fi
exit $status
