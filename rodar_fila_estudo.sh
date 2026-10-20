#!/bin/bash
# Medições do estudo de melhoria (20/10), uma depois da outra:
#   1. YawDD do zero, com as colunas novas (EAR/MAR em 3D, ângulo do rosto,
#      brilho dos olhos) para comparar 2D x 3D
#   2. UTA-RLDD, 12 pessoas (Fold1), sono de verdade
# O autoteste e a gravação de vídeos pausam esta fila sozinhos.
# Uso: setsid nohup ./rodar_fila_estudo.sh > fila_estudo.log 2>&1 < /dev/null &
cd "$(dirname "$0")" || exit 1
PY=./venv/bin/python
echo "== $(date '+%d/%m %H:%M') 1/2 YawDD com medidas 3D (349 vídeos)"
$PY avaliar_yawdd.py "$HOME/datasets/YawDD/YawDD dataset"
echo "== $(date '+%d/%m %H:%M') 2/2 UTA-RLDD Fold1 (12 pessoas)"
(cd uta_rldd && ../venv/bin/python processar_uta.py Fold1_part1 Fold1_part2)
echo "== $(date '+%d/%m %H:%M') FIM"
