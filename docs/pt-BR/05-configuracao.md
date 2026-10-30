# 5. Configuração

> [Índice](README.md) · Anterior: [4. Referência de código](04-referencia-de-codigo.md) · Próximo: [6. Execução e testes](06-execucao-e-testes.md)

O comportamento do pipeline vem de quatro arquivos YAML e de uma variável de ambiente. Este capítulo documenta cada chave e mostra como fazer as mudanças mais comuns.

| Arquivo | Pergunta que responde | Quem lê |
|---|---|---|
| `config/settings.yaml` | Onde ficam as coisas? | `src/config.py` → todos os módulos |
| `catalog/inventory.yaml` | Quais tabelas existem, o que significam, quem é dono, o que é crítico? | Ingestão, DAMA, lineage, severidade, certificação |
| `contracts/<tabela>.contract.yaml` | O que esta tabela precisa cumprir? | Validador, DAMA, pilar de volume |
| `config/slo.yaml` | Quais metas cada tabela precisa cumprir? | `src/quality/sli_slo.py` |

## 5.1 `config/settings.yaml`

```yaml
environment: local

local:
  raw_path: "data/raw"
  staging_path: "data/staging"
  curated_path: "data/curated"
  ingestion_logs_path: "reports/ingestion_logs"
  contracts_path: "contracts"
  dq_reports_path: "reports/dq_scorecards"
  quality_history_path: "reports/quality_history"
  alerts_path: "reports/alerts"
  incidents_path: "reports/incidents"
  certification_path: "reports/certification"
  dashboard_path: "reports/maturity_dashboard"

catalog_path: "catalog/inventory.yaml"
slo_path: "config/slo.yaml"
```

| Chave | Conteúdo |
|---|---|
| `environment` | Qual bloco usar. Um bloco `prod` com outros caminhos permitiria trocar de ambiente sem tocar no código |
| `raw_path` | CSVs originais |
| `staging_path` / `curated_path` | Parquet validado / camada analítica |
| `ingestion_logs_path` | Log JSON de cada ingestão |
| `dq_reports_path` | Relatórios de contrato, scorecards DAMA e relatórios de pilares |
| `quality_history_path` | Snapshots por tabela (linha de base da observabilidade) |
| `alerts_path` / `incidents_path` | Alertas / incidentes, resoluções, RCAs e postmortems |
| `certification_path` / `dashboard_path` | Status de certificação / dashboards de maturidade |
| `catalog_path` / `slo_path` | Fora do bloco de ambiente: são definições, não dados |

Todos os caminhos são relativos à raiz do projeto (`PROJECT_ROOT`).

## 5.2 `catalog/inventory.yaml`

O inventário de metadados. Exemplo de uma entrada:

```yaml
version: 1
domain: e-commerce
owner_default: "data-eng-team"

tables:
  order_items:
    file: olist_order_items_dataset.csv
    description: >
      Items for each order, with price and freight per item.
    primary_key: [order_id, order_item_id]
    foreign_keys:
      - column: order_id
        references: orders.order_id
      - column: product_id
        references: products.product_id
    criticality: high
    owner: null
    known_gotchas: >
      price and freight_value are the fields used to calculate revenue...
```

| Chave | Usada por | Efeito |
|---|---|---|
| `owner_default` | Certificação | Dono herdado pelas tabelas com `owner: null` |
| `tables.<nome>` | Todos | A ordem das entradas é a ordem de processamento |
| `file` | Ingestão, simulador | Nome do CSV em `raw_path` |
| `description` | Pessoas | Documentação |
| `primary_key` | DAMA (unicidade) | `str`, `list` (composta) ou `null` (sem PK, como `geolocation`) |
| `foreign_keys` | DAMA (consistência), lineage de tabela | Cada item `{column, references: "tabela.coluna"}` |
| `criticality` | Severidade dos alertas | `high` → critical, `medium` → warning, `low` → info |
| `owner` | Certificação | Dono específico; `null` herda `owner_default` |
| `known_gotchas` | Pessoas | Comportamentos que parecem erro mas são esperados |

**Criticidade atual:** `orders`, `order_items` e `order_payments` são `high`; `customers`, `order_reviews` e `products` são `medium`; `sellers`, `geolocation` e `category_translation` são `low`.

**Gotchas documentados:**

- `orders`: datas de entrega nulas em pedidos cancelados.
- `customers`: `customer_id` ≠ cliente (é um id por pedido); 278 CEPs sem coordenada.
- `order_reviews`: `review_id` se repete em 814 linhas; a PK é `(review_id, order_id)`.
- `products`: as colunas `product_name_lenght` e `product_description_lenght` estão com erro de grafia na origem e foram mantidas.
- `geolocation`: o mesmo CEP aparece em várias linhas; não há PK.

## 5.3 `contracts/<tabela>.contract.yaml`

