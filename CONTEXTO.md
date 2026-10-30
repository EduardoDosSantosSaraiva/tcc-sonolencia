# TCC: detecção de sonolência no Raspberry Pi 5 (contexto e instruções)

Atualizado em 20/10/2026, 16h. Projeto em `~/tcc_sonolencia/`, cópia no
GitHub (privado): https://github.com/EduardoDosSantosSaraiva/tcc-sonolencia

**Autores:** Antonio Eduardo dos Santos Saraiva e Lucas Baliza Barreto
(UniCEUB, Engenharia da Computação). Orientador: Prof. Fábio de Oliveira
Guimarães.

---

## 1. O que o sistema faz

Uma câmera filma o rosto do motorista. A cada quadro, o MediaPipe acha 478
pontos do rosto, e o programa calcula:

- **EAR:** abertura dos olhos. Olho fechado por 1,5 s dispara o **alarme de
  sono** (2,5 s se a pessoa estiver falando ou sorrindo).
- **PERCLOS:** porcentagem do último minuto com o olho fechado.
- **Piscadas longas:** 3 em 2 minutos dão aviso de atenção.
- **Bocejo:** pelos lábios (MAR) ou pela mandíbula. Gera aviso de fadiga e
  **nunca** dispara o alarme de sono.
- **Cabeça caindo:** com os olhos se fechando.
- **Distração:** rosto sumido por 3 s.
- **Óculos escuros:** detectados pelo brilho dos olhos; aí o sistema passa a
  vigiar a boca e a cabeça.

Tudo roda **dentro do Pi**, sem internet. Os alarmes são de voz, no fone
Bluetooth, com buzzer de reserva no GPIO26 se o fone cair. O sistema
**calibra sempre que liga**, durante 5 s olhando para a frente.

Feito em **Python** (5.300 linhas, ~97% do código; o resto são scripts de
atalho em Bash). O trabalho pesado fica com o MediaPipe, em C++: a parte em
Python é só ~6% do tempo de cada quadro.

## 2. Resultados até agora (números para o TCC)

| O quê | Resultado |
|---|---|
| Testes automáticos | 45 passando |
| Autoteste ao vivo (8 cenários) | 8/8 em 20/10 13h46, ainda com a câmera antiga |
| YawDD (349 vídeos, 2,7 h) | bocejo 86,1% (antes 46,1%); precisão 99%; F1 92,1% |
| Validação cruzada (pessoas não vistas) | F1 91,8%, ou seja, o ajuste não inflou o resultado |
| Alarmes de sono falsos no YawDD | 6 em 2,7 h (antes 76) |
| Desempenho (20 min) | 36,9 fps, 24,6 ms por quadro, 60 °C, 2,4 GHz estável |

## 3. O que foi feito em 20/10

1. **Monografia:** capítulos 2 (Fundamentação), 3 (Metodologia) e 4
   (Resultados) escritos, com 27 referências conferidas, Figura 1
   (arquitetura), gráficos (Figuras 2 a 4) e 6 quadros. PDF na Área de
   Trabalho: **"TCC - Capitulos 1 a 4.pdf"**.
2. **GitHub:** repositório privado criado. Tudo commitado e enviado.
3. **Medições do Cap. 4:** YawDD completo, validação cruzada, regressão e
   teste de resistência.
4. **Início automático:** o monitoramento abre sozinho quando o Pi liga
   (espera o fone conectar).
5. **Câmera corrigida para 30 fps.** Havia um erro antigo: o programa jogava
   fora metade dos quadros e rodava a ~7,7 fps. Agora a câmera usa o modo
   1296×972 travado em 30 fps, a mesma taxa dos vídeos do YawDD.
6. **Estudo do rosto de lado (começado):** EAR e MAR em 3D e ângulo do rosto,
   já medidos mas ainda fora da decisão; gravador de vídeos em vários
   ângulos; avaliador trecho a trecho.
7. **Sono real (UTA-RLDD):** download por pedaços pronto, um vídeo por vez,
   apagado depois de medido. Ainda não rodou.
8. **Plano de melhorias:** "TCC - Plano de melhorias.md" na Área de Trabalho.

## 4. Como usar (ícones da Área de Trabalho)

