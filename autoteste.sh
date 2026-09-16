#!/bin/bash
# Atalho pro modo autoteste guiado por voz (os 8 cenários de protocolo_testes.md).
cd "$(dirname "$0")" || exit 1
./venv/bin/python eye_fatigue.py --modo-teste --no-log "$@"
status=$?
if [ $status -ne 0 ]; then
    echo
    echo "O programa fechou com erro (codigo $status). Veja a mensagem acima."
    read -r -p "Pressione Enter para fechar esta janela..." _
fi
exit $status
