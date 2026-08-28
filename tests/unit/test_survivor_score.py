from datetime import datetime, timezone
from decimal import Decimal
from src.config.survival_config import SurvivalCriteriaConfig
from src.domain.enums import PositionSide
from src.domain.trade import ClosedTrade
from src.metrics.calculator import PerformanceCalculator
from src.scoring.survivor_v1 import SurvivorScoreV1


def generate_trades_series(
    trader_id: str,
    win_count: int,
    loss_count: int,
    win_amount: str,
    loss_amount: str,
    start_date: datetime
) -> list[ClosedTrade]:
    trades = []
    current_time = start_date
    trade_id_idx = 1
    
    # Gera operações intercaladas
    total = win_count + loss_count
    wins_left = win_count
    losses_left = loss_count

    for i in range(total):
        # Alterna para simular regularidade
        is_win = (wins_left > 0 and (i % 2 == 0 or losses_left == 0))
        if is_win:
            wins_left -= 1
            pnl = Decimal(win_amount)
            ret_pct = Decimal("0.02")
        else:
            losses_left -= 1
            pnl = Decimal(loss_amount)  # negativo
            ret_pct = Decimal("-0.01")

        entry_t = current_time
        current_time = datetime.fromtimestamp(current_time.timestamp() + 86400, tz=timezone.utc)
        exit_t = current_time

        trades.append(
            ClosedTrade(
                trade_id=f"{trader_id}-t{trade_id_idx}",
                trader_id=trader_id,
                symbol="WIN$",
                side=PositionSide.LONG,
                quantity=Decimal("1"),
                entry_price=Decimal("120000"),
                exit_price=Decimal("120500") if is_win else Decimal("119750"),
                entry_time=entry_t,
                exit_time=exit_t,
                gross_pnl=pnl,
                commission=Decimal("0.0"),
                net_pnl=pnl,
                return_pct=ret_pct
            )
        )
        trade_id_idx += 1

    return trades


def test_consistent_surviving_trader_high_score():
    config = SurvivalCriteriaConfig(
        min_history_days=60,
        min_trade_count=30,
        max_allowed_drawdown_pct=20.0,
        max_single_trade_loss_pct=3.0,
        min_profit_factor=1.2,
    )
    scorer = SurvivorScoreV1(config=config)
    
    start_time = datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)
    # 70 trades: 45 wins (+150), 25 losses (-70) em 70 dias
    trades = generate_trades_series(
        trader_id="trader_consistent",
        win_count=45,
        loss_count=25,
        win_amount="150.00",
        loss_amount="-70.00",
        start_date=start_time
    )

    as_of = datetime(2025, 9, 15, 18, 0, tzinfo=timezone.utc)
    perf = PerformanceCalculator.calculate(
        trader_id="trader_consistent",
        trades=trades,
        as_of=as_of,
        initial_capital=Decimal("10000.00")
    )

    score = scorer.evaluate(perf)
    assert score.is_qualified is True
    assert len(score.disqualification_reasons) == 0
    assert score.score_total >= 60.0
    assert score.drawdown_score > 80.0


