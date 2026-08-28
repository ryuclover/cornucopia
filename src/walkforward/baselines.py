from datetime import datetime, timezone
from decimal import Decimal
import statistics
from typing import Any, Optional, Sequence
from src.config.consensus_config import ConsensusConfig
from src.config.walkforward_config import BaselineMode, EvaluationStatus, OutcomeClassification, WalkForwardConfig
from src.consensus.engine import ConsensusEngine
from src.consensus.models import ConsensusDirection
from src.signals.engine import TraderSignalEngine
from src.signals.models import SignalState
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.walkforward.models import BaselineComparisonResult, ForwardReturnOutcome, ShadowStrategyResult, WalkForwardDecision
from src.walkforward.outcomes import ForwardOutcomeEvaluator
from src.walkforward.simulator import ConsensusShadowStrategySimulator
from src.weighting.diagnostics import WeightDiagnosticsCalculator
from src.weighting.models import CoreWeightSnapshot, TraderWeight


class BaselineEngine:
    """
    Motor de Baselines Point-in-Time para Comparação e Medição de Valor Incremental.
    
    Implementa 3 visões de comparação rigorosas:
    A. Native Strategy Performance: cada política opera com suas regras e abstenções próprias.
    B. Common Opportunity Comparison: pareamento estrito com missing-data parity no mesmo conjunto de oportunidades.
    C. Common Directional Decision Comparison: isolamento das ocasiões onde ambos os métodos agiram direcionalmente.
    """
    def __init__(
        self,
        signal_engine: TraderSignalEngine,
        consensus_engine: ConsensusEngine,
        price_repo: SQLiteMarketPriceRepository,
        config: WalkForwardConfig
    ):
        self.signal_engine = signal_engine
        self.consensus_engine = consensus_engine
        self.price_repo = price_repo
        self.config = config
        self.simulator = ConsensusShadowStrategySimulator(price_repo, config.friction)
        self.evaluator = ForwardOutcomeEvaluator(price_repo, config)

    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def generate_baseline_decisions(
        self,
        mode: BaselineMode,
        base_decisions: Sequence[WalkForwardDecision]
    ) -> list[WalkForwardDecision]:
        """
        Gera decisões alternativas para o modo de baseline especificado a partir das decisões base.
        """
        baseline_decs: list[WalkForwardDecision] = []

        for d in base_decisions:
            as_of = self._normalize_utc(d.decision_as_of)
            sym = d.symbol
            tids = d.selected_trader_ids

            if not tids:
                baseline_decs.append(d)
                continue

            # Sinais dos traders em as_of
            signals_map = self.signal_engine.extract_core_signals(as_of, tids, symbols=[sym])
            sigs = signals_map.get(sym, [])

            if mode == BaselineMode.EQUAL_WEIGHT:
                # 1. Pesos iguais 1/N
                w_each = round(1.0 / len(tids), 4)
                tw_list = [
                    TraderWeight(
                        trader_id=tid,
                        as_of=as_of,
                        survivor_score=80.0,
                        redundancy_group_id=None,
                        sample_status="SUFFICIENT",
                        quality_component=0.80,
                        independence_component=1.0,
                        confidence_component=1.0,
                        raw_weight=w_each,
                        normalized_weight=w_each,
                        weight_pct=round(w_each * 100.0, 2),
                        caps_applied=[],
                        reasons=["Equal Weight Baseline"]
                    )
                    for tid in tids
                ]
                w_snap = self._build_weight_snapshot(as_of, tw_list)
                inst_cons = self.consensus_engine.calculate_instrument_consensus(sym, as_of, w_snap, sigs)
                direction = inst_cons.consensus_direction

            elif mode == BaselineMode.SIMPLE_MAJORITY:
                # 2. Maioria simples direta
                long_voters = sum(1 for s in sigs if s.signal_state == SignalState.LONG)
                short_voters = sum(1 for s in sigs if s.signal_state == SignalState.SHORT)
                flat_voters = sum(1 for s in sigs if s.signal_state == SignalState.FLAT)
                active_voters = long_voters + short_voters + flat_voters

                cov_rate = active_voters / len(tids) if tids else 0.0
                if cov_rate < 0.50:
                    direction = ConsensusDirection.INSUFFICIENT_COVERAGE
                elif long_voters > short_voters and long_voters >= 2:
                    direction = ConsensusDirection.LONG
                elif short_voters > long_voters and short_voters >= 2:
                    direction = ConsensusDirection.SHORT
                elif flat_voters >= active_voters * 0.5:
                    direction = ConsensusDirection.NEUTRAL
                else:
                    direction = ConsensusDirection.NO_CONSENSUS

            elif mode == BaselineMode.QUALITY_ONLY:
                # 3. Pesos por qualidade pura (sem diluição de grupo)
                w_each = round(1.0 / len(tids), 4)
                tw_list = [
                    TraderWeight(
                        trader_id=tid,
                        as_of=as_of,
                        survivor_score=80.0,
                        redundancy_group_id=None,
                        sample_status="SUFFICIENT",
                        quality_component=0.80,
                        independence_component=1.0,
                        confidence_component=1.0,
                        raw_weight=w_each,
                        normalized_weight=w_each,
                        weight_pct=round(w_each * 100.0, 2),
                        caps_applied=[],
                        reasons=["Quality Only Baseline"]
                    )
                    for tid in tids
                ]
                w_snap = self._build_weight_snapshot(as_of, tw_list)
                cfg = ConsensusConfig(minimum_supporting_independent_groups=1)
                inst_cons = self.consensus_engine.calculate_instrument_consensus(sym, as_of, w_snap, sigs, config=cfg)
                direction = inst_cons.consensus_direction
            else:
                direction = d.consensus_direction

            b_dec = WalkForwardDecision(
                decision_id=f"BASE_{mode.value}_{d.decision_id}",
                decision_as_of=as_of,
                symbol=sym,
                selected_trader_ids=tids,
                selected_core_count=len(tids),
                trader_weights=d.trader_weights,
                consensus_direction=direction,
                config_fingerprint=f"BASELINE_{mode.value}",
                reasons=[f"Decisão do baseline {mode.value}"]
            )
            baseline_decs.append(b_dec)

        return baseline_decs

    def _build_weight_snapshot(self, as_of: datetime, tw_list: list[TraderWeight]) -> CoreWeightSnapshot:
        tot_w = round(sum(tw.normalized_weight for tw in tw_list), 4)
        conc = WeightDiagnosticsCalculator.calculate_concentration(tw_list, [])
        return CoreWeightSnapshot(
            as_of=as_of,
            selected_traders=[tw.trader_id for tw in tw_list],
            selected_trader_ids=[tw.trader_id for tw in tw_list],
            trader_weights=tw_list,
            weights_map={tw.trader_id: tw for tw in tw_list},
            group_summaries=[],
            concentration_metrics=conc,
            effective_trader_count=conc.effective_trader_count,
            highest_weight_trader_id=tw_list[0].trader_id if tw_list else None,
            highest_weight_pct=tw_list[0].weight_pct if tw_list else 0.0,
            lowest_weight_trader_id=tw_list[-1].trader_id if tw_list else None,
            lowest_weight_pct=tw_list[-1].weight_pct if tw_list else 0.0,
            total_normalized_weight=tot_w,
            diagnostics={}
        )

    def compare_with_baseline(
        self,
        mode: BaselineMode,
        cornucopia_decisions: Sequence[WalkForwardDecision],
        cornucopia_shadow: ShadowStrategyResult,
        default_horizon_days: int = 5
    ) -> BaselineComparisonResult:
        """
        Executa a simulação comparativa entre Cornucopia e o Baseline em 3 visões pareadas.
        """
        symbol = cornucopia_shadow.symbol
        base_decs = self.generate_baseline_decisions(mode, cornucopia_decisions)
        base_shadow = self.simulator.simulate_shadow_strategy(symbol, base_decs)

        # 1. Visão A: Native Strategy Performance
        native_corn = {
            "cumulative_net_return_pct": cornucopia_shadow.cumulative_net_return,
            "max_drawdown": cornucopia_shadow.max_drawdown,
            "total_turnover": cornucopia_shadow.total_turnover,
            "time_in_market_pct": cornucopia_shadow.time_in_market_pct,
            "flat_exposure_rate_pct": cornucopia_shadow.flat_exposure_rate_pct,
            "total_simulated_costs_pct": cornucopia_shadow.total_simulated_costs,
            "sharpe_ratio": cornucopia_shadow.sharpe_ratio
        }

        native_base = {
            "cumulative_net_return_pct": base_shadow.cumulative_net_return,
            "max_drawdown": base_shadow.max_drawdown,
            "total_turnover": base_shadow.total_turnover,
            "time_in_market_pct": base_shadow.time_in_market_pct,
            "flat_exposure_rate_pct": base_shadow.flat_exposure_rate_pct,
            "total_simulated_costs_pct": base_shadow.total_simulated_costs,
            "sharpe_ratio": base_shadow.sharpe_ratio
        }

        # 2. Visão B: Common Opportunity Comparison (com Missing-Data Parity)
        h = default_horizon_days
        c_outs = [self.evaluator.evaluate_decision_outcome(d, horizon_days=h) for d in cornucopia_decisions]
        b_outs = [self.evaluator.evaluate_decision_outcome(d, horizon_days=h) for d in base_decs]

        paired_common_c: list[ForwardReturnOutcome] = []
        paired_common_b: list[ForwardReturnOutcome] = []
        missing_count = 0

        for co, bo in zip(c_outs, b_outs):
            # Missing data parity: ambos precisam estar validamente avaliados (EVALUATED)
            if co.evaluation_status == EvaluationStatus.EVALUATED and bo.evaluation_status == EvaluationStatus.EVALUATED:
                paired_common_c.append(co)
                paired_common_b.append(bo)
            else:
                missing_count += 1

        # Métricas no Common Opportunity Set
        common_count = len(paired_common_c)
        c_signed = [o.signed_return_pct for o in paired_common_c if o.signed_return_pct is not None]
        b_signed = [o.signed_return_pct for o in paired_common_b if o.signed_return_pct is not None]

        c_corr = sum(1 for o in paired_common_c if o.outcome_class == OutcomeClassification.CORRECT)
        b_corr = sum(1 for o in paired_common_b if o.outcome_class == OutcomeClassification.CORRECT)

        c_dir_count = sum(1 for d in cornucopia_decisions if d.consensus_direction in (ConsensusDirection.LONG, ConsensusDirection.SHORT))
        b_dir_count = sum(1 for d in base_decs if d.consensus_direction in (ConsensusDirection.LONG, ConsensusDirection.SHORT))

        c_hit = round((c_corr / common_count) * 100.0, 2) if common_count > 0 else None
        b_hit = round((b_corr / common_count) * 100.0, 2) if common_count > 0 else None

        common_opp_metrics = {
            "common_opportunity_count": common_count,
            "missing_data_removed_count": missing_count,
            "cornucopia_hit_rate_pct": c_hit,
            "baseline_hit_rate_pct": b_hit,
            "cornucopia_mean_signed_pct": round(statistics.mean(c_signed), 4) if c_signed else 0.0,
            "baseline_mean_signed_pct": round(statistics.mean(b_signed), 4) if b_signed else 0.0,
            "incremental_signed_mean_pct": round((statistics.mean(c_signed) - statistics.mean(b_signed)), 4) if c_signed and b_signed else 0.0
        }

        # 3. Visão C: Common Directional Decision Comparison
        both_dir_c: list[ForwardReturnOutcome] = []
        both_dir_b: list[ForwardReturnOutcome] = []
        concordance_matches = 0

        for cd, bd, co, bo in zip(cornucopia_decisions, base_decs, c_outs, b_outs):
            if cd.consensus_direction == bd.consensus_direction:
                concordance_matches += 1

            c_is_dir = cd.consensus_direction in (ConsensusDirection.LONG, ConsensusDirection.SHORT)
            b_is_dir = bd.consensus_direction in (ConsensusDirection.LONG, ConsensusDirection.SHORT)

            if c_is_dir and b_is_dir and co.evaluation_status == EvaluationStatus.EVALUATED and bo.evaluation_status == EvaluationStatus.EVALUATED:
                both_dir_c.append(co)
                both_dir_b.append(bo)

        common_dir_count = len(both_dir_c)
        common_dir_metrics: Optional[dict[str, Any]] = None

        if common_dir_count > 0:
            c_both_corr = sum(1 for o in both_dir_c if o.outcome_class == OutcomeClassification.CORRECT)
            b_both_corr = sum(1 for o in both_dir_b if o.outcome_class == OutcomeClassification.CORRECT)
            common_dir_metrics = {
                "common_directional_count": common_dir_count,
                "cornucopia_hit_rate_pct": round((c_both_corr / common_dir_count) * 100.0, 2),
                "baseline_hit_rate_pct": round((b_both_corr / common_dir_count) * 100.0, 2),
            }

        concordance_rate = (concordance_matches / len(cornucopia_decisions)) * 100.0 if cornucopia_decisions else 0.0
        inc_ret = round(cornucopia_shadow.cumulative_net_return - base_shadow.cumulative_net_return, 4)
        c_sharpe = cornucopia_shadow.sharpe_ratio or 0.0
        b_sharpe = base_shadow.sharpe_ratio or 0.0
        inc_sharpe = round(c_sharpe - b_sharpe, 4)

        return BaselineComparisonResult(
            baseline_mode=mode,
            decision_count=len(base_decs),
            common_opportunity_count=common_count,
            missing_data_removed_count=missing_count,
            common_directional_count=common_dir_count,
            directional_concordance_rate=round(concordance_rate, 2),
            native_cornucopia=native_corn,
            native_baseline=native_base,
            common_opportunity_metrics=common_opp_metrics,
            common_directional_metrics=common_dir_metrics,
            incremental_return=inc_ret,
            incremental_sharpe=inc_sharpe,
            diagnostics={
                "horizon_days": h,
                "baseline_decisions_count": len(base_decs)
            }
        )
