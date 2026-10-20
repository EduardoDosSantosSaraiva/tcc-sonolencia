#!/bin/bash
# Gera os números do Capítulo 4, um de cada vez. O benchmark fica por último
# e sozinho: qualquer programa pesado rodando junto (navegador, PDF) derruba
# o FPS medido.
# Uso: setsid nohup ./rodar_fila_cap4.sh > fila_cap4.log 2>&1 < /dev/null &
cd "$(dirname "$0")"
YAWDD="$HOME/datasets/YawDD/YawDD dataset"
PY=./venv/bin/python
echo "== $(date '+%d/%m %H:%M') 1/4 YawDD completo, do zero (349 vídeos)"
$PY avaliar_yawdd.py "$YAWDD"
echo "== $(date '+%d/%m %H:%M') 2/4 verificação de regressão"
$PY verificar_regressao.py
echo "== $(date '+%d/%m %H:%M') 3/4 validação cruzada por participante"
$PY validacao_cruzada.py
echo "== $(date '+%d/%m %H:%M') 4/4 teste de resistência (60 min)"
$PY benchmark.py --video "$YAWDD/Dash/Female/1-FemaleNoGlasses.avi" --minutos 60
echo "== $(date '+%d/%m %H:%M') FIM"
