# Plano de melhorias do sistema (a partir de 20/10/2026)

Objetivo: deixar a detecção mais robusta e **provar** cada melhoria com dados.
Detecção "perfeita" não existe; o que dá para garantir é que cada mudança
seja medida antes de entrar no sistema e que nada piore. Regra para todas as
etapas: **só entra no programa o que melhorar nos vídeos E no autoteste, sem
piorar o teste de regressão.**

Situação de hoje: bocejo 86,1% no YawDD (F1 92%, confirmado na validação
cruzada), alarmes falsos de sono 6 em 2,7 h, autoteste 8/8, câmera corrigida
para 30 fps (antes rodava a ~7,7 fps por um erro de captura).

---

## Fase 0: ao religar o Pi (5 min)

- [ ] **Fonte de 5 V / 5 A.** O Pi registra subtensão desde que liga. No carro
  é obrigatório (a tensão cai na partida do motor).
- [ ] **Nada pesado junto das medições:** sem compilar o ESP32 e sem o Claude
  Desktop trabalhando enquanto o Pi mede desempenho.
- [ ] O monitoramento abre sozinho ao ligar (início automático). Para as
  medições longas, feche a janela dele (tecla **q**).

## Fase 1: validar o que mudou hoje (você, ~15 min, com o fone)

- [ ] **Autoteste a 30 fps** (ícone "TCC - Autoteste Guiado"): a rodada final
  do Cap. 4 precisa ser com a câmera corrigida.
- [ ] **Gravar os vídeos de ângulo** (ícone "TCC - Gravar vídeos de teste
  (ângulos)"): de frente, ~30°, ~45° e ~60°. Ter um jeito de girar a câmera
  (ou o corpo) entre as posições. Os vídeos ficam só no Pi.

## Fase 2: medições longas (o Pi sozinho, ~5 h, pode ser à noite)

- [ ] Rodar `./rodar_fila_estudo.sh` (ou me pedir):
  1. YawDD com as medidas novas (EAR/MAR em 3D, ângulo do rosto, brilho dos
     olhos), ~1 h.
  2. UTA-RLDD, 12 pessoas alerta / pouco vigilante / **com sono de verdade**,
     ~3 a 4 h. Baixa um vídeo por vez e apaga depois de medir.

## Fase 3: estudos e melhorias (eu faço; cada uma com critério de aceite)

| # | Melhoria | Por quê | Como saber se melhorou |
|---|---|---|---|
| 1 | **Rosto de lado: EAR e MAR em 3D** (já medidos, fora da decisão) | Em 2D, de lado, olho e boca "achatam": a 45° o MAR de quem só fala sobe ~40% e encosta no limiar do bocejo | Nos seus vídeos de ângulo, mais trechos certos de lado e nenhum a menos de frente; no YawDD, F1 do bocejo e alarmes falsos iguais ou melhores (validação cruzada) |
| 2 | **De lado: decidir pelo olho mais perto da câmera** | Acima de ~25° o olho de longe fica mal medido ou escondido, e a regra hoje exige os dois olhos fechados | Trecho "olho fechado" dispara o alarme a 45° e 60°; "piscar" e "neutro" sem alarme falso |
| 3 | **Aviso de distração por cabeça virada** | Hoje só avisa quando o rosto some; olhar o celular ou a janela com o rosto visível passa batido | Trecho "olhar para o lado" avisa em todos os ângulos; nenhum aviso nos trechos neutros nem no YawDD |
| 4 | **Sono real (UTA-RLDD)** | Fecha a maior limitação do TCC: até hoje todo microssono foi simulado | Na mesma pessoa, PERCLOS, piscadas longas e duração das piscadas sobem do vídeo "alerta" para o "com sono"; ajustar os limiares de PERCLOS e piscada longa com validação cruzada por pessoa |
| 5 | **Óculos escuros** | Grupo mais fraco no YawDD (76,9%) e do único bocejo falso | Estudar a razão de brilho (agora gravada) e o *blendshape* de piscar como alternativa ao EAR com lente |
| 6 | **Pouca luz** (realce de contraste já escrito, desligado) | Uso à noite | Medir em vídeos escuros antes de ligar; sem câmera infravermelha o ganho é limitado |
| 7 | **Regressão sempre** | Garantir que nenhuma melhoria piore outra coisa | `verificar_regressao.py` + autoteste depois de cada mudança |

## Fase 4: monografia

- [ ] 4.2: autoteste final a 30 fps.
- [ ] 4.5: desempenho (teste de resistência de 20/10).
- [ ] Seções novas no Cap. 4: rosto de lado e sono real (UTA-RLDD), se os
  resultados saírem a tempo.
- [ ] Cap. 5 (Conclusão), Resumo e Abstract.
- [ ] Revisar o tamanho do Cap. 3 com o orientador.
- [ ] Tornar o repositório do GitHub público antes da banca e pôr o link no texto.

## Hardware para o carro (depende de compra)

- Câmera NoIR + LEDs infravermelhos de 850 nm (noite e, possivelmente, óculos escuros).
- Fonte veicular de 5 V / 5 A.
- Suporte firme no painel atrás do volante ou perto do retrovisor (as duas
  posições do YawDD); evitar coluna e porta.

## Ordem sugerida

Fase 0 → Fase 1 → Fase 2 (à noite) → melhorias 1, 2 e 3 (usam os seus vídeos
e o YawDD) → melhoria 4 (UTA-RLDD) → monografia. As melhorias 5 e 6 só se
sobrar tempo antes da entrega.
