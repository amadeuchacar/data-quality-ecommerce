# 6. Execução e testes

> [Índice](README.md) · Anterior: [5. Configuração](05-configuracao.md) · Próximo: [7. Guia de estudo](07-guia-de-estudo.md)

## 6.1 Pré-requisitos e instalação

- Python 3.11 ou superior
- [Poetry](https://python-poetry.org/) 2.x
- Os 9 CSVs do Olist em `data/raw/`

```bash
poetry install
```

O projeto usa `package-mode = false`: o Poetry instala as dependências, mas não empacota o projeto. Dependências de execução: pandas, pyarrow e pyyaml. Dependências de desenvolvimento: pytest, ruff e kaggle.

**Download dos dados** (detalhes em `scripts/download_instructions.md`):

```bash
# autentique antes: poetry run kaggle auth login (ou um KAGGLE_API_TOKEN; o .env não é lido)
poetry run kaggle datasets download -d olistbr/brazilian-ecommerce -p data/raw --unzip
```

## 6.2 Executando

### Tudo de uma vez

```bash
poetry run python -m orchestration.local_pipeline
```

Saída resumida de uma execução limpa:

```
STEP 1/6 - Ingestion + Contract Validation
[ingestion] completed: 9/9 tables processed successfully.
STEP 2/6 - DAMA Quality Dimensions (staging)
STEP 3/6 - Observability Pillars + SLOs
[checks_runner] SLOs: 5/5 meeting objectives.
STEP 4/6 - Certification
[certify] 9/9 tables certified.
STEP 5/6 - Curated Layer + Field Lineage
[build_curated] order_items -> item_total_value ... ok
[build_curated] orders -> delivery_delay_days, order_total_value ... ok
STEP 6/6 - Maturity Dashboard
[maturity_dashboard] SLOs met: 5/5 | certified tables: 9/9
SUMMARY
Tables blocked at ingestion: none
SLOs breached: none
Certified tables: 9/9
```

> **Primeiras execuções:** os pilares de volume e distribution precisam de 3 execuções anteriores. Até lá, eles respondem `insufficient_history`, e a certificação fica negada por falta de evidência. Isso é esperado (D9). Rode a ingestão algumas vezes para formar a linha de base.

### Etapa por etapa

| Comando | O que faz | Exit code |
|---|---|---|
| `poetry run python -m src.ingestion.load_raw` | Ingestão + contrato + snapshot | 1 se alguma tabela for barrada |
| `poetry run python -m src.quality.dimensions_dama` | Scorecard DAMA sobre o staging | sempre 0 |
| `poetry run python -m src.quality.checks_runner` | Pilares, SLOs, alertas e incidentes | 1 se algum SLO quebrar |
| `poetry run python -m src.certification.certify` | Certificação | sempre 0 |
| `poetry run python -m src.transform.build_curated` | Camada curada | sempre 0 |
| `poetry run python -m src.reporting.maturity_dashboard` | Dashboard | sempre 0 |
| `poetry run python -m src.incidents.cli <subcomando>` | Processo humano de incidentes | 1 em erro de uso |

Sempre use `-m` a partir da raiz do projeto: os imports são do tipo `from src.ingestion...`.

## 6.3 Onde ficam as saídas

| Arquivo | Gerado por | Conteúdo |
|---|---|---|
| `data/staging/<tabela>.parquet` | ingestão | Dado aprovado e tipado |
| `data/curated/*_curated.parquet` | camada curada | Tabelas com campos derivados |
| `reports/ingestion_logs/run_<ts>.json` | ingestão | Log técnico completo, por tabela |
| `reports/dq_scorecards/contract_report_<ts>.md` | ingestão | Resumo e violações de contrato |
| `reports/dq_scorecards/dama_scorecard_<ts>.json` / `.md` | DAMA | Dimensões por tabela e coluna |
| `reports/dq_scorecards/pillars_report_<ts>.md` | runner | SLOs e pilares por tabela |
| `reports/quality_history/<tabela>.jsonl` | ingestão | Um snapshot por execução aprovada |
| `reports/alerts/alerts.jsonl` | runner | Um alerta por linha |
| `reports/incidents/incidents.jsonl` | runner | Incidentes, com TTD |
| `reports/incidents/resolutions.jsonl` | CLI | Resoluções, com TTR |
| `reports/incidents/rca.jsonl` | CLI | Análises de causa raiz |
| `reports/incidents/postmortems/<id>.md` | CLI | Postmortems |
| `reports/certification/certification_status.yaml` | certificação | Último status por tabela |
| `reports/maturity_dashboard/dashboard_<ts>.md` | dashboard | KPIs de maturidade |

## 6.4 Testes automatizados e lint

```bash
poetry run pytest                                    # 172 testes passam; 6 são pulados de propósito
poetry run pytest tests/test_pillars.py              # um arquivo
poetry run pytest -k freshness                       # testes cujo nome contém "freshness"
poetry run pytest -v                                 # nome de cada teste
poetry run ruff check .                              # lint
```

Os 6 testes pulados são de tabelas sem `primary_key` declarada no contrato. Os testes **não leem** `data/raw/`: rodam em menos de 1 segundo e funcionam sem baixar o dataset. A descrição de cada arquivo de teste está em [4.21](04-referencia-de-codigo.md#421-tests).

## 6.5 Simulando incidentes

O simulador (`scripts/simulate_incident.py`) altera um CSV bruto por vez para exercitar uma defesa específica. Ele copia `data/raw/` para `data/raw_backup/` antes da primeira alteração.

### Roteiro

```bash
# 0. Linha de base: pelo menos 4 execuções limpas
poetry run python -m src.ingestion.load_raw          # repita 3x
poetry run python -m orchestration.local_pipeline    # deve terminar com 9/9 certificadas

# 1. Ver os cenários
poetry run python -m scripts.simulate_incident list

# 2. Aplicar um cenário e rodar o pipeline
poetry run python -m scripts.simulate_incident volume_drop
poetry run python -m orchestration.local_pipeline

# 3. Restaurar os dados originais e rodar de novo
poetry run python -m scripts.simulate_incident restore
poetry run python -m orchestration.local_pipeline

# 4. Fechar o incidente (ver 6.6)
```

> **Atenção:** a simulação deixa marcas no seu histórico (`reports/`). Para estudar sem afetar seu estado atual, faça um backup de `reports/` antes ou use uma cópia do projeto. Para recomeçar do zero, veja 6.9.

### Resultados observados

Os resultados abaixo foram obtidos numa cópia do projeto, com 4 execuções limpas de linha de base e os cenários aplicados em sequência.

**`contract_violation`** (10 preços negativos em `order_items`)

```
[ingestion] Processing 'order_items' ... contract_violation
[ingestion] completed: 8/9 tables processed successfully.
[curated] SKIPPED: source tables blocked or missing in this ingestion: ['order_items'].
Tables blocked at ingestion: ['order_items']
Certified tables: 9/9
EXIT=1
```

O que observar:

- O circuit breaker barrou só `order_items`; as outras 8 tabelas seguiram.
- A camada curada **não** foi reconstruída, porque uma das fontes não passou nesta execução.
- A certificação continuou 9/9: o último snapshot bom de `order_items` ainda está dentro do SLO de freshness (24h). Se o problema persistir por mais de 24h, o pilar de freshness denuncia a tabela.

**`volume_drop`** (últimos 8% de `orders` removidos)

```
[ALERT-CRITICAL] [VOLUME] orders: 91485 rows (Z-score=-8.00, baseline[moving]: mean=99441, std=994).
  | downstream impact: order_items, order_payments, order_reviews
  [FAILED] orders_volume_stability: 91485 rows (Z-score=-8.00, ...)
[certify] orders: not certified
SLOs breached: ['orders_volume_stability']
EXIT=1
```

O que observar:

- 91.485 linhas **passam** no contrato (mínimo 90.000). Só a estatística sobre o histórico pegou a queda: defesa em profundidade.
- O desvio de 994 é o piso de 1% da média (o histórico era constante). Por isso uma queda de 8% dá exatamente z = −8.
- A severidade é `critical` porque `orders` tem criticidade `high`.
- **Depois do `restore`:** o SLO volta a passar (a poda MAD descartou o ponto anômalo da linha de base), mas `orders` **continua sem certificação** até o incidente ser resolvido.

**`distribution_shift`** (`freight_value × 3`)

```
[ALERT-CRITICAL] [DISTRIBUTION] order_items: freight_value: current mean=59.97 (Z=200.00) | downstream impact: none
  [FAILED] order_items_distribution_stability: freight_value: current mean=59.97 (Z=200.00)
```

O contrato não pega a mudança, porque `freight_value` só tem `min_value: 0`. A distribuição pega.

**`null_spike`** (30% de `product_category_name` nulos)

```
[ALERT-WARNING] [DISTRIBUTION] products: product_category_name: nulls=31.29% (Z=1590.36) | downstream impact: order_items
SLOs breached: none
EXIT=0
```

O que observar:

- A coluna é `nullable: true`, então o contrato aceita os nulos.
- Houve incidente e a certificação foi perdida, mas **nenhum SLO quebrou** e o exit code é 0, porque `products` só tem SLO de freshness. Incidente e SLO são mecanismos diferentes: um diz "algo mudou", o outro diz "um compromisso foi quebrado".
- A severidade é `warning` (`products` tem criticidade `medium`).

**`schema_drift`** (coluna nova `review_channel` em `order_reviews`)

```
- **[warning]** `unexpected_column` - column `review_channel`: ... (potential schema drift).
[ALERT-WARNING] [SCHEMA] order_reviews: added columns: ['review_channel'] | downstream impact: none
  [FAILED] order_reviews_schema_stability: added columns: ['review_channel']
```

O contrato só **avisa** (uma coluna nova não quebra quem consome); o pilar de schema registra a mudança e quebra o SLO.

> **Limitação observada:** depois do `restore`, o pilar de schema dispara **de novo**, com "removed columns: ['review_channel']". Ele compara com a execução anterior, que era a quebrada, então a reversão também é uma mudança. Ver [7.3](07-guia-de-estudo.md#73-limitações-conhecidas).

### Freshness (manual)

Freshness depende do relógio. Para simular sem esperar 24h, reduza o limite temporariamente em `config/slo.yaml` (`orders_freshness: max_delay_hours: 0.01`) e rode só os pilares:

```bash
poetry run python -m src.quality.checks_runner
```

```
[ALERT-CRITICAL] [FRESHNESS] orders: Last successful ingestion was 0.1h ago (threshold: 0.01h).
  [FAILED] orders_freshness: Last successful ingestion was 0.1h ago (threshold: 0.01h).
```

O incidente registra um TTD de cerca de 343s: o tempo desde que o atraso cruzou o limite, e não desde a última carga. **Volte o valor para 24 depois.**

## 6.6 Fechando um incidente

```bash
poetry run python -m src.incidents.cli list
```

```
9147bf21  critical  orders/volume                 detected 2026-10-26T21:41:01  91485 rows (Z-score=-8.00, ...)
6b517382  critical  order_items/distribution      detected 2026-10-26T21:41:35  freight_value: current mean=59.97 (Z=200.00)
```

Os ids aceitam qualquer prefixo único:

```bash
poetry run python -m src.incidents.cli rca 9147bf21 \
  --why "The orders export ran before the upstream job finished" \
  --why "The export has no completion check" \
  --root-cause "Missing dependency between upstream job and export" \
  --by "Amadeu"

poetry run python -m src.incidents.cli postmortem 9147bf21 \
  --impact "Revenue dashboards understated by 8% for one run" \
  --timeline "Partial export ingested" \
  --timeline "Volume pillar raised a critical alert" \
  --action "Add a completion flag check before export"

poetry run python -m src.incidents.cli resolve 9147bf21 --summary "Export re-run after upstream completion"
```

```
RCA recorded for 9147bf21-... (2 whys).
Postmortem saved to: reports\incidents\postmortems\9147bf21-....md
Resolved 9147bf21-... - TTR 0.01h.
```

Na execução seguinte, `orders` volta a ser **CERTIFIED** e o dashboard passa a mostrar o TTR médio:

```
[certify] orders: CERTIFIED
[maturity_dashboard] incidents: 5 (4 open) | mean TTD: 5.7s | mean TTR: 53.2s
```

O postmortem gerado tem estas seções: resumo (tabela, pilar, detecção, TTD, tabelas impactadas), cronologia, impacto, os 5 porquês, causa raiz, ações corretivas e metadados da investigação. Ele não tem campo de culpado.

## 6.7 Testando o webhook

```bash
# PowerShell
$env:ALERT_WEBHOOK_URL = "https://hooks.slack.com/services/..."
poetry run python -m src.quality.checks_runner
```

Uma URL que não comece com `https://` é ignorada com um aviso, e uma falha de rede nunca interrompe o pipeline.

## 6.8 Inspecionando os resultados

```python
import pandas as pd
pd.read_parquet("data/staging/orders.parquet").dtypes      # tipos do contrato
pd.read_parquet("data/curated/orders_curated.parquet")[["order_id", "order_total_value", "delivery_delay_days"]]

from src.lineage.field_lineage import describe_lineage
describe_lineage("orders_curated", "order_total_value")
```

## 6.9 Recomeçando do zero

Tudo em `data/staging/`, `data/curated/` e `reports/` é gerado e pode ser apagado. O `data/raw/` não deve ser apagado. Para apagar só o estado de observabilidade e de incidentes:

```bash
# Git Bash
rm -rf reports/quality_history/*.jsonl reports/alerts reports/incidents reports/certification
```

Depois disso, serão necessárias de novo 4 execuções para formar a linha de base.

## 6.10 Problemas comuns

| Sintoma | Causa | Solução |
|---|---|---|
| Todos os pilares estatísticos em `insufficient_history` e nada certificado | Menos de 4 execuções no histórico | Rode a ingestão mais vezes |
| `ModuleNotFoundError: No module named 'src'` | Executado sem `-m` ou fora da raiz | `poetry run python -m ...` a partir da raiz |
| Tabela `missing_file` | CSV ausente em `data/raw/` | Baixe os dados; confira o `file` no catálogo |
| SLO de freshness quebrado sem causa aparente | Nenhuma ingestão nas últimas 24h | Rode a ingestão; é o comportamento esperado |
| `restore` diz "nothing to restore" | Nenhum cenário aplicado, ou já restaurado | Nada a fazer |
| `UnicodeEncodeError` num print | Console Windows em cp1252 com emoji | O projeto já evita emoji no console; não adicione emoji a `print` |
