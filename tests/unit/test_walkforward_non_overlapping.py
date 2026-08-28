from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.evaluation_config import EvaluationFrequency
from src.config.walkforward_config import WalkForwardConfig
from src.consensus.models import ConsensusDirection
from src.domain.enums import AssetClass
from src.domain.instrument import MarketInstrument
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories.base import MarketPriceRecord
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.walkforward.metrics import WalkForwardMetricsCalculator
from src.walkforward.models import ForwardReturnOutcome, WalkForwardDecision
from src.walkforward.outcomes import ForwardOutcomeEvaluator


def setup_env():
    db = SQLiteDatabaseManager(":memory:")
    inst_repo = SQLiteInstrumentRepository(db)
    inst_repo.save(MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL"))
    price_repo = SQLiteMarketPriceRepository(db)
    return price_repo


def test_overlapping_outcomes_warning_when_horizon_exceeds_interval():
    """
    Decisões DAILY com horizonte de 20d geram OVERLAPPING_OUTCOMES_WARNING.
    """
    cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        decision_frequency=EvaluationFrequency.DAILY,
        forward_horizons_days=[20]
    )
    calc = WalkForwardMetricsCalculator(cfg)
    warnings = calc.generate_statistical_warnings(
        efficacy_by_horizon={20: {"directional_decisions_count": 25}},
        episodes=[]
    )
    assert any("OVERLAPPING_OUTCOMES_WARNING" in w for w in warnings)


def test_no_overlapping_warning_when_horizon_within_interval():
    """
    Decisões MONTHLY (~30d) com horizonte de 20d NÃO geram OVERLAPPING_OUTCOMES_WARNING.
    """
    cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        decision_frequency=EvaluationFrequency.MONTHLY,
        forward_horizons_days=[20]
    )
    calc = WalkForwardMetricsCalculator(cfg)
    warnings = calc.generate_statistical_warnings(
        efficacy_by_horizon={20: {"directional_decisions_count": 25}},
        episodes=[]
    )
    assert not any("OVERLAPPING_OUTCOMES_WARNING" in w for w in warnings)


def test_non_overlapping_subset_extraction_and_determinism():
    """
    Verifica que o subconjunto non-overlapping:
    1. Possui menos observações que o conjunto ALL_OBSERVATIONS;
    2. É 100% determinístico entre execuções;
    3. As decisões selecionadas não compartilham intervalos temporais.
    """
    price_repo = setup_env()

    # Preços diários de 01/Jan a 60/Jan
    for d in range(1, 61):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc) if d == 1 else datetime(2026, 1, 1, tzinfo=timezone.utc) + pytest.importorskip("datetime").timedelta(days=d - 1)
        price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=dt, price=Decimal(str(30.0 + d * 0.1))))

    cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 2, 28, tzinfo=timezone.utc),
        decision_frequency=EvaluationFrequency.DAILY,
        forward_horizons_days=[10]
    )
    evaluator = ForwardOutcomeEvaluator(price_repo, cfg)

    # 30 decisões consecutivas diárias
    decisions = []
    for d in range(1, 31):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc) + pytest.importorskip("datetime").timedelta(days=d - 1)
        decisions.append(WalkForwardDecision(
            decision_id=f"D_{d}",
            decision_as_of=dt,
            symbol="PETR4",
            consensus_direction=ConsensusDirection.LONG
        ))

    outcomes = [evaluator.evaluate_decision_outcome(dec, horizon_days=10) for dec in decisions]
    assert len(outcomes) == 30

    non_overlap_1 = evaluator.extract_non_overlapping_outcomes(outcomes, horizon_days=10)
    non_overlap_2 = evaluator.extract_non_overlapping_outcomes(outcomes, horizon_days=10)

    # Menos observações (com 30 dias e horizonte 10d, teremos cerca de 3 observações não-sobrepostas)
    assert len(non_overlap_1) < len(outcomes)
    assert len(non_overlap_1) == 3

    # Determinismo absoluto
    assert [o.decision_id for o in non_overlap_1] == [o.decision_id for o in non_overlap_2]
    assert [o.decision_id for o in non_overlap_1] == ["D_1", "D_11", "D_21"]
