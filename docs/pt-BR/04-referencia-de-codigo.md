# 4. Referência de código

> [Índice](README.md) · Anterior: [3. Arquitetura](03-arquitetura.md) · Próximo: [5. Configuração](05-configuracao.md)

Os módulos aparecem na ordem do fluxo de dados. Para cada um: **para que serve**, **por que existe**, **funções** (parâmetros e retorno) e **decisões de implementação**.

Convenção usada nas tabelas: `df` = `pandas.DataFrame`; "puro" = função sem leitura ou escrita de arquivos.

---

## 4.1 `src/config.py`

**Para que serve.** Resolver a raiz do projeto e carregar `config/settings.yaml` e `catalog/inventory.yaml`.

**Por que existe.** Antes dele, cinco módulos tinham cópias próprias de `load_settings`, `load_catalog` e `PROJECT_ROOT`, cada uma com pequenas diferenças (uma resolvia o caminho do catálogo por dentro, outra esperava que quem chama resolvesse).

| Elemento | Parâmetros | Retorno | Comportamento |
|---|---|---|---|
| `PROJECT_ROOT` | — | `Path` | `Path(__file__).resolve().parents[1]`: a raiz do repositório, calculada a partir do próprio arquivo. Funciona de qualquer diretório de trabalho |
| `load_settings(path=...)` | caminho do settings (padrão `config/settings.yaml`) | `dict` | Devolve uma **cópia** do bloco do ambiente ativo (`environment: local`) e acrescenta `catalog_path`, `slo_path` e `environment` |
| `load_catalog(catalog_path)` | caminho **absoluto** | `dict` | Lê o YAML do catálogo |

**Decisão.** `load_settings` devolve `dict(settings[env])`, uma cópia, para que um módulo que altere o dicionário não afete outro.

---

## 4.2 `src/ingestion/type_casting.py`

**Para que serve.** Converter colunas para os tipos declarados no contrato.

**Por que existe.** A mesma conversão serve a dois propósitos: o validador a usa para **detectar** valores que não cabem no tipo, e a ingestão a usa para **gravar** o staging tipado.

| Elemento | Retorno | Comportamento |
|---|---|---|
| `TYPE_CASTERS` | `dict[str, Callable]` | `string` → `astype("string")`; `integer` → `_to_integer`; `float` → `to_numeric(...).astype("float64")`; `timestamp` → `to_datetime(errors="coerce")` |
| `_to_integer(series)` | série `Int64` | `to_numeric(errors="coerce")` e depois anula valores não inteiros (`numeric % 1 == 0`) |
| `cast_to_contract(df, contract)` | novo `df` | Aplica o caster de cada coluna declarada; colunas sem tipo conhecido ficam como estão; o original não é alterado |

**Decisões.**

- **`errors="coerce"`** transforma em nulo o que não converte. É o que permite detectar valores inválidos: um nulo que não existia antes da conversão é um valor que não cabia no tipo.
- **`_to_integer` recusa 1.5.** Sem isso, `to_numeric` aceitaria 1.5 como "inteiro" e o contrato não perceberia.
- **`Int64` (com I maiúsculo)** é o inteiro *nullable* do pandas. O `int64` NumPy não aceita nulo e converteria a coluna para float.

---

## 4.3 `src/ingestion/contract_validator.py`

**Para que serve.** Validar um DataFrame contra o contrato da tabela e dizer se ele pode seguir.

### Tipos

| Tipo | Campos | Uso |
|---|---|---|
| `Severity` | `"blocking"` ou `"warning"` | Só *blocking* reprova a tabela |
| `Violation` | `rule`, `column`, `detail`, `severity` | Uma regra quebrada |
| `ContractValidationResult` | `table`, `passed`, `violations`; `as_dict()` | O veredito da tabela, serializável em JSON |

### Funções

| Função | Parâmetros | Retorno |
|---|---|---|
| `load_contract(table_name, contracts_dir)` | nome da tabela, pasta dos contratos | `dict` do `<tabela>.contract.yaml` |
| `column_rule_violations(series, rules)` | valores da coluna, regras da coluna | `dict[regra, máscara booleana]`, só com as regras declaradas |
| `validate_dataframe(df, contract)` | DataFrame bruto, contrato | `ContractValidationResult` |
| `_check_primary_key(df, primary_key)` | DataFrame, PK simples (`str`) ou composta (`list`) | lista de `Violation` |