| Ícone | Para quê |
|---|---|
| **TCC - Detecção de Sonolência** | Abre o monitoramento (ele também abre sozinho ao ligar o Pi). Fecha com a tecla **q** |
| **TCC - Autoteste Guiado** | Os 8 cenários guiados por voz (~4 min, com o fone). Relatório em `autoteste_resultados/` |
| **TCC - Gravar vídeos de teste (ângulos)** | Grava você de frente, a 30°, 45° e 60° (~10 min). Aperte Enter entre as posições |

Comandos úteis (num terminal):

```
cd ~/tcc_sonolencia
./inicio_automatico.sh desligar        # parar de abrir sozinho ao ligar
./inicio_automatico.sh ligar           # voltar a abrir sozinho
./teste_resistencia.sh                 # teste de desempenho (20 min, Pi parado)
./rodar_fila_estudo.sh                 # YawDD 3D + UTA-RLDD (~5 h)
./venv/bin/python -m pytest -q         # testes automáticos
./venv/bin/python avaliar_angulos.py   # avalia os vídeos de ângulo gravados
cd monografia && python3 gerar_html.py && ../venv/bin/python gerar_pdf.py --texto   # refaz o PDF
```

Para a fila longa não morrer ao fechar o terminal:
`setsid nohup ./rodar_fila_estudo.sh > fila_estudo.log 2>&1 < /dev/null &`

## 5. O que falta (detalhes no plano)

1. **Fonte de 5 V / 5 A** (o Pi acusa subtensão).
2. **Autoteste a 30 fps**: é a rodada final do Cap. 4 (seção 4.2 marcada como pendente).
3. **Gravar os vídeos de ângulo.**
4. **Fila longa** (à noite): YawDD com medidas 3D + UTA-RLDD.
5. Melhorias, cada uma só entra se melhorar sem piorar nada:
   - rosto de lado (3D e olho mais perto);
   - aviso de cabeça virada;
   - ajuste com sono real;
   - pouca luz.
   (Óculos escuros: resolvido pela luz infravermelha da câmera; o modo óculos
   foi removido em 30/10/2026.)
6. Monografia: Cap. 5 (Conclusão), Resumo, Abstract; tornar o GitHub público
   antes da banca.
7. Hardware do carro: câmera NoIR + LEDs 850 nm, fonte veicular, suporte.

## 6. Cuidados importantes

- **Medição de desempenho só com o Pi parado.** Compilar o ESP32, o Claude
  Desktop trabalhando ou o navegador aberto derrubam o resultado (já
  estragaram três tentativas). O autoteste e o gravador pausam sozinhos as
  medições longas, mas o teste de resistência precisa do Pi livre.
- **Fone ligado antes** de rodar qualquer coisa com voz. Se a saída de som
  aparecer como "Dummy Output", o fone não está conectado.
- **Vídeos e fotos do rosto nunca vão para o GitHub** (dado biométrico, LGPD):
  `alarme_snapshots/`, `videos_proprios/`, o banco `sonolencia.db` e os vídeos
  do YawDD e da UTA-RLDD ficam só no Pi.
- **Não reiniciar o Pi com uma medição longa rodando:** ela não volta sozinha.
- Pastas de benchmark com `NAO_USAR.txt` são medições contaminadas: não usar.

## 7. Onde está cada coisa

| Caminho | O quê |
|---|---|
| `core.py` | medidas, calibração, regras de decisão, câmera |
| `eye_fatigue.py` | programa principal, alarmes, tela, autoteste |
| `test_core.py` | testes automáticos |
| `avaliar_yawdd.py`, `validacao_cruzada.py`, `verificar_regressao.py` | avaliação na base YawDD |
| `benchmark.py` | desempenho |
| `gravar_angulos.py`, `avaliar_angulos.py` | vídeos próprios de frente e de lado |
| `uta_rldd/` | base com sono real (UTA-RLDD) |
| `monografia/` | texto (.md), figuras e geradores de HTML/PDF |
| `PLANO_MELHORIAS.md` | o plano |
| `avaliacao_yawdd/`, `benchmark_resultados/`, `autoteste_resultados/`, `validacao_cruzada/` | resultados das medições |
