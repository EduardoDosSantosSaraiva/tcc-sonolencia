#!/bin/bash
# Início automático do monitoramento quando o Pi liga. Chamado pela sessão
# gráfica (~/.config/autostart/tcc-sonolencia.desktop, criado por
# inicio_automatico.sh ligar), então já tem tela e áudio do usuário.
#
# 1. Espera o fone Bluetooth conectar (até 60 s): sem ele, as falas da
#    calibração e os alarmes não seriam ouvidos.
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

for _ in $(seq 1 12); do
    fone_ativo && break
    for mac in $fones; do
        timeout 10 bluetoothctl connect "$mac" >/dev/null 2>&1
    done
    fone_ativo && break
    sleep 5
done
if fone_ativo; then
    echo "Fone Bluetooth conectado."
else
    echo "Fone Bluetooth não conectou em 60 s: alarmes na saída padrão e no buzzer de reserva."
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