### `column_rule_violations`: a regra de validade em um lugar só

Devolve uma máscara por regra, `True` onde o valor **viola**:

| Chave | Cálculo |
|---|---|
| `type_mismatch` | `coerced.isna() & series.notna()`: nulo novo após a conversão |
| `unexpected_value` | `coerced.notna() & ~coerced.isin(allowed_values)` |
| `below_min_value` | `(numeric < min_value).fillna(False)` |
| `above_max_value` | `(numeric > max_value).fillna(False)` |

Detalhes que importam:

- **Nulo nunca é inválido aqui.** Nulo é assunto de nulabilidade (no contrato) e de completude (no DAMA).
- **`.fillna(False).astype(bool)`:** depois do cast, uma coluna `Int64` comparada com um número devolve `<NA>` onde o valor era nulo. Sem o `fillna`, `mask.any()` poderia devolver `NA` e o `|` entre máscaras propagaria o `NA`.
- **O validador conta as máscaras (`sum`) e o DAMA calcula a média (`mean`).** Os dois módulos nunca discordam sobre o que é válido.

### `validate_dataframe`: ordem das verificações

1. Colunas do contrato ausentes no DataFrame → `missing_column` (*blocking*).
2. Colunas do DataFrame ausentes no contrato → `unexpected_column` (*warning*: schema drift).
3. Para cada coluna declarada e presente: tipo, nulos (`nullable: false`), unicidade (`unique: true`), valores aceitos e faixa.
4. Chave primária da tabela (`primary_key`), simples ou composta: nulos e duplicatas.
5. Volume mínimo (`sla.min_expected_volume`).

`passed = True` somente se nenhuma violação for *blocking*.

**Decisões.**

- **Não para no primeiro erro:** coleta todas as violações, para o relatório mostrar o quadro completo.
- **Unicidade com `duplicated(keep=False)`:** conta todas as linhas envolvidas na duplicata, inclusive a primeira, porque o objetivo é listar quem está em conflito.
- **PK composta no nível da tabela:** `order_items` usa `[order_id, order_item_id]`; `order_id` se repete sozinho, só o par precisa ser único. `order_reviews` usa `[review_id, order_id]`, porque `review_id` se repete em 814 linhas nos dados reais.

---

## 4.4 `src/ingestion/load_raw.py`

**Para que serve.** Executar a Etapa 1: CSV → contrato → Parquet tipado → snapshot → relatórios.

| Função | Parâmetros | Retorno |
|---|---|---|
| `ingest_table(table_name, table_meta, raw_dir, staging_dir, contracts_dir, history_dir)` | tabela, entrada do catálogo, quatro pastas | `dict` de resultado da tabela |
| `generate_markdown_report(log_payload, report_path)` | log da execução, destino | — (grava o arquivo) |
| `run()` | — | lista de resultados |
| `main()` | — | exit code: 0 se todas `ok`, 1 caso contrário |

**Fluxo de `ingest_table`** (para no primeiro passo que falhar):

1. O arquivo existe? Se não, `status: missing_file`.
2. `pd.read_csv` funciona? Erros de I/O, encoding ou parsing (`OSError`, `ValueError`) dão `status: read_error`.
3. Perfil bruto: linhas, colunas e dtypes como o pandas leu.
4. Contrato: violação *blocking* → `status: contract_violation`, e a função retorna (**circuit breaker**).
5. `cast_to_contract` → grava `data/staging/<tabela>.parquet`.
6. `build_snapshot` + `append_snapshot` → `reports/quality_history/<tabela>.jsonl`.

**Dicionário de resultado:** `table`, `source_file`, `status`, `error`, `n_rows`, `n_columns`, `columns`, `dtypes`, `contract_passed`, `contract_violations` e, se aprovada, `staging_path`.

**Saídas de `run()`:**

- `reports/ingestion_logs/run_<timestamp>.json`: o log técnico completo.
- `reports/dq_scorecards/contract_report_<timestamp>.md`: tabela-resumo e lista de violações.

**Decisões.**

