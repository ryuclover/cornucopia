from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.walkforward_config import BacktestFrictionConfig
from src.consensus.models import ConsensusDirection
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories.base import MarketPriceRecord
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.walkforward.models import WalkForwardDecision
from src.walkforward.simulator import ConsensusShadowStrategySimulator


from src.domain.enums import AssetClass
from src.domain.instrument import MarketInstrument
from src.storage.repositories.instruments import SQLiteInstrumentRepository


def setup_simulator_environment(friction_bps: float = 10.0):
    db = SQLiteDatabaseManager(":memory:")
    inst_repo = SQLiteInstrumentRepository(db)
    inst_repo.save(MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL"))
    price_repo = SQLiteMarketPriceRepository(db)
    friction = BacktestFrictionConfig(commission_bps=friction_bps, spread_bps=0.0, slippage_bps=0.0)
    sim = ConsensusShadowStrategySimulator(price_repo, friction)
    return price_repo, sim


def make_dummy_decision(sym: str, as_of: datetime, direction: ConsensusDirection) -> WalkForwardDecision:
    return WalkForwardDecision(
        decision_id=f"{sym}_{as_of.strftime('%Y%m%d')}",
        decision_as_of=as_of,
        symbol=sym,
        consensus_direction=direction
    )


def test_turnover_and_friction_accounting():
    """
    Testa turnover e custos em diferentes transições:
    - 0 -> LONG (+1): turnover = 1
    - LONG -> LONG: turnover = 0
    - LONG -> SHORT (-1): turnover = 2 (Direct Flip)
    - SHORT -> FLAT (0): turnover = 1
    """
    price_repo, sim = setup_simulator_environment(friction_bps=10.0)  # 10 bps = 0.10%

    # Preços para cada dia
    d1 = datetime(2026, 1, 10, tzinfo=timezone.utc)
    d2 = datetime(2026, 1, 11, tzinfo=timezone.utc)
    d3 = datetime(2026, 1, 12, tzinfo=timezone.utc)
    d4 = datetime(2026, 1, 13, tzinfo=timezone.utc)
    d5 = datetime(2026, 1, 14, tzinfo=timezone.utc)

    for d, p in [(d1, "100.00"), (d2, "100.00"), (d3, "100.00"), (d4, "100.00"), (d5, "100.00")]:
        price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=d, price=Decimal(p)))

    decs = [
        make_dummy_decision("PETR4", d1, ConsensusDirection.LONG),          # 0 -> 1: turnover 1
        make_dummy_decision("PETR4", d2, ConsensusDirection.LONG),          # 1 -> 1: turnover 0
        make_dummy_decision("PETR4", d3, ConsensusDirection.SHORT),         # 1 -> -1: turnover 2
        make_dummy_decision("PETR4", d4, ConsensusDirection.NO_CONSENSUS),  # -1 -> 0: turnover 1
        make_dummy_decision("PETR4", d5, ConsensusDirection.NO_CONSENSUS),  # 0 -> 0: turnover 0
    ]

    res = sim.simulate_shadow_strategy("PETR4", decs)

    assert res.total_turnover == 4.0
    # Com 4 giros a 10 bps cada: custo total = 40 bps = 0.40%
    assert res.total_simulated_costs == pytest.approx(0.40, abs=1e-2)
    # Com preço constante (0% retorno bruto), retorno líquido = -0.399% aprox
    assert res.cumulative_net_return < res.cumulative_gross_return


def test_gross_versus_net_returns_and_drawdowns():
    """
    Testa retorno líquido com alta e queda e cálculo de max drawdown.
    """
    price_repo, sim = setup_simulator_environment(friction_bps=5.0)

    d1 = datetime(2026, 1, 10, tzinfo=timezone.utc)
    d2 = datetime(2026, 1, 11, tzinfo=timezone.utc)
    d3 = datetime(2026, 1, 12, tzinfo=timezone.utc)

    # Dia 1: 100 -> Dia 2: 110 (+10% com LONG)
    # Dia 2: 110 -> Dia 3: 99 (-10% com LONG)
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=d1, price=Decimal("100.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=d2, price=Decimal("110.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=d3, price=Decimal("99.00")))

    decs = [
        make_dummy_decision("PETR4", d1, ConsensusDirection.LONG),
        make_dummy_decision("PETR4", d2, ConsensusDirection.LONG),
        make_dummy_decision("PETR4", d3, ConsensusDirection.LONG),
    ]

    res = sim.simulate_shadow_strategy("PETR4", decs)

    # Peak líquido atingido em D2 (~1.099), queda em D3 (~0.989) -> drawdown ~10% (0.10)
    assert res.max_drawdown > 0.09
    assert len(res.equity_curve) == 3
    assert res.time_in_market_pct == 100.0
    assert res.long_exposure_rate_pct == 100.0
