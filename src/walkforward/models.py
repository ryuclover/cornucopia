from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from pydantic import BaseModel, Field
from src.config.walkforward_config import (
    BaselineMode,
    EvaluationStatus,
    OutcomeClassification,
    RunPurpose,
)
from src.consensus.models import ConsensusDirection


class WalkForwardDecision(BaseModel):
    """
    Decisão de consenso individual e imutável congelada estritamente em 'decision_as_of'.
    """
    decision_id: str = Field(..., description="ID único determinístico da decisão")
    decision_as_of: datetime = Field(..., description="Timestamp UTC de congelamento da decisão")
    symbol: str = Field(..., description="Símbolo do instrumento avaliado")
    
    selected_trader_ids: list[str] = Field(default_factory=list, description="IDs dos traders selecionados no Core em decision_as_of")
    selected_core_count: int = Field(default=0, ge=0, description="Tamanho do Selected Core no instante da decisão")
    trader_weights: dict[str, float] = Field(default_factory=dict, description="Mapeamento trader_id -> peso normalizado no Core")
    
    consensus_direction: ConsensusDirection = Field(..., description="Direção de consenso apurada")
    long_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Peso ponderado pró-LONG")
    short_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Peso ponderado pró-SHORT")
    flat_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Peso ponderado de traders FLAT")
    no_opinion_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Peso de traders sem opinião (NO_OPINION)")
    unknown_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Peso de traders com estado desconhecido (UNKNOWN)")
    
    coverage_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Fração de autoridade do Core com opinião")
    directional_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Fração de autoridade posicionada direcionalmente")
    directional_agreement_long: float = Field(default=0.0, ge=0.0, le=1.0, description="Concordância direcional pró-LONG")
    directional_agreement_short: float = Field(default=0.0, ge=0.0, le=1.0, description="Concordância direcional pró-SHORT")
    consensus_margin: float = Field(default=0.0, description="Margem líquida: long_weight - short_weight")
    
    supporting_trader_count: int = Field(default=0, ge=0, description="Quantidade de traders apoiando a direção vencedora")
    supporting_independent_group_count: int = Field(default=0, ge=0, description="Quantidade de grupos independentes confirmando a direção")
    group_direction_breakdown: dict[str, Any] = Field(default_factory=dict, description="Detalhamento do estado interno de cada grupo")
    
    config_fingerprint: str = Field(default="", description="Hash ou identificador da configuração congelada")
    reasons: list[str] = Field(default_factory=list, description="Trilha explicativa da decisão de consenso")
    triggered_rules: list[str] = Field(default_factory=list, description="Regras acionadas na decisão")
    diagnostics: dict[str, Any] = Field(default_factory=dict, description="Metadados internos da decisão")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class WalkForwardDecisionJournal(BaseModel):
    """
    Diário cronológico completo de todas as decisões congeladas durante o Walk-Forward.
    """
    decisions: list[WalkForwardDecision] = Field(default_factory=list, description="Lista cronológica de decisões")
    decisions_by_symbol: dict[str, list[WalkForwardDecision]] = Field(default_factory=dict, description="Decisões agrupadas por símbolo")
    
    total_decisions: int = Field(default=0, ge=0, description="Total de decisões registradas")
    long_decisions: int = Field(default=0, ge=0, description="Total de decisões LONG")
    short_decisions: int = Field(default=0, ge=0, description="Total de decisões SHORT")
    neutral_decisions: int = Field(default=0, ge=0, description="Total de decisões NEUTRAL")
    no_consensus_decisions: int = Field(default=0, ge=0, description="Total de decisões NO_CONSENSUS")
    insufficient_coverage_decisions: int = Field(default=0, ge=0, description="Total de decisões INSUFFICIENT_COVERAGE")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class ForwardReturnOutcome(BaseModel):
    """
    Desfecho e retorno futuro de uma decisão congelada medido em determinado horizonte temporal.
    """
    decision_id: str = Field(..., description="ID da decisão correspondente")
    symbol: str = Field(..., description="Símbolo do instrumento")
    decision_as_of: datetime = Field(..., description="Timestamp da decisão congelada")
    horizon_days: int = Field(..., ge=1, description="Horizonte de medição em dias (+1, +5, +20)")
    outcome_as_of: Optional[datetime] = Field(default=None, description="Timestamp do preço futuro de avaliação")
    
    reference_price: Optional[Decimal] = Field(default=None, description="Preço de referência no instante da decisão")
    future_price: Optional[Decimal] = Field(default=None, description="Preço futuro no horizonte avaliado")
    
    raw_return_pct: Optional[float] = Field(default=None, description="Retorno do ativo: (future_price - ref_price) / ref_price")
    signed_return_pct: Optional[float] = Field(default=None, description="Retorno condicionado à direção (+return para LONG, -return para SHORT)")
    
    direction_correct: Optional[bool] = Field(default=None, description="True se a direção econômica do consenso se confirmou")
    outcome_class: OutcomeClassification = Field(default=OutcomeClassification.UNEVALUABLE, description="Classificação do desfecho")
    evaluation_status: EvaluationStatus = Field(default=EvaluationStatus.EVALUATED, description="Status de integridade da avaliação")
    
    mae_pct: Optional[float] = Field(default=None, description="Maximum Adverse Excursion no período em %")
    mfe_pct: Optional[float] = Field(default=None, description="Maximum Favorable Excursion no período em %")
    
    diagnostics: dict[str, Any] = Field(default_factory=dict, description="Metadados do cálculo do outcome")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class ConsensusEpisode(BaseModel):
    """
    Episódio direcional contínuo de consenso de um instrumento.
    """
    episode_id: str = Field(..., description="ID único do episódio")
    symbol: str = Field(..., description="Símbolo do instrumento")
    direction: ConsensusDirection = Field(..., description="Direção contínua do episódio (LONG ou SHORT)")
    
    start_as_of: datetime = Field(..., description="Timestamp da primeira decisão do episódio")
    end_as_of: datetime = Field(..., description="Timestamp da última decisão antes da transição")
    decision_count: int = Field(default=1, ge=1, description="Quantidade de decisões consecutivas mantendo a direção")
    
    terminated_by: ConsensusDirection = Field(..., description="Direção que encerrou o episódio")
    is_direct_flip: bool = Field(default=False, description="True se encerrou em reversão direta severa (LONG <-> SHORT)")
    
    entry_reference_price: Optional[Decimal] = Field(default=None, description="Preço de entrada no início do episódio")
    exit_reference_price: Optional[Decimal] = Field(default=None, description="Preço de saída no término do episódio")
    
    episode_raw_return_pct: Optional[float] = Field(default=None, description="Retorno bruto do ativo durante todo o episódio")
    episode_signed_return_pct: Optional[float] = Field(default=None, description="Retorno assinado acumulado do episódio")
    episode_outcome_class: OutcomeClassification = Field(default=OutcomeClassification.UNEVALUABLE, description="Classificação do episódio")
    
    average_consensus_margin: float = Field(default=0.0, description="Margem média de liderança no episódio")
    average_independent_groups: float = Field(default=0.0, description="Média de grupos independentes apoiando o episódio")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class ShadowEquityPoint(BaseModel):
    """
    Ponto na curva de equity da Shadow Strategy unitária.
    """
    as_of: datetime = Field(..., description="Timestamp do ponto de equity")
    symbol: str = Field(..., description="Símbolo do instrumento")
    consensus_direction: ConsensusDirection = Field(..., description="Direção de consenso vigente")
    target_exposure: float = Field(..., description="Exposição normalizada: +1.0, -1.0 ou 0.0")
    
    raw_price_return: float = Field(default=0.0, description="Retorno percentual do preço do ativo no período")
    gross_period_return: float = Field(default=0.0, description="Retorno bruto gerado pela exposição no período")
    turnover: float = Field(default=0.0, ge=0.0, description="Giro de exposição na transição (0, 1 ou 2)")
    friction_cost: float = Field(default=0.0, ge=0.0, description="Custo financeiro de fricção no período")
    net_period_return: float = Field(default=0.0, description="Retorno líquido após fricção no período")
    
    gross_equity: float = Field(default=1.0, ge=0.0, description="Valor acumulado da cota bruta (base 1.0)")
    net_equity: float = Field(default=1.0, ge=0.0, description="Valor acumulado da cota líquida (base 1.0)")
    drawdown: float = Field(default=0.0, ge=0.0, le=1.0, description="Drawdown corrente sobre o pico líquido em fração (0.0 a 1.0)")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class ShadowStrategyResult(BaseModel):
    """
    Resultado global consolidado da Shadow Strategy.
    """
    symbol: str = Field(..., description="Símbolo do instrumento avaliado")
    equity_curve: list[ShadowEquityPoint] = Field(default_factory=list, description="Série temporal de pontos de equity")
    
    cumulative_gross_return: float = Field(default=0.0, description="Retorno bruto acumulado total (%)")
    cumulative_net_return: float = Field(default=0.0, description="Retorno líquido acumulado total (%)")
    annualized_return: Optional[float] = Field(default=None, description="Retorno anualizado líquido (se aplicável)")
    
    volatility: Optional[float] = Field(default=None, description="Volatilidade anualizada dos retornos líquidos (%)")
    sharpe_ratio: Optional[float] = Field(default=None, description="Índice Sharpe simplificado (taxa livre de risco = 0)")
    sortino_ratio: Optional[float] = Field(default=None, description="Índice Sortino sobre retornos negativos")
    max_drawdown: float = Field(default=0.0, ge=0.0, le=1.0, description="Drawdown máximo da estratégia em fração (0.0 a 1.0)")
    calmar_ratio: Optional[float] = Field(default=None, description="Índice Calmar (Retorno Anualizado / Max Drawdown)")
    
    positive_period_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="% de períodos com retorno líquido positivo")
    total_turnover: float = Field(default=0.0, ge=0.0, description="Giro acumulado total de exposição")
    total_simulated_costs: float = Field(default=0.0, ge=0.0, description="Total de custos simulados deduzidos (%)")
    
    time_in_market_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="% do tempo posicionado (LONG ou SHORT)")
    long_exposure_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="% do tempo posicionado em LONG")
    short_exposure_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="% do tempo posicionado em SHORT")
    flat_exposure_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="% do tempo zerado/sem consenso")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class EfficacyMetricSet(BaseModel):
    """
    Conjunto padronizado de métricas de eficácia para um grupo de outcomes (ALL ou NON_OVERLAPPING).
    """
    observation_count: int = Field(default=0, ge=0, description="Total de observações no conjunto")
    directional_count: int = Field(default=0, ge=0, description="Total de observações direcionais (LONG ou SHORT)")
    correct_count: int = Field(default=0, ge=0, description="Decisões direcionais corretas")
    incorrect_count: int = Field(default=0, ge=0, description="Decisões direcionais incorretas")
    neutral_outcome_count: int = Field(default=0, ge=0, description="Decisões na banda neutra")
    
    hit_rate_pct: Optional[float] = Field(default=None, description="Taxa de acerto direcional (%)")
    average_signed_return_pct: float = Field(default=0.0, description="Retorno assinado médio (%)")
    median_signed_return_pct: float = Field(default=0.0, description="Retorno assinado mediano (%)")
    return_std_pct: float = Field(default=0.0, description="Desvio padrão dos retornos assinados (%)")
    payoff_ratio: Optional[float] = Field(default=None, description="Razão de Payoff: |méd_pos / méd_neg|")
    
    average_positive_return_pct: float = Field(default=0.0, description="Média dos retornos positivos (%)")
    average_negative_return_pct: float = Field(default=0.0, description="Média dos retornos negativos (%)")
    best_outcome_pct: Optional[float] = Field(default=None, description="Melhor outcome individual (%)")
    worst_outcome_pct: Optional[float] = Field(default=None, description="Pior outcome individual (%)")
    
    top_1_outcome_pct: float = Field(default=0.0, description="Contribuição do melhor trade individual (%)")
    top_5_sum_pct: float = Field(default=0.0, description="Soma dos 5 melhores trades (%)")
    top_10_percent_sum_pct: float = Field(default=0.0, description="Soma do top 10% de trades (%)")
    percentiles: dict[str, Optional[float]] = Field(default_factory=dict, description="Percentis (P10, P25, Mediana, P75, P90)")
    by_direction: dict[str, Any] = Field(default_factory=dict, description="Quebra segregada LONG vs SHORT")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class HorizonEfficacySummary(BaseModel):
    """
    Sumário completo de eficácia por horizonte contendo visões ALL_OBSERVATIONS e NON_OVERLAPPING.
    """
    horizon_days: int = Field(..., ge=1, description="Horizonte em dias")
    all_observation_count: int = Field(default=0, ge=0, description="Total de observações avaliáveis")
    non_overlapping_observation_count: int = Field(default=0, ge=0, description="Total de observações sem sobreposição temporal")
    episode_count: int = Field(default=0, ge=0, description="Total de episódios direcionais")
    
    all_observations: EfficacyMetricSet = Field(..., description="Métricas sobre todos os snapshots")
    non_overlapping: EfficacyMetricSet = Field(..., description="Métricas sobre a amostra não-sobreposta")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class BaselineComparisonResult(BaseModel):
    """
    Comparação pareada de performance do Cornucopia contra um baseline em 3 visões:
    A. Native Strategy Performance
    B. Common Opportunity Comparison
    C. Common Directional Decision Comparison
    """
    baseline_mode: BaselineMode = Field(..., description="Modo do baseline avaliado")
    decision_count: int = Field(default=0, ge=0, description="Total de decisões avaliadas")
    common_opportunity_count: int = Field(default=0, ge=0, description="Total de oportunidades pareadas com dados válidos em ambos")
    missing_data_removed_count: int = Field(default=0, ge=0, description="Oportunidades removidas por falta de preço/outcome em um dos lados")
    common_directional_count: int = Field(default=0, ge=0, description="Oportunidades onde ambos emitiram sinal direcional (LONG/SHORT)")
    directional_concordance_rate: float = Field(default=0.0, description="% de decisões com mesma direção")
    
    # Visão A: Native Strategy Performance
    native_cornucopia: dict[str, Any] = Field(default_factory=dict, description="Performance nativa do Cornucopia com suas abstenções")
    native_baseline: dict[str, Any] = Field(default_factory=dict, description="Performance nativa do baseline com suas regras")
    
    # Visão B: Common Opportunity Comparison
    common_opportunity_metrics: dict[str, Any] = Field(default_factory=dict, description="Comparativo pareado estrito no Common Opportunity Set")
    
    # Visão C: Common Directional Decision Comparison
    common_directional_metrics: Optional[dict[str, Any]] = Field(default=None, description="Comparativo onde ambos agiram direcionalmente")
    
    incremental_return: float = Field(default=0.0, description="Retorno incremental nativo: Cornucopia - Baseline")
    incremental_sharpe: Optional[float] = Field(default=None, description="Sharpe incremental nativo")
    diagnostics: dict[str, Any] = Field(default_factory=dict, description="Metadados diagnósticos da comparação")

    @property
    def cornucopia_net_return(self) -> float:
        return float(self.native_cornucopia.get("cumulative_net_return_pct", 0.0))

    @property
    def baseline_net_return(self) -> float:
        return float(self.native_baseline.get("cumulative_net_return_pct", 0.0))

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class WalkForwardRun(BaseModel):
    """
    Registro completo, auditável e serializável de uma execução do motor Walk-Forward.
    """
    run_id: str = Field(..., description="Identificador único da execução")
    created_at: datetime = Field(..., description="Timestamp UTC de criação do run")
    config_fingerprint: str = Field(..., description="Hash SHA-256 da configuração congelada")
    dataset_fingerprint: str = Field(default="", description="Hash SHA-256 do conjunto de dados históricos")
    
    run_purpose: RunPurpose = Field(default=RunPurpose.DEVELOPMENT, description="Finalidade do run (DEVELOPMENT, VALIDATION, FINAL_HOLDOUT)")
    experiment_name: Optional[str] = Field(default=None, description="Nome do experimento testado")
    parent_experiment_id: Optional[str] = Field(default=None, description="ID do experimento pai para auditoria de parâmetros")
    trial_sequence_number: Optional[int] = Field(default=None, description="Sequencial da tentativa de parâmetro")
    segment_label: Optional[str] = Field(default=None, description="Rótulo do segmento específico")
    
    start: datetime = Field(..., description="Timestamp de início do período")
    end: datetime = Field(..., description="Timestamp de término do período")
    warmup_start: datetime = Field(..., description="Timestamp de início do warm-up")
    first_decision_at: Optional[datetime] = Field(default=None, description="Timestamp da primeira decisão out-of-sample")
    
    decision_journal: WalkForwardDecisionJournal = Field(..., description="Diário cronológico de decisões congeladas")
    outcomes_by_horizon: dict[int, list[ForwardReturnOutcome]] = Field(default_factory=dict, description="Outcomes de todos os snapshots por horizonte")
    non_overlapping_outcomes_by_horizon: dict[int, list[ForwardReturnOutcome]] = Field(default_factory=dict, description="Outcomes do subconjunto não-sobreposto")
    episodes: list[ConsensusEpisode] = Field(default_factory=list, description="Lista de episódios direcionais rastreados")
    
    shadow_strategy_by_symbol: dict[str, ShadowStrategyResult] = Field(default_factory=dict, description="Resultados da Shadow Strategy por símbolo")
    baseline_comparisons: dict[str, BaselineComparisonResult] = Field(default_factory=dict, description="Comparativos pareados contra baselines")
    
    efficacy_summaries_by_horizon: dict[int, HorizonEfficacySummary] = Field(default_factory=dict, description="Sumários estruturados por horizonte (ALL vs NON_OVERLAPPING)")
    efficacy_metrics_by_horizon: dict[int, dict[str, Any]] = Field(default_factory=dict, description="Métricas de eficácia de sinal por horizonte (legado)")
    bucket_metrics_by_horizon: dict[int, dict[str, Any]] = Field(default_factory=dict, description="Análise de eficácia por buckets de consenso")
    segment_metrics: dict[str, dict[str, Any]] = Field(default_factory=dict, description="Métricas quebradas por segmentos temporais/regimes")
    
    holdout_metrics: Optional[dict[str, Any]] = Field(default=None, description="Resultados isolados do FINAL_HOLDOUT (sem misturar com development)")
    full_period_diagnostic_metrics: Optional[dict[str, Any]] = Field(default=None, description="Métricas globais explicitamente rotuladas como diagnósticas")
    
    data_quality_summary: dict[str, Any] = Field(default_factory=dict, description="Sumário de qualidade de dados e integridade")
    warnings: list[str] = Field(default_factory=list, description="Avisos estatísticos e diagnósticos (OVERLAPPING_OUTCOMES_WARNING, etc.)")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