```yaml
table: order_items                      # deve ser igual à chave do catálogo
owner: data-eng-team
version: 1
primary_key: [order_id, order_item_id]  # opcional; simples ou composta

columns:
  order_item_id:
    type: integer
    nullable: false
  price:
    type: float
    nullable: false
    min_value: 0
    max_value: 10000
  # outras opções: unique: true, allowed_values: [...]

sla:
  min_expected_volume: 100000
```

| Chave | Valores | Regra gerada | Severidade |
|---|---|---|---|
| `columns.<col>` | — | coluna faltando / coluna extra | blocking / warning |
| `type` | `string`, `integer`, `float`, `timestamp` | `type_mismatch` | blocking |
| `nullable` | `true` (padrão) / `false` | `unexpected_nulls` | blocking |
| `unique` | `true` / `false` (padrão) | `uniqueness_violation` | blocking |
| `allowed_values` | lista | `unexpected_value` | blocking |
| `min_value` / `max_value` | número (inclusivo) | `below_min_value` / `above_max_value` | blocking |
| `primary_key` | `str` ou `list` | `primary_key_nulls` / `primary_key_violation` | blocking |
| `sla.min_expected_volume` | inteiro | `volume_below_expected` | blocking |

**Volumes mínimos atuais** (linhas reais entre parênteses): orders 90.000 (99.441), customers 90.000 (99.441), order_items 100.000 (112.650), order_payments 95.000 (103.886), order_reviews 90.000 (99.224), products 30.000 (32.951), sellers 3.000 (3.095), geolocation 900.000 (1.000.163), category_translation 60 (71).

## 5.4 `config/slo.yaml`

```yaml
slos:
  - id: orders_volume_stability
    table: orders
    pillar: volume
    description: >
      The volume of 'orders' must not deviate by more than 3 standard deviations...
    z_threshold: 3.0
    method: moving
    window: 10
```

| Chave | Obrigatória | Significado |
|---|---|---|
| `id` | sim | Identificador único (testado) |
| `table` | sim | Tabela do catálogo (testado) |
| `pillar` | sim | `freshness`, `volume`, `schema` ou `distribution` (`load_slos` recusa outro valor) |
| `description` | sim | O SLA em palavras |
| `max_delay_hours` | freshness | Atraso máximo |
| `z_threshold` | volume, distribution | Limiar de \|z\| |
| `method` | volume, distribution | `zscore`, `moving` ou `ewma` |
| `window` | volume, distribution | Execuções na linha de base |

Um teste garante que cada SLO só use parâmetros que o seu pilar entende. Os pares (tabela, pilar) **sem** SLO usam `DEFAULT_PILLAR_PARAMS`: freshness de 24h, e `z_threshold: 3.0, method: moving, window: 10` para os estatísticos.

**SLOs atuais:**

| id | Tabela | Pilar | Meta |
|---|---|---|---|
| `orders_freshness` | orders | freshness | 24h |
| `orders_volume_stability` | orders | volume | \|z\| ≤ 3, moving, janela 10 |
| `order_items_distribution_stability` | order_items | distribution | \|z\| ≤ 3, moving, janela 10 |
| `order_reviews_schema_stability` | order_reviews | schema | sem mudança de schema |
| `products_freshness` | products | freshness | 48h |

## 5.5 Variável de ambiente

| Variável | Efeito |
|---|---|
| `ALERT_WEBHOOK_URL` | Se definida com uma URL `https://`, cada alerta também é enviado como `{"text": "..."}` (formato de webhook do Slack). Outros esquemas são ignorados com um aviso |

A variável é lida do ambiente do processo: nada no projeto (nem o Poetry) carrega um arquivo `.env`, então defina-a na sessão do terminal, como em [6.7](06-execucao-e-testes.md#67-testando-o-webhook). O mesmo vale para o token do Kaggle usado no download dos dados (ver `scripts/download_instructions.md`).

## 5.6 Receitas

**Endurecer uma regra.** Edite o contrato, por exemplo `max_value: 5000` em `price`. A próxima ingestão aplica a regra. Teste: `poetry run pytest tests/test_contracts.py`.

**Criar um SLO.** Acrescente um item em `config/slo.yaml`. `tests/test_slo.py` valida id único, tabela existente, pilar conhecido e parâmetros compatíveis.

**Declarar uma nova relação.** Acrescente em `foreign_keys` no catálogo. Sem código: a consistência DAMA passa a medi-la e o lineage de tabela passa a considerá-la no impacto dos incidentes.

**Incluir uma nova tabela:**

1. Coloque o CSV em `data/raw/`.
2. Acrescente a entrada em `catalog/inventory.yaml`.
3. Crie `contracts/<tabela>.contract.yaml` com `table:` igual à chave do catálogo.
4. Rode `poetry run pytest tests/test_contracts.py`: ele falha se o catálogo e os contratos estiverem dessincronizados.

**Criar um campo derivado na camada curada:**

1. Implemente-o numa função `curate_*` em `src/transform/build_curated.py`.
2. Registre-o em `FIELD_LINEAGE_REGISTRY`.
3. Se esquecer o passo 2, `tests/test_curated.py` falha.
