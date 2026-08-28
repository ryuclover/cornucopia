from datetime import datetime, timezone
from decimal import Decimal
from src.config.weight_config import WeightConfig
from src.evaluation.models import (
    QualificationStatus,
    ScoreTrend,
    TraderEvaluationSnapshot,
    WindowEvaluationResult,
    WindowSampleStatus,
)
from src.weighting.quality import TraderQualityCalculator


def make_snapshot(
    survivor_score: float,
    qual_status: QualificationStatus = QualificationStatus.QUALIFIED,
    score_30d: float = None,
    score_90d: float = None,
    score_180d: float = None,
    trend: ScoreTrend = ScoreTrend.STABLE
) -> TraderEvaluationSnapshot:
    dt = datetime(2026, 3, 30, tzinfo=timezone.utc)
    return TraderEvaluationSnapshot(
        trader_id="T_TEST",
        as_of=dt,
        history_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        history_days=90.0,
        trade_count=50,
        realized_pnl=Decimal("1500.00"),
        realized_equity=Decimal("11500.00"),
        net_return_pct=15.0,
        max_drawdown_pct=4.5,
        win_rate=0.65,
        profit_factor=2.1,
        sharpe_ratio=2.3,
        sortino_ratio=3.1,
        largest_loss_pct=1.2,
        max_consecutive_losses=2,
        top_1_trade_pnl_contribution_pct=10.0,
        top_5_trades_pnl_contribution_pct=35.0,
        top_10_percent_trades_pnl_contribution_pct=40.0,
        survivor_score=survivor_score,
        is_qualified=(qual_status == QualificationStatus.QUALIFIED),
        qualification_status=qual_status,
        score_30d=score_30d,
        score_90d=score_90d,
        score_180d=score_180d,
        score_lifetime=survivor_score
    )


def test_quality_score_dominance():
    cfg = WeightConfig()
    snap_high = make_snapshot(survivor_score=92.0, score_30d=90.0, score_90d=92.0, trend=ScoreTrend.IMPROVING)
    snap_low = make_snapshot(survivor_score=65.0, score_30d=60.0, score_90d=65.0, trend=ScoreTrend.DETERIORATING)

    q_high, diag_high = TraderQualityCalculator.calculate_quality(snap_high, cfg)
    q_low, diag_low = TraderQualityCalculator.calculate_quality(snap_low, cfg)

    assert q_high > q_low
    assert q_high >= 0.85
    assert q_low <= 0.80
    assert diag_high["survivor_score_norm"] == 0.92
    assert diag_low["survivor_score_norm"] == 0.65


def test_quality_renormalization_when_recent_windows_missing():
    cfg = WeightConfig()
    # Trader com score 85, mas sem janela de 30d e 180d (apenas 90d disponível)
    snap_partial = make_snapshot(survivor_score=85.0, score_90d=85.0)

    q_score, diag = TraderQualityCalculator.calculate_quality(snap_partial, cfg)
    # Não deve zerar a saúde recente por falta de 30d/180d
    assert q_score >= 0.80
    assert diag["recent_health"] == 0.85
