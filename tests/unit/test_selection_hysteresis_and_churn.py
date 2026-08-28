from datetime import datetime, timezone
from decimal import Decimal
from src.config.selection_config import SelectionConfig
from src.evaluation.models import QualificationStatus, ScoreTrend, TraderEvaluationSnapshot
from src.selection.engine import TraderSelectionEngine
from src.selection.models import SelectedCoreSnapshot, SelectionStatus, TraderSelectionDecision
from src.selection.policy import TraderSelectionPolicy


def test_hysteresis_prevents_state_churn_on_minor_fluctuations():
    # min_survivor_score_selected = 75.0, watchlist_score_threshold = 68.0
    cfg = SelectionConfig(
        min_survivor_score_candidate=65.0,
        min_survivor_score_selected=75.0,
        watchlist_score_threshold=68.0,
        candidate_confirmation_periods=1
    )

    base_snap = TraderEvaluationSnapshot(
        trader_id="T001",
        as_of=datetime(2026, 1, 30, tzinfo=timezone.utc),
        history_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        history_days=30.0,
        trade_count=20,
        realized_pnl=Decimal("1000.00"),
        realized_equity=Decimal("11000.00"),
        net_return_pct=10.0,
        max_drawdown_pct=5.0,
        win_rate=0.6,
        profit_factor=1.5,
        sharpe_ratio=1.5,
        sortino_ratio=1.8,
        largest_loss_pct=1.0,
        max_consecutive_losses=1,
        top_1_trade_pnl_contribution_pct=20.0,
        top_5_trades_pnl_contribution_pct=50.0,
        top_10_percent_trades_pnl_contribution_pct=30.0,
        survivor_score=78.0,
        is_qualified=True,
        qualification_status=QualificationStatus.QUALIFIED,
        score_lifetime=78.0
    )

    # 1. Torna-se CANDIDATE e é promovido a SELECTED
    dec1 = TraderSelectionPolicy.evaluate_transition(base_snap, previous_decision=None, config=cfg)
    assert dec1.new_status == SelectionStatus.SELECTED

    # 2. Score oscila ligeiramente de 78 para 73 (abaixo de 75, mas acima de 68) -> PERMANECE SELECTED graças à histerese!
    snap2 = base_snap.model_copy(update={"as_of": datetime(2026, 2, 28, tzinfo=timezone.utc), "survivor_score": 73.0})
    dec2 = TraderSelectionPolicy.evaluate_transition(snap2, previous_decision=dec1, config=cfg)
    assert dec2.new_status == SelectionStatus.SELECTED
    assert "MAINTAIN_SELECTED" in dec2.triggered_rules

    # 3. Score oscila para 74 -> Continua SELECTED
    snap3 = base_snap.model_copy(update={"as_of": datetime(2026, 3, 30, tzinfo=timezone.utc), "survivor_score": 74.0})
    dec3 = TraderSelectionPolicy.evaluate_transition(snap3, previous_decision=dec2, config=cfg)
    assert dec3.new_status == SelectionStatus.SELECTED


def test_selection_churn_calculation():
    # Cria decisões simuladas para dois momentos t1 e t2
    def make_decision(trader_id: str, status: SelectionStatus, as_of: datetime):
        return TraderSelectionDecision(
            trader_id=trader_id,
            as_of=as_of,
            previous_status=SelectionStatus.INSUFFICIENT_DATA,
            new_status=status,
            survivor_score=80.0,
            qualification_status=QualificationStatus.QUALIFIED,
            score_trend=ScoreTrend.STABLE
        )

    t1 = datetime(2026, 1, 30, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 28, tzinfo=timezone.utc)

    # Core em T1: T_A, T_B, T_C selecionados
    core1 = SelectedCoreSnapshot(
        as_of=t1,
        selected_traders=[
            make_decision("T_A", SelectionStatus.SELECTED, t1),
            make_decision("T_B", SelectionStatus.SELECTED, t1),
            make_decision("T_C", SelectionStatus.SELECTED, t1),
        ],
        selected_count=3,
        candidate_count=1,
        watchlist_count=0,
        suspended_count=0,
        excluded_count=0,
        insufficient_data_count=0
    )

    # Core em T2: T_B sai (foi para watchlist/suspenso), T_D entra (promovido)
    # Selecionados em T2: T_A, T_C, T_D
    core2 = SelectedCoreSnapshot(
        as_of=t2,
        selected_traders=[
            make_decision("T_A", SelectionStatus.SELECTED, t2),
            make_decision("T_C", SelectionStatus.SELECTED, t2),
            make_decision("T_D", SelectionStatus.SELECTED, t2),
        ],
        selected_count=3,
        candidate_count=0,
        watchlist_count=1,
        suspended_count=0,
        excluded_count=0,
        insufficient_data_count=0
    )

    churn = TraderSelectionEngine.calculate_churn(core1, core2)

    assert churn.promoted_to_selected == ["T_D"]
    assert churn.demoted_from_selected == ["T_B"]
    assert churn.churn_count == 2
    assert churn.churn_rate_pct == round(100.0 * 2 / 6, 2)
