#!/bin/bash
# Atalho pro modo autoteste guiado por voz (os 8 cenários de protocolo_testes.md).
cd "$(dirname "$0")" || exit 1

# As medições pesadas em segundo plano (YawDD, validação cruzada) ocupam os
# 4 núcleos e derrubariam o FPS da câmera no meio do teste. Ficam pausadas
# enquanto ele roda e continuam sozinhas no fim. Os processos que fazem o
# trabalho são filhos (multiprocessing), então pausa a árvore toda.
arvore() { for p in "$@"; do echo "$p"; arvore $(pgrep -P "$p"); done; }
# só processos python rodando esses scripts (um shell que só cita o nome
# deles na linha de comando não pode entrar, ou pausaria a si mesmo)
pausados=$(arvore $(pgrep -f "bin/python[0-9.]* (avaliar_yawdd|validacao_cruzada)\.py"))
if [ -n "$pausados" ]; then
    echo "Pausando as medições em segundo plano enquanto o teste roda..."
    kill -STOP $pausados 2>/dev/null
    trap 'kill -CONT $pausados 2>/dev/null; echo "Medições em segundo plano retomadas."' EXIT
    trap 'exit 130' INT TERM HUP   # Ctrl+C ou janela fechada também retomam
fi
if pgrep -f "benchmark.py" >/dev/null; then
    echo "Atenção: o teste de resistência (benchmark.py) está rodando agora."
    echo "Rodar o autoteste junto estraga a medição dele."
    read -r -p "Enter para rodar mesmo assim, Ctrl+C para cancelar... " _
fi

# o monitoramento aberto no boot (iniciar_no_boot.sh) segura a câmera
monitor=$(pgrep -f "bin/python[0-9.]* eye_fatigue\.py")
if [ -n "$monitor" ]; then
    echo "O monitoramento está aberto e usando a câmera."
    read -r -p "Enter para fechá-lo e começar o autoteste, Ctrl+C para cancelar... " _
    kill -INT $monitor 2>/dev/null
    for _ in $(seq 1 20); do
        pgrep -f "bin/python[0-9.]* eye_fatigue\.py" >/dev/null || break
        sleep 0.5
    done
fi

./venv/bin/python eye_fatigue.py --modo-teste --no-log "$@"
status=$?
if [ $status -ne 0 ]; then
    echo
    echo "O programa fechou com erro (codigo $status). Veja a mensagem acima."
    read -r -p "Pressione Enter para fechar esta janela..." _
fi
exit $status
