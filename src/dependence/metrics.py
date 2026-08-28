import math
from datetime import timedelta
from decimal import Decimal
from typing import Optional, Sequence, Union
from src.config.dependence_config import DependenceConfig
from src.dependence.models import DependenceLevel, TraderTimeSeriesFrame
from src.domain.execution import Execution
from src.domain.trade import ClosedTrade


def calculate_return_correlation(returns_a: list[float], returns_b: list[float]) -> Optional[float]:
    """
    Calcula a correlação linear de Pearson entre duas séries temporais alinhadas de retornos.
    Retorna valor em [-1.0, 1.0] ou None caso não haja variância suficiente (matematicamente indefinida por variância zero).
    """
    n = len(returns_a)
    if n < 2 or len(returns_b) != n:
        return None

    mean_a = sum(returns_a) / n
    mean_b = sum(returns_b) / n

    diff_a = [x - mean_a for x in returns_a]
    diff_b = [y - mean_b for y in returns_b]

    var_a = sum(x * x for x in diff_a)
    var_b = sum(y * y for y in diff_b)

    # Se uma ou ambas as séries não possuem variabilidade (variância zero), a correlação de Pearson é matematicamente indefinida
    if var_a <= 1e-12 or var_b <= 1e-12:
        return None

    covariance = sum(diff_a[i] * diff_b[i] for i in range(n))
    denom = math.sqrt(var_a * var_b)
    if denom <= 1e-12:
        return None

    r = covariance / denom
    # Clamp numérico para evitar erros de ponto flutuante fora de [-1, 1]
    return round(max(min(r, 1.0), -1.0), 4)


def calculate_directional_agreement(
    frames_a: list[TraderTimeSeriesFrame],
    frames_b: list[TraderTimeSeriesFrame]
) -> Optional[float]:
    """
    Calcula a concordância direcional líquida entre dois traders.
    
    Regra estrita anti-inflação: ignora períodos em que ambos permaneceram FLAT (inativos),
    avaliando prioritariamente períodos em que pelo menos um dos traders esteve ativo.
    """
    if len(frames_a) != len(frames_b) or not frames_a:
        return None

    comparable_periods = 0
    same_direction_periods = 0

    for fa, fb in zip(frames_a, frames_b):
        # Determina direção líquida agregada de cada trader no período
        dir_a = 0.0
        if fa.position_directions:
            dir_a = sum(fa.position_directions.values())
        elif fa.net_return != 0.0:
            dir_a = 1.0 if fa.net_return > 0 else -1.0

        dir_b = 0.0
        if fb.position_directions:
            dir_b = sum(fb.position_directions.values())
        elif fb.net_return != 0.0:
            dir_b = 1.0 if fb.net_return > 0 else -1.0

        # Sign normalizado: +1.0 (Long), -1.0 (Short), 0.0 (Flat)
        sign_a = 1.0 if dir_a > 1e-6 else (-1.0 if dir_a < -1e-6 else 0.0)
        sign_b = 1.0 if dir_b > 1e-6 else (-1.0 if dir_b < -1e-6 else 0.0)

        # Se ambos estão FLAT, ignora para não inflar artificialmente a concordância
        if sign_a == 0.0 and sign_b == 0.0 and not fa.is_active and not fb.is_active:
            continue

        comparable_periods += 1
        if sign_a == sign_b and (sign_a != 0.0 or sign_b != 0.0):
            same_direction_periods += 1

    if comparable_periods == 0:
        return 0.0

    return round((same_direction_periods / comparable_periods) * 100.0, 2)


def calculate_position_overlap(
    frames_a: list[TraderTimeSeriesFrame],
    frames_b: list[TraderTimeSeriesFrame]
) -> Optional[float]:
    """
    Calcula a sobreposição posicional específica por ativo e direção.
    
    Distingue:
    - mesma direção + mesmo ativo (soma à sobreposição)
    - mesma direção em ativos diferentes (não conta como sobreposição deste ativo)
    """
    if len(frames_a) != len(frames_b) or not frames_a:
        return None

    total_active_instrument_slots = 0
    coincident_direction_slots = 0

    for fa, fb in zip(frames_a, frames_b):
        symbols_a = set(fa.position_directions.keys())
        symbols_b = set(fb.position_directions.keys())
        all_symbols = symbols_a | symbols_b

        for sym in all_symbols:
            dir_a = fa.position_directions.get(sym, 0.0)
            dir_b = fb.position_directions.get(sym, 0.0)

            sign_a = 1.0 if dir_a > 1e-6 else (-1.0 if dir_a < -1e-6 else 0.0)
            sign_b = 1.0 if dir_b > 1e-6 else (-1.0 if dir_b < -1e-6 else 0.0)

            if sign_a != 0.0 or sign_b != 0.0:
                total_active_instrument_slots += 1
                if sign_a == sign_b and sign_a != 0.0:
                    coincident_direction_slots += 1

    if total_active_instrument_slots == 0:
        return 0.0

    return round((coincident_direction_slots / total_active_instrument_slots) * 100.0, 2)


