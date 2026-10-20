# Benchmark — 30/09/2026 14:33

Entrada: vídeo `1-FemaleNoGlasses.avi` na velocidade máxima. Duração: 1.3 min. Quadros: 2741 (100.0% com rosto).

**FPS médio: 36.3** (por intervalo de 10 s: mín 36.2, máx 36.6)

| Tempo por quadro (ms) | média | mediana | p95 | p99 | máx |
|---|---|---|---|---|---|
| Só o MediaPipe | 23.6 | 23.2 | 25.0 | 25.9 | 37.3 |
| Quadro completo (até a decisão) | 25.0 | 24.6 | 26.5 | 27.5 | 39.3 |

## Temperatura e energia

Temperatura: início 53.5 °C, máxima 54.5 °C, fim 54.5 °C. Frequência do processador: mínima 2400 MHz, máxima 2400 MHz.

Registro do Pi no início: 0x50000: subtensão desde que ligou, velocidade reduzida desde que ligou.
Registro do Pi no fim: 0x50000: subtensão desde que ligou, velocidade reduzida desde que ligou.

## Tempo de reação ao olho fechado

O alarme cheio dispara quando o olho fica fechado por 1.5 s (2.5 s falando ou sorrindo). O processamento soma no pior caso comum (p95) 26 ms por quadro, mais o intervalo entre quadros. O atraso do áudio Bluetooth não entra aqui: é do fone, não do programa.
