from datetime import datetime, timezone
from decimal import Decimal
from src.config.dependence_config import DependenceConfig
from src.dependence.engine import TraderDependenceEngine
from src.dependence.models import DependenceLevel
from src.domain.enums import AssetClass, TraderStatus
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories import (
    SQLiteExecutionRepository,
    SQLiteInstrumentRepository,
    SQLiteMarketPriceRepository,
    SQLiteTraderRepository,
)
from src.synthetic.generator import SyntheticDataGenerator


def test_core_dependence_integration_pipeline_with_all_profiles():
    # 1. Configuração do ambiente de teste SQLite em memória
    db = SQLiteDatabaseManager(":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)
    
    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    config = DependenceConfig(
        analysis_window_days=180,
        minimum_overlap_periods=15,
        minimum_overlap_trades=5,
        grouping_redundancy_threshold=65.0
    )
    engine = TraderDependenceEngine(replay_engine=replay, config=config)

    # 2. Cadastro dos Instrumentos
    inst1 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    inst2 = MarketInstrument(symbol="VALE3", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    inst_repo.save(inst1)
    inst_repo.save(inst2)

    # 3. Criação de Traders
    base_time = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    traders = [
        Trader(trader_id="T_BASE", name="Base Steady", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_MIRROR", name="Mirror of Base", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_ANTI", name="Anti Correlated", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_IND", name="Independent Trader", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_TIMING", name="Different Timing", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")),
    ]
    for t in traders:
        trader_repo.save(t)

    # 4. Geração de Dados Sintéticos
    gen = SyntheticDataGenerator(seed=42)
    start_dt = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)

    # Base: Steady Survivor
    execs_base = gen.generate_profile_steady_survivor("T_BASE", symbol="PETR4", start_date=start_dt, trade_count=30)
    # Mirror: Quase idêntico a Base
    execs_mirror = gen.generate_profile_mirror_trader(execs_base, "T_MIRROR", time_shift_seconds=60)
    # Anti-Correlated: Opera contra o Base
    execs_anti = gen.generate_profile_anti_correlated(execs_base, "T_ANTI")
    # Independent: Outro ativo (VALE3) com semente diferente
    gen_ind = SyntheticDataGenerator(seed=999)
    execs_ind = gen_ind.generate_executions_for_trader("T_IND", symbol="VALE3", trade_count=30, start_date=start_dt)
    # Timing diferente: Mesmo ativo (PETR4), mas semanas depois
    execs_timing = gen.generate_profile_different_timing(execs_base, "T_TIMING", time_shift_days=20)

    for ex in execs_base + execs_mirror + execs_anti + execs_ind + execs_timing:
        exec_repo.insert(ex)

    as_of = datetime(2026, 5, 30, tzinfo=timezone.utc)

    # 5. Análise de Pares Individuais
    pair_base_mirror = engine.analyze_pair("T_BASE", "T_MIRROR", as_of=as_of)
    assert pair_base_mirror.sample_status == "SUFFICIENT"
    assert pair_base_mirror.return_correlation is not None and pair_base_mirror.return_correlation > 0.85
    assert pair_base_mirror.composite_redundancy_score is not None and pair_base_mirror.composite_redundancy_score >= 80.0
    assert pair_base_mirror.dependence_level in (DependenceLevel.HIGH, DependenceLevel.VERY_HIGH)

    pair_base_anti = engine.analyze_pair("T_BASE", "T_ANTI", as_of=as_of)
    assert pair_base_anti.sample_status == "SUFFICIENT"
    assert pair_base_anti.return_correlation is not None and pair_base_anti.return_correlation < -0.70
    # Correlação negativa NÃO é tratada como alta redundância!
    assert pair_base_anti.composite_redundancy_score is not None and pair_base_anti.composite_redundancy_score < 40.0
    assert pair_base_anti.dependence_level == DependenceLevel.LOW

    pair_base_ind = engine.analyze_pair("T_BASE", "T_IND", as_of=as_of)
    assert pair_base_ind.sample_status == "SUFFICIENT"
    assert pair_base_ind.instrument_overlap == 0.0 # PETR4 vs VALE3
    assert pair_base_ind.composite_redundancy_score is not None and pair_base_ind.composite_redundancy_score < 30.0
    assert pair_base_ind.dependence_level == DependenceLevel.LOW

    # 6. Análise Integrada do Snapshot do Núcleo
    trader_ids = ["T_BASE", "T_MIRROR", "T_ANTI", "T_IND", "T_TIMING"]
    core_snap = engine.analyze_core(as_of=as_of, selected_trader_ids=trader_ids)

    assert len(core_snap.selected_trader_ids) == 5
    assert core_snap.dependence_matrix.matrix[0][0] == 100.0
    
    # Grupos de Redundância: T_BASE e T_MIRROR devem estar no mesmo grupo, enquanto os outros formam grupos independentes
    group_members = [set(g.member_trader_ids) for g in core_snap.redundancy_groups]
    
    # Encontra o grupo contendo T_BASE
    base_group = next(g for g in group_members if "T_BASE" in g)
    assert "T_MIRROR" in base_group
    
    # Confirma que T_IND e T_ANTI NÃO estão no mesmo grupo de T_BASE
    assert "T_IND" not in base_group
    assert "T_ANTI" not in base_group
    
    # Effective independent groups deve ser menor que o total de 5 traders (ex: 4 blocos independentes)
    assert core_snap.effective_independent_groups_count == 4
    assert len(core_snap.highly_redundant_pairs) >= 1
