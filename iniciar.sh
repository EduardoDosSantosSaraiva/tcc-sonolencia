#!/bin/bash
# Atalho pra iniciar o protótipo de detecção de sonolência do TCC.
# Ativa o venv do projeto e roda a câmera ao vivo com calibração e alarme.
cd "$(dirname "$0")" || exit 1
./venv/bin/python eye_fatigue.py "$@"
status=$?
if [ $status -ne 0 ]; then
    echo
    echo "O programa fechou com erro (codigo $status). Veja a mensagem acima."
    read -r -p "Pressione Enter para fechar esta janela..." _
fi
exit $status