- **`except (OSError, ValueError)` em vez de `Exception`:** captura só as falhas esperadas de leitura. `UnicodeDecodeError` e `pandas.errors.ParserError` são subclasses de `ValueError`.
- **O snapshot é calculado sobre o DataFrame tipado**, para que os dtypes do histórico sejam estáveis entre execuções (o pilar de schema compara dtypes).
- **Timestamps em UTC**, com `:` trocado por `-` nos nomes de arquivo (o Windows não aceita `:`).

---

## 4.5 `src/monitoring/history_store.py`

**Para que serve.** Guardar um retrato das métricas de cada tabela a cada ingestão aprovada. É a memória dos pilares.

| Elemento | Detalhe |
|---|---|
| `TableSnapshot` | `table`, `timestamp` (UTC ISO-8601), `n_rows`, `columns`, `dtypes`, `null_rates` (% de nulos por coluna, de 0 a 100, 4 casas), `numeric_stats` (`mean` e `std` populacional de cada coluna numérica) |
| `build_snapshot(table_name, df)` | Calcula o snapshot |
| `append_snapshot(snapshot, history_dir)` | Acrescenta uma linha ao `<tabela>.jsonl` |
| `load_history(table_name, history_dir)` | Lê todas as linhas em ordem cronológica; `[]` se não houver arquivo |

**Decisões.**

- **JSON Lines, append-only:** uma linha por execução, fácil de ler e de auditar, sem banco de dados (D7).
- **Desvio-padrão populacional (`ddof=0`):** o snapshot descreve a tabela inteira, não uma amostra.
- **Só médias e desvios, não o dado:** o histórico fica pequeno (alguns KB por tabela) mesmo com centenas de execuções.

---

## 4.6 `src/quality/dimensions_dama.py`

**Para que serve.** Medir quatro dimensões DAMA, em porcentagem, sobre o staging.

| Função | Parâmetros | Retorno |
|---|---|---|
| `completeness(df)` | DataFrame | `{coluna: % não nulo}`; `None` por coluna se a tabela estiver vazia |
| `uniqueness(df, key_columns)` | DataFrame, colunas da PK | % de linhas que não repetem a chave; `None` se não houver PK utilizável |
| `validity(df, contract)` | DataFrame, contrato | `{coluna: % válido}`, usando `column_rule_violations` |
| `consistency(df, foreign_keys, reference_values)` | DataFrame, FKs do catálogo, função de consulta | `{"col -> tabela.col": % encontrado}`; `None` se não der para avaliar |
| `score_table(df, contract, table_meta, reference_values)` | — | as quatro dimensões + `summary` com o pior valor de cada uma |
| `run()` | — | o scorecard completo |

`StagingReferenceValues` é a função de consulta usada em produção. Ela lê do Parquet **só a coluna referenciada**, guarda os valores distintos num `pd.Index` e mantém um cache por (tabela, coluna).

**Decisões.**

- **Injeção de dependência em `consistency`:** a função não sabe de onde vêm os valores da tabela pai. No teste, eles vêm de um dicionário; em produção, do Parquet.
- **Leitura colunar com cache:** `orders.order_id` é referenciado por três tabelas e é lido uma única vez; `geolocation` (1 milhão de linhas, 5 colunas) é lido numa só coluna.
- **`pd.Index` para o `isin`:** busca por hash, O(1) por elemento.
- **Unicidade com `duplicated()` padrão (`keep="first"`):** mede quantas linhas sobram se cada chave ficar uma vez só. O validador usa `keep=False`, porque lá o objetivo é listar os conflitos.
- **Resumo pelo pior valor:** uma coluna ruim compromete a tabela; uma média esconderia isso.
- **Completude resumida só pelas colunas obrigatórias:** coluna opcional não tem requisito de preenchimento. No detalhe, as opcionais aparecem marcadas como *(optional)* e sem ⚠️.

**Saídas:** `reports/dq_scorecards/dama_scorecard_<timestamp>.json` e `.md`.

---

## 4.7 `src/quality/checks_runner.py`

**Para que serve.** Executar os pilares em todas as tabelas, avaliar os SLOs e, para cada anomalia, alertar e registrar um incidente.

