# 7. Guia de estudo

> [Índice](README.md) · Anterior: [6. Execução e testes](06-execucao-e-testes.md)

## 7.1 Roteiro de leitura do código

Uma ordem que segue o caminho do dado e vai do mais simples ao mais composto:

1. `src/config.py`: de onde vêm os caminhos.
2. `contracts/orders.contract.yaml` e `catalog/inventory.yaml`: as regras e os metadados.
3. `src/ingestion/type_casting.py` → `contract_validator.py` → `load_raw.py`: a Etapa 1.
4. `src/monitoring/history_store.py`: o que é guardado a cada execução.
5. `src/quality/dimensions_dama.py`: a Etapa 2.
6. `src/monitoring/anomaly_detection.py` → `src/quality/pillars/` → `sli_slo.py` → `checks_runner.py`: a Etapa 3.
7. `src/incidents/` → `src/certification/` → `src/transform/` + `src/lineage/` → `src/reporting/`: a Etapa 4.
8. `orchestration/local_pipeline.py`: tudo junto.

Para cada módulo, leia o teste correspondente em `tests/`. Os testes são a especificação executável do comportamento.

## 7.2 Glossário

| Termo | Definição |
|---|---|
| **Data downtime** | Período em que os dados estão parciais, errados, ausentes ou imprecisos; estimado por nº de incidentes × (TTD + TTR) |
| **Contrato de dados** | Acordo declarado sobre schema, regras e volume que uma tabela precisa cumprir |
| **Circuit breaker** | Mecanismo que interrompe o fluxo de um dado ruim antes que ele se propague |
| **Staging** | Camada com dado validado e tipado |
| **Curated** | Camada analítica, com campos derivados documentados |
| **Snapshot** | Retrato das métricas de uma tabela numa execução (linhas, colunas, tipos, nulos, médias) |
| **Pilar** | Uma dimensão de observabilidade: freshness, volume, schema, distribution, lineage |
| **Z-score** | Distância de um valor à média, em desvios-padrão |
| **MAD** | *Median absolute deviation*: medida de dispersão robusta a outliers |
| **EWMA** | Média móvel exponencialmente ponderada: dá mais peso ao recente |
| **Linha de base** | Os valores históricos contra os quais o presente é comparado |
| **SLA / SLI / SLO** | Acordo / indicador medido / meta numérica |
| **TTD / TTR** | Tempo até a detecção / tempo até a resolução |
| **RCA** | Análise de causa raiz (aqui, a técnica dos 5 porquês) |
| **Postmortem *blameless*** | Registro do incidente focado em aprendizado, sem atribuir culpa |
| **Lineage de tabela / de campo** | Dependência entre tabelas / entre colunas |
| **Downstream / upstream** | A jusante (quem consome) / a montante (de onde vem) |
| **Certificação** | Selo de "pode usar", concedido com evidência e sem incidente aberto |
| **Dimensões DAMA** | Completude, unicidade, validade, consistência, atualidade, acurácia |
| **`insufficient_history`** | "Não há evidência suficiente para afirmar nada" |

## 7.3 Limitações conhecidas

Limitações conscientes, com a solução que um próximo passo poderia adotar:

| Limitação | Consequência | Solução possível |
|---|---|---|
| **Schema comparado só com a execução anterior** | Reverter uma mudança também conta como mudança e abre um segundo incidente (observado no `schema_drift`) | Comparar também com o schema do contrato, tratando o retorno ao esperado como recuperação |
| **Freshness mede a carga, não o dado** | O dataset é estático (2016–2018). "Atualizado" significa "carregado recentemente", não "contém pedidos recentes" | Medir também o `max(order_purchase_timestamp)` |
| **Lineage de tabela via chaves estrangeiras** | Superestima o impacto: quase tudo alcança `orders` | Lineage extraído dos jobs e das queries |
| **SLO por execução** | Um SLO real mede uma janela ("99% das execuções do mês") | Calcular sobre `alerts.jsonl` e o histórico |
| **Tabela nunca carregada = `insufficient_history`** | Uma tabela que nunca chegou ao staging não dispara freshness | Tratar "sem histórico" como anomalia de freshness para tabelas do catálogo |
| **Camada curada não é monitorada** | Os pilares olham só o staging | Snapshots também das tabelas curadas |
| **Percentuais DAMA não viram SLO** | Consistência de 99,72% é só informativa | Pilar `dama` com metas no `slo.yaml` |
| **CEP lido como inteiro** | Zeros à esquerda se perdem (01001 vira 1001) | Declarar o CEP como `string` e fixar o dtype na leitura |
| **Data downtime não é calculado** | O dashboard tem os três fatores, mas não o produto | `n_incidents × (ttd_mean + ttr_mean)` no dashboard |
| **pandas, JSONL e processo único** | Não escala nem aceita concorrência | Ver [3.5](03-arquitetura.md#35-custos-aceitos-e-alternativas-para-produção) |

## 7.4 Perguntas de revisão

As respostas curtas estão entre parênteses.

**Conceitos**

1. Por que um pipeline sem erro pode causar data downtime? (O job mede se *rodou*, não se o dado está *certo*.)
2. Quais são as três alavancas da fórmula de data downtime, e qual etapa ataca cada uma? (Menos incidentes: contrato. TTD: pilares. TTR: processo de incidentes.)
3. Por que o contrato não basta? (Só pega o que alguém previu; os pilares pegam o imprevisto.)
4. Qual a diferença entre SLA, SLI e SLO? (Acordo, indicador medido, meta numérica.)
5. Por que o postmortem é *blameless*? (Para que as pessoas relatem problemas em vez de escondê-los; o foco é por que o sistema permitiu a falha.)

**Arquitetura**

6. Se `products` violar o contrato, o que acontece com `sellers`? (Nada: o circuit breaker é por tabela.)
7. Por que o snapshot não é gravado quando o contrato falha? (Para não contaminar a linha de base, e para que a tabela envelheça e o freshness a denuncie.)
8. Por que validade e unicidade dão 100% no staging? (O contrato já barrou antes o que as quebraria; no staging elas são certificação do portão.)
9. Por que cada pilar é avaliado uma única vez por execução? (Para o alerta, o incidente, o SLO, a certificação e o dashboard nunca discordarem.)
10. Por que `insufficient_history` passa no SLO, mas impede a certificação? (Reprovar sem dados geraria alarme falso; certificar sem dados daria confiança sem base.)
11. Por que a severidade não usa a pior criticidade entre as tabelas impactadas? (Com FKs como lineage, quase tudo alcança `orders` e todo alerta viraria crítico.)
12. Por que a camada curada só é reconstruída se as fontes passaram nesta execução? (Para não misturar dado novo de uma tabela com dado velho de outra.)

**Código**

13. Como o validador descobre que um valor não é do tipo declarado? (Converte com `errors="coerce"` e conta os nulos que não existiam antes.)
14. Por que `.fillna(False)` nas máscaras de faixa? (Comparações com `Int64` devolvem `<NA>` onde o valor é nulo.)
15. Por que a poda de outliers usa mediana e MAD, e não média e desvio? (São robustas: o próprio outlier quase não as afeta.)
16. Por que uma queda de 8% em `orders` deu z = −8 exatamente? (Histórico constante → o piso de desvio é 1% da média → 8% / 1% = 8.)
17. Como o sistema evita abrir o mesmo incidente a cada execução? (Chave (tabela, pilar, snapshot) em `find_incident`.)
18. Como o TTD é estimado? (Pilares de dados: a partir do snapshot que trouxe o dado; freshness: a partir do momento em que o atraso cruzou o SLO.)
19. O que impede o registro de lineage de campo de ficar desatualizado? (O teste que compara as colunas criadas pelo código com as declaradas no registro.)
20. Por que `total_order_value` foi renomeado? (O valor era por item, e 9.803 pedidos têm vários itens; quem somasse a coluna erraria a receita.)

## 7.5 Exercícios sugeridos

1. Aplique o `null_spike` e confirme pelo `pillars_report` qual coluna disparou e com qual Z.
2. Crie um SLO de distribution para `products` e repita o exercício 1: agora o exit code deve ser 1.
3. Leia `test_moving_detects_anomaly_masked_by_contaminated_history` em `tests/test_anomaly_detection.py` e calcule à mão a média e o desvio do histórico com e sem o outlier. Explique por que o Z-score global não vê o valor 500.
4. Acrescente ao catálogo uma FK que aponte para uma coluna inexistente (por exemplo, `sellers.seller_region → geolocation.region`) e observe o resultado `-` (não aplicável) na consistência DAMA. Depois remova a FK.
5. Implemente o cálculo de data downtime no dashboard e escreva o teste correspondente.
