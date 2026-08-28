from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.walkforward_config import (
    EvaluationStatus,
    OutcomeClassification,
    WalkForwardConfig,
)
from src.consensus.models import ConsensusDirection
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories.base import MarketPriceRecord
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.walkforward.models import WalkForwardDecision
from src.walkforward.outcomes import ForwardOutcomeEvaluator


from src.domain.enums import AssetClass
from src.domain.instrument import MarketInstrument
from src.storage.repositories.instruments import SQLiteInstrumentRepository


def setup_outcomes_environment():
    db = SQLiteDatabaseManager(":memory:")
    inst_repo = SQLiteInstrumentRepository(db)
    inst_repo.save(MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL"))
    price_repo = SQLiteMarketPriceRepository(db)
    cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        forward_horizons_days=[5],
        neutral_return_band_bps=10.0,
        minimum_price_freshness_seconds=86400.0 * 5,
        maximum_future_price_delay_seconds=86400.0 * 5
    )
    evaluator = ForwardOutcomeEvaluator(price_repo, cfg)
    return price_repo, evaluator, cfg


def test_long_correct_and_incorrect():
    """
    LONG com alta de preço -> CORRECT (signed_return > 0).
    LONG com queda de preço -> INCORRECT (signed_return < 0).
    """
    price_repo, evaluator, _ = setup_outcomes_environment()

    # Preço inicial em 10/Jan: R$ 30.00
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 10, 16, 0, tzinfo=timezone.utc), price=Decimal("30.00")))
    # Preço futuro em 15/Jan (+5d): R$ 33.00 (+10%)
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc), price=Decimal("33.00")))

    # 1. Decisão LONG
    dec_long = WalkForwardDecision(
        decision_id="D_LONG",
        decision_as_of=datetime(2026, 1, 10, 16, 0, tzinfo=timezone.utc),
        symbol="PETR4",
        consensus_direction=ConsensusDirection.LONG
    )
    out_long = evaluator.evaluate_decision_outcome(dec_long, horizon_days=5)

    assert out_long.evaluation_status == EvaluationStatus.EVALUATED
    assert out_long.raw_return_pct == pytest.approx(10.0, abs=1e-2)
    assert out_long.signed_return_pct == pytest.approx(10.0, abs=1e-2)
    assert out_long.direction_correct is True
    assert out_long.outcome_class == OutcomeClassification.CORRECT

    # 2. Decisão SHORT no mesmo movimento de alta -> INCORRECT
    dec_short = WalkForwardDecision(
        decision_id="D_SHORT",
        decision_as_of=datetime(2026, 1, 10, 16, 0, tzinfo=timezone.utc),
        symbol="PETR4",
        consensus_direction=ConsensusDirection.SHORT
    )
    out_short = evaluator.evaluate_decision_outcome(dec_short, horizon_days=5)

    assert out_short.signed_return_pct == pytest.approx(-10.0, abs=1e-2)
    assert out_short.direction_correct is False
    assert out_short.outcome_class == OutcomeClassification.INCORRECT


def test_short_correct_and_incorrect():
    """
    SHORT com queda de preço -> CORRECT (signed_return > 0).
    """
    price_repo, evaluator, _ = setup_outcomes_environment()

    # Preço inicial: R$ 30.00
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 10, 16, 0, tzinfo=timezone.utc), price=Decimal("30.00")))
    # Preço futuro (+5d): R$ 27.00 (-10%)
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc), price=Decimal("27.00")))

    dec_short = WalkForwardDecision(
        decision_id="D_SHORT_WIN",
        decision_as_of=datetime(2026, 1, 10, 16, 0, tzinfo=timezone.utc),
        symbol="PETR4",
        consensus_direction=ConsensusDirection.SHORT
    )
    out_short = evaluator.evaluate_decision_outcome(dec_short, horizon_days=5)

    assert out_short.raw_return_pct == pytest.approx(-10.0, abs=1e-2)
    assert out_short.signed_return_pct == pytest.approx(10.0, abs=1e-2)
    assert out_short.direction_correct is True
    assert out_short.outcome_class == OutcomeClassification.CORRECT


def test_neutral_return_band():
    """
    Movimento minúsculo (+0.05% < 10 bps de banda) -> NEUTRAL_OUTCOME.
    """
    price_repo, evaluator, _ = setup_outcomes_environment()

    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 10, 16, 0, tzinfo=timezone.utc), price=Decimal("100.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc), price=Decimal("100.05")))

    dec = WalkForwardDecision(
        decision_id="D_BAND",
        decision_as_of=datetime(2026, 1, 10, 16, 0, tzinfo=timezone.utc),
        symbol="PETR4",
        consensus_direction=ConsensusDirection.LONG
    )
    out = evaluator.evaluate_decision_outcome(dec, horizon_days=5)

    assert out.raw_return_pct == pytest.approx(0.05, abs=1e-3)
    assert out.direction_correct is None
    assert out.outcome_class == OutcomeClassification.NEUTRAL_OUTCOME


def test_missing_and_stale_prices_unevaluable():
    """
    Preço futuro ausente ou cotação inicial defasada (stale) resulta em UNEVALUABLE sem inventar preços.
    """
    price_repo, evaluator, _ = setup_outcomes_environment()

    # Cotação de 01/Jan (14 dias antes de 15/Jan > 5d limite de frescor)
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc), price=Decimal("30.00")))

    dec = WalkForwardDecision(
        decision_id="D_STALE",
        decision_as_of=datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc),
        symbol="PETR4",
        consensus_direction=ConsensusDirection.LONG
    )
    out = evaluator.evaluate_decision_outcome(dec, horizon_days=5)

    assert out.evaluation_status == EvaluationStatus.STALE_REFERENCE_PRICE
    assert out.outcome_class == OutcomeClassification.UNEVALUABLE
    assert out.raw_return_pct is None