| Função | Parâmetros | Retorno |
|---|---|---|
| `occurrence_time(finding, snapshot_at, params)` | finding, timestamp do snapshot, parâmetros do pilar | quando o problema começou |
| `handle_anomaly(finding, table_meta, snapshot_at, params, graph, known_incidents, alerts_dir, incidents_dir)` | — | — (alerta e grava o incidente, se ainda não existir) |
| `evaluate_catalog(catalog, slos, history_dir, contracts_dir)` | — | `{tabela: {pilar: PillarFinding}}`, **sem efeitos colaterais** |
| `run()` | — | `(findings_by_table, slo_results)` |
| `main()` | — | exit code: 1 se algum SLO foi quebrado |

**`occurrence_time`**, a base do TTD realista:

- Pilares de dados (volume, schema, distribution): o problema começou quando o snapshot anômalo foi gravado, ou seja, na ingestão que trouxe o dado.
- Freshness: o problema começou quando o atraso cruzou o SLO (última carga + `max_delay_hours`).

**`handle_anomaly`**, passo a passo:

1. Se já existe incidente para (tabela, pilar, snapshot), não faz nada (deduplicação, D10).
2. Calcula as tabelas impactadas a jusante pelo grafo de lineage.
3. Define a severidade pela criticidade da tabela (`SEVERITY_BY_CRITICALITY`: `high` → `critical`, `medium` → `warning`, `low` → `info`).
4. Envia o alerta e registra o incidente com `occurred_at`, `severity` e `snapshot_at`.

**Por que existe `evaluate_catalog`.** A certificação e o dashboard precisam dos vereditos dos pilares, mas não podem disparar alertas nem abrir incidentes. Essa função entrega os vereditos sem efeitos colaterais.

**Saída:** `reports/dq_scorecards/pillars_report_<timestamp>.md`, com os SLOs no topo e os pilares por tabela.

---

## 4.8 `src/monitoring/anomaly_detection.py`

**Para que serve.** Responder "este valor é anômalo em relação ao histórico?". Não sabe nada de tabelas, SLOs ou negócio.

### Constantes

| Constante | Valor | Significado |
|---|---|---|
| `DEFAULT_WINDOW` | 10 | Execuções recentes usadas como linha de base |
| `DEFAULT_EWMA_ALPHA` | 0,3 | Peso da execução mais recente no EWMA |
| `DEFAULT_MIN_REL_STD` | 0,01 | Piso do desvio-padrão: 1% da média |
| `_MAD_TO_STD` | 1,4826 | Converte o MAD em desvio-padrão equivalente de uma normal |
| `METHODS` | `zscore`, `moving`, `ewma` | Métodos aceitos |

### Funções

| Função | Parâmetros | Retorno |
|---|---|---|
| `zscore_check(current_value, historical_values, threshold=3.0)` | valor atual, histórico, limiar | `ZScoreResult(is_anomaly, z_score, historical_mean, historical_std)` |
| `moving_check(current_value, historical_values, threshold=3.0, window=10, method="moving", ewma_alpha=0.3, prune_outliers=True, min_rel_std=0.01)` | — | `MovingResult` (o anterior + `method`, `window_used`, `pruned`) |
| `detect(current_value, historical_values, threshold=3.0, method="zscore", window=10, **kwargs)` | — | ponto de entrada único: escolhe o detector pelo `method` |
| `_prune_outliers(values, threshold, min_rel_std)` | — | valores sem outliers |
| `_ewma_stats(values, alpha)` | — | `(média, desvio)` exponencialmente ponderados |

### Como `moving_check` funciona

1. Pega as últimas `window` execuções.
2. Com menos de 2 pontos, devolve "não é anomalia" e `z_score = None`.
3. Poda os outliers: calcula a mediana e o MAD (mediana dos desvios absolutos) e descarta os pontos a mais de `threshold` desvios robustos da mediana. Nunca poda a ponto de restar menos de 2 pontos.
4. Calcula média e desvio: simples (`moving`) ou exponencialmente ponderados (`ewma`).
5. Aplica o piso: `std = max(std, min_rel_std × |média|)`.
6. `z = (atual − média) / std`; anomalia se `|z| > threshold`.

**Por que mediana e MAD na poda.** A média e o desvio-padrão são eles mesmos distorcidos pelo outlier que se quer remover. A mediana e o MAD são estatísticas robustas: um ponto extremo quase não as afeta.

