# 2 FUNDAMENTAÇÃO TEÓRICA

Este capítulo reúne os conceitos em que o sistema se apoia: os mecanismos fisiológicos da sonolência e os sinais comportamentais que ela produz, as abordagens existentes para detectá-la, as métricas visuais empregadas, a tecnologia de estimativa de marcos faciais e as plataformas embarcadas consideradas. Ao final, os trabalhos relacionados são confrontados para delimitar a lacuna que este trabalho procura preencher.

## 2.1 Sonolência e microssono na condução

A propensão ao sono é descrita classicamente pelo modelo de dois processos, segundo o qual ela resulta da interação entre um processo homeostático, que acumula pressão de sono ao longo da vigília e a dissipa durante o sono, e um processo circadiano, oscilação de aproximadamente 24 horas comandada pelo relógio biológico¹⁶. O risco ao volante é maior quando os dois efeitos se somam: a vigília prolongada eleva a pressão homeostática, e o vale circadiano da madrugada reduz a capacidade de compensá-la. Esse mecanismo é coerente com a concentração de acidentes por sono entre meia-noite e seis horas observada nos dados da PRF³ e com o prejuízo psicomotor comparável ao do álcool descrito por Dawson e Reid⁵.

A manifestação mais perigosa desse estado é o microssono, intrusão involuntária de sono durante a vigília com duração inferior a 15 segundos, situada em uma zona de transição entre os dois estados¹⁷. Durante o episódio, o condutor deixa de processar a informação visual da via, muitas vezes sem perceber o lapso. A 80 km/h, um microssono de dois segundos corresponde a cerca de 44 metros percorridos sem controle do veículo.

Antes de chegar a esse ponto, a sonolência produz sinais observáveis. O mais estudado é a alteração das piscadas. Caffier, Erdmann e Ullsperger, em sessões com 60 participantes, mostraram que a duração da piscada e o tempo de reabertura da pálpebra variam de forma confiável com o aumento da sonolência, e que a proporção de piscadas de fechamento longo também é um parâmetro informativo¹⁸. Schleicher *et al.*, com 129 participantes, apontaram a duração da piscada, o atraso na reabertura, o intervalo entre piscadas e a velocidade de fechamento da pálpebra como os melhores indicadores, e registraram diferenças individuais substanciais em todas essas variáveis¹⁹. Esse último achado tem consequência direta para o projeto: um limiar único, igual para todos os condutores, tende a errar, o que motiva a calibração individual descrita no Capítulo 3. Além das piscadas, a pessoa sonolenta apresenta movimentos faciais característicos, como o cabeceio (a cabeça que pende ou oscila) e os bocejos frequentes²⁰.

Esses sinais não têm o mesmo significado. O bocejo indica cansaço e costuma anteceder os episódios mais graves, mas não implica perda de consciência; o fechamento prolongado dos olhos, por outro lado, coincide com o próprio lapso. A distinção entre fadiga e sonolência efetiva fundamenta a hierarquia de alertas adotada neste trabalho.

## 2.2 Abordagens para detecção de sonolência

Sahayadhas, Sundaraj e Murugappan agrupam os métodos de detecção em três famílias²⁰. As medidas baseadas no veículo monitoram o desvio da posição na faixa, o movimento do volante e a pressão no acelerador; são indiretas e dependem de fatores externos, como a sinalização da via e as condições climáticas e de iluminação. As medidas fisiológicas, como a eletroencefalografia e a eletrocardiografia, apresentam a maior confiabilidade entre as três, mas exigem sensores em contato com o corpo, e sua natureza intrusiva permanece um obstáculo ao uso cotidiano. As medidas comportamentais, adotadas neste trabalho, observam por câmera o fechamento dos olhos, as piscadas, os bocejos e a pose da cabeça, sem contato com o condutor. Sua principal limitação, segundo os mesmos autores, é a iluminação: câmeras comuns têm desempenho ruim à noite, o que leva parte dos sistemas a empregar iluminação infravermelha ativa²⁰.

## 2.3 Indicadores visuais de sonolência

O *Eye Aspect Ratio* (EAR), proposto por Soukupová e Čech, estima a abertura do olho a partir de seis marcos posicionados ao seu redor: *p*~1~ e *p*~4~ nos cantos do olho e os pares (*p*~2~, *p*~6~) e (*p*~3~, *p*~5~) nas pálpebras superior e inferior¹⁰. A métrica é a razão entre as distâncias verticais e a distância horizontal, conforme a Equação 1.

$$ *EAR* = (‖*p*~2~ − *p*~6~‖ + ‖*p*~3~ − *p*~5~‖) / (2 · ‖*p*~1~ − *p*~4~‖) | (1)

