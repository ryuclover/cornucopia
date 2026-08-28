from datetime import datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import Optional
from pydantic import BaseModel, Field
from src.config.evaluation_config import EvaluationFrequency


class OutcomeClassification(str, Enum):
    """Classificação do desfecho do sinal após o horizonte avaliado."""
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    NEUTRAL_OUTCOME = "NEUTRAL_OUTCOME"
    UNEVALUABLE = "UNEVALUABLE"


class EvaluationStatus(str, Enum):
    """Status da avaliação do outcome futuro."""
    EVALUATED = "EVALUATED"
    MISSING_REFERENCE_PRICE = "MISSING_REFERENCE_PRICE"
    MISSING_FORWARD_PRICE = "MISSING_FORWARD_PRICE"
    STALE_REFERENCE_PRICE = "STALE_REFERENCE_PRICE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class BaselineMode(str, Enum):
    """Modos de baseline para comparação quantitativa."""
    CORNUCOPIA = "CORNUCOPIA"
    EQUAL_WEIGHT = "EQUAL_WEIGHT"
    SIMPLE_MAJORITY = "SIMPLE_MAJORITY"
    QUALITY_ONLY = "QUALITY_ONLY"


class RunPurpose(str, Enum):
    """Finalidade de execução do run para auditoria e governança."""
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    FINAL_HOLDOUT = "FINAL_HOLDOUT"
    RESEARCH = "RESEARCH"


class BacktestFrictionConfig(BaseModel):
    """
    Configuração de fricção e custos simulados de execução.
    """
    commission_bps: float = Field(default=2.0, ge=0.0, description="Corretagem/taxas em basis points por giro")
    spread_bps: float = Field(default=3.0, ge=0.0, description="Spread bid-ask estimado em basis points por giro")
    slippage_bps: float = Field(default=2.0, ge=0.0, description="Slippage médio de execução em basis points por giro")

    @property
    def total_friction_bps(self) -> float:
        return self.commission_bps + self.spread_bps + self.slippage_bps

    @property
    def total_friction_rate(self) -> float:
        return self.total_friction_bps / 10000.0

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class WalkForwardConfig(BaseModel):
    """
    Configurações centralizadas para o motor Walk-Forward e validação out-of-sample.
    """
    start: datetime = Field(..., description="Timestamp de início do período total")
    end: datetime = Field(..., description="Timestamp de término do período total")
    warmup_days: int = Field(default=60, ge=0, description="Dias de warm-up para maturidade de scoring e seleção antes da 1ª decisão")
    
    decision_frequency: EvaluationFrequency = Field(
        default=EvaluationFrequency.DAILY,
        description="Frequência cronológica de tomada e congelamento de decisões"
    )
    
    forward_horizons_days: list[int] = Field(
        default_factory=lambda: [1, 5, 20],
        description="Lista de horizontes futuros em dias para medição de retorno (+1d, +5d, +20d)"
    )
    
    minimum_price_freshness_seconds: float = Field(
        default=86400.0 * 5,
        ge=0.0,
        description="Tolerância máxima de idade da última cotação para o preço de referência"
    )
    maximum_future_price_delay_seconds: float = Field(
        default=86400.0 * 5,
        ge=0.0,
        description="Tolerância máxima de atraso para localização do preço futuro no horizonte"
    )
    
    neutral_return_band_bps: float = Field(
        default=10.0,
        ge=0.0,
        description="Banda neutra em basis points para classificar desfecho direcional (+/- 10 bps)"
    )
    
    evaluate_consensus_episodes: bool = Field(
        default=True,
        description="Se True, rastreia e avalia episódios direcionais contínuos de consenso"
    )
    enable_shadow_strategy: bool = Field(
        default=True,
        description="Se True, constrói curva de equity e estatísticas da Shadow Strategy unitária"
    )
    
    friction: BacktestFrictionConfig = Field(
        default_factory=BacktestFrictionConfig,
        description="Parâmetros de custos e fricção operacional"
    )
    
    baseline_modes: list[BaselineMode] = Field(
        default_factory=lambda: [
            BaselineMode.EQUAL_WEIGHT,
            BaselineMode.SIMPLE_MAJORITY,
            BaselineMode.QUALITY_ONLY
        ],
        description="Baselines a serem computados e comparados contra o Cornucopia"
    )
    
    minimum_sample_for_reporting: int = Field(
        default=10,
        ge=1,
        description="Amostra mínima de decisões direcionais para suprimir avisos estatísticos"
    )
    
    segments: dict[str, tuple[datetime, datetime]] = Field(
        default_factory=dict,
        description="Segmentos/regimes temporais explícitos para quebra diagnóstica"
    )
    
    holdout_start: Optional[datetime] = Field(
        default=None,
        description="Timestamp de corte a partir do qual os resultados são rotulados como HOLDOUT"
    )

    # Auditoria de Parâmetros e Provenance
    run_purpose: RunPurpose = Field(
        default=RunPurpose.DEVELOPMENT,
        description="Finalidade do run (DEVELOPMENT, VALIDATION, FINAL_HOLDOUT, RESEARCH)"
    )
    experiment_name: Optional[str] = Field(
        default=None,
        description="Nome ou rótulo do experimento / hipótese testada"
    )
    parent_experiment_id: Optional[str] = Field(
        default=None,
        description="ID do experimento pai para auditoria genealógica de tentativas"
    )
    trial_sequence_number: Optional[int] = Field(
        default=None,
        ge=1,
        description="Número sequencial da tentativa de parâmetro antes do holdout"
    )
    segment_label: Optional[str] = Field(
        default=None,
        description="Rótulo do segmento específico avaliado neste run"
    )

    def compute_config_fingerprint(self) -> str:
        """
        Gera um hash SHA-256 determinístico de todos os parâmetros congelados da configuração.
        """
        payload = {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "warmup_days": self.warmup_days,
            "decision_frequency": self.decision_frequency.value,
            "forward_horizons_days": sorted(self.forward_horizons_days),
            "neutral_return_band_bps": self.neutral_return_band_bps,
            "minimum_price_freshness_seconds": self.minimum_price_freshness_seconds,
            "maximum_future_price_delay_seconds": self.maximum_future_price_delay_seconds,
            "friction": {
                "commission_bps": self.friction.commission_bps,
                "spread_bps": self.friction.spread_bps,
                "slippage_bps": self.friction.slippage_bps,
            },
            "baseline_modes": [m.value for m in sorted(self.baseline_modes, key=lambda x: x.value)],
            "run_purpose": self.run_purpose.value,
            "experiment_name": self.experiment_name,
            "trial_sequence_number": self.trial_sequence_number
        }
        dumped = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
