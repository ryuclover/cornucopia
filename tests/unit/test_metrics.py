from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.domain.enums import PositionSide
from src.domain.trade import ClosedTrade
from src.metrics.drawdown import compute_drawdown_series
from src.metrics.pnl import compute_pnl_summary
from src.metrics.ratios import compute_risk_adjusted_ratios


def create_trade(net_pnl_val: str, return_val: str, entry_d: int, exit_d: int) -> ClosedTrade:
    pnl = Decimal(net_pnl_val)
    gross = pnl + Decimal("5.00")
    return ClosedTrade(
        trade_id=f"t-{entry_d}-{exit_d}",
        trader_id="T1",
        symbol="PETR4",
        side=PositionSide.LONG,
        quantity=Decimal("100"),
        entry_price=Decimal("30.00"),
        exit_price=Decimal("32.00") if pnl >= 0 else Decimal("28.00"),
        entry_time=datetime(2026, 1, entry_d, 10, 0, tzinfo=timezone.utc),
        exit_time=datetime(2026, 1, exit_d, 15, 0, tzinfo=timezone.utc),
        gross_pnl=gross,
        commission=Decimal("5.00"),
        net_pnl=pnl,
        return_pct=Decimal(return_val)
    )


def test_pnl_summary_math():
    trades = [
        create_trade("100.00", "0.033", 1, 1),
        create_trade("200.00", "0.066", 2, 2),
        create_trade("-100.00", "-0.033", 3, 3),
        create_trade("150.00", "0.05", 4, 4),
        create_trade("-50.00", "-0.016", 5, 5),
        create_trade("0.00", "0.0", 6, 6),
    ]

    summary = compute_pnl_summary(trades)
    assert summary["total_trades"] == 6
    assert summary["winning_trades"] == 3
    assert summary["losing_trades"] == 2
    assert summary["scratch_trades"] == 1
    assert summary["net_pnl"] == Decimal("300.00")
    assert summary["win_rate"] == 0.50

    # Total gains = 450, Total losses = 150 -> Profit Factor = 3.0
    assert summary["profit_factor"] == 3.0
    # Avg win = 150, Avg loss = 75 -> Payoff = 2.0
    assert summary["avg_win"] == Decimal("150.00")
    assert summary["avg_loss"] == Decimal("75.00")
    assert summary["payoff_ratio"] == 2.0
    # Top 1 win: 200 / 450 = 44.44%
    assert pytest.approx(summary["top_1_trade_pnl_contribution_pct"], 0.01) == 44.44
    # Top 3 wins: 200 + 150 + 100 = 450 / 450 = 100%
    assert summary["top_n_trades_pnl_contribution_pct"] == 100.0
    # Top 5 wins: 450 / 450 = 100%
    assert summary["top_5_trades_pnl_contribution_pct"] == 100.0
    # Top 10% (1 de 3 wins) = 44.44%
    assert pytest.approx(summary["top_10_percent_trades_pnl_contribution_pct"], 0.01) == 44.44


def test_drawdown_calculation():
    initial_cap = Decimal("10000.00")
    # Evolução do patrimônio:
    # 10000 -> +500 (10500, HWM=10500, DD=0)
    # 10500 -> +500 (11000, HWM=11000, DD=0)
    # 11000 -> -1100 (9900, HWM=11000, DD=1100 / 11000 = 10%)
    # 9900 -> -550 (9350, HWM=11000, DD=1650 / 11000 = 15%)
    # 9350 -> +2000 (11350, HWM=11350, DD=0)
    trades = [
        create_trade("500.00", "0.05", 1, 1),
        create_trade("500.00", "0.05", 2, 2),
        create_trade("-1100.00", "-0.10", 3, 3),
        create_trade("-550.00", "-0.05", 4, 4),
        create_trade("2000.00", "0.20", 5, 5),
    ]
    dd_stats = compute_drawdown_series(trades, initial_cap)
    assert dd_stats["max_drawdown_amount"] == Decimal("1650.00")
    assert pytest.approx(dd_stats["max_drawdown_pct"], 0.01) == 15.0


def test_risk_adjusted_ratios_empty_and_valid():
    empty_res = compute_risk_adjusted_ratios([], Decimal("10000.00"), 30.0)
    assert empty_res["sharpe_ratio"] == 0.0
    assert empty_res["sortino_ratio"] == 0.0

    trades = [
        create_trade("100.00", "0.02", 1, 1),
        create_trade("120.00", "0.024", 2, 2),
        create_trade("80.00", "0.016", 3, 3),
        create_trade("-20.00", "-0.004", 4, 4),
    ]
    res = compute_risk_adjusted_ratios(trades, Decimal("10000.00"), 30.0, max_drawdown_pct=2.0)
    assert res["sharpe_ratio"] > 0
    assert res["sortino_ratio"] > 0
    assert res["calmar_ratio"] > 0
