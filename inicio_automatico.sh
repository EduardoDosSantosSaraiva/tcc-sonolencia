#!/bin/bash
# Liga ou desliga o início automático do monitoramento quando o Pi liga.
#   ./inicio_automatico.sh ligar
#   ./inicio_automatico.sh desligar
#   ./inicio_automatico.sh          (mostra se está ligado)
AQUI="$(cd "$(dirname "$0")" && pwd)"
ATALHO="$HOME/.config/autostart/tcc-sonolencia.desktop"

case "$1" in
    ligar)
        mkdir -p "$(dirname "$ATALHO")"
        cat > "$ATALHO" <<EOF
[Desktop Entry]
Type=Application
Name=TCC - Detecção de Sonolência (início automático)
Comment=Abre o monitoramento do motorista quando o Raspberry Pi liga
Exec=$AQUI/iniciar_no_boot.sh
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
        echo "Início automático LIGADO: o monitoramento abre sozinho na próxima vez que o Pi ligar."
        ;;
    desligar)
        rm -f "$ATALHO"
        echo "Início automático DESLIGADO."
        ;;
    *)
        if [ -f "$ATALHO" ]; then echo "Início automático está LIGADO ($ATALHO)."
        else echo "Início automático está DESLIGADO."; fi
        ;;
esac