**Por que o piso de 1%.** Com histórico constante (99.441 linhas sempre), o desvio é zero e uma linha a menos daria z infinito. Com o piso, o desvio mínimo é ~994 linhas, e só variações acima de ~3% (3 × 1%) disparam. O cenário `volume_drop` (−8%) deu z = −8,00 exatamente por isso.

**A recorrência do EWMA** (`_ewma_stats`), aplicada a cada valor `v`:

```
diff = v - média
média = média + α·diff
var   = (1 - α)·(var + diff·α·diff)
```

É a forma incremental padrão da média e da variância exponencialmente ponderadas.

---

## 4.9 `src/quality/pillars/`

Todos os pilares devolvem um `PillarFinding` (`base.py`): `table`, `pillar`, `status` (`ok`, `anomaly` ou `insufficient_history`) e `detail` (texto legível). `MIN_HISTORY_FOR_ZSCORE = 3` é o mínimo de execuções **anteriores** para confiar numa linha de base estatística.

| Pilar | Função | Parâmetros principais | Lógica |
|---|---|---|---|
| Freshness | `check_freshness(table_name, history, max_delay_hours, now=None)` | atraso máximo em horas; `now` injetável para testes | Horas desde o `timestamp` do último snapshot. Anomalia se passar do limite |
| Volume | `check_volume(table_name, current_n_rows, history, min_expected_volume=None, z_threshold=3.0, method="moving", window=10)` | — | Compara `n_rows` atual com `history[:-1]`. Com menos de 3 execuções anteriores, recorre ao `min_expected_volume` do contrato |
| Schema | `check_schema(table_name, history)` | — | Colunas adicionadas, removidas e dtypes alterados em relação à execução anterior. Precisa de 2 snapshots |
| Distribution | `check_distribution(table_name, history, z_threshold=3.0, method="moving", window=10)` | — | Para cada coluna numérica, detecta desvio na média; para cada coluna, desvio na taxa de nulos. Lista todas as colunas anômalas |
| Lineage | `build_lineage_graph(catalog)`, `downstream_impact(table, graph)`, `upstream_dependencies(table, graph)` | — | Grafo bidirecional a partir das FKs; busca em largura transitiva |

**Decisões.**

- **`history[-1]` é a execução atual.** O runner roda logo depois da ingestão, e os pilares comparam o último snapshot com os anteriores.
- **`now` injetável no freshness:** sem isso, o teste dependeria do relógio e quebraria com o tempo.
- **Lineage com `deque` e uma única `_traverse(direction)`:** `popleft()` é O(1) (a versão original usava `list.pop(0)`, que é O(n)), e uma só função serve às duas direções. As arestas são registradas sem repetição.
- **O padrão é `method="moving"`:** os testes documentam casos em que o Z-score global falha (contaminação, tendência).

---

## 4.10 `src/quality/sli_slo.py`

**Para que serve.** Transformar os SLOs do YAML em parâmetros de pilar, avaliar os pilares e montar os resultados de SLO.

| Elemento | Parâmetros | Retorno / conteúdo |
|---|---|---|
| `PILLARS` | — | `("freshness", "volume", "schema", "distribution")` |
| `DEFAULT_PILLAR_PARAMS` | — | freshness: `max_delay_hours: 24`; volume e distribution: `z_threshold: 3.0, method: moving, window: 10`; schema: `{}` |
| `SLOResult` | — | `slo_id`, `table`, `pillar`, `description`, `passed`, `finding` |
| `load_slos(path)` | caminho do `slo.yaml` | lista de SLOs; `ValueError` se o pilar for desconhecido |
| `pillar_params(table, pillar, slos)` | — | parâmetros padrão sobrescritos pelo SLO do par, se houver |
| `evaluate_pillar(table, pillar, history, contract, params)` | — | `PillarFinding`: o único despacho para os quatro pilares |
| `evaluate_table(table, history, contract, slos)` | — | `(findings por pilar, parâmetros por pilar)` |
| `evaluate_slos(slos, findings_by_table)` | — | lista de `SLOResult` que **leem** findings já calculados |
| `evaluate_all(history_by_table, contracts_by_table, slos)` | — | conveniência: avalia só os pares com SLO |

