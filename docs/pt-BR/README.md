# Documentação — Qualidade de Dados no E-Commerce Olist

> [English version](../en/README.md)

Material de estudo do projeto: a teoria do livro **Data Quality Fundamentals** (Moses, Gavish e Vorwerck, O'Reilly), o que cada parte do código faz, por que ela existe, por que foi feita daquela forma e como executar, testar e simular incidentes.

| Capítulo | Conteúdo |
|---|---|
| [1. Visão geral](01-visao-geral.md) | O problema, o que o projeto entrega, o fluxo de ponta a ponta e os princípios |
| [2. Fundamentos teóricos](02-fundamentos-teoricos.md) | Data downtime, contratos, circuit breaker, DAMA, os cinco pilares, detecção de anomalias, SLA/SLI/SLO, incidentes, lineage, certificação e maturidade, cada um ligado ao código |
| [3. Arquitetura](03-arquitetura.md) | Camadas, módulos, as 17 decisões de arquitetura com justificativa e custo aceito, e as alternativas para produção |
| [4. Referência de código](04-referencia-de-codigo.md) | Cada módulo: funções, parâmetros, retornos, saídas e decisões de implementação |
| [5. Configuração](05-configuracao.md) | Todas as chaves dos YAMLs e receitas para mudanças comuns |
| [6. Execução e testes](06-execucao-e-testes.md) | Como rodar, onde ficam as saídas, testes, simulação de incidentes com resultados reais, CLI de incidentes e problemas comuns |
| [7. Guia de estudo](07-guia-de-estudo.md) | Roteiro de leitura, glossário, limitações conhecidas, perguntas de revisão e exercícios |

**Para começar rápido:** leia o capítulo 1, rode o roteiro de [6.5](06-execucao-e-testes.md#65-simulando-incidentes) e volte aos capítulos 2 e 3 com os resultados na mão.
