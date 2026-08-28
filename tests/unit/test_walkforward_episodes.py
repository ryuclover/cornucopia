from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.walkforward_config import OutcomeClassification, WalkForwardConfig
from src.consensus.models import ConsensusDirection
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories.base import MarketPriceRecord
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.walkforward.episodes import ConsensusEpisodeTracker
from src.walkforward.models import WalkForwardDecision


from src.domain.enums import AssetClass
from src.domain.instrument import MarketInstrument
from src.storage.repositories.instruments import SQLiteInstrumentRepository


def setup_episodes_environment():
    db = SQLiteDatabaseManager(":memory:")
    inst_repo = SQLiteInstrumentRepository(db)
    inst_repo.save(MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL"))
    price_repo = SQLiteMarketPriceRepository(db)
    cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        neutral_return_band_bps=10.0
    )
    tracker = ConsensusEpisodeTracker(price_repo, cfg)
    return price_repo, tracker


def make_dummy_decision(sym: str, as_of: datetime, direction: ConsensusDirection) -> WalkForwardDecision:
    return WalkForwardDecision(
        decision_id=f"{sym}_{as_of.strftime('%Y%m%d')}",
        decision_as_of=as_of,
        symbol=sym,
        consensus_direction=direction,
        consensus_margin=0.40,
        supporting_independent_group_count=2
    )


def test_repeated_decisions_group_into_single_episode():
    """
    4 decisões consecutivas LONG formam exatamente 1 único episódio direcional.
    """
    price_repo, tracker = setup_episodes_environment()

    # Preços
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc), price=Decimal("30.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 13, tzinfo=timezone.utc), price=Decimal("33.00")))

    decs = [
        make_dummy_decision("PETR4", datetime(2026, 1, 10, tzinfo=timezone.utc), ConsensusDirection.LONG),
        make_dummy_decision("PETR4", datetime(2026, 1, 11, tzinfo=timezone.utc), ConsensusDirection.LONG),
        make_dummy_decision("PETR4", datetime(2026, 1, 12, tzinfo=timezone.utc), ConsensusDirection.LONG),
        make_dummy_decision("PETR4", datetime(2026, 1, 13, tzinfo=timezone.utc), ConsensusDirection.LONG),
    ]

    episodes = tracker.track_episodes_for_symbol("PETR4", decs)
    assert len(episodes) == 1
    assert episodes[0].direction == ConsensusDirection.LONG
    assert episodes[0].decision_count == 4
    assert episodes[0].start_as_of == datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert episodes[0].end_as_of == datetime(2026, 1, 13, tzinfo=timezone.utc)
    assert episodes[0].episode_signed_return_pct == pytest.approx(10.0, abs=1e-2)
    assert episodes[0].episode_outcome_class == OutcomeClassification.CORRECT


def test_interrupted_consensus_creates_two_episodes():
    """
    LONG -> NO_CONSENSUS -> LONG produz 2 episódios distintos.
    """
    price_repo, tracker = setup_episodes_environment()

    decs = [
        make_dummy_decision("PETR4", datetime(2026, 1, 10, tzinfo=timezone.utc), ConsensusDirection.LONG),
        make_dummy_decision("PETR4", datetime(2026, 1, 11, tzinfo=timezone.utc), ConsensusDirection.NO_CONSENSUS),
        make_dummy_decision("PETR4", datetime(2026, 1, 12, tzinfo=timezone.utc), ConsensusDirection.LONG),
    ]

    episodes = tracker.track_episodes_for_symbol("PETR4", decs)
    assert len(episodes) == 2
    assert episodes[0].direction == ConsensusDirection.LONG
    assert episodes[0].decision_count == 1
    assert episodes[0].terminated_by == ConsensusDirection.NO_CONSENSUS
    assert episodes[0].is_direct_flip is False

    assert episodes[1].direction == ConsensusDirection.LONG
    assert episodes[1].decision_count == 1


def test_direct_flip_detection():
    """
    LONG -> SHORT encerra o episódio LONG marcando is_direct_flip = True e inicia o episódio SHORT.
    """
    price_repo, tracker = setup_episodes_environment()

    decs = [
        make_dummy_decision("PETR4", datetime(2026, 1, 10, tzinfo=timezone.utc), ConsensusDirection.LONG),
        make_dummy_decision("PETR4", datetime(2026, 1, 11, tzinfo=timezone.utc), ConsensusDirection.LONG),
        make_dummy_decision("PETR4", datetime(2026, 1, 12, tzinfo=timezone.utc), ConsensusDirection.SHORT),
    ]

    episodes = tracker.track_episodes_for_symbol("PETR4", decs)
    assert len(episodes) == 2

    # Episódio 1: LONG encerrado por SHORT (Direct Flip)
    assert episodes[0].direction == ConsensusDirection.LONG
    assert episodes[0].decision_count == 2
    assert episodes[0].terminated_by == ConsensusDirection.SHORT
    assert episodes[0].is_direct_flip is True

    # Episódio 2: SHORT
    assert episodes[1].direction == ConsensusDirection.SHORT
    assert episodes[1].decision_count == 1
