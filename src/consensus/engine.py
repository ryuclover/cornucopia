from datetime import datetime, timezone
from typing import Any, Optional, Sequence
from src.config.consensus_config import ConsensusConfig
from src.config.evaluation_config import EvaluationFrequency
from src.consensus.diagnostics import ConsensusDiagnosticsCalculator
from src.consensus.models import (
    ConsensusDirection,
    ConsensusTurnoverMetric,
    CoreConsensusSnapshot,
    GroupDirectionalState,
    InstrumentConsensusSnapshot,
)
from src.signals.engine import TraderSignalEngine
from src.signals.models import SignalState, TraderSignal
from src.weighting.models import CoreWeightSnapshot


class ConsensusEngine:
    """
    Motor Quantitativo de Consenso Ponderado por Instrumento (Point-in-Time Consensus Engine).
    
    Orquestra:
    1. Agregação ponderada das opiniões individuais do núcleo (LONG, SHORT, FLAT, NO_OPINION, UNKNOWN).
    2. Verificação de cobertura mínima (coverage) e concordância direcional.
    3. Exigência de confirmação por múltiplos Redundancy Groups independentes.
    4. Decisão formal e auditável por ativo (LONG, SHORT, NEUTRAL, NO_CONSENSUS, INSUFFICIENT_COVERAGE).
    """
    def __init__(
        self,
        signal_engine: TraderSignalEngine,
        config: Optional[ConsensusConfig] = None
    ):
        self.signal_engine = signal_engine
        self.config = config or ConsensusConfig()

    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def calculate_instrument_consensus(
        self,
        symbol: str,
        as_of: datetime,
        core_weight_snapshot: CoreWeightSnapshot,
        trader_signals: Sequence[TraderSignal],
        config: Optional[ConsensusConfig] = None
    ) -> InstrumentConsensusSnapshot:
        """
        Calcula o consenso ponderado e auditável para um instrumento específico em 'as_of'.
        """
        cfg = config or self.config
        as_of = self._normalize_utc(as_of)
        snap_as_of = self._normalize_utc(core_weight_snapshot.as_of)

        # 1. Validação de Integridade Temporal e de Pesos
        if snap_as_of != as_of:
            raise ValueError(
                f"Inconsistência temporal: CoreWeightSnapshot.as_of ({snap_as_of.isoformat()}) "
                f"!= Consensus.as_of ({as_of.isoformat()})"
            )

        if abs(core_weight_snapshot.total_normalized_weight - 1.0) > 1e-3 and len(core_weight_snapshot.trader_weights) > 0:
            raise ValueError(
                f"CoreWeightSnapshot inválido: soma total dos pesos = {core_weight_snapshot.total_normalized_weight} (!= 1.0)"
            )

        if not core_weight_snapshot.trader_weights:
            return InstrumentConsensusSnapshot(
                symbol=symbol,
                as_of=as_of,
                consensus_direction=ConsensusDirection.INSUFFICIENT_COVERAGE,
                reasons=["Núcleo de traders selecionados está vazio em as_of"]
            )

        # 2. Mapeamento de Sinais Filtrados Estritamente pelo Core
        core_weights_map = core_weight_snapshot.weights_map
        valid_signals_map: dict[str, TraderSignal] = {}

        for sig in trader_signals:
            if sig.trader_id in core_weights_map:
                valid_signals_map[sig.trader_id] = sig

        # 3. Agregação Individual e por Redundancy Group
        long_w = 0.0
        short_w = 0.0
        flat_w = 0.0
        no_opinion_w = 0.0
        unknown_w = 0.0

        long_traders: list[str] = []
        short_traders: list[str] = []
        flat_traders: list[str] = []
        no_opinion_traders: list[str] = []

        # Estrutura intermediária para agregação interna de cada grupo
        groups_raw: dict[str, dict[str, Any]] = {}
        synthetic_group_counter = -1

        for tw in core_weight_snapshot.trader_weights:
            tid = tw.trader_id
            w = tw.normalized_weight
            gid = tw.redundancy_group_id

            if gid is not None:
                g_key = f"Group_{gid}"
                effective_gid = gid
            else:
                g_key = f"Independent_{tid}"
                effective_gid = synthetic_group_counter
                synthetic_group_counter -= 1

            if g_key not in groups_raw:
                groups_raw[g_key] = {
                    "group_key": g_key,
                    "effective_gid": effective_gid,
                    "is_formal_group": gid is not None,
                    "trader_ids": [],
                    "LONG": 0.0,
                    "SHORT": 0.0,
                    "FLAT": 0.0,
                    "NO_OPINION": 0.0,
                    "UNKNOWN": 0.0,
                    "total_weight": 0.0,
                }

            sig = valid_signals_map.get(tid)
            state = sig.signal_state if sig is not None else SignalState.NO_OPINION

            groups_raw[g_key]["trader_ids"].append(tid)
            groups_raw[g_key]["total_weight"] += w

            if state == SignalState.LONG:
                long_w += w
                long_traders.append(tid)
                groups_raw[g_key]["LONG"] += w
            elif state == SignalState.SHORT:
                short_w += w
                short_traders.append(tid)
                groups_raw[g_key]["SHORT"] += w
            elif state == SignalState.FLAT:
                flat_w += w
                flat_traders.append(tid)
                groups_raw[g_key]["FLAT"] += w
            elif state == SignalState.UNKNOWN:
                unknown_w += w
                groups_raw[g_key]["UNKNOWN"] += w
            else: # NO_OPINION
                no_opinion_w += w
                no_opinion_traders.append(tid)
                groups_raw[g_key]["NO_OPINION"] += w

        # 4. Avaliação e Classificação Direcional Interna de Cada Grupo
        long_confirmed_groups: list[int] = []
        short_confirmed_groups: list[int] = []
        group_support_breakdown: dict[str, Any] = {}
        group_direction_breakdown: dict[str, Any] = {}

        for g_key, g_data in groups_raw.items():
            g_long = round(g_data["LONG"], 4)
            g_short = round(g_data["SHORT"], 4)
            g_flat = round(g_data["FLAT"], 4)
            g_noop = round(g_data["NO_OPINION"], 4)
            g_unkn = round(g_data["UNKNOWN"], 4)
            g_tot = round(g_data["total_weight"], 4)

            g_dir_w = round(g_long + g_short, 4)
            g_margin = round(g_long - g_short, 4)
            g_long_agr = round(g_long / g_dir_w, 4) if g_dir_w > 0 else 0.0
            g_short_agr = round(g_short / g_dir_w, 4) if g_dir_w > 0 else 0.0

            # Condição LONG:
            # - Apoio material: g_long >= cfg.minimum_independent_group_support_weight
            # - Pureza direcional interna: g_long_agr >= cfg.minimum_group_directional_agreement
            # - Margem interna: g_margin >= cfg.minimum_group_directional_margin
            is_long = (
                g_long >= cfg.minimum_independent_group_support_weight and
                g_long_agr >= cfg.minimum_group_directional_agreement and
                g_margin >= cfg.minimum_group_directional_margin
            )

            # Condição SHORT (Simétrica):
            is_short = (
                g_short >= cfg.minimum_independent_group_support_weight and
                g_short_agr >= cfg.minimum_group_directional_agreement and
                (-g_margin) >= cfg.minimum_group_directional_margin
            )

            # Determinação de Estado com Exclusão Mútua Estrita (nunca apoia LONG e SHORT simultaneamente)
            if is_long and not is_short:
                group_state = GroupDirectionalState.LONG
                long_confirmed_groups.append(g_data["effective_gid"])
            elif is_short and not is_long:
                group_state = GroupDirectionalState.SHORT
                short_confirmed_groups.append(g_data["effective_gid"])
            elif g_dir_w > 0 and (g_long > 0 and g_short > 0):
                group_state = GroupDirectionalState.CONFLICT
            elif g_flat >= (g_tot * 0.5) or (g_flat > 0 and g_dir_w == 0):
                group_state = GroupDirectionalState.NEUTRAL
            elif g_unkn > (g_tot * 0.5):
                group_state = GroupDirectionalState.UNKNOWN
            else:
                group_state = GroupDirectionalState.NO_OPINION

            breakdown_entry = {
                "group_id": g_data["effective_gid"],
                "is_formal_group": g_data["is_formal_group"],
                "trader_ids": g_data["trader_ids"],
                "direction": group_state.value,
                "long_weight": g_long,
                "short_weight": g_short,
                "flat_weight": g_flat,
                "no_opinion_weight": g_noop,
                "unknown_weight": g_unkn,
                "total_weight": g_tot,
                "directional_weight": g_dir_w,
                "directional_margin": g_margin,
                "directional_agreement_long": g_long_agr,
                "directional_agreement_short": g_short_agr,
                "is_independent_support_long": (group_state == GroupDirectionalState.LONG),
                "is_independent_support_short": (group_state == GroupDirectionalState.SHORT),
            }

            group_support_breakdown[g_key] = {
                "LONG": g_long,
                "SHORT": g_short,
                "FLAT": g_flat,
                "NO_OPINION": g_noop,
                "UNKNOWN": g_unkn,
                "direction": group_state.value
            }
            group_direction_breakdown[g_key] = breakdown_entry

        # 5. Cálculo das Métricas Globais de Cobertura e Concordância Direcional
        coverage_w = round(long_w + short_w + flat_w, 4)
        directional_w = round(long_w + short_w, 4)
        dir_agree_long = round(long_w / directional_w, 4) if directional_w > 0 else 0.0
        dir_agree_short = round(short_w / directional_w, 4) if directional_w > 0 else 0.0
        margin = round(long_w - short_w, 4)

        long_groups_list = [g for g in long_confirmed_groups if g >= 0]
        short_groups_list = [g for g in short_confirmed_groups if g >= 0]
        long_group_count = len(long_confirmed_groups)
        short_group_count = len(short_confirmed_groups)

        # 6. Avaliação das Regras Determinísticas de Decisão de Consenso
        direction: ConsensusDirection = ConsensusDirection.NO_CONSENSUS
        reasons: list[str] = []
        triggered_rules: list[str] = []

        # Regra 1: Cobertura Mínima (Coverage Check)
        if coverage_w < cfg.minimum_coverage_weight:
            direction = ConsensusDirection.INSUFFICIENT_COVERAGE
            reasons.append(
                f"Cobertura ponderada insuficiente: {coverage_w * 100:.1f}% < mínimo exigido de {cfg.minimum_coverage_weight * 100:.1f}%"
            )
            triggered_rules.append("INSUFFICIENT_COVERAGE")

        # Regra 2: Avaliação de Consenso LONG
        elif (
            long_w >= cfg.minimum_core_support and
            directional_w >= cfg.minimum_directional_weight and
            dir_agree_long >= cfg.minimum_directional_agreement and
            margin >= cfg.minimum_consensus_margin and
            len(long_traders) >= cfg.minimum_supporting_traders and
            long_group_count >= cfg.minimum_supporting_independent_groups and
            short_w <= cfg.maximum_opposition_weight
        ):
            direction = ConsensusDirection.LONG
            reasons.append(
                f"Consenso COMPRADO (LONG) robusto: {long_w * 100:.1f}% de suporte do Core, "
                f"{dir_agree_long * 100:.1f}% de concordância direcional, "
                f"{long_group_count} grupos independentes confirmando e oposição controlada ({short_w * 100:.1f}% <= {cfg.maximum_opposition_weight * 100:.1f}%)"
            )
            triggered_rules.append("STRONG_LONG_CONSENSUS")

        # Regra 3: Avaliação de Consenso SHORT (Simétrica)
        elif (
            short_w >= cfg.minimum_core_support and
            directional_w >= cfg.minimum_directional_weight and
            dir_agree_short >= cfg.minimum_directional_agreement and
            (-margin) >= cfg.minimum_consensus_margin and
            len(short_traders) >= cfg.minimum_supporting_traders and
            short_group_count >= cfg.minimum_supporting_independent_groups and
            long_w <= cfg.maximum_opposition_weight
        ):
            direction = ConsensusDirection.SHORT
            reasons.append(
                f"Consenso VENDIDO (SHORT) robusto: {short_w * 100:.1f}% de suporte do Core, "
                f"{dir_agree_short * 100:.1f}% de concordância direcional, "
                f"{short_group_count} grupos independentes confirmando e oposição controlada ({long_w * 100:.1f}% <= {cfg.maximum_opposition_weight * 100:.1f}%)"
            )
            triggered_rules.append("STRONG_SHORT_CONSENSUS")

        # Regra 4: Avaliação de Estado NEUTRAL (Maioria FLAT)
        elif flat_w >= cfg.minimum_coverage_weight and directional_w < cfg.minimum_directional_weight:
            direction = ConsensusDirection.NEUTRAL
            reasons.append(
                f"Consenso NEUTRO: Cobertura satisfatória ({coverage_w * 100:.1f}%), mas {flat_w * 100:.1f}% do núcleo está deliberadamente zerado (FLAT)"
            )
            triggered_rules.append("MAJORITY_FLAT_NEUTRAL")

        # Regra 5: Conflito / Margem ou Grupos Insuficientes (NO_CONSENSUS)
        else:
            direction = ConsensusDirection.NO_CONSENSUS
            if long_group_count < cfg.minimum_supporting_independent_groups and long_w > short_w:
                reasons.append(
                    f"Sem consenso: Apoiadores LONG confirmados pertencem a apenas {long_group_count} grupo(s) independente(s) (< mínimo exigido de {cfg.minimum_supporting_independent_groups})"
                )
                triggered_rules.append("INSUFFICIENT_INDEPENDENT_GROUPS")
            elif short_group_count < cfg.minimum_supporting_independent_groups and short_w > long_w:
                reasons.append(
                    f"Sem consenso: Apoiadores SHORT confirmados pertencem a apenas {short_group_count} grupo(s) independente(s) (< mínimo exigido de {cfg.minimum_supporting_independent_groups})"
                )
                triggered_rules.append("INSUFFICIENT_INDEPENDENT_GROUPS")
            elif short_w > cfg.maximum_opposition_weight and long_w > short_w:
                reasons.append(
                    f"Sem consenso: Oposição SHORT de {short_w * 100:.1f}% excede teto tolerado de {cfg.maximum_opposition_weight * 100:.1f}%"
                )
                triggered_rules.append("HIGH_OPPOSITION_BLOCK")
            elif long_w > cfg.maximum_opposition_weight and short_w > long_w:
                reasons.append(
                    f"Sem consenso: Oposição LONG de {long_w * 100:.1f}% excede teto tolerado de {cfg.maximum_opposition_weight * 100:.1f}%"
                )
                triggered_rules.append("HIGH_OPPOSITION_BLOCK")
            else:
                reasons.append(
                    f"Sem consenso claro: Disputa direcional ou margem insuficiente ({abs(margin) * 100:.1f}% < {cfg.minimum_consensus_margin * 100:.1f}%)"
                )
                triggered_rules.append("DIRECTIONAL_DISPUTE")

        return InstrumentConsensusSnapshot(
            symbol=symbol,
            as_of=as_of,
            consensus_direction=direction,
            long_weight=round(long_w, 4),
            short_weight=round(short_w, 4),
            flat_weight=round(flat_w, 4),
            no_opinion_weight=round(no_opinion_w, 4),
            unknown_weight=round(unknown_w, 4),
            coverage_weight=coverage_w,
            directional_weight=directional_w,
            directional_agreement_long=dir_agree_long,
            directional_agreement_short=dir_agree_short,
            consensus_margin=margin,
            long_supporting_traders=long_traders,
            short_supporting_traders=short_traders,
            flat_traders=flat_traders,
            no_opinion_traders=no_opinion_traders,
            long_supporting_groups=sorted(long_groups_list),
            short_supporting_groups=sorted(short_groups_list),
            long_supporting_group_count=long_group_count,
            short_supporting_group_count=short_group_count,
            group_support_breakdown=group_support_breakdown,
            group_direction_breakdown=group_direction_breakdown,
            reasons=reasons,
            triggered_rules=triggered_rules,
            diagnostics={
                "core_traders_count": len(core_weight_snapshot.trader_weights),
                "valid_signals_count": len(valid_signals_map),
            }
        )

    def calculate_core_consensus(
        self,
        as_of: datetime,
        core_weight_snapshot: CoreWeightSnapshot,
        symbols: Optional[Sequence[str]] = None,
        config: Optional[ConsensusConfig] = None
    ) -> CoreConsensusSnapshot:
        """
        Calcula o snapshot agregado de consenso para todo o universo ativo de instrumentos em 'as_of'.
        """
        cfg = config or self.config
        as_of = self._normalize_utc(as_of)
        snap_as_of = self._normalize_utc(core_weight_snapshot.as_of)

        if snap_as_of != as_of:
            raise ValueError(
                f"Inconsistência temporal: CoreWeightSnapshot.as_of ({snap_as_of.isoformat()}) "
                f"!= Consensus.as_of ({as_of.isoformat()})"
            )

        active_trader_ids = [tw.trader_id for tw in core_weight_snapshot.trader_weights]
        all_signals_by_symbol = self.signal_engine.extract_core_signals(
            as_of=as_of,
            trader_ids=active_trader_ids,
            symbols=symbols,
            config=None
        )

        target_symbols = list(all_signals_by_symbol.keys())
        consensus_map: dict[str, InstrumentConsensusSnapshot] = {}

        long_count = 0
        short_count = 0
        neutral_count = 0
        no_cons_count = 0
        insuff_count = 0

        for sym in target_symbols:
            sigs = all_signals_by_symbol[sym]
            inst_snap = self.calculate_instrument_consensus(
                symbol=sym,
                as_of=as_of,
                core_weight_snapshot=core_weight_snapshot,
                trader_signals=sigs,
                config=cfg
            )
            consensus_map[sym] = inst_snap

            if inst_snap.consensus_direction == ConsensusDirection.LONG:
                long_count += 1
            elif inst_snap.consensus_direction == ConsensusDirection.SHORT:
                short_count += 1
            elif inst_snap.consensus_direction == ConsensusDirection.NEUTRAL:
                neutral_count += 1
            elif inst_snap.consensus_direction == ConsensusDirection.NO_CONSENSUS:
                no_cons_count += 1
            elif inst_snap.consensus_direction == ConsensusDirection.INSUFFICIENT_COVERAGE:
                insuff_count += 1

        return CoreConsensusSnapshot(
            as_of=as_of,
            weight_snapshot_as_of=snap_as_of,
            instruments=target_symbols,
            consensus_by_instrument=consensus_map,
            long_consensus_count=long_count,
            short_consensus_count=short_count,
            neutral_count=neutral_count,
            no_consensus_count=no_cons_count,
            insufficient_coverage_count=insuff_count,
            total_instruments_analyzed=len(target_symbols),
            diagnostics={
                "core_traders_count": len(active_trader_ids),
                "symbols_count": len(target_symbols),
            }
        )

    def calculate_consensus_series(
        self,
        start: datetime,
        end: datetime,
        weight_series: Sequence[CoreWeightSnapshot],
        symbols: Optional[Sequence[str]] = None,
        frequency: EvaluationFrequency = EvaluationFrequency.MONTHLY,
        config: Optional[ConsensusConfig] = None
    ) -> tuple[list[CoreConsensusSnapshot], list[ConsensusTurnoverMetric]]:
        """
        Gera a série histórica de snapshots de consenso e métricas de turnover/flips.
        """
        cfg = config or self.config
        snapshots: list[CoreConsensusSnapshot] = []

        for w_snap in weight_series:
            c_snap = self.calculate_core_consensus(
                as_of=w_snap.as_of,
                core_weight_snapshot=w_snap,
                symbols=symbols,
                config=cfg
            )
            snapshots.append(c_snap)

        turnovers: list[ConsensusTurnoverMetric] = []
        for i in range(len(snapshots) - 1):
            t_metric = ConsensusDiagnosticsCalculator.calculate_turnover(snapshots[i], snapshots[i + 1])
            turnovers.append(t_metric)

        return snapshots, turnovers
