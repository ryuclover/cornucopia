from datetime import datetime, timezone
from decimal import Decimal
from src.config.evaluation_config import EvaluationFrequency
from src.dependence.alignment import TimeSeriesAligner
from src.synthetic.generator import SyntheticDataGenerator


def test_calendar_buckets_generation():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    
    daily_buckets = TimeSeriesAligner.generate_calendar_buckets(start, end, EvaluationFrequency.DAILY)
    assert len(daily_buckets) >= 10
    assert daily_buckets[0] == start
    assert daily_buckets[-1] == end


def test_trader_time_series_building_and_alignment():
    gen = SyntheticDataGenerator(seed=123)
    execs_a = gen.generate_executions_for_trader("T001", symbol="PETR4", trade_count=20, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    
    from src.domain.enums import AssetClass
    from src.domain.instrument import MarketInstrument
    from src.domain.position_tracker import PositionTracker

    inst = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    tracker = PositionTracker(instrument=inst, trader_id="T001")
    for e in execs_a:
        tracker.process_execution(e)

    series_a = TimeSeriesAligner.build_trader_time_series(
        trader_id="T001",
        trades=tracker.closed_trades,
        executions=execs_a,
        as_of=datetime(2026, 2, 28, tzinfo=timezone.utc),
        window_days=60,
        initial_capital=Decimal("10000.00"),
        frequency=EvaluationFrequency.DAILY
    )
    assert len(series_a) > 0
    # Verifica que trades fechados geraram retornos e P&L não nulo em alguns buckets
    active_days = [f for f in series_a if f.is_active]
    assert len(active_days) > 0

    # Cria série B e alinha
    execs_b = gen.generate_executions_for_trader("T002", symbol="PETR4", trade_count=15, start_date=datetime(2026, 1, 10, tzinfo=timezone.utc))
    tracker_b = PositionTracker(instrument=inst, trader_id="T002")
    for e in execs_b:
        tracker_b.process_execution(e)

    series_b = TimeSeriesAligner.build_trader_time_series(
        trader_id="T002",
        trades=tracker_b.closed_trades,
        executions=execs_b,
        as_of=datetime(2026, 2, 28, tzinfo=timezone.utc),
        window_days=60,
        initial_capital=Decimal("10000.00"),
        frequency=EvaluationFrequency.DAILY
    )

    aligned_a, aligned_b = TimeSeriesAligner.align_pair_series(series_a, series_b)
    assert len(aligned_a) == len(aligned_b)
    for fa, fb in zip(aligned_a, aligned_b):
        assert fa.timestamp == fb.timestamp
