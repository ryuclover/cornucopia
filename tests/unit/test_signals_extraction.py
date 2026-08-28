from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.signal_config import SignalConfig
from src.domain.enums import AssetClass, OrderSide, PositionSide, TraderStatus
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.replay.engine import TraderReplayEngine
from src.signals.extractor import TraderSignalExtractor
from src.signals.models import SignalState
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories import (
    SQLiteExecutionRepository,
    SQLiteInstrumentRepository,
    SQLiteMarketPriceRepository,
    SQLiteTraderRepository,
)


def setup_replay_environment():
    db = SQLiteDatabaseManager(":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    vale3 = MarketInstrument(symbol="VALE3", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    inst_repo.save(petr4)
    inst_repo.save(vale3)

    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    return trader_repo, exec_repo, replay


def test_signal_extraction_open_long_and_short():
    """
    Posição comprada aberta -> LONG (+1.0).
    Posição vendida aberta -> SHORT (-1.0).
    """
    trader_repo, exec_repo, replay = setup_replay_environment()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t_id = "T1"
    trader_repo.save(Trader(trader_id=t_id, name=t_id, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    # Compra PETR4
    exec_repo.insert(Execution(execution_id="E1", trader_id=t_id, symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
    # Vende VALE3 (short)
    exec_repo.insert(Execution(execution_id="E2", trader_id=t_id, symbol="VALE3", timestamp=datetime(2026, 1, 10, 11, 0, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("200"), price=Decimal("60.00")))

    as_of = datetime(2026, 1, 15, tzinfo=timezone.utc)
    sig_petr4 = TraderSignalExtractor.extract_signal(t_id, "PETR4", as_of, replay)
    sig_vale3 = TraderSignalExtractor.extract_signal(t_id, "VALE3", as_of, replay)

    assert sig_petr4.signal_state == SignalState.LONG
    assert sig_petr4.position_side == PositionSide.LONG
    assert sig_petr4.position_quantity == Decimal("100")
    assert sig_petr4.normalized_exposure == 1.0

    assert sig_vale3.signal_state == SignalState.SHORT
    assert sig_vale3.position_side == PositionSide.SHORT
    assert sig_vale3.position_quantity == Decimal("200")
    assert sig_vale3.normalized_exposure == -1.0


def test_signal_extraction_never_traded_is_no_opinion():
    """
    Trader nunca negociou o ativo -> NO_OPINION.
    """
    trader_repo, _, replay = setup_replay_environment()
    t_id = "T1"
    trader_repo.save(Trader(trader_id=t_id, name=t_id, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    as_of = datetime(2026, 1, 15, tzinfo=timezone.utc)
    sig = TraderSignalExtractor.extract_signal(t_id, "PETR4", as_of, replay)

    assert sig.signal_state == SignalState.NO_OPINION
    assert sig.position_side is None
    assert sig.position_quantity == Decimal("0.0")
    assert sig.normalized_exposure == 0.0
    assert sig.last_execution_at is None


def test_signal_extraction_recent_flat_versus_stale_flat():
    """
    Posição zerada com atividade recente (<= lookback de 30d) -> FLAT.
    Posição zerada com atividade antiga (> lookback de 30d) -> NO_OPINION.
    """
    trader_repo, exec_repo, replay = setup_replay_environment()
    t_id = "T1"
    trader_repo.save(Trader(trader_id=t_id, name=t_id, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    # Compra e fecha em 10/Jan
    exec_repo.insert(Execution(execution_id="E1", trader_id=t_id, symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
    exec_repo.insert(Execution(execution_id="E2", trader_id=t_id, symbol="PETR4", timestamp=datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("100"), price=Decimal("31.00")))

    cfg = SignalConfig(flat_activity_lookback_days=30)

    # 15 dias após o encerramento (25/Jan): deve ser FLAT
    as_of_recent = datetime(2026, 1, 25, tzinfo=timezone.utc)
    sig_recent = TraderSignalExtractor.extract_signal(t_id, "PETR4", as_of_recent, replay, config=cfg)
    assert sig_recent.signal_state == SignalState.FLAT
    assert sig_recent.position_side is None
    assert sig_recent.normalized_exposure == 0.0

    # 45 dias após o encerramento (25/Fev): deve ser NO_OPINION
    as_of_stale = datetime(2026, 2, 25, tzinfo=timezone.utc)
    sig_stale = TraderSignalExtractor.extract_signal(t_id, "PETR4", as_of_stale, replay, config=cfg)
    assert sig_stale.signal_state == SignalState.NO_OPINION
    assert sig_stale.position_side is None


def test_old_open_position_remains_active():
    """
    Posição aberta antiga (aberta há 60 dias e não encerrada) continua LONG/SHORT ativa.
    """
    trader_repo, exec_repo, replay = setup_replay_environment()
    t_id = "T1"
    trader_repo.save(Trader(trader_id=t_id, name=t_id, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    exec_repo.insert(Execution(execution_id="E1", trader_id=t_id, symbol="PETR4", timestamp=datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))

    as_of_60d = datetime(2026, 3, 6, tzinfo=timezone.utc)
    sig = TraderSignalExtractor.extract_signal(t_id, "PETR4", as_of_60d, replay)

    assert sig.signal_state == SignalState.LONG
    assert sig.position_side == PositionSide.LONG
    assert sig.position_quantity == Decimal("100")


def test_position_reversal_and_future_execution_insulation():
    """
    Reversão de posição (Buy 100 -> Sell 250 -> Short 150).
    Execução futura em T2 não altera o sinal em T1.
    """
    trader_repo, exec_repo, replay = setup_replay_environment()
    t_id = "T1"
    trader_repo.save(Trader(trader_id=t_id, name=t_id, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    t1 = datetime(2026, 1, 15, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 25, tzinfo=timezone.utc)

    # Execução em 10/Jan: Buy 100
    exec_repo.insert(Execution(execution_id="E1", trader_id=t_id, symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))

    # Execução futura em 20/Jan: Sell 250 (Reversão para Short 150)
    exec_repo.insert(Execution(execution_id="E2", trader_id=t_id, symbol="PETR4", timestamp=datetime(2026, 1, 20, 10, 0, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("250"), price=Decimal("32.00")))

    # Em T1 (15/Jan): Deve ser estritamente LONG 100
    sig_t1 = TraderSignalExtractor.extract_signal(t_id, "PETR4", t1, replay)
    assert sig_t1.signal_state == SignalState.LONG
    assert sig_t1.position_quantity == Decimal("100")

    # Em T2 (25/Jan): Deve ser SHORT 150
    sig_t2 = TraderSignalExtractor.extract_signal(t_id, "PETR4", t2, replay)
    assert sig_t2.signal_state == SignalState.SHORT
    assert sig_t2.position_quantity == Decimal("150")


def test_signal_series_and_symbol_discovery():
    """
    Testa geração de séries temporais de sinais e descoberta de símbolos ativos.
    """
    from src.config.evaluation_config import EvaluationFrequency
    from src.signals.engine import TraderSignalEngine

    trader_repo, exec_repo, replay = setup_replay_environment()
    t_id = "T1"
    trader_repo.save(Trader(trader_id=t_id, name=t_id, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    # Execução em 10/Jan
    exec_repo.insert(Execution(execution_id="E1", trader_id=t_id, symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))

    sig_engine = TraderSignalEngine(replay)

    # 1. Descoberta de símbolos ativos
    active_syms = sig_engine.discover_active_symbols(datetime(2026, 1, 31, tzinfo=timezone.utc), [t_id])
    assert active_syms == ["PETR4"]

    # 2. Série de sinais mensal
    series = sig_engine.extract_signal_series(
        trader_id=t_id,
        symbol="PETR4",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        frequency=EvaluationFrequency.MONTHLY
    )
    assert len(series) >= 2
    # No início de janeiro (01/01), antes da execução de 10/01 -> NO_OPINION
    assert series[0].signal_state == SignalState.NO_OPINION
    # Nos meses subsequentes (31/01, 28/02, 31/03), após a execução -> LONG
    assert all(s.signal_state == SignalState.LONG for s in series[1:])


def test_unknown_symbol_produces_unknown_state():
    """
    Símbolo não cadastrado no repositório de instrumentos retorna UNKNOWN.
    """
    _, _, replay = setup_replay_environment()
    t_id = "T1"
    as_of = datetime(2026, 1, 15, tzinfo=timezone.utc)
    sig = TraderSignalExtractor.extract_signal(t_id, "NON_EXISTING_SYM", as_of, replay)
    assert sig.signal_state == SignalState.UNKNOWN

