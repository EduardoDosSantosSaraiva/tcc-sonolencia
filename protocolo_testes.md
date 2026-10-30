# Protocolo de testes — TCC detecção de sonolência

Metodologia de validação do protótipo, pra virar evidência (não só "testei e
pareceu certo") no capítulo de resultados da monografia.

## Como testar

1. Rode `./venv/bin/python eye_fatigue.py` (calibra sempre no início — aguarde
   o aviso de "prepare-se" antes de fazer a pose neutra).
2. Execute o cenário descrito na tabela abaixo.
3. Ao terminar (ou pressione `q` pra fechar), rode:
   `./venv/bin/python resumo_sessao.py sonolencia.db`
4. Preencha a linha correspondente na seção **Resultados** com o que o
   resumo mostrou, comparando com o **resultado esperado** da tabela.
5. Se o cenário 4 (microssono) disparar o alarme cheio, confira o print
   salvo em `alarme_snapshots/` — cite o arquivo como evidência.

Atalho: `./autoteste.sh` roda os 8 cenários guiado por voz e salva o
relatório em `autoteste_resultados/`. Pra repetir só alguns cenários:
`./autoteste.sh --cenario 7` (ou `--cenario 4 6 7`).

Repita cada cenário 2-3 vezes em dias/horários diferentes, se possível —
uma única execução não é suficiente pra afirmar uma taxa de acerto.

## Cenários

| # | Cenário | Procedimento | Resultado esperado |
|---|---|---|---|
| 1 | Baseline | Comportamento normal, olhos abertos, ~2min | Nenhum alarme; nível `aberto` na maior parte do tempo |
| 2 | Piscada normal | Piscar normalmente várias vezes ao longo de ~1min | Nenhum evento `piscada_longa` |
| 3 | Piscada longa | Fechar o olho por ~0,8s e reabrir | 1 evento `piscada_longa`; **sem** alarme cheio |
| 4 | Microssono | Fechar o olho e segurar 2s+ | Nível `fechado` (alarme cheio); print salvo em `alarme_snapshots/` |
| 5 | Fala normal | Conversar olhando pra câmera por ~30s | Nenhum evento `bocejo` |
| 6 | Bocejo real | Bocejar de verdade (mesmo fechando o olho) | 1 evento `bocejo`; nível `atencao`; **nunca** `fechado` |
| 7 | Cabeça caindo | Fechar os olhos e deixar a cabeça pender pra frente por 2s (cochilo) | 1 evento `cabeca_caindo` (só se a calibração de pitch tiver rodado). Desde 30/09 cabeça baixa de olho aberto não conta: é o motorista olhando o painel, não sono |
| 8 | Óculos | De óculos (escuro ou polarizado), olhar ~5s e depois fechar o olho por 3s | Alarme `fechado` dispara: a luz infravermelha da câmera atravessa a lente e o EAR enxerga o olho. (O antigo "modo óculos escuros", que parava de olhar o olho quando a região ficava escura, foi removido em 30/10/2026: com infravermelho a lente não esconde o olho, e o modo quase ligou num olho fechado sem óculos, o que desligaria o alarme principal.) |

## Resultados

### Cenário 1 — Baseline

| Data | Sessão (nº) | Duração | Alarmes falsos? | % tempo `aberto` | Passou? | Observações |
|---|---|---|---|---|---|---|
| | | | | | | |

### Cenário 2 — Piscada normal

| Data | Sessão (nº) | `piscada_longa` disparado? | Passou? | Observações |
|---|---|---|---|---|
| | | | | |

### Cenário 3 — Piscada longa

| Data | Sessão (nº) | `piscada_longa` contado? | Alarme cheio disparou (não devia)? | Passou? | Observações |
|---|---|---|---|---|---|
| | | | | | |

### Cenário 4 — Microssono

| Data | Sessão (nº) | Alarme cheio disparou? | Tempo até disparar (s) | Print salvo? (nome do arquivo) | Passou? | Observações |
|---|---|---|---|---|---|---|
| | | | | | | |

### Cenário 5 — Fala normal

| Data | Sessão (nº) | `bocejo` disparado à toa? | Passou? | Observações |
|---|---|---|---|---|
| | | | | |

### Cenário 6 — Bocejo real

| Data | Sessão (nº) | `bocejo` contado? | Nível chegou a `fechado`? (não devia) | Passou? | Observações |
|---|---|---|---|---|---|
| | | | | | |

### Cenário 7 — Cabeça caindo

| Data | Sessão (nº) | Pitch calibrado? | `cabeca_caindo` contado? | Passou? | Observações |
|---|---|---|---|---|---|
| | | | | | |

### Cenário 8 — Óculos

| Data | Sessão (nº) | Tipo de lente | Alarme `fechado` disparou? | Passou? | Observações |
|---|---|---|---|---|---|
| | | | | | |

## Resumo geral (preencher ao final de todas as rodadas)

- Total de sessões testadas:
- Taxa de acerto por cenário:
- Falsos positivos observados (situação + frequência):
- Falsos negativos observados (situação + frequência):
- Limitações identificadas durante os testes:
