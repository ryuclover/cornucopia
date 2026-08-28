# Cornucopia 🌽

Sistema quantitativo de inteligência e agregação de sinais de traders experientes baseado no paradigma de **sobrevivência, consistência e preservação de capital**.

> *"Traders que historicamente demonstraram competência e sobrevivência estão concordando suficientemente nesta operação?"*

---

## 🏛️ Princípios Arquiteturais & Filosofia

1. **Sobreviver Primeiro, Lucrar Depois**: O sistema rejeita explicitamente abordagens que priorizam retornos explosivos à custa de risco de ruína ou drawdowns profundos.
2. **Separação Rígida em 3 Camadas**:
   - **Performance**: O que aconteceu factualmente com o trader (estatísticas puramente descritivas).
   - **Score**: Avaliação determinística da qualidade, estabilidade e sobrevivência do trader (0 a 100).
   - **Weight**: Poder de voto / alocação de capital futuro em um consenso (desacoplado do score).
3. **Ausência Estrita de Vieses**:
   - **Look-Ahead Bias**: Todo cálculo de métricas opera em modo *Point-in-Time* (`timestamp <= as_of`).
   - **Survivorship Bias**: Preservação de histórico de coortes completas, incluindo traders desqualificados.
   - **Auditoria e Imutabilidade**: Ordens e execuções (`Execution`) são eventos atômicos e imutáveis.

---

## 📦 Estrutura do Projeto

```
Cornucopia/
├── pyproject.toml              # Configurações de empacotamento e pytest
├── README.md                   # Documentação conceitual e técnica
├── src/
│   ├── domain/                 # Entidades fundamentais e regras de negócio
│   │   ├── enums.py            # OrderSide, PositionSide, AssetClass, TraderStatus
│   │   ├── instrument.py       # MarketInstrument (multiplicador, ticks, P&L)
│   │   ├── execution.py        # Execution (fill atômico e imutável para auditoria)
│   │   ├── trade.py            # ClosedTrade (operação finalizada / round-trip)
│   │   ├── position.py         # Position & PositionTracker (contabilidade FIFO, scale-in/out, reversões)
│   │   └── trader.py           # Trader (metadados e capital de referência)
│   ├── storage/                # [ETAPA 2] Persistência em SQLite
│   │   ├── database.py         # DatabaseManager e controle de transações
│   │   ├── schema.py           # DDL de tabelas e índices temporais
│   │   └── repositories/       # Repositórios (Trader, Instrument, Execution, MarketPrice)
│   ├── ingestion/              # [ETAPA 2] Ingestão e validação (CSV / JSON)
│   │   ├── models.py           # CanonicalExecutionInput, ImportReport, RowError
│   │   ├── csv_parser.py       # CsvParser com validação de colunas e linhas
│   │   ├── json_parser.py      # JsonParser (Array e JSON Lines)
│   │   ├── validator.py        # IngestionValidator com integridade referencial
│   │   └── importer.py         # ExecutionImporter com inserção idempotente em lote
│   ├── replay/                 # [ETAPA 2] Motor de Replay cronológico ponto-no-tempo
│   │   ├── models.py           # TraderReplayResult
│   │   └── engine.py           # TraderReplayEngine
│   ├── evaluation/             # [ETAPA 3] Avaliação longitudinal e estabilidade temporal
│   │   ├── models.py           # TraderEvaluationSnapshot, QualificationStatus, TraderStabilityMetrics
│   │   └── engine.py           # TraderEvaluationEngine (janelas 30d/90d/180d e séries temporais)
│   ├── portfolio/              # [ETAPA 3] Portfólio virtual individual
│   │   ├── models.py           # TraderVirtualPortfolio
│   │   └── service.py          # TraderVirtualPortfolioService (curvas de equity e drawdown)
│   ├── ranking/                # [ETAPA 3] Motor de ranking histórico e persistência
│   │   ├── models.py           # TraderRankingSnapshot, TraderRankingItem, TraderRankPersistence, Turnover
│   │   ├── engine.py           # TraderRankingEngine (Full e Qualified rankings point-in-time)
│   │   └── persistence.py      # RankPersistenceCalculator (% Top-N e turnover temporal)
│   ├── selection/              # [ETAPA 4] Seleção formal do núcleo de especialistas
│   │   ├── models.py           # SelectionStatus, TraderSelectionDecision, SelectedCoreSnapshot, SelectionChurnMetric
│   │   ├── policy.py           # TraderSelectionPolicy (máquina de estados determinística com histerese)
│   │   └── engine.py           # TraderSelectionEngine (avaliação de séries, snapshot do núcleo e churn)
│   ├── dependence/             # [ETAPA 5] Análise de independência, similaridade e correlação entre traders
│   │   ├── models.py           # DependenceLevel, TraderPairDependence, DependenceMatrix, RedundancyGroup, CoreDependenceSnapshot
│   │   ├── alignment.py        # TimeSeriesAligner (sincronização temporal de séries e exposições ponto-no-tempo)
│   │   ├── metrics.py          # DependenceCalculator (Pearson, Directional Agreement, Overlap de Posições/Ativos, Timing)
│   │   ├── clustering.py       # RedundancyClusterer (agrupamento transparente em blocos via componentes conexos)
│   │   └── engine.py           # TraderDependenceEngine (matriz de dependência e snapshots de independência do núcleo)
│   ├── synthetic/              # [ETAPAS 2/3/4/5] Gerador determinístico de dados e perfis comportamentais
│   │   └── generator.py        # SyntheticDataGenerator (Steady, Gambler, Deteriorating, Recovering, Outlier, Mirror, Anti, Timing)
│   ├── config/                 # Configurações e critérios de seleção
│   │   ├── survival_config.py  # SurvivalCriteriaConfig (limites configuráveis de sobrevivência)
│   │   ├── evaluation_config.py# [ETAPA 3] EvaluationConfig & EvaluationFrequency
│   │   ├── selection_config.py # [ETAPA 4] SelectionConfig (histerese e thresholds de seleção)
│   │   └── dependence_config.py# [ETAPA 5] DependenceConfig (pesos e thresholds de redundância)
│   ├── metrics/                # Motor de cálculo estatístico e métricas de risco
│   └── scoring/                # Algoritmo Survivor Score V1
└── tests/
    ├── unit/                   # Testes unitários cobrindo todos os módulos
    └── integration/            # Testes de integração multi-trader, pipeline longitudinal, seleção e dependência do núcleo
```

---

## 📊 Especificação do Survivor Score V1

O Survivor Score V1 é uma função determinística de 0 a 100 pontos composta por 4 sub-scores e filtros de corte (gatekeepers):

| Componente | Peso | Critério de Avaliação |
| :--- | :---: | :--- |
| **Preservação de Capital (Drawdown)** | **40%** | $100$ pts para DD $\le 5\%$; decaimento linear até $0$ no limite de DD tolerado. |
| **Consistência / Tail Risk & Concentração** | **25%** | Avalia perdas atípicas, sequências de derrotas e penaliza concentração excessiva nos Top trades (`top_n_trades_pnl_contribution_pct`). |
| **Retorno Ajustado ao Risco** | **20%** | Avalia Sortino Ratio (foco em downside deviation) e Profit Factor com saturação suave. |
| **Maturidade e Amostragem** | **15%** | Recompensa histórico maduro ($\ge 180$ dias e $\ge 100$ operações). |

### Gatekeepers Rígidos (Desqualificação)
Se um trader violar limites como Drawdown Máximo permitido, Perda Catastrófica Individual ou apresentar Retorno Líquido negativo (`min_net_return_pct`), ele é marcado como desqualificado (`is_qualified = False`) e tem seu score zerado para fins de consenso. O *Win Rate* permanece como métrica descritiva para não prejudicar estratégias de *trend following* com alto *payoff*.

---

## 🧪 Executando os Testes

```bash
python -m pytest -v
```
