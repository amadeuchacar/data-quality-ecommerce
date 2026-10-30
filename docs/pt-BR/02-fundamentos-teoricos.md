# 2. Fundamentos teóricos

> [Índice](README.md) · Anterior: [1. Visão geral](01-visao-geral.md) · Próximo: [3. Arquitetura](03-arquitetura.md)

Este capítulo apresenta os conceitos do livro **Data Quality Fundamentals** usados no projeto e mostra, para cada um, onde ele vive no código.

As dimensões DAMA (seção 2.4) não vêm do livro: são um complemento do DAMA UK, incluído porque dá uma medida gradual que o livro não formaliza.

## 2.1 Data downtime

**Conceito.** Data downtime é o período em que os dados estão parciais, errados, ausentes ou imprecisos. O livro propõe medi-lo como:

```
data downtime = nº de incidentes × (TTD + TTR)
```

- **TTD (time to detection):** quanto tempo o problema existiu até alguém (ou algo) perceber.
- **TTR (time to resolution):** quanto tempo levou da detecção até a correção.

A fórmula mostra as três alavancas para reduzir o downtime: ter menos incidentes (prevenção), detectar mais rápido (observabilidade) e resolver mais rápido (processo de incidentes).

**No projeto.** Cada etapa ataca uma alavanca:

| Alavanca | Mecanismo | Onde |
|---|---|---|
| Menos incidentes | Contrato bloqueia dado ruim antes do staging | `src/ingestion/` |
| TTD menor | Pilares comparam cada execução com o histórico | `src/quality/pillars/`, `src/quality/checks_runner.py` |
| TTR menor | Incidente já nasce com impacto, severidade e contexto; CLI guia a resolução | `src/incidents/` |
| Medir as três | Dashboard mostra nº de incidentes, TTD médio e TTR médio | `src/reporting/maturity_dashboard.py` |

