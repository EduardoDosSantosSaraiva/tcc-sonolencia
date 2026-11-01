# CENTRO UNIVERSITÁRIO DE BRASÍLIA

## ANTONIO EDUARDO DOS SANTOS SARAIVA

## LUCAS BALIZA BARRETO

# SISTEMA EMBARCADO DE DETECÇÃO DE SONOLÊNCIA DO MOTORISTA EM TEMPO REAL COM PROCESSAMENTO LOCAL EM RASPBERRY PI 5

### Brasília-DF

### 2026

---

## ANTONIO EDUARDO DOS SANTOS SARAIVA

## LUCAS BALIZA BARRETO

# SISTEMA EMBARCADO DE DETECÇÃO DE SONOLÊNCIA DO MOTORISTA EM TEMPO REAL COM PROCESSAMENTO LOCAL EM RASPBERRY PI 5

Trabalho de Conclusão de Curso apresentado ao curso de Engenharia da Computação como requisito parcial para obtenção do título de Engenheiro da Computação.

Orientador: Prof. Fábio de Oliveira Guimarães

### Brasília

### 2026

---

# FOLHA DE APROVAÇÃO

**Título do trabalho:** Sistema embarcado de detecção de sonolência do motorista em tempo real com processamento local em Raspberry Pi 5

**Autores:** Antonio Eduardo dos Santos Saraiva e Lucas Baliza Barreto

**Banca examinadora:**

\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
Orientador

\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
Membro da banca

\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
Membro da banca

Aprovado em: \_\_\_\_/\_\_\_\_/\_\_\_\_\_\_

---

# Resumo

A sonolência ao volante é uma causa subnotificada de acidentes: nas rodovias federais brasileiras, entre 2020 e 2025, os acidentes atribuídos ao condutor dormindo tiveram letalidade de 9,9 mortos por 100 ocorrências, acima da média geral de 8,4, e 40,5% deles ocorreram entre meia-noite e seis horas. Este trabalho desenvolveu e avaliou um sistema embarcado de detecção de sonolência do motorista que opera integralmente em um Raspberry Pi 5, sem transmitir dados para fora do dispositivo. O sistema obtém 478 marcos faciais por quadro com o MediaPipe, calcula a abertura dos olhos (EAR), o PERCLOS, a abertura da boca (MAR) e da mandíbula e a queda da cabeça, calibra os limiares para cada condutor ao ser ligado e emite alertas de voz distintos para sono, atenção, fadiga e distração. A avaliação combinou 44 testes automatizados, um protocolo de oito cenários guiado por voz, a base pública YawDD, com 349 vídeos e validação cruzada por participante, e a medição de desempenho na própria placa. O processamento levou 24,6 ms por quadro, cerca de 37 quadros por segundo, de forma estável ao longo de 20 minutos. Na YawDD, a sensibilidade na detecção de bocejos chegou a 86,1%, com precisão de 99,0%, e os alarmes de sono falsos caíram de 76 para 6 em relação à versão inicial. Os resultados indicam a viabilidade da detecção local em hardware de baixo custo; a validação com sonolência espontânea e em veículo permanece como trabalho futuro.

**Palavras-chave:** sonolência ao volante; visão computacional; sistemas embarcados; segurança viária; Raspberry Pi.

# Abstract

Drowsy driving is an underreported cause of road crashes: on Brazilian federal highways, from 2020 to 2025, crashes attributed to a sleeping driver had a fatality rate of 9.9 deaths per 100 crashes, above the overall average of 8.4, and 40.5% of them occurred between midnight and 6 a.m. This work developed and evaluated an embedded driver drowsiness detection system that runs entirely on a Raspberry Pi 5, without sending any data off the device. The system extracts 478 facial landmarks per frame with MediaPipe, computes eye opening (EAR), PERCLOS, mouth (MAR) and jaw opening, and head drop, calibrates its thresholds for each driver at startup, and issues distinct voice alerts for sleep, attention, fatigue and distraction. The evaluation combined 44 automated tests, a voice-guided protocol with eight scenarios, the public YawDD dataset, with 349 videos and participant-wise cross-validation, and performance measurements on the board itself. Processing took 24.6 ms per frame, about 37 frames per second, stable over 20 minutes. On YawDD, yawn detection reached 86.1% sensitivity with 99.0% precision, and false sleep alarms dropped from 76 to 6 compared with the initial version. The results indicate that local detection on low-cost hardware is feasible; validation with spontaneous drowsiness and in a vehicle remains as future work.

**Keywords:** driver drowsiness; computer vision; embedded systems; road safety; Raspberry Pi.

---

## Observações para revisão (não fazem parte do texto)

- **Orientador x coordenador:** você mencionou o professor Fábio de Oliveira Guimarães como coordenador. No TCC-modelo ele aparece como "Orientador: Fábio de Oliveira", e foi assim que registrei aqui. Se ele for coordenador do curso e o orientador for outra pessoa, é só me dizer que eu corrijo a folha de rosto.
- **Ordem dos autores:** listei na ordem em que você informou (Antonio Eduardo, depois Lucas). Muitos cursos pedem ordem alfabética — confirme com a coordenação.
- **Título:** o título longo é o sugerido. Alternativa mais enxuta, caso a banca prefira: "Detecção de sonolência do motorista em tempo real com visão computacional embarcada".
