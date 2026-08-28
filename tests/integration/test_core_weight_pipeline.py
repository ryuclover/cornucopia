from datetime import datetime, timezone
from decimal import Decimal
from src.config.dependence_config import DependenceConfig
from src.config.evaluation_config import EvaluationFrequency
from src.config.weight_config import WeightConfig
from src.dependence.engine import TraderDependenceEngine
from src.domain.enums import AssetClass, TraderStatus
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.evaluation.engine import TraderEvaluationEngine
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories import (
    SQLiteExecutionRepository,
    SQLiteInstrumentRepository,
    SQLiteMarketPriceRepository,
    SQLiteTraderRepository,
)
from src.synthetic.generator import SyntheticDataGenerator
from src.weighting.engine import TraderWeightEngine


def test_core_weight_pipeline_integrated_6_profiles_scenario():
    # 1. Configuração do ambiente em memória SQLite
    db = SQLiteDatabaseManager(":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    # 2. Cadastro dos Instrumentos
    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    vale3 = MarketInstrument(symbol="VALE3", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    win = MarketInstrument(symbol="WIN$", asset_class=AssetClass.FUTURES, tick_size=Decimal("5.0"), tick_value=Decimal("1.0"), contract_multiplier=Decimal("0.2"), currency="BRL")
    inst_repo.save(petr4)
    inst_repo.save(vale3)
    inst_repo.save(win)

    # 3. Criação dos 6 Traders do Cenário da Etapa 6
    base_time = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    traders = [
        Trader(trader_id="T_A", name="Steady Survivor Base", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_B", name="Mirror of A", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_C", name="Worse Clone of A", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_D", name="Strong Independent", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_E", name="Median Independent", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_F", name="Few Evidence Trader", created_at=datetime(2026, 3, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")),
    ]
    for t in traders:
        trader_repo.save(t)

    # 4. Geração dos Dados Sintéticos Ponto no Tempo
    gen = SyntheticDataGenerator(seed=42)
    start_dt = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)

    # T_A: Steady Survivor de alta qualidade em PETR4 (35 trades)
    execs_a = gen.generate_profile_steady_survivor("T_A", symbol="PETR4", start_date=start_dt, trade_count=35)
    # T_B: Mirror quase idêntico a A em PETR4 (35 trades)
    execs_b = gen.generate_profile_mirror_trader(execs_a, "T_B", time_shift_seconds=60)
    # T_C: Clone de A em PETR4
    execs_c = gen.generate_profile_mirror_trader(execs_a, "T_C", time_shift_seconds=120)
    # T_D: Trader Independente Forte em VALE3 (35 trades consistentes)
    gen_d = SyntheticDataGenerator(seed=101)
    execs_d = gen_d.generate_profile_steady_survivor("T_D", symbol="VALE3", start_date=start_dt, trade_count=35)
    # T_E: Trader Independente Mediano em WIN$ (30 trades)
    gen_e = SyntheticDataGenerator(seed=202)
    execs_e = gen_e.generate_executions_for_trader("T_E", symbol="WIN$", start_date=start_dt, trade_count=30, win_rate=0.52)
    # T_F: Pouca evidência (iniciado em março, apenas 6 trades)
    gen_f = SyntheticDataGenerator(seed=303)
    execs_f = gen_f.generate_executions_for_trader("T_F", symbol="PETR4", start_date=datetime(2026, 3, 5, tzinfo=timezone.utc), trade_count=6)

    for ex in execs_a + execs_b + execs_c + execs_d + execs_e + execs_f:
        exec_repo.insert(ex)

    # 5. Inicialização dos Motores
    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    eval_engine = TraderEvaluationEngine(replay)
    dep_engine = TraderDependenceEngine(replay)
    weight_engine = TraderWeightEngine(
        evaluation_engine=eval_engine,
        dependence_engine=dep_engine
    )

    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)
    selected_ids = ["T_A", "T_B", "T_C", "T_D", "T_E", "T_F"]

    cfg = WeightConfig(
        quality_weight=0.50,
        independence_weight=0.35,
        confidence_weight=0.15,
        maximum_trader_weight=0.30,
        maximum_group_weight=0.45
    )

    core_weight_snap = weight_engine.calculate_core_weights(
        as_of=as_of,
        config=cfg,
        trader_ids=selected_ids
    )

    weights_map = core_weight_snap.weights_map

    # 6. Validação das Propriedades Fundamentais da Etapa 6:
    
    # Propriedade 1: Normalização exata (Soma dos pesos = 1.0)
    total_w = sum(tw.normalized_weight for tw in core_weight_snap.trader_weights)
    assert abs(total_w - 1.0) < 1e-4

    # Propriedade 2: O trader independente e forte (T_D) recebe peso alto e não é suprimido pelo grupo de clones
    w_d = weights_map["T_D"].normalized_weight
    assert w_d >= 0.20

    # Propriedade 3: O grupo de clones {T_A, T_B, T_C} sofre Group Dilution (não triplica a influência do bloco)
    w_a = weights_map["T_A"].normalized_weight
    w_b = weights_map["T_B"].normalized_weight
    w_c = weights_map["T_C"].normalized_weight
    group_abc_total = w_a + w_b + w_c
    # O grupo inteiro {A, B, C} não ultrapassa o teto e nem 45% do sistema
    assert group_abc_total <= 0.4501

    # Propriedade 4: Trader com pouca evidência (T_F) sofre redução por confidence
    w_f = weights_map["T_F"].normalized_weight
    assert weights_map["T_F"].confidence_component < weights_map["T_A"].confidence_component
    assert w_f < w_d

    # Propriedade 5: Métricas de concentração e número efetivo de traders
    assert core_weight_snap.effective_trader_count >= 3.5
    assert core_weight_snap.concentration_metrics.top_1_weight_share_pct <= 30.1 # Respeita teto individual de 30%

    # 7. Série Longitudinal de Pesos e Turnover
    weight_series, turnovers = weight_engine.calculate_weight_series(
        start=datetime(2026, 1, 31, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        frequency=EvaluationFrequency.MONTHLY,
        config=cfg,
        trader_ids=selected_ids
    )
    assert len(weight_series) >= 2
    assert len(turnovers) >= 1
    for t_metric in turnovers:
        assert t_metric.turnover_pct >= 0.0