Por ser uma razão entre distâncias medidas no próprio olho, o EAR não depende da distância entre o rosto e a câmera. Com o olho aberto, permanece aproximadamente constante; à medida que o olho fecha, aproxima-se de zero. Os autores ressalvam que a métrica é apenas parcialmente independente da pessoa e da pose da cabeça¹⁰, de modo que o valor de olho aberto varia de um condutor para outro, o que reforça a necessidade de calibração individual.

O PERCLOS (*percentage of eyelid closure over the pupil over time*) mede a fração de tempo, dentro de uma janela, em que a pálpebra cobre a pupila. Na variante P80, considera-se fechado o olho com pelo menos 80% da pupila coberta. Dinges e Grace compararam diversas medidas de alerta com o desempenho em uma tarefa de vigilância psicomotora e concluíram que o PERCLOS foi a mais confiável entre elas⁹. Sendo *t*~fechado~ o tempo com os olhos fechados em uma janela de duração *T*, a métrica é dada pela Equação 2.

$$ *PERCLOS* = (*t*~fechado~ / *T*) × 100% | (2)

O EAR e o PERCLOS são complementares. O primeiro, observado quadro a quadro, detecta um fechamento prolongado no momento em que ele ocorre; o segundo acumula fechamentos que, isoladamente, são curtos demais para indicar um lapso, mas que, somados ao longo de um minuto, revelam a pálpebra pesada típica da sonolência.

A mesma razão de seis pontos pode ser aplicada à boca, resultando no *Mouth Aspect Ratio* (MAR), empregado em sistemas de detecção como indicador de bocejo¹². Com *m*~1~ e *m*~4~ nos cantos da boca e os pares (*m*~2~, *m*~6~) e (*m*~3~, *m*~5~) no contorno interno dos lábios, o MAR é dado pela Equação 3.

$$ *MAR* = (‖*m*~2~ − *m*~6~‖ + ‖*m*~3~ − *m*~5~‖) / (2 · ‖*m*~1~ − *m*~4~‖) | (3)

A dificuldade do MAR está em separar o bocejo da fala. Falar também abre a boca, porém de forma breve e repetida, sílaba a sílaba, enquanto o bocejo abre a boca de forma ampla e sustentada por alguns segundos. A confusão é frequente o bastante para que a base pública YawDD, voltada à detecção de bocejo, inclua vídeos de condutores falando ou cantando ao volante, ao lado de vídeos de condução normal e de bocejos²¹.

O cabeceio, por fim, corresponde a uma rotação da cabeça para baixo (arfagem, ou *pitch*), causada pela perda do tônus muscular do pescoço no início do sono. Ele pode ser estimado pela pose tridimensional completa da cabeça, como no trabalho de Moredo, Celino e Ibarra¹², ou por relações geométricas mais simples entre marcos faciais, abordagem adotada neste trabalho e detalhada no Capítulo 3.

## 2.4 Estimativa de marcos faciais com o MediaPipe

O MediaPipe é um arcabouço de código aberto, desenvolvido pelo Google, para construção de *pipelines* de percepção, nos quais cada etapa do processamento de dados sensoriais, como vídeo e áudio, é um componente de um grafo²². Entre as soluções prontas do arcabouço está o *Face Landmarker*, que combina três modelos¹⁵: um detector facial, que localiza o rosto e alguns pontos-chave; um modelo de malha, que produz o mapeamento completo da face; e um modelo de *blendshapes*, que recebe a malha e prevê 52 coeficientes de expressão facial. O detector descende do BlazeFace, rede projetada para detectar rostos em menos de um milissegundo em processadores gráficos de dispositivos móveis²³. A malha segue a proposta de Kartynnik *et al.*, que infere em tempo real a geometria tridimensional da superfície facial a partir de vídeo monocular, com 468 pontos¹¹; a eles somam-se 10 pontos de íris, introduzidos por Ablavatski *et al.*²⁴, totalizando os 478 marcos tridimensionais devolvidos pela tarefa¹⁵.

Os *blendshapes* seguem um vocabulário de expressões nomeadas, cada uma com um coeficiente entre 0 e 1, como a abertura da mandíbula (*jawOpen*), o sorriso (*mouthSmileLeft* e *mouthSmileRight*) e o fechamento das pálpebras (*eyeBlinkLeft* e *eyeBlinkRight*). Diferentemente das razões geométricas, esses coeficientes são estimados por um modelo treinado e capturam movimentos que nem sempre alteram a distância entre dois pontos do contorno, como a descida da mandíbula com os lábios unidos ou cobertos pela mão.

