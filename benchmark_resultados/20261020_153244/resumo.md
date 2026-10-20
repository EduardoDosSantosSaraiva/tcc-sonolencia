# Benchmark — 20/10/2026 15:52

Entrada: vídeo `1-FemaleNoGlasses.avi` na velocidade máxima. Duração: 20.0 min. Quadros: 44253 (100.0% com rosto).

**FPS médio: 36.9** (por intervalo de 10 s: mín 34.0, máx 37.6)

| Tempo por quadro (ms) | média | mediana | p95 | p99 | máx |
|---|---|---|---|---|---|
| Só o MediaPipe | 23.2 | 22.8 | 24.9 | 26.7 | 113.4 |
| Quadro completo (até a decisão) | 24.6 | 24.2 | 26.5 | 28.6 | 129.8 |

## Temperatura e energia

Temperatura: início 57.3 °C, máxima 60.0 °C, fim 59.5 °C. Frequência do processador: mínima 2400 MHz, máxima 2400 MHz.

Registro do Pi no início: 0x50000: subtensão desde que ligou, velocidade reduzida desde que ligou.
Registro do Pi no fim: 0x50000: subtensão desde que ligou, velocidade reduzida desde que ligou.

## Tempo de reação ao olho fechado

O alarme cheio dispara quando o olho fica fechado por 1.5 s (2.5 s falando ou sorrindo). O processamento soma no pior caso comum (p95) 26 ms por quadro, mais o intervalo entre quadros. O atraso do áudio Bluetooth não entra aqui: é do fone, não do programa.