def calculate_instrument_overlap(
    symbols_or_trades_a: Union[set[str], Sequence[ClosedTrade], Sequence[Execution]],
    symbols_or_trades_b: Union[set[str], Sequence[ClosedTrade], Sequence[Execution]]
) -> float:
    """
    Calcula o índice de Jaccard do universo de instrumentos negociados pelos traders.
    J(A, B) = |A ∩ B| / |A ∪ B| * 100.0
    """
    if isinstance(symbols_or_trades_a, set):
        syms_a = symbols_or_trades_a
    else:
        syms_a = {item.symbol for item in symbols_or_trades_a}

    if isinstance(symbols_or_trades_b, set):
        syms_b = symbols_or_trades_b
    else:
        syms_b = {item.symbol for item in symbols_or_trades_b}

    if not syms_a and not syms_b:
        return 0.0
    union = syms_a | syms_b
    if not union:
        return 0.0
    intersection = syms_a & syms_b
    return round((len(intersection) / len(union)) * 100.0, 2)


def calculate_timing_similarity(
    items_a: Union[Sequence[Execution], Sequence[ClosedTrade]],
    items_b: Union[Sequence[Execution], Sequence[ClosedTrade]],
    tolerance_hours: float = 24.0
) -> float:
    """
    Calcula a proximidade temporal das execuções ou trades de entrada dos traders.
    
    Verifica a proporção de ações de um trader que possuem uma ação correspondente
    no mesmo símbolo e mesmo lado no outro trader dentro da janela de tolerância temporal.
    """
    if not items_a or not items_b:
        return 0.0

    tol_delta = timedelta(hours=tolerance_hours)
    
    # Extrai (symbol, side_str, timestamp) de cada item
    def extract_entry(item):
        sym = item.symbol
        if hasattr(item, "entry_time"):
            ts = item.entry_time
            side = item.side.value if hasattr(item.side, "value") else str(item.side)
        else:
            ts = item.timestamp
            side = item.side.value if hasattr(item.side, "value") else str(item.side)
        return sym, side, ts

    entries_a = [extract_entry(i) for i in items_a]
    entries_b = [extract_entry(i) for i in items_b]

    # 1. Checa correspondência de A para B
    matched_a = 0
    for sym_a, side_a, ts_a in entries_a:
        for sym_b, side_b, ts_b in entries_b:
            if sym_a == sym_b and (side_a == side_b or (side_a == "BUY" and side_b == "LONG") or (side_a == "SELL" and side_b == "SHORT")):
                if abs(ts_a - ts_b) <= tol_delta:
                    matched_a += 1
                    break

    # 2. Checa correspondência de B para A
    matched_b = 0
    for sym_b, side_b, ts_b in entries_b:
        for sym_a, side_a, ts_a in entries_a:
            if sym_b == sym_a and (side_b == side_a or (side_b == "BUY" and side_a == "LONG") or (side_b == "SELL" and side_a == "SHORT")):
                if abs(ts_b - ts_a) <= tol_delta:
                    matched_b += 1
                    break

    total_items = len(entries_a) + len(entries_b)
    if total_items == 0:
        return 0.0

    return round(((matched_a + matched_b) / total_items) * 100.0, 2)