A tarefa oferece três modos de execução: imagem isolada, quadros decodificados de vídeo e transmissão ao vivo¹⁵. No modo de vídeo, usado neste trabalho, cada quadro é enviado com uma marca de tempo crescente, e a tarefa aplica limiares de confiança distintos para a detecção do rosto e para o seu rastreamento entre quadros consecutivos. O modelo é executado na CPU, sem necessidade de acelerador dedicado.

## 2.5 Plataformas embarcadas

O Raspberry Pi 5 é um computador de placa única baseado no sistema em chip Broadcom BCM2712, com processador de quatro núcleos Arm Cortex-A76 de 64 bits a 2,4 GHz, unidade gráfica VideoCore VII e memória LPDDR4X disponível em versões de 1 GB a 16 GB. Dispõe de duas interfaces MIPI de quatro vias para câmera ou tela e é alimentado com 5 V e até 5 A por conector USB-C²⁵. Por executar um sistema operacional Linux completo, oferece as mesmas bibliotecas de um computador de mesa: interpretador Python, bibliotecas de visão computacional, pilha de câmera, áudio por Bluetooth e banco de dados local.

No extremo oposto estão os microcontroladores. O ESP32-S3, por exemplo, possui processador de dois núcleos Xtensa LX7 de 32 bits a até 240 MHz, com 512 KB de SRAM e 384 KB de ROM internas²⁶. Apenas o pacote de modelos do *Face Landmarker* utilizado neste trabalho ocupa cerca de 3,8 MB, mais de sete vezes a SRAM interna do chip. Alajlan e Ibrahim destacam que dispositivos dessa classe possuem memória, autonomia e capacidade de processamento limitadas, o que inviabiliza os modelos convencionais de detecção, cujos tamanhos variam de 10 MB a 54 MB, e exige modelos TinyML quantizados¹³. Na prática, isso obriga a reduzir drasticamente a resolução e a complexidade do modelo, que passa a classificar apenas o estado do olho em uma pequena região de interesse, sem a análise simultânea da boca e da cabeça. O Raspberry Pi 5, em contrapartida, permite executar localmente a estimativa completa dos marcos faciais, ao custo de maior preço e maior consumo de energia que um microcontrolador, compromisso considerado aceitável para um equipamento instalado no veículo e alimentado por ele.

## 2.6 Trabalhos relacionados e lacunas da literatura

Diversos trabalhos recentes combinam marcos faciais e aprendizado de máquina para detectar sonolência. Essahraui *et al.* propuseram um sistema de detecção em tempo real baseado em análise facial e aprendizado de máquina, com desempenho elevado em bases públicas²⁷. Moredo, Celino e Ibarra implementaram, em um Raspberry Pi 4, um modelo LSTM alimentado por EAR, MAR e ângulos de pose da cabeça, treinado na base NTHU-DDD e operando a 10 quadros por segundo¹². No outro extremo do espectro de recursos, Alajlan e Ibrahim investigaram a viabilidade de modelos TinyML quantizados para microcontroladores¹³.

A comparação entre esses trabalhos evidencia lacunas que orientam o presente estudo. A primeira é a ênfase na acurácia de classificadores sobre vídeos previamente gravados, com pouca descrição do sistema em operação: como os limiares se ajustam a cada condutor, que alerta é emitido para cada sinal e como o alarme chega efetivamente ao motorista. A segunda é a variabilidade individual, documentada na literatura fisiológica¹⁹, raramente tratada por calibração no próprio uso. A terceira são as situações cotidianas que produzem sinais semelhantes aos da sonolência sem que o condutor esteja sonolento, como falar, rir, olhar para o painel ou usar óculos escuros, e que, se ignoradas, geram alarmes falsos e levam o motorista a desligar o equipamento. A quarta é a escassez de medições de desempenho na própria plataforma-alvo, com latência e estabilidade térmica em uso prolongado. Este trabalho procura cobrir essas lacunas com um sistema completo, executado integralmente no Raspberry Pi 5, cuja construção é descrita no capítulo seguinte.

# REFERÊNCIAS BIBLIOGRÁFICAS

> Referências citadas pela primeira vez neste capítulo. A numeração continua a da introdução (1 a 15); na montagem do PDF, todas formam uma única lista ao final do texto.

16. BORBÉLY, Alexander A.; DAAN, Serge; WIRZ-JUSTICE, Anna; DEBOER, Tom. The two-process model of sleep regulation: a reappraisal. **Journal of Sleep Research**, v. 25, n. 2, p. 131–143, 2016. DOI: https://doi.org/10.1111/jsr.12371.

