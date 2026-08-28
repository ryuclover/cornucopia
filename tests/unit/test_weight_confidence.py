from datetime import datetime, timedelta, timezone
from decimal import Decimal
from src.config.weight_config import WeightConfig
from src.evaluation.models import QualificationStatus, ScoreTrend, TraderEvaluationSnapshot
from src.weighting.confidence import TraderConfidenceCalculator
from src.weighting.quality import TraderQualityCalculator


def make_eval_snapshot(
    trader_id: str,
    as_of: datetime,
    survivor_score: float,
    trade_count: int,
    history_days: float,
    qual_status: QualificationStatus = QualificationStatus.QUALIFIED
) -> TraderEvaluationSnapshot:
    return TraderEvaluationSnapshot(
        trader_id=trader_id,
        as_of=as_of,
        history_start=as_of - timedelta(days=int(history_days)),
        history_days=history_days,
        trade_count=trade_count,
        realized_pnl=Decimal("1000.00"),
        realized_equity=Decimal("11000.00"),
        net_return_pct=10.0,
        max_drawdown_pct=5.0,
        win_rate=0.60,
        profit_factor=2.0,
        sharpe_ratio=2.0,
        sortino_ratio=2.5,
        largest_loss_pct=1.0,
        max_consecutive_losses=2,
        top_1_trade_pnl_contribution_pct=10.0,
        top_5_trades_pnl_contribution_pct=30.0,
        top_10_percent_trades_pnl_contribution_pct=35.0,
        survivor_score=survivor_score,
        is_qualified=(qual_status == QualificationStatus.QUALIFIED),
        qualification_status=qual_status,
        score_lifetime=survivor_score
    )


def test_confidence_scaling_and_saturation():
    cfg = WeightConfig(
        confidence_target_trades=100,
        confidence_target_days=180,
        minimum_confidence_factor=0.20
    )
    dt = datetime(2026, 6, 30, tzinfo=timezone.utc)
    
    # Trader com histórico longo (300 dias) e 200 trades -> Confiança máxima 1.0
    snap_deep = make_eval_snapshot(
        trader_id="T_DEEP", as_of=dt, survivor_score=85.0,
        trade_count=200, history_days=300.0
    )
    conf_deep, diag_deep = TraderConfidenceCalculator.calculate_confidence(
        snap_deep, as_of=dt, config=cfg, created_at=dt - timedelta(days=300)
    )
    assert conf_deep == 1.0
    assert diag_deep["trade_confidence"] == 1.0
    assert diag_deep["history_confidence"] == 1.0

    # Trader recém-qualificado com poucos trades (15 trades, 25 dias)
    snap_new = make_eval_snapshot(
        trader_id="T_NEW", as_of=dt, survivor_score=85.0,
        trade_count=15, history_days=25.0
    )
    conf_new, diag_new = TraderConfidenceCalculator.calculate_confidence(
        snap_new, as_of=dt, config=cfg, created_at=dt - timedelta(days=25)
    )
    assert conf_new < 0.35
    assert conf_new >= cfg.minimum_confidence_factor


def test_confidence_does_not_create_quality():
    cfg = WeightConfig()
    dt = datetime(2026, 6, 30, tzinfo=timezone.utc)
    
    # Trader ruim com enorme histórico (500 trades, 700 dias)
    snap_bad_long = make_eval_snapshot(
        trader_id="T_BAD", as_of=dt, survivor_score=35.0,
        trade_count=500, history_days=700.0,
        qual_status=QualificationStatus.DISQUALIFIED
    )
    conf, _ = TraderConfidenceCalculator.calculate_confidence(snap_bad_long, as_of=dt, config=cfg)
    qual, _ = TraderQualityCalculator.calculate_quality(snap_bad_long, cfg)

    assert conf == 1.0
    # Qualidade continua baixa
    assert qual <= 0.50
