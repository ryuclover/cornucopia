from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from src.config.dependence_config import DependenceConfig
from src.dependence.metrics import DependenceCalculator
from src.dependence.models import DependenceLevel, TraderTimeSeriesFrame
from src.domain.enums import PositionSide
from src.domain.trade import ClosedTrade


def test_pearson_correlation_identical_and_opposite():
    # Séries idênticas -> correlação 1.0
    s1 = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert DependenceCalculator.compute_pearson_correlation(s1, s1) == 1.0

    # Séries opostas -> correlação -1.0
    s2 = [-1.0, -2.0, -3.0, -4.0, -5.0]
    assert DependenceCalculator.compute_pearson_correlation(s1, s2) == -1.0

    # Sem variância (uma constante, uma variável) -> None (indefinido)
    flat = [2.0, 2.0, 2.0, 2.0]
    assert DependenceCalculator.compute_pearson_correlation(flat, s1[:4]) is None

    # Ambas constantes -> None (indefinido)
    flat2 = [5.0, 5.0, 5.0, 5.0]
    assert DependenceCalculator.compute_pearson_correlation(flat, flat2) is None

    # Poucos elementos -> None
    assert DependenceCalculator.compute_pearson_correlation([1.0], [2.0]) is None


def test_directional_agreement_and_flat_inflation_immunity():
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    
    # 2 dias operando na mesma direção (Long), e 8 dias onde ambos estão FLAT
    frames_a = [
        TraderTimeSeriesFrame(timestamp=dt + timedelta(days=0), net_return=1.0, is_active=True, position_directions={"PETR4": 1.0}),
        TraderTimeSeriesFrame(timestamp=dt + timedelta(days=1), net_return=1.0, is_active=True, position_directions={"PETR4": 1.0}),
    ] + [
        TraderTimeSeriesFrame(timestamp=dt + timedelta(days=i), net_return=0.0, is_active=False, position_directions={})
        for i in range(2, 10)
    ]
    
    frames_b = [
        TraderTimeSeriesFrame(timestamp=dt + timedelta(days=0), net_return=1.5, is_active=True, position_directions={"PETR4": 1.0}),
        TraderTimeSeriesFrame(timestamp=dt + timedelta(days=1), net_return=-1.0, is_active=True, position_directions={"PETR4": -1.0}), # Divergência no dia 1
    ] + [
        TraderTimeSeriesFrame(timestamp=dt + timedelta(days=i), net_return=0.0, is_active=False, position_directions={})
        for i in range(2, 10)
    ]

    # Dos 2 dias ativos: 1 dia concorda (Long-Long) e 1 dia discorda (Long-Short).
    # Os 8 dias FLAT NÃO inflam a concordância para 90%. O resultado sobre os períodos ativos deve ser 50%.
    agree = DependenceCalculator.compute_directional_agreement(frames_a, frames_b)
    assert agree == 50.0


def test_position_overlap_metric():
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Mesmo ativo e mesma direção
    fa1 = TraderTimeSeriesFrame(timestamp=dt, is_active=True, position_directions={"PETR4": 1.0, "VALE3": -1.0})
    fb1 = TraderTimeSeriesFrame(timestamp=dt, is_active=True, position_directions={"PETR4": 1.0, "VALE3": -1.0})
    assert DependenceCalculator.compute_position_overlap([fa1], [fb1]) == 100.0

    # Ativos totalmente diferentes
    fb2 = TraderTimeSeriesFrame(timestamp=dt, is_active=True, position_directions={"WIN$": 1.0, "WDO$": -1.0})
    assert DependenceCalculator.compute_position_overlap([fa1], [fb2]) == 0.0


def test_instrument_overlap_jaccard():
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = ClosedTrade(
        trade_id="t1", trader_id="T1", symbol="PETR4", side=PositionSide.LONG,
        entry_time=dt, exit_time=dt + timedelta(hours=2),
        entry_price=Decimal("30"), exit_price=Decimal("31"), quantity=Decimal("100"),
        gross_pnl=Decimal("100"), net_pnl=Decimal("95"), total_commission=Decimal("5"),
        return_pct=3.33
    )
    t2 = ClosedTrade(
        trade_id="t2", trader_id="T1", symbol="VALE3", side=PositionSide.LONG,
        entry_time=dt, exit_time=dt + timedelta(hours=2),
        entry_price=Decimal("60"), exit_price=Decimal("62"), quantity=Decimal("100"),
        gross_pnl=Decimal("200"), net_pnl=Decimal("195"), total_commission=Decimal("5"),
        return_pct=3.33
    )
    t3 = ClosedTrade(
        trade_id="t3", trader_id="T2", symbol="PETR4", side=PositionSide.LONG,
        entry_time=dt, exit_time=dt + timedelta(hours=2),
        entry_price=Decimal("30"), exit_price=Decimal("31"), quantity=Decimal("100"),
        gross_pnl=Decimal("100"), net_pnl=Decimal("95"), total_commission=Decimal("5"),
        return_pct=3.33
    )

    # Overlap entre {PETR4, VALE3} e {PETR4} -> 1 / 2 = 50.0%
    assert DependenceCalculator.compute_instrument_overlap([t1, t2], [t3]) == 50.0