def calculate_composite_redundancy_score(
    config: Optional[DependenceConfig] = None,
    return_correlation: Optional[float] = None,
    directional_agreement: Optional[float] = None,
    position_overlap: Optional[float] = None,
    instrument_overlap: Optional[float] = None,
    timing_similarity: Optional[float] = None,
    **kwargs
) -> Optional[float]:
    """
    Calcula o Composite Redundancy Score (0 a 100) combinando as métricas disponíveis.
    
    Política de Renormalização para Correlação Indefinida (Zero Variância):
    - Se a correlação de Pearson for indefinida (None), o componente é removido da soma e os pesos
      das métricas válidas restantes são proporcionalmente renormalizados para somar 100%.
    - A ausência de correlação linear NÃO é interpretada como evidência de independência.
    
    Tratamento de Correlação Negativa:
    - Correlação positiva (r > 0) contribui diretamente para a redundância (r * 100).
    - Correlação nula ou negativa (r <= 0) representa diversificação/independência, contribuindo com 0.0 de redundância.
    """
    cfg = config or kwargs.get("cfg") or DependenceConfig()

    components: list[tuple[float, float]] = []

    if return_correlation is not None:
        corr_score = max(0.0, return_correlation) * 100.0
        components.append((cfg.weight_return_correlation, corr_score))

    if directional_agreement is not None:
        components.append((cfg.weight_directional_agreement, directional_agreement))

    if position_overlap is not None:
        components.append((cfg.weight_position_overlap, position_overlap))

    if instrument_overlap is not None:
        components.append((cfg.weight_instrument_overlap, instrument_overlap))

    if timing_similarity is not None:
        components.append((cfg.weight_timing_similarity, timing_similarity))

    if not components:
        return None

    total_weight = sum(w for w, _ in components)
    if total_weight <= 0.0:
        return None

    weighted_sum = sum(w * val for w, val in components)
    raw_score = weighted_sum / total_weight

    return round(max(0.0, min(100.0, raw_score)), 2)


def classify_dependence_level(
    score: Optional[float],
    sample_status_or_config: Union[str, DependenceConfig] = "SUFFICIENT",
    config: Optional[DependenceConfig] = None
) -> DependenceLevel:
    """
    Classifica categoricamente o nível de redundância entre dois traders.
    """
    if isinstance(sample_status_or_config, DependenceConfig):
        cfg = sample_status_or_config
        sample_status = "SUFFICIENT"
    else:
        sample_status = sample_status_or_config
        cfg = config or DependenceConfig()

    if sample_status == "INSUFFICIENT_DATA" or score is None:
        return DependenceLevel.INSUFFICIENT_DATA
    if score >= cfg.very_high_redundancy_threshold:
        return DependenceLevel.VERY_HIGH
    if score >= cfg.high_redundancy_threshold:
        return DependenceLevel.HIGH
    if score >= cfg.moderate_redundancy_threshold:
        return DependenceLevel.MODERATE
    return DependenceLevel.LOW


class DependenceCalculator:
    """
    Classe estática agregadora de cálculos e métricas de dependência entre traders.
    """
    @staticmethod
    def compute_pearson_correlation(returns_a: list[float], returns_b: list[float]) -> Optional[float]:
        return calculate_return_correlation(returns_a, returns_b)

    @staticmethod
    def compute_directional_agreement(
        frames_a: list[TraderTimeSeriesFrame],
        frames_b: list[TraderTimeSeriesFrame]
    ) -> Optional[float]:
        return calculate_directional_agreement(frames_a, frames_b)

    @staticmethod
    def compute_position_overlap(
        frames_a: list[TraderTimeSeriesFrame],
        frames_b: list[TraderTimeSeriesFrame]
    ) -> Optional[float]:
        return calculate_position_overlap(frames_a, frames_b)

    @staticmethod
    def compute_instrument_overlap(
        trades_a: Union[set[str], Sequence[ClosedTrade], Sequence[Execution]],
        trades_b: Union[set[str], Sequence[ClosedTrade], Sequence[Execution]]
    ) -> float:
        return calculate_instrument_overlap(trades_a, trades_b)

    @staticmethod
    def compute_timing_similarity(
        items_a: Union[Sequence[Execution], Sequence[ClosedTrade]],
        items_b: Union[Sequence[Execution], Sequence[ClosedTrade]],
        tolerance_hours: float = 24.0
    ) -> float:
        return calculate_timing_similarity(items_a, items_b, tolerance_hours)

    @staticmethod
    def compute_composite_redundancy_score(
        return_correlation: Optional[float] = None,
        directional_agreement: Optional[float] = None,
        position_overlap: Optional[float] = None,
        instrument_overlap: Optional[float] = None,
        timing_similarity: Optional[float] = None,
        config: Optional[DependenceConfig] = None
    ) -> Optional[float]:
        return calculate_composite_redundancy_score(
            config=config,
            return_correlation=return_correlation,
            directional_agreement=directional_agreement,
            position_overlap=position_overlap,
            instrument_overlap=instrument_overlap,
            timing_similarity=timing_similarity
        )

    @staticmethod
    def classify_dependence_level(
        score: Optional[float],
        config: Optional[DependenceConfig] = None,
        sample_status: str = "SUFFICIENT"
    ) -> DependenceLevel:
        return classify_dependence_level(score, sample_status, config)
