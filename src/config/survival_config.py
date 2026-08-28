from decimal import Decimal
from pydantic import BaseModel, Field


class SurvivalCriteriaConfig(BaseModel):
    """
    Critérios configuráveis para qualificação de um trader como 'sobrevivente'.
    
    NOTA SOBRE AMBIENTES:
    Os valores default atuais (ex: 60 dias, 30 trades) são reduzidos para facilitar desenvolvimento,
    testes unitários e iteração rápida no MVP.
    Para avaliações e homologação em produção, configure janelas mais robustas:
    - min_history_days: 180 a 365 dias
    - min_trade_count: 100+ operações
    """
    min_history_days: int = Field(
        default=60,
        ge=1,
        description="Tempo mínimo de histórico em dias para qualificação estatística (Dev: 60, Prod: 180-365)"
    )
    min_trade_count: int = Field(
        default=30,
        ge=5,
        description="Número mínimo de operações fechadas necessárias para avaliação (Dev: 30, Prod: 100+)"
    )
    max_allowed_drawdown_pct: float = Field(
        default=25.0,
        gt=0.0,
        le=100.0,
        description="Drawdown máximo histórico tolerado em porcentagem (ex: 25.0 para 25%)"
    )
    max_single_trade_loss_pct: float = Field(
        default=5.0,
        gt=0.0,
        le=100.0,
        description="Perda máxima permitida em uma única operação sobre o capital de referência (%)"
    )
    min_profit_factor: float = Field(
        default=1.15,
        ge=0.0,
        description="Profit factor mínimo (Soma dos ganhos / Soma das perdas)"
    )
    min_net_return_pct: float = Field(
        default=0.0,
        description="Retorno percentual líquido mínimo exigido sobre o capital base (%) para garantir retorno não negativo"
    )
    max_consecutive_losses: int = Field(
        default=8,
        ge=1,
        description="Limite de perdas consecutivas antes de desqualificação/penalidade"
    )
    min_sharpe_ratio: float = Field(
        default=0.0,
        description="Sharpe Ratio mínimo de referência"
    )
    max_top_trades_concentration_pct: float = Field(
        default=60.0,
        ge=0.0,
        le=100.0,
        description="Concentração máxima tolerada dos Top 3 trades sobre o lucro bruto total (%)"
    )
    risk_free_rate: float = Field(
        default=0.10,
        ge=0.0,
        description="Taxa livre de risco anual de referência (ex: 10% CDI/Selic) para Sharpe e Sortino"
    )

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