def test_timing_similarity_metric():
    dt = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t1 = ClosedTrade(
        trade_id="t1", trader_id="T1", symbol="PETR4", side=PositionSide.LONG,
        entry_time=dt, exit_time=dt + timedelta(hours=2),
        entry_price=Decimal("30"), exit_price=Decimal("31"), quantity=Decimal("100"),
        gross_pnl=Decimal("100"), net_pnl=Decimal("95"), total_commission=Decimal("5"),
        return_pct=3.33
    )
    # Entrada com apenas 1 hora de diferença (muito próximo dentro de tolerância de 24h)
    t2_close = ClosedTrade(
        trade_id="t2", trader_id="T2", symbol="PETR4", side=PositionSide.LONG,
        entry_time=dt + timedelta(hours=1), exit_time=dt + timedelta(hours=3),
        entry_price=Decimal("30"), exit_price=Decimal("31"), quantity=Decimal("100"),
        gross_pnl=Decimal("100"), net_pnl=Decimal("95"), total_commission=Decimal("5"),
        return_pct=3.33
    )
    score_close = DependenceCalculator.compute_timing_similarity([t1], [t2_close], tolerance_hours=24.0)
    assert score_close > 90.0

    # Entrada com 5 dias de diferença (fora da tolerância de 24h)
    t2_distant = ClosedTrade(
        trade_id="t3", trader_id="T2", symbol="PETR4", side=PositionSide.LONG,
        entry_time=dt + timedelta(days=5), exit_time=dt + timedelta(days=5, hours=2),
        entry_price=Decimal("30"), exit_price=Decimal("31"), quantity=Decimal("100"),
        gross_pnl=Decimal("100"), net_pnl=Decimal("95"), total_commission=Decimal("5"),
        return_pct=3.33
    )
    score_distant = DependenceCalculator.compute_timing_similarity([t1], [t2_distant], tolerance_hours=24.0)
    assert score_distant == 0.0


def test_composite_redundancy_score_and_negative_correlation():
    cfg = DependenceConfig()
    
    # Caso 1: Perfeita similaridade em tudo
    score_mirror = DependenceCalculator.compute_composite_redundancy_score(
        return_correlation=1.0,
        directional_agreement=100.0,
        position_overlap=100.0,
        instrument_overlap=100.0,
        timing_similarity=100.0,
        config=cfg
    )
    assert score_mirror == 100.0
    assert DependenceCalculator.classify_dependence_level(score_mirror, cfg) == DependenceLevel.VERY_HIGH

    # Caso 2: Correlação fortemente negativa (-0.8) NÃO infla o score de redundância
    score_negative = DependenceCalculator.compute_composite_redundancy_score(
        return_correlation=-0.8,
        directional_agreement=0.0,
        position_overlap=0.0,
        instrument_overlap=100.0,
        timing_similarity=0.0,
        config=cfg
    )
    # Contribuição de correlação é 0.0 (e não 80.0)
    assert score_negative is not None
    assert score_negative <= 20.0
    assert DependenceCalculator.classify_dependence_level(score_negative, cfg) == DependenceLevel.LOW


def test_undefined_correlation_renormalization_and_hardening():
    cfg = DependenceConfig(
        weight_return_correlation=0.30,
        weight_directional_agreement=0.25,
        weight_position_overlap=0.20,
        weight_instrument_overlap=0.15,
        weight_timing_similarity=0.10
    )

    # Caso 1: Pearson indefinido (None), mas todas as outras métricas perfeitas (100%)
    # Pesos restantes = 0.25 + 0.20 + 0.15 + 0.10 = 0.70
    # Renormalização: (0.70 * 100) / 0.70 = 100.0
    score_all_perfect = DependenceCalculator.compute_composite_redundancy_score(
        return_correlation=None,
        directional_agreement=100.0,
        position_overlap=100.0,
        instrument_overlap=100.0,
        timing_similarity=100.0,
        config=cfg
    )
    assert score_all_perfect == 100.0
    assert DependenceCalculator.classify_dependence_level(score_all_perfect, cfg) == DependenceLevel.VERY_HIGH

    # Caso 2: Pearson indefinido (None), métricas moderadas:
    # directional=80, position=60, instrument=100, timing=50
    # Numerador = (0.25*80 + 0.20*60 + 0.15*100 + 0.10*50) = 20 + 12 + 15 + 5 = 52.0
    # Denominador = 0.70
    # Score renormalizado = 52.0 / 0.70 = 74.29
    score_renorm = DependenceCalculator.compute_composite_redundancy_score(
        return_correlation=None,
        directional_agreement=80.0,
        position_overlap=60.0,
        instrument_overlap=100.0,
        timing_similarity=50.0,
        config=cfg
    )
    assert score_renorm == 74.29
    assert DependenceCalculator.classify_dependence_level(score_renorm, cfg) == DependenceLevel.HIGH

    # Caso 3: Ausência de Pearson não é tratada como independência automática quando estrutura é 100% igual
    # Se fosse tratado como 0 com peso fixo (sem renormalizar), o score seria 70.0 em vez de 100.0
    assert score_all_perfect > 70.0