O TTD é calculado de forma realista: o momento em que o problema "começou" é o timestamp do snapshot que trouxe o dado anômalo, e não o momento da detecção (ver [4.7](04-referencia-de-codigo.md#47-srcqualitychecks_runnerpy)).

## 2.2 Testes declarativos e contratos de dados

**Conceito.** A primeira defesa é declarar, *antes* de olhar o dado, o que ele precisa cumprir: schema, nulabilidade, unicidade, valores aceitos, faixas de negócio, volume mínimo. É o equivalente, em dados, aos testes unitários em software. O termo de mercado para esse acordo entre produtor e consumidor é **contrato de dados**.

O limite dessa abordagem, que o livro enfatiza, é que testes só pegam o que alguém pensou em escrever. Por isso eles são necessários, mas não suficientes.

**No projeto.** Um YAML por tabela em `contracts/`, validado por `validate_dataframe()` (ver [4.3](04-referencia-de-codigo.md#43-srcingestioncontract_validatorpy)). Nove regras, oito delas bloqueantes:

| Regra | Exemplo real que ela pega |
|---|---|
| Coluna faltando | O contrato declarava `product_lenght_cm`, mas o CSV tem `product_length_cm` |
| Coluna extra (*warning*) | Uma coluna nova aparece no CSV (schema drift) |
| Tipo não conversível | Texto em `price` |
| Nulo proibido | `order_id` vazio |
| Duplicata | `order_id` repetido |
| PK nula ou duplicada (simples ou composta) | O mesmo `(order_id, order_item_id)` duas vezes |
| Valor fora da lista | `review_score = 6` |
| Fora da faixa | `price` negativo |
| Volume abaixo do SLA | `orders` com 5 mil linhas em vez de 99 mil |

## 2.3 Circuit breaker

**Conceito.** Um circuit breaker interrompe o fluxo de um dado ruim antes que ele se propague para quem consome. A ideia vem da engenharia elétrica e de sistemas distribuídos: é melhor desligar uma parte do que deixar a falha se espalhar.

**No projeto.** Em `ingest_table()`, se o contrato tiver qualquer violação *blocking*, a função retorna antes de gravar o Parquet. Três decisões importam aqui:

1. **O breaker é por tabela, não por pipeline.** `products` reprovada não impede `sellers` de seguir.
2. **A tabela barrada não grava snapshot.** Ela vai "envelhecendo" até o SLO de freshness disparar. Assim o bloqueio fica visível para todos os consumidores, sem um mecanismo extra.
3. **O processo termina com exit code 1**, para que um orquestrador (cron, CI, Airflow) também reaja.

## 2.4 Dimensões de qualidade (DAMA UK)

**Conceito.** O DAMA UK define seis dimensões de qualidade: completude, unicidade, validade, consistência, atualidade (*timeliness*) e acurácia. Elas expressam a qualidade como **porcentagem**, e não como passa/falha.

**No projeto.** Quatro dimensões são medidas sobre o staging (`src/quality/dimensions_dama.py`). Atualidade fica com o pilar de freshness. Acurácia exigiria uma fonte de verdade externa que o dataset não tem.

Uma consequência importante, discutida em [3.3](03-arquitetura.md#33-decisões-de-arquitetura): como só dado aprovado chega ao staging, validade e unicidade dão 100% **por construção**. No staging elas funcionam como certificação do portão. A informação nova vem de duas dimensões:

- **Consistência:** cruza tabelas, algo que o contrato (que olha uma tabela por vez) não faz. Ela encontrou 278 clientes e 7 vendedores com CEP sem coordenada em `geolocation`, e categorias de produto sem tradução.
- **Completude de colunas opcionais:** é perfil, não reprovação.

## 2.5 Os cinco pilares da observabilidade de dados

**Conceito.** Observabilidade é a capacidade de entender a saúde do dado a partir dos sinais que ele emite. O livro organiza esses sinais em cinco pilares:

| Pilar | Pergunta | Exemplo de falha |
|---|---|---|
| **Freshness** | O dado está atualizado? | O job de ontem não rodou |
| **Volume** | A quantidade está dentro do esperado? | Chegou metade das linhas |
| **Schema** | A estrutura mudou? | Coluna removida, tipo trocado |
| **Distribution** | Os valores estão no padrão? | Média de preço dobrou, nulos dispararam |
| **Lineage** | De onde vem e para onde vai? | Quem é afetado se esta tabela quebrar |

A diferença em relação aos testes da seção 2.2: os pilares não precisam que alguém preveja a falha. Eles comparam o presente com o passado.

**No projeto.** Um módulo por pilar em `src/quality/pillars/`, todos com a mesma interface de retorno (`PillarFinding`). O histórico vem de `reports/quality_history/<tabela>.jsonl`, uma linha por ingestão aprovada.

| Pilar | Como é medido |
|---|---|
| Freshness | Horas desde o último snapshot vs `max_delay_hours` |
| Volume | Z-score de `n_rows` contra as execuções recentes |
| Schema | Colunas e dtypes vs execução anterior |
| Distribution | Z-score da média de cada coluna numérica e da taxa de nulos de cada coluna |
| Lineage | Grafo de dependência construído a partir das `foreign_keys` do catálogo |

## 2.6 Detecção de anomalias

**Conceito.** Limites fixos ("alerte se vier menos de 90 mil linhas") são frágeis: ou são frouxos e não pegam nada, ou são apertados e geram alarme falso. O livro defende limites **aprendidos com o histórico**. O mais simples é o Z-score:

```
z = (valor_atual - média_histórica) / desvio_padrão_histórico
```

Um |z| > 3 indica que o valor está a mais de 3 desvios-padrão da média, algo improvável se nada mudou.

**Os problemas do Z-score global, e como o projeto os resolve:**

| Problema | Exemplo | Solução no projeto |
|---|---|---|
| **Contaminação:** um incidente antigo no histórico infla o desvio-padrão e esconde problemas novos | Um dia com 7,5×10¹⁰ linhas faz uma queda de 100 para 500 parecer normal | Poda de outliers por mediana/MAD antes de calcular a linha de base |
| **Tendência:** numa série crescente, um valor antigo parece normal | Série de 100 a 390; o valor 150 está dentro da média global, mas é uma queda enorme em relação a ~390 | Janela móvel com as últimas 10 execuções (`method: moving`) |
| **Variância zero:** com histórico constante, qualquer diferença vira alarme | 99.441 linhas sempre; 99.440 dispara | Piso de desvio-padrão de 1% da média (`min_rel_std`), que tolera variações de até ~3% |
| **Pouco histórico** | Com 1 ou 2 pontos não há desvio-padrão confiável | Abaixo de 3 execuções anteriores o resultado é `insufficient_history` |

Também está disponível o EWMA (média móvel exponencial), que dá mais peso às execuções recentes. Tudo isso está em `src/monitoring/anomaly_detection.py` (ver [4.8](04-referencia-de-codigo.md#48-srcmonitoringanomaly_detectionpy)).

## 2.7 SLA, SLI e SLO

**Conceito.** A tríade vem da engenharia de confiabilidade (SRE) e o livro a adapta para dados:

- **SLA (acordo):** o compromisso com quem consome. Exemplo: "`orders` nunca fica mais de 24h sem carga."
- **SLI (indicador):** o que se mede. Exemplo: horas desde a última carga bem-sucedida.
- **SLO (objetivo):** a meta numérica que o SLI precisa cumprir. Exemplo: `max_delay_hours: 24`.

**No projeto.** Em `config/slo.yaml`, cada SLO aponta uma tabela, um pilar, uma descrição (o SLA) e os parâmetros (o SLO). O pilar é o SLI. Uma decisão central (ver [3.3](03-arquitetura.md#33-decisões-de-arquitetura)) é que cada par (tabela, pilar) é avaliado **uma única vez**, com os parâmetros do SLO; o alerta, o incidente, o SLO e a certificação leem o mesmo veredito.

## 2.8 Gestão de incidentes

**Conceito.** Detectar é metade do trabalho. O livro descreve um processo de resposta com estes elementos:

- **Triagem com contexto:** severidade, impacto e quem foi afetado.
- **Comunicação:** alerta para as pessoas certas.
- **Análise de causa raiz (RCA):** por que o problema foi possível.
- **Postmortem *blameless*:** registrar o aprendizado sem procurar culpados, para que as pessoas relatem problemas em vez de escondê-los.

**No projeto:**

| Elemento | Implementação |
|---|---|
| Severidade | Criticidade da tabela no catálogo: `high` → critical, `medium` → warning, `low` → info |
| Impacto | Tabelas a jusante pelo grafo de lineage, gravadas no incidente |
| Comunicação | Console + `reports/alerts/alerts.jsonl` + webhook opcional (Slack) |
| Registro | `reports/incidents/incidents.jsonl`, com TTD |
| Sem duplicatas | Um incidente por (tabela, pilar, snapshot) |
| RCA | Técnica dos 5 porquês, registrada pela CLI |
| Postmortem | Markdown *blameless* por desenho: tem campo de "quem investigou", nunca de "quem causou" |
| Resolução | CLI registra a resolução e calcula o TTR |

## 2.9 Lineage

**Conceito.** Lineage responde duas perguntas de incidente:

- **"Quem é afetado?"** (impacto a jusante, ou *downstream*)
- **"De onde veio o problema?"** (causa a montante, ou *upstream*)

O livro distingue o lineage **de tabela** do lineage **de campo** e defende reconstruir a cadeia inteira de dependências, não só a relação direta.

**No projeto:**

- **Lineage de tabela** (`src/quality/pillars/lineage.py`): derivado das chaves estrangeiras do catálogo. Se `order_items` referencia `orders`, um problema em `orders` alcança quem cruza as duas tabelas. É uma aproximação, discutida em [7.3](07-guia-de-estudo.md#73-limitações-conhecidas).
- **Lineage de campo** (`src/lineage/field_lineage.py`): sem SQL para analisar (o projeto usa pandas), cada transformação é **declarada** num registro. A recursão reconstrói a cadeia completa: `orders_curated.order_total_value` → `order_items_curated.item_total_value` → `order_items.price` + `order_items.freight_value`. Um teste falha se o código criar uma coluna sem entrada no registro.

## 2.10 Certificação de dados

**Conceito.** A certificação é um selo explícito de "pode usar", concedido apenas quando o dado cumpre critérios objetivos. O livro descreve passos para chegar lá: observabilidade, donos definidos, definição de "dado bom", SLAs/SLOs, processo de incidentes, o mecanismo de certificação e o treinamento das pessoas.

**No projeto** (`src/certification/certify.py`), uma tabela é certificada quando:

1. tem dono (específico ou o `owner_default` do catálogo);
2. tem histórico suficiente para **todos** os pilares darem um veredito;
3. nenhum pilar está em anomalia;
4. não tem incidente aberto.

O critério 2 aplica o princípio "falta de evidência não é evidência". O critério 4 conecta o processo de incidentes à certificação: a tabela só volta a ser certificada depois que alguém registra a resolução.

## 2.11 Maturidade

**Conceito.** Confiabilidade de dados é uma prática que amadurece com o tempo: de reativa (descobrir problemas pelos consumidores) a proativa e automatizada. Para saber se a prática está melhorando, é preciso medir.

**No projeto.** O dashboard (`src/reporting/maturity_dashboard.py`) consolida a cada execução:

- incidentes (total, abertos, resolvidos, por severidade);
- TTD médio e TTR médio;
- porcentagem de SLOs cumpridos;
- porcentagem de tabelas certificadas.

Com esses números, a fórmula de data downtime da seção 2.1 pode ser estimada diretamente.

## 2.12 Resumo: do livro ao código

| Conceito | Arquivo principal |
|---|---|
| Data downtime (nº × (TTD + TTR)) | `src/reporting/maturity_dashboard.py` |
| Testes declarativos / contrato | `contracts/*.yaml`, `src/ingestion/contract_validator.py` |
| Circuit breaker | `src/ingestion/load_raw.py` (`ingest_table`) |
| Dimensões DAMA (complemento) | `src/quality/dimensions_dama.py` |
| Freshness, volume, schema, distribution | `src/quality/pillars/*.py` |
| Detecção de anomalias | `src/monitoring/anomaly_detection.py` |
| SLA / SLI / SLO | `config/slo.yaml`, `src/quality/sli_slo.py` |
| Alertas | `src/monitoring/alerting.py` |
| Incidentes, TTD, TTR | `src/incidents/incident_log.py` |
| RCA e postmortem *blameless* | `src/incidents/rca.py`, `src/incidents/cli.py` |
| Lineage de tabela | `src/quality/pillars/lineage.py` |
| Lineage de campo | `src/lineage/field_lineage.py` |
| Certificação | `src/certification/certify.py` |
| Maturidade | `src/reporting/maturity_dashboard.py` |
