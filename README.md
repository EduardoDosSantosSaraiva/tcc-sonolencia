# Detecção de sonolência do motorista em tempo real (Raspberry Pi 5)

Sistema embarcado que monitora o rosto do condutor por câmera e dispara alertas
sonoros ao identificar sinais de sonolência. **Todo o processamento acontece no
próprio dispositivo** — nenhuma imagem sai do Raspberry Pi e nada é enviado para
serviços externos.

Código desenvolvido para o Trabalho de Conclusão de Curso em Engenharia da
Computação do Centro Universitário de Brasília (UniCEUB), por Antonio Eduardo dos
Santos Saraiva e Lucas Baliza Barreto, sob orientação do Prof. Fábio de Oliveira
Guimarães (2026).

## Como funciona

A cada quadro, o [MediaPipe Face Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker)
estima 478 pontos da malha facial, dos quais são derivados quatro indicadores:

| Indicador | O que mede | Uso na decisão |
|---|---|---|
| **EAR** (*Eye Aspect Ratio*) | abertura dos olhos | olho fechado por ~1,5 s contínuos → alarme |
| **PERCLOS** | fração do tempo com olho fechado em 60 s | acima de 15% → eleva o nível de alerta |
| **MAR** (*Mouth Aspect Ratio*) | abertura da boca | bocejo (≥ 2 s e ≥ 2,2× a linha de base) → aviso de fadiga |
| **Inclinação da cabeça** | *pitch* pela matriz de transformação facial | cabeça caindo → eleva o nível de alerta |

Decisões de projeto que valem destaque:

- **Calibração individual a cada execução.** Os limiares de EAR, MAR e *pitch* são
  medidos na pessoa que está na frente da câmera, no início de cada sessão, com
  aviso prévio para ela se preparar. Nada é cacheado entre execuções: a montagem
  da câmera e a iluminação mudam, e um limiar absoluto erraria.
- **Bocejo nunca dispara o alarme máximo.** Fechar os olhos ao bocejar é reflexo
  comum; tratar isso como sono gerava falso positivo. Bocejo tem aviso próprio
  ("sinal de fadiga"), em nível separado do alarme de sono.
- **O alarme toca até o fim.** Se o condutor reabre os olhos no meio do áudio, o
  som não é interrompido — a função dele é acordar de verdade, não reagir ao
  estado instantâneo.
- **Óculos escuros.** A luz infravermelha da câmera atravessa a lente (escura ou
  polarizada), então o olho continua medido pelo EAR e o microssono dispara
  igual. Um modo que desligava o EAR com a região do olho escura existiu até
  30/10/2026 e foi removido: com infravermelho ele não era necessário e podia
  ligar sozinho num olho fechado, desligando o alarme principal.
- **Seleção do condutor.** Com até três rostos no quadro, o sistema acompanha o
  maior (mais próximo da câmera), ignorando passageiros.

## Hardware

- Raspberry Pi 5
- Câmera CSI (testado com OV5647; a IA processa em 640 × 480)
- Saída de áudio para os alertas (testado com fone Bluetooth via PipeWire)

## Instalação

```bash
sudo apt install -y python3-picamera2 libcamera-apps sox
git clone <URL-DO-REPOSITORIO> tcc_sonolencia
cd tcc_sonolencia
./baixar_modelos.sh                       # modelos do MediaPipe e do Piper
python3 -m venv --system-site-packages venv
./venv/bin/pip install -r requirements.txt
```

## Uso

```bash
./iniciar.sh                 # monitoramento normal (calibra e começa a vigiar)
./autoteste.sh               # autoteste guiado por voz pelos 8 cenários de teste
./venv/bin/python -m pytest  # testes automatizados da lógica de decisão
```

Ferramentas auxiliares:

```bash
./venv/bin/python focus_check.py      # ajuste do foco da lente (nitidez ao vivo)
./venv/bin/python benchmark.py        # latência e FPS do pipeline
./venv/bin/python resumo_sessao.py    # resumo de uma sessão gravada no banco
./venv/bin/python export_csv.py       # exporta as métricas para CSV
```

## Estrutura

```
core.py                 métricas, máquina de estados, câmera, banco de dados
eye_fatigue.py          aplicação principal, alarmes e modo de autoteste
interface.py            telas do monitoramento e do autoteste
test_core.py            46 testes da lógica de decisão e do preparo da imagem
test_interface.py       5 testes da interface
protocolo_testes.md     roteiro dos 8 cenários de validação
avaliar_yawdd.py        avaliação na base YawDD (bocejo e alarmes falsos)
validacao_cruzada.py    validação cruzada por participante na YawDD
verificar_regressao.py  confere se uma mudança nas regras piorou algum número
uta_rldd/               avaliação com sono real na base UTA-RLDD
benchmark.py            latência e taxa de quadros na placa
autoteste_resultados/   relatórios gerados pelo autoteste guiado
monografia/             texto do TCC, figuras e apuração dos dados da PRF
```

## Validação

- **Testes automatizados:** `./venv/bin/python -m pytest` (51 testes).
- **Protocolo guiado:** `protocolo_testes.md` define oito cenários: referência,
  piscada normal, piscada longa, microssono, fala normal, bocejo, queda de cabeça
  e óculos escuros. O `./autoteste.sh` narra cada cenário por voz, avalia
  sozinho se o sinal esperado foi detectado e salva o relatório em
  `autoteste_resultados/`.
- **YawDD** (349 vídeos gravados em carro; 90 pessoas no conjunto Mirror e 29
  no Dash): bocejo com
  sensibilidade de 86,1% e precisão de 99,0%; alarmes de sono falsos caíram de
  76 para 6 com as regras finais. A validação cruzada por participante dá F1 de
  91,8%, contra 92,1% na base inteira.
- **UTA-RLDD** (sono real autodeclarado, 58 pessoas com um vídeo alerta e um
  com sono): a piscada ficou mais longa com sono em 55 das 58, e o sistema,
  calibrado com a pessoa descansada, alertou em 43 dos 58 vídeos com sono,
  contra 10 dos 58 alertas.
- **Desempenho no Raspberry Pi 5:** 24,6 ms por quadro (36,9 quadros por
  segundo), estável por 20 minutos a cerca de 60 °C.

Os vídeos das bases não são redistribuídos aqui. Cada script explica como
baixá-los, e os números completos estão no Capítulo 4 da monografia.

## Privacidade

O sistema não transmite nada pela rede. No banco local (`sonolencia.db`) são
gravadas apenas métricas numéricas. Quando o alarme dispara, uma foto do momento
é salva em `alarme_snapshots/` como evidência para os testes — essa pasta **não é
versionada**, por conter dado biométrico (LGPD, art. 5º, II). Para desligar o
registro em banco, use `--no-log`.

## Licença

Código sob a licença MIT (arquivo `LICENSE`). As bases YawDD e UTA-RLDD têm
termos próprios e devem ser obtidas nas fontes originais.
