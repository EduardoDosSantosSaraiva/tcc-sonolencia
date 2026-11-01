#!/bin/bash
# Início automático do monitoramento quando o Pi liga. Chamado pela sessão
# gráfica (~/.config/autostart/tcc-sonolencia.desktop, criado por
# inicio_automatico.sh ligar), então já tem tela e áudio do usuário.
#
# 1. Tenta conectar o fone Bluetooth e espera por ele no máximo 15 s (as falas
#    da calibração saem melhor nele); depois abre assim mesmo.
# 2. Roda o monitoramento. Se ele cair por erro (câmera ocupada, travou),
#    tenta de novo em 5 s, até 10 vezes. Se o usuário fechar a janela (q),
#    para de vez.
# Tudo que o programa imprime vai para logs/boot.log.

cd "$(dirname "$0")" || exit 1
mkdir -p logs
exec >>logs/boot.log 2>&1
echo
echo "== $(date '+%d/%m/%Y %H:%M:%S') Pi ligado, iniciando o monitoramento"

fone_ativo() { wpctl inspect @DEFAULT_AUDIO_SINK@ 2>/dev/null | grep -q bluez; }
# fones pareados: dispositivos Bluetooth com ícone de fone/headset
fones=$(for mac in $(bluetoothctl devices Paired 2>/dev/null | awk '{print $2}'); do
            bluetoothctl info "$mac" 2>/dev/null | grep -qE "Icon: audio-(headset|headphones)" && echo "$mac"
        done)

# Tenta conectar os fones em segundo plano (até ~1 min) e só segura a abertura
# do monitoramento por ESPERA_FONE segundos. Antes o script esperava o fone
# antes de tudo, tentando um aparelho por vez com até 10 s cada: com a caixa
# desligada, a janela só aparecia ~3 min depois do login e parecia que o
# programa não tinha aberto. Se o fone conectar depois, o programa percebe
# sozinho (AudioOutputMonitor) e os alarmes passam a tocar nele.
ESPERA_FONE=15
(
    for _ in $(seq 1 6); do
        fone_ativo && exit 0
        for mac in $fones; do
            timeout 8 bluetoothctl connect "$mac" >/dev/null 2>&1 &
        done
        wait
        fone_ativo && { echo "== $(date '+%H:%M:%S') fone Bluetooth conectou"; exit 0; }
        sleep 2
    done
) &
for _ in $(seq 1 $ESPERA_FONE); do
    fone_ativo && break
    sleep 1
done
if fone_ativo; then
    echo "Fone Bluetooth conectado."
else
    echo "Fone Bluetooth ainda não conectou em $ESPERA_FONE s: abrindo assim mesmo (se ele conectar depois, os alarmes passam para ele)."
fi

for tentativa in $(seq 1 10); do
    ./venv/bin/python eye_fatigue.py
    status=$?
    if [ $status -eq 0 ]; then
        echo "== $(date '+%H:%M:%S') monitoramento encerrado pelo usuário"
        exit 0
    fi
    echo "== $(date '+%H:%M:%S') o programa saiu com erro (código $status), tentativa $tentativa de 10; reiniciando em 5 s"
    sleep 5
done
echo "== desisti depois de 10 tentativas; veja os erros acima"