**Decisões.**

- **Uma avaliação por (tabela, pilar)** (D5): o SLO não recalcula nada; ele lê o finding.
- **`pillar_params` copia o padrão** (`dict(...)`), para que um SLO não altere o padrão das outras tabelas, e filtra os metadados (`id`, `table`, `pillar`, `description`).
- **Volume sem histórico devolve `insufficient_history`**, em vez de fingir `n_rows = 0`, o que geraria um falso alarme contra o volume mínimo.
- **`passed = status != "anomaly"`:** `insufficient_history` não reprova o SLO (D9).

---

## 4.11 `src/monitoring/alerting.py`

| Elemento | Detalhe |
|---|---|
| `Severity` | `"info"`, `"warning"` ou `"critical"` |
| `send_alert(message, severity="warning", alerts_dir=None)` | Imprime `[ALERT-<SEVERIDADE>] mensagem`, acrescenta `{timestamp, severity, message}` em `alerts.jsonl` e, se `ALERT_WEBHOOK_URL` existir, envia `{"text": ...}` |
| `_post_webhook(url, text)` | Só aceita `https://`; timeout de 3s; captura `(OSError, ValueError)` |

**Decisões:** console sem emoji (D15); falha de webhook nunca derruba o pipeline (D17); payload `{"text": ...}`, o formato aceito por webhooks do Slack.

---

## 4.12 `src/incidents/incident_log.py`

| Elemento | Detalhe |
|---|---|
| `Incident` | `incident_id` (UUID4), `table`, `pillar`, `detail`, `affected_tables`, `occurred_at`, `detected_at`, `ttd_seconds`, `severity`, `snapshot_at` |
| `register_incident(table, pillar, detail, affected_tables, incidents_dir, occurred_at=None, severity="warning", snapshot_at=None)` | Cria o incidente e acrescenta em `incidents.jsonl`; sem `occurred_at`, o TTD é 0 |
| `load_incidents(incidents_dir)` | Lista de incidentes |
| `find_incident(incidents, table, pillar, snapshot_at)` | Incidente já registrado para o trio; `None` se `snapshot_at` for `None` (nunca deduplica sem snapshot) |
| `Resolution` | `incident_id`, `resolution_summary`, `resolved_at`, `ttr_seconds` |
| `register_resolution(incident_id, resolution_summary, incidents_dir, detected_at=None, resolved_at=None)` | Grava em `resolutions.jsonl`; recupera `detected_at` do incidente; `ValueError` se o incidente não existir **ou já tiver sido resolvido** |
| `open_incidents(incidents, resolutions)` | Incidentes sem resolução |
| `compute_ttd_seconds` / `compute_ttr_seconds` | Diferença em segundos |

**Decisões.**

- **Resolução em arquivo separado, não editando o incidente:** append-only preserva o histórico (quando foi detectado, quando foi resolvido) sem reescrever linhas.
- **Dupla resolução é recusada:** contaria duas vezes no TTR médio e poderia levar a porcentagem de resolvidos acima de 100%.

---

## 4.13 `src/incidents/rca.py`

| Elemento | Detalhe |
|---|---|
| `RCA` | `incident_id`, `whys` (lista de respostas aos "porquês"), `root_cause`, `conducted_by`, `conducted_at` |
| `save_rca(rca, incidents_dir)` | Acrescenta em `rca.jsonl` |
| `load_rca_for_incident(incident_id, incidents_dir)` | O RCA **mais recente** do incidente (uma investigação pode ser refeita) |
| `render_postmortem(incident, rca, timeline, impact, corrective_actions)` | Markdown com resumo, cronologia, impacto, 5 porquês, causa raiz, ações corretivas e metadados |
| `save_postmortem(incident_id, markdown, incidents_dir)` | Grava em `postmortems/<incident_id>.md` |

**Decisão:** é *blameless* por desenho. O modelo não tem campo para "quem causou o erro", só para quem investigou e quais ações corretivas foram tomadas. A estrutura do código impõe a cultura que o livro recomenda.

---

## 4.14 `src/incidents/cli.py`

**Para que serve.** Ser a porta de entrada humana do processo de incidentes. Sem ela, resolver um incidente exigiria escrever Python, e o TTR nunca seria medido.