def test_gambler_trader_drawdown_disqualification():
    config = SurvivalCriteriaConfig(
        min_history_days=30,
        min_trade_count=10,
        max_allowed_drawdown_pct=25.0
    )
    scorer = SurvivorScoreV1(config=config)

    # Trader que ganhou muito mas teve um drawdown de 45%
    start_time = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
    trades = [
        # Ganha 5000 (patrimonio 15000)
        ClosedTrade(
            trade_id="g1", trader_id="gambler", symbol="WIN$", side=PositionSide.LONG,
            quantity=Decimal("1"), entry_price=Decimal("100"), exit_price=Decimal("150"),
            entry_time=start_time, exit_time=datetime(2025, 1, 10, tzinfo=timezone.utc),
            gross_pnl=Decimal("5000.00"), commission=Decimal("0.0"), net_pnl=Decimal("5000.00"),
            return_pct=Decimal("0.50")
        ),
        # Perde 7000 (patrimonio cai de 15000 para 8000 -> DD de 46.6%)
        ClosedTrade(
            trade_id="g2", trader_id="gambler", symbol="WIN$", side=PositionSide.LONG,
            quantity=Decimal("1"), entry_price=Decimal("150"), exit_price=Decimal("80"),
            entry_time=datetime(2025, 1, 11, tzinfo=timezone.utc), exit_time=datetime(2025, 1, 20, tzinfo=timezone.utc),
            gross_pnl=Decimal("-7000.00"), commission=Decimal("0.0"), net_pnl=Decimal("-7000.00"),
            return_pct=Decimal("-0.466")
        ),
        # Ganha 10000 depois
        ClosedTrade(
            trade_id="g3", trader_id="gambler", symbol="WIN$", side=PositionSide.LONG,
            quantity=Decimal("1"), entry_price=Decimal("80"), exit_price=Decimal("180"),
            entry_time=datetime(2025, 1, 21, tzinfo=timezone.utc), exit_time=datetime(2025, 2, 15, tzinfo=timezone.utc),
            gross_pnl=Decimal("10000.00"), commission=Decimal("0.0"), net_pnl=Decimal("10000.00"),
            return_pct=Decimal("1.25")
        ),
    ]

    as_of = datetime(2025, 2, 28, tzinfo=timezone.utc)
    perf = PerformanceCalculator.calculate(
        trader_id="gambler",
        trades=trades,
        as_of=as_of,
        initial_capital=Decimal("10000.00")
    )

    score = scorer.evaluate(perf)
    assert score.is_qualified is False
    assert any("Drawdown excessivo" in r for r in score.disqualification_reasons)
    # Score zerado por violação fatal de drawdown
    assert score.score_total == 0.0


def test_trend_follower_low_win_rate_not_disqualified():
    # Estratégia de trend following: apenas 28% de win rate, mas payoff alto (ganhos de 500, perdas de 60)
    config = SurvivalCriteriaConfig(
        min_history_days=60,
        min_trade_count=30,
        max_allowed_drawdown_pct=20.0,
        min_profit_factor=1.2,
        max_consecutive_losses=8,
    )
    scorer = SurvivorScoreV1(config=config)

    # Gera 50 trades: 14 vitórias intercaladas a cada ~2.5 derrotas (max consecutive losses = 3)
    start_time = datetime(2025, 5, 1, 10, 0, tzinfo=timezone.utc)
    trades = []
    current_time = start_time
    # Padrão: [Win, Loss, Loss, Loss, Win, Loss, Loss...]
    pattern = [True, False, False, False] * 12 + [True, False] # 13 wins, 37 losses
    for idx, is_win in enumerate(pattern, 1):
        pnl = Decimal("600.00") if is_win else Decimal("-60.00")
        ret_pct = Decimal("0.06") if is_win else Decimal("-0.006")
        entry_t = current_time
        current_time = datetime.fromtimestamp(current_time.timestamp() + 86400 * 2, tz=timezone.utc)
        trades.append(
            ClosedTrade(
                trade_id=f"tf-t{idx}",
                trader_id="trend_follower",
                symbol="WIN$",
                side=PositionSide.LONG,
                quantity=Decimal("1"),
                entry_price=Decimal("120000"),
                exit_price=Decimal("123000") if is_win else Decimal("119700"),
                entry_time=entry_t,
                exit_time=current_time,
                gross_pnl=pnl,
                commission=Decimal("0.0"),
                net_pnl=pnl,
                return_pct=ret_pct
            )
        )

    as_of = datetime(2025, 9, 1, 18, 0, tzinfo=timezone.utc)
    perf = PerformanceCalculator.calculate(
        trader_id="trend_follower",
        trades=trades,
        as_of=as_of,
        initial_capital=Decimal("10000.00")
    )

    assert perf.win_rate < 0.30  # Win rate < 30%
    assert perf.profit_factor > 2.0 # Alto lucro
    assert perf.net_pnl > 0
    score = scorer.evaluate(perf)
    # Não deve ser desqualificado por win rate baixo
    assert score.is_qualified is True
    assert score.score_total > 50.0
