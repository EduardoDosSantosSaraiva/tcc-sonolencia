# 2 FUNDAMENTAÇÃO TEÓRICA (rascunho)

> Material retirado da introdução durante o enxugamento para duas páginas. Está em estado de rascunho: os parágrafos abaixo já estão escritos e com as referências conferidas, e servem de ponto de partida para as seções do Capítulo 2. A numeração das citações precisará ser refeita quando o capítulo estiver montado.

## 2.x Abordagens para detecção de sonolência

Do ponto de vista técnico, as abordagens para detecção de sonolência costumam ser agrupadas em três famílias. A primeira baseia-se em sinais fisiológicos, como eletroencefalografia e eletrocardiografia, que oferecem alta acurácia, mas exigem eletrodos ou sensores de contato, sendo intrusivos para uso cotidiano. A segunda observa o comportamento do veículo (variação da posição na faixa, padrão de movimentação do volante), medida indireta e sensível ao traçado da via e ao estilo de condução. A terceira, adotada neste trabalho, apoia-se em sinais visuais e comportamentais captados por câmera, sem contato com o condutor. Nessa linha, a métrica PERCLOS (*percentage of eyelid closure over the pupil over time*) foi validada por Dinges e Grace, em estudo conduzido para a *Federal Highway Administration*, como o indicador mais confiável de alerta entre diversas alternativas avaliadas, incluindo medidas eletroencefalográficas. A extração desses sinais tornou-se computacionalmente viável com o uso de marcos faciais: Soukupová e Čech propuseram o *Eye Aspect Ratio* (EAR), escalar calculado a partir de seis pontos periorbitais que caracteriza a abertura do olho quadro a quadro, enquanto modelos como o *Face Mesh* do MediaPipe passaram a inferir centenas de pontos da superfície facial em tempo real a partir de vídeo monocular, com desempenho compatível com processadores móveis.

## 2.x Trabalhos relacionados e lacunas da literatura

Diversos trabalhos recentes exploram essa combinação. Essahraui *et al.* propuseram um sistema de detecção em tempo real baseado em análise facial e aprendizado de máquina, reportando desempenho elevado em bases públicas; Moredo, Celino e Ibarra implementaram, em um Raspberry Pi 4, um modelo LSTM alimentado por EAR, *Mouth Aspect Ratio* (MAR) e ângulos de pose da cabeça, treinado na base NTHU-DDD e operando a 10 quadros por segundo. No extremo oposto do espectro de recursos, Alajlan e Ibrahim investigaram a viabilidade de modelos TinyML quantizados para microcontroladores, destacando que dispositivos dessa classe possuem memória, autonomia e capacidade de processamento limitadas, o que inviabiliza os modelos convencionais, cujos tamanhos variam de 10 MB a 54 MB. Esse compromisso é central para o presente trabalho: plataformas de altíssima restrição, como o ESP32, obrigam a reduzir drasticamente a resolução e a complexidade do modelo, tipicamente classificando apenas o estado do olho em uma pequena região de interesse, ao passo que um computador de placa única como o Raspberry Pi 5 permite executar localmente a estimativa completa de marcos faciais e, com ela, a análise simultânea de olhos, boca e postura da cabeça.

## Referência adicional (saiu da introdução, usar aqui)

ESSAHRAUI, Siham *et al.* Real-time driver drowsiness detection using facial analysis and machine learning techniques. **Sensors**, v. 25, n. 3, art. 812, 2025. DOI: https://doi.org/10.3390/s25030812.

## O que ainda falta escrever no Capítulo 2

- Mecanismos fisiológicos da sonolência: ritmo circadiano, pressão homeostática de sono, microssono.
- Definição formal e fórmulas de EAR, MAR e PERCLOS, com as figuras dos pontos faciais usados.
- Estimativa de pose da cabeça pela matriz de transformação facial do MediaPipe.
- Arquitetura do MediaPipe Face Landmarker (detector de face + modelo de malha, 478 pontos com íris).
- Justificativa da escolha do Raspberry Pi 5 frente ao ESP32, com os números de memória e resolução.