| Subcomando | Argumentos | Efeito |
|---|---|---|
| `list` | `--all` (inclui resolvidos) | Lista id curto, severidade, tabela/pilar, data e detalhe |
| `resolve <id>` | `--summary` | Registra a resolução e mostra o TTR |
| `rca <id>` | `--why` (repetível), `--root-cause`, `--by` | Registra o RCA |
| `postmortem <id>` | `--impact`, `--timeline` (repetível), `--action` (repetível) | Gera o postmortem; exige RCA registrado antes |

**Decisões:**

- **`find_by_prefix`** aceita qualquer prefixo único do UUID, como hashes de commit no Git. Um prefixo ambíguo ou inexistente dá erro claro.
- **`main()` devolve 1** para erros de uso.

---

## 4.15 `src/transform/build_curated.py`

| Elemento | Detalhe |
|---|---|
| `CURATED_SOURCES` | `{"order_items", "orders"}`: tabelas de que a camada depende (usado pelo orquestrador) |
| `curate_order_items(order_items)` | Puro. Acrescenta `item_total_value = price + freight_value` |
| `curate_orders(orders, order_items_curated)` | Puro. Acrescenta `delivery_delay_days` (`Int64`) e `order_total_value` (soma por pedido) |
| `run()` | Lê o staging, grava `data/curated/*.parquet` e devolve as contagens |

**Decisões.**

- **`item_total_value` e não `total_order_value`:** o valor é de **um item**, e 9.803 pedidos têm mais de um item. O nome anterior induzia quem consome a somar errado: é a mesma armadilha semântica que o catálogo documenta para `customer_id`.
- **`order_total_value` nulo para pedido sem itens:** "sem itens" (pedido cancelado antes do faturamento) não é o mesmo que "valor zero".
- **`merge(..., validate="one_to_one")`:** se a agregação por pedido gerar duplicatas por algum erro, o pandas lança exceção em vez de multiplicar linhas em silêncio.
- **`delivery_delay_days` positivo = atraso.** O arredondamento de `.dt.days` foi conferido: como a data estimada é sempre meia-noite, o resultado coincide com a diferença em dias de calendário nas 96.476 entregas.

---

## 4.16 `src/lineage/field_lineage.py`

| Elemento | Detalhe |
|---|---|
| `FieldTransformation` | `frozen`: `source_table`, `source_fields` (tupla), `transformation` (texto), `target_table`, `target_field` |
| `FIELD_LINEAGE_REGISTRY` | As três transformações da camada curada |
| `lineage_for_target_field(table, field)` | Entrada do registro ou `None` |
| `derived_fields(table)` | Campos de uma tabela produzidos por transformação |
| `trace_to_source_fields(table, field, _depth=0, _max_depth=10)` | Resolve recursivamente até os campos de origem, sem duplicatas; `RecursionError` se passar da profundidade máxima (proteção contra ciclos) |
| `describe_lineage(table, field)` | Texto legível |

Exemplo, com dois níveis de recursão:

```python
>>> trace_to_source_fields("orders_curated", "order_total_value")
[('order_items', 'price'), ('order_items', 'freight_value')]
```

**Decisão:** `frozen=True` com tuplas torna o registro imutável; nenhum código pode alterá-lo em execução.

---

## 4.17 `src/certification/certify.py`

| Função | Parâmetros | Retorno |
|---|---|---|
| `certify_table(table_name, table_meta, owner_default, findings, table_open_incidents)` | — | `CertificationResult(table, certified, owner, reasons)` |
| `certify_all(catalog, findings_by_table, unresolved)` | — | Puro: um resultado por tabela |
| `evaluate_certification(settings, catalog, findings_by_table=None)` | — | Carrega findings (se não vierem prontos) e incidentes abertos, e certifica |
| `write_status(results, output_path)` | — | Grava o YAML de status |
| `run(findings_by_table=None)` | — | Certifica, imprime e grava |

**Critérios** (todos precisam ser atendidos; cada falha vira um motivo em `reasons`):

1. dono definido;
2. histórico existente;
3. nenhum pilar em `insufficient_history`;
4. nenhum pilar em `anomaly`;
5. nenhum incidente aberto.

**Saída:** `reports/certification/certification_status.yaml`.

---

