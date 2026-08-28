from pydantic import BaseModel, Field


class SelectionConfig(BaseModel):
    """
    Configurações centralizadas da política formal de seleção do núcleo de especialistas.
    
    Implementa histerese, thresholds de segurança e períodos de confirmação.
    """
    # Thresholds de Pontuação
    min_survivor_score_candidate: float = Field(
        default=60.0,
        ge=0.0,
        le=100.0,
        description="Survivor Score mínimo para admissão como CANDIDATE"
    )
    min_survivor_score_selected: float = Field(
        default=70.0,
        ge=0.0,
        le=100.0,
        description="Survivor Score mínimo para promoção a SELECTED"
    )
    watchlist_score_threshold: float = Field(
        default=63.0,
        ge=0.0,
        le=100.0,
        description="Threshold de score abaixo do qual um trader SELECTED entra em WATCHLIST (Histerese: 63 < 70)"
    )
    suspension_score_threshold: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        description="Threshold de score abaixo do qual ocorre SUSPENSÃO imediata"
    )
    
    # Critérios de Risco e Concentração
    max_allowed_drawdown_pct: float = Field(
        default=20.0,
        gt=0.0,
        le=100.0,
        description="Máximo drawdown percentual tolerado para o núcleo selecionado"
    )
    catastrophic_drawdown_pct: float = Field(
        default=35.0,
        gt=0.0,
        le=100.0,
        description="Drawdown percentual severo que provoca EXCLUSÃO imediata"
    )
    max_top_trade_concentration_pct: float = Field(
        default=60.0,
        ge=0.0,
        le=100.0,
        description="Concentração máxima permitida no Top 1 trade (protege contra Lucky Outliers)"
    )
    
    # Períodos de Confirmação (Histerese Temporal)
    candidate_confirmation_periods: int = Field(
        default=2,
        ge=1,
        description="Quantidade de avaliações consecutivas saudáveis exigidas para CANDIDATE ser promovido a SELECTED"
    )
    watchlist_trigger_periods: int = Field(
        default=1,
        ge=1,
        description="Períodos de deterioração necessários para mover SELECTED para WATCHLIST"
    )
    watchlist_recovery_confirmation_periods: int = Field(
        default=2,
        ge=1,
        description="Quantidade de avaliações consecutivas saudáveis exigidas para sair de WATCHLIST e retornar a SELECTED"
    )
    suspension_trigger_periods: int = Field(
        default=2,
        ge=1,
        description="Quantidade de períodos em WATCHLIST antes de ser movido para SUSPENDED"
    )
    reentry_confirmation_periods: int = Field(
        default=2,
        ge=1,
        description="Quantidade de períodos consecutivos saudáveis exigidos para SUSPENDED voltar a CANDIDATE"
    )
    
    # Parâmetros de Tamanho do Núcleo (Opcionais)
    min_core_size: int = Field(
        default=0,
        ge=0,
        description="Tamanho mínimo ideal do núcleo (0 = sem forçar seleção de traders inadequados)"
    )
    max_core_size: int = Field(
        default=50,
        ge=1,
        description="Tamanho máximo de segurança para o núcleo"
    )

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }

    @classmethod
    def conservative(cls) -> "SelectionConfig":
        """Preset Conservador: Exige maior estabilidade e períodos mais longos de confirmação."""
        return cls(
            min_survivor_score_candidate=70.0,
            min_survivor_score_selected=80.0,
            watchlist_score_threshold=72.0,
            suspension_score_threshold=60.0,
            max_allowed_drawdown_pct=15.0,
            catastrophic_drawdown_pct=30.0,
            candidate_confirmation_periods=4,
            suspension_trigger_periods=1,
            reentry_confirmation_periods=3
        )

    @classmethod
    def balanced(cls) -> "SelectionConfig":
        """Preset Equilibrado: Padrão recomendado para operação do sistema."""
        return cls()

    @classmethod
    def permissive(cls) -> "SelectionConfig":
        """Preset Permissivo: Útil para testes rápidos com histórico reduzido."""
        return cls(
            min_survivor_score_candidate=60.0,
            min_survivor_score_selected=70.0,
            watchlist_score_threshold=62.0,
            suspension_score_threshold=50.0,
            max_allowed_drawdown_pct=25.0,
            catastrophic_drawdown_pct=40.0,
            candidate_confirmation_periods=2,
            suspension_trigger_periods=3,
            reentry_confirmation_periods=1
        )
