#!/bin/bash
# Teste de resistência do Cap. 4: 60 min processando um vídeo do YawDD na
# velocidade máxima. Rodar com o Pi parado (sem navegador, compilação ou o
# monitoramento aberto): qualquer coisa pesada junto derruba o FPS medido.
cd "$(dirname "$0")" || exit 1
if pgrep -f "bin/python[0-9.]* eye_fatigue\.py" >/dev/null; then
    echo "Feche o monitoramento (tecla q na janela) antes de rodar o teste."
    exit 1
fi
./venv/bin/python benchmark.py --video "$HOME/datasets/YawDD/YawDD dataset/Dash/Female/1-FemaleNoGlasses.avi" --minutos 60