## 4.18 `src/reporting/maturity_dashboard.py`

| Função | Detalhe |
|---|---|
| `compute_kpis(incidents, resolutions, slo_results, certification_results)` | Puro. Incidentes (total, abertos, resolvidos distintos, por severidade), TTD médio, TTR médio, % de SLOs, % de certificadas |
| `build_dashboard(findings_by_table=None, certification_results=None)` | Reaproveita o que vier pronto; calcula o resto sem efeitos colaterais |
| `render_dashboard_markdown(dashboard)` | Markdown com KPIs, SLOs e certificação |
| `_fmt_duration(seconds)` | s abaixo de 1 min, min abaixo de 1 h, h acima |
| `run(...)` | Grava `reports/maturity_dashboard/dashboard_<timestamp>.md` e imprime um resumo |

**Decisões.**

- **Resoluções de incidentes inexistentes são ignoradas** e os resolvidos são contados por id distinto.
- **O dashboard não grava a certificação:** antes, ele chamava `run_certification()`, e montar um relatório reescrevia outro.

---

## 4.19 `orchestration/local_pipeline.py`

Seis passos: ingestão → DAMA → pilares → certificação → camada curada (condicionada) → dashboard. Depois imprime o resumo (tabelas barradas, SLOs quebrados, tabelas certificadas) e devolve o exit code (D16). Os findings do passo 3 são repassados aos passos 4 e 6.

---

## 4.20 `scripts/simulate_incident.py`

Cada cenário altera **um** CSV bruto para exercitar um mecanismo específico. A primeira alteração copia `data/raw/` para `data/raw_backup/`, e `restore` devolve os originais.

| Cenário | Tabela | Alteração | Mecanismo exercitado |
|---|---|---|---|
| `contract_violation` | order_items | 10 preços = −1 | Contrato + circuit breaker |
| `volume_drop` | orders | Remove os últimos 8% das linhas | Pilar de volume (passa no contrato: 91.485 ≥ 90.000) |
| `distribution_shift` | order_items | `freight_value × 3` | Pilar de distribution (frete não tem máximo no contrato) |
| `null_spike` | products | 30% das categorias viram nulo | Taxa de nulos (coluna `nullable: true`) |
| `schema_drift` | order_reviews | Coluna nova `review_channel` | Contrato só avisa (*warning*); pilar de schema dispara |

As saídas reais de cada cenário estão em [6.5](06-execucao-e-testes.md#65-simulando-incidentes).

---

## 4.21 `tests/`

| Arquivo | O que garante |
|---|---|
| `conftest.py` | Histórico de exemplo compartilhado (`stable_history`) e um `NOW` fixo |
| `test_type_casting.py` | Conversões; 1.5 como inteiro vira nulo; o original não é alterado |
| `test_contract_validator.py` | Cada regra do contrato dispara quando deve, e só quando deve |
| `test_contracts.py` | Coerência catálogo ↔ contratos (toda tabela tem contrato, tipos conhecidos, colunas da PK declaradas) |
| `test_dimensions_dama.py` | As quatro dimensões, o cache de referência e o resumo |
| `test_anomaly_detection.py` | Z-score, janela móvel, EWMA, poda e casos de contaminação e tendência |
| `test_pillars.py` | Os cinco pilares |
| `test_slo.py` | Resolução de parâmetros, SLO lendo finding, coerência de `slo.yaml` |
| `test_incidents.py` | TTD, TTR, deduplicação, dupla resolução, incidentes abertos |
| `test_checks_runner.py` | Severidade, impacto, deduplicação e `occurrence_time` |
| `test_curated.py` | Transformações e o teste de guarda do lineage de campo |
| `test_certification.py` | Os cinco critérios de certificação |
| `test_rca.py` | RCA, postmortem e a CLI |
| `test_maturity_dashboard.py` | KPIs e renderização |
| `test_simulate_incident.py` | Os cenários do simulador |

Técnicas recorrentes: fixtures (dados novos por teste), `tmp_path` (pasta temporária apagada depois), `monkeypatch` (redirecionar a CLI para uma pasta temporária), `parametrize` (um teste com vários casos), `pytest.raises` (erro esperado) e números escolhidos para dar contas exatas (4 linhas → cada linha vale 25%).
