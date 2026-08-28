from datetime import datetime, timezone
from decimal import Decimal
from src.domain.enums import AssetClass, TraderStatus
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.ingestion.importer import ExecutionImporter
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.synthetic.generator import SyntheticDataGenerator


def test_multi_trader_ingestion_and_isolated_replay():
    db = DatabaseManager(db_path=":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    # 1. Cadastra 3 Traders e 2 Instrumentos
    traders = [
        Trader(trader_id="T_ALPHA", name="Alpha Trader", initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_BETA", name="Beta Trader", initial_capital=Decimal("20000.00")),
        Trader(trader_id="T_GAMMA", name="Gamma Trader", initial_capital=Decimal("50000.00")),
    ]
    for t in traders:
        trader_repo.save(t)

    instruments = [
        MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY),
        MarketInstrument(symbol="VALE3", asset_class=AssetClass.EQUITY),
    ]
    for inst in instruments:
        inst_repo.save(inst)

    importer = ExecutionImporter(
        execution_repo=exec_repo,
        trader_repo=trader_repo,
        instrument_repo=inst_repo
    )

    # 2. Gera datasets sintéticos para cada trader
    gen = SyntheticDataGenerator(seed=777)
    execs_alpha = gen.generate_executions_for_trader("T_ALPHA", symbol="PETR4", trade_count=50) # 100 execs
    execs_beta = gen.generate_executions_for_trader("T_BETA", symbol="VALE3", trade_count=75)   # 150 execs
    execs_gamma = gen.generate_executions_for_trader("T_GAMMA", symbol="PETR4", trade_count=40) # 80 execs

    assert len(execs_alpha) == 100
    assert len(execs_beta) == 150
    assert len(execs_gamma) == 80

    # 3. Importa todos os dados através de CSV e JSON misturados
    rep_alpha = importer.import_csv(gen.executions_to_csv(execs_alpha), source_name="alpha.csv")
    rep_beta = importer.import_json(gen.executions_to_json(execs_beta), source_name="beta.json")
    rep_gamma = importer.import_csv(gen.executions_to_csv(execs_gamma), source_name="gamma.csv")

    assert rep_alpha.inserted == 100 and rep_alpha.is_success
    assert rep_beta.inserted == 150 and rep_beta.is_success
    assert rep_gamma.inserted == 80 and rep_gamma.is_success

    # Total no banco deve ser exatamente 330 execuções
    assert len(exec_repo.find_by_trader("T_ALPHA")) == 100
    assert len(exec_repo.find_by_trader("T_BETA")) == 150
    assert len(exec_repo.find_by_trader("T_GAMMA")) == 80

    # 4. Executa Replay independente para cada um dos traders
    engine = TraderReplayEngine(
        trader_repo=trader_repo,
        instrument_repo=inst_repo,
        execution_repo=exec_repo,
        market_price_repo=price_repo
    )

    as_of = datetime(2027, 1, 1, tzinfo=timezone.utc)

    res_alpha = engine.replay_trader("T_ALPHA", as_of=as_of)
    res_beta = engine.replay_trader("T_BETA", as_of=as_of)
    res_gamma = engine.replay_trader("T_GAMMA", as_of=as_of)

    # 5. Validação rigorosa de isolamento mútuo (sem contaminação cruzada)
    assert len(res_alpha.closed_trades) == 50
    assert all(t.trader_id == "T_ALPHA" for t in res_alpha.closed_trades)
    assert all(t.symbol == "PETR4" for t in res_alpha.closed_trades)

    assert len(res_beta.closed_trades) == 75
    assert all(t.trader_id == "T_BETA" for t in res_beta.closed_trades)
    assert all(t.symbol == "VALE3" for t in res_beta.closed_trades)

    assert len(res_gamma.closed_trades) == 40
    assert all(t.trader_id == "T_GAMMA" for t in res_gamma.closed_trades)
    assert all(t.symbol == "PETR4" for t in res_gamma.closed_trades)

    # Cada trader possui patrimônio e métricas totalmente isoladas
    assert res_alpha.total_equity != res_beta.total_equity != res_gamma.total_equity
    assert res_alpha.performance.total_trades == 50
    assert res_beta.performance.total_trades == 75
    assert res_gamma.performance.total_trades == 40