17. HERTIG-GODESCHALK, Anneke *et al.* Microsleep episodes in the borderland between wakefulness and sleep. **Sleep**, v. 43, n. 1, art. zsz163, 2020. DOI: https://doi.org/10.1093/sleep/zsz163.

18. CAFFIER, Philipp P.; ERDMANN, Udo; ULLSPERGER, Peter. Experimental evaluation of eye-blink parameters as a drowsiness measure. **European Journal of Applied Physiology**, v. 89, n. 3–4, p. 319–325, 2003. DOI: https://doi.org/10.1007/s00421-003-0807-5.

19. SCHLEICHER, Robert; GALLEY, Niels; BRIEST, Susanne; GALLEY, Lars. Blinks and saccades as indicators of fatigue in sleepiness warnings: looking tired? **Ergonomics**, v. 51, n. 7, p. 982–1010, 2008. DOI: https://doi.org/10.1080/00140130701817062.

20. SAHAYADHAS, Arun; SUNDARAJ, Kenneth; MURUGAPPAN, Murugappan. Detecting driver drowsiness based on sensors: a review. **Sensors**, v. 12, n. 12, p. 16937–16953, 2012. DOI: https://doi.org/10.3390/s121216937.

21. ABTAHI, Shabnam; OMIDYEGANEH, Mona; SHIRMOHAMMADI, Shervin; HARIRI, Behnoosh. YawDD: a yawning detection dataset. In: **Proceedings of the 5th ACM Multimedia Systems Conference (MMSys '14)**. Singapore: ACM, 2014. p. 24–28. DOI: https://doi.org/10.1145/2557642.2563678.

22. LUGARESI, Camillo *et al.* MediaPipe: a framework for building perception pipelines. **arXiv preprint** arXiv:1906.08172, 2019. DOI: https://doi.org/10.48550/arXiv.1906.08172.

23. BAZAREVSKY, Valentin; KARTYNNIK, Yury; VAKUNOV, Andrey; RAVEENDRAN, Karthik; GRUNDMANN, Matthias. BlazeFace: sub-millisecond neural face detection on mobile GPUs. **arXiv preprint** arXiv:1907.05047, 2019. DOI: https://doi.org/10.48550/arXiv.1907.05047.

24. ABLAVATSKI, Artsiom; VAKUNOV, Andrey; GRISHCHENKO, Ivan; RAVEENDRAN, Karthik; ZHDANOVICH, Matsvei. Real-time pupil tracking from monocular video for digital puppetry. **arXiv preprint** arXiv:2006.11341, 2020. DOI: https://doi.org/10.48550/arXiv.2006.11341.

25. RASPBERRY PI LTD. **Raspberry Pi 5**: product brief. Cambridge: Raspberry Pi Ltd, 2026. Disponível em: https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-product-brief.pdf. Acesso em: 20 out. 2026.

26. ESPRESSIF SYSTEMS. **ESP32-S3 series datasheet**. Versão 2.2. Shanghai: Espressif Systems, 2026. Disponível em: https://documentation.espressif.com/esp32-s3_datasheet_en.pdf. Acesso em: 20 out. 2026.

27. ESSAHRAUI, Siham *et al.* Real-time driver drowsiness detection using facial analysis and machine learning techniques. **Sensors**, v. 25, n. 3, art. 812, 2025. DOI: https://doi.org/10.3390/s25030812.

---

## Observações para revisão (não fazem parte do texto)

- **Origem:** o rascunho `02_fundamentacao_rascunho.md` (material que saiu da introdução) foi incorporado nas seções 2.2 e 2.6 e removido.
- **Tamanho:** cerca de 2.000 palavras, acima das 1.420 do Cap. 2 do TCC-modelo, porque cobre fisiologia, métricas, MediaPipe e plataforma.
- **Referências conferidas uma a uma:** DOIs na Crossref, resumos no PubMed (Caffier, Schleicher, Sahayadhas), metadados no arXiv (Lugaresi, Bazarevsky, Ablavatski) e especificações nos datasheets oficiais do Raspberry Pi 5 e do ESP32-S3 (lidos em 20/10/2026). As afirmações atribuídas a cada fonte constam dos resumos ou do texto completo.
- **Referência 15 (MediaPipe):** o endereço ai.google.dev agora redireciona para developers.google.com/edge/mediapipe; o conteúdo citado (478 pontos, 52 *blendshapes*, três modelos, três modos) foi conferido no endereço novo.
- **Figuras sugeridas para este capítulo:** os seis pontos do EAR sobre um olho e a curva do EAR ao longo de uma piscada (dá para gerar a partir de uma sessão gravada no `sonolencia.db`).
