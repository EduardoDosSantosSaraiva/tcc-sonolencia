#!/bin/bash
# Mede a UTA-RLDD (60 pessoas, em 10 zips no Google Drive; Fold1 primeiro,
# alerta e sonolento de todas antes do pouco vigilante). Roda durante a
# noite: baixa um vídeo por vez e apaga depois de medir (fica só a série de
# números em uta_rldd/series/). Pode fechar esta janela: o processamento
# continua sozinho. Se o Google bloquear um zip (cota), passa para outro e
# volta nele em 30 min. Se for interrompido (Pi desligado, sem internet), rodar
# de novo continua de onde parou (uta_rldd/progresso.csv).
# Progresso: tail -f ~/tcc_sonolencia/fila_uta.log
cd "$(dirname "$0")" || exit 1

if pgrep -f "bin/python[0-9.]* processar_uta\.py" >/dev/null; then
    echo "A medição já está rodando. Últimas linhas do registro:"
    tail -n 5 fila_uta.log
    read -r -p "Enter para fechar esta janela..." _
    exit 0
fi

# o monitoramento aberto no boot disputaria o processador a noite toda
monitor=$(pgrep -f "bin/python[0-9.]* eye_fatigue\.py")
if [ -n "$monitor" ]; then
    echo "Fechando o monitoramento para liberar o processador..."
    kill -INT $monitor 2>/dev/null
    for _ in $(seq 1 20); do
        pgrep -f "bin/python[0-9.]* eye_fatigue\.py" >/dev/null || break
        sleep 0.5
    done
fi

echo "== $(date '+%d/%m %H:%M') UTA-RLDD (60 pessoas; Fold1 primeiro, pula pacote bloqueado pela cota)" >> fila_uta.log
(cd uta_rldd && setsid nohup ../venv/bin/python processar_uta.py \
    >> ../fila_uta.log 2>&1 < /dev/null &)
sleep 10
echo
tail -n 4 fila_uta.log
echo
echo "Começou. Deixe o Pi ligado e com internet até de manhã; pode fechar esta janela."
read -r -p "Enter para fechar..." _
