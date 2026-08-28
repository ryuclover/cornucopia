from datetime import datetime, timezone
import hashlib
import json
import uuid
from typing import Optional, Sequence
from src.config.walkforward_config import WalkForwardConfig
from src.consensus.engine import ConsensusEngine
from src.dependence.engine import TraderDependenceEngine
from src.evaluation.engine import TraderEvaluationEngine
from src.replay.engine import TraderReplayEngine
from src.selection.engine import TraderSelectionEngine
from src.signals.engine import TraderSignalEngine
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.walkforward.baselines import BaselineEngine
from src.walkforward.decision import WalkForwardDecisionEngine
from src.walkforward.episodes import ConsensusEpisodeTracker
from src.walkforward.metrics import WalkForwardMetricsCalculator
from src.walkforward.models import (
    BaselineComparisonResult,
    HorizonEfficacySummary,
    ShadowStrategyResult,
    WalkForwardRun,
)
from src.walkforward.outcomes import ForwardOutcomeEvaluator
from src.walkforward.simulator import ConsensusShadowStrategySimulator
from src.weighting.engine import TraderWeightEngine


class WalkForwardEngine:
    """
    Motor Master de Execução Walk-Forward, Backtest Out-of-Sample e Validação Coletiva.
    
    Orquestra deterministicamente todo o ciclo:
    1. Warm-up
    2. Tomada e congelamento de decisões cronológicas point-in-time
    3. Avaliação de outcomes futuros (+1d, +5d, +20d) em visões ALL e NON_OVERLAPPING
    4. Agrupamento de episódios direcionais e direct flips
    5. Simulação da Shadow Strategy (curva de equity unitária + fricção)
    6. Comparativo pareado contra baselines (Native, Common Opportunity e Common Directional)
    7. Consolidação de métricas, buckets, isolamento de Holdout, proveniência e integridade
    """
    def __init__(
        self,
        replay_engine: TraderReplayEngine,
        evaluation_engine: TraderEvaluationEngine,
        selection_engine: TraderSelectionEngine,
        dependence_engine: TraderDependenceEngine,
        weight_engine: TraderWeightEngine,
        signal_engine: TraderSignalEngine,
        consensus_engine: ConsensusEngine,
        price_repo: SQLiteMarketPriceRepository,
        config: WalkForwardConfig
    ):
        self.replay_engine = replay_engine
        self.evaluation_engine = evaluation_engine
        self.selection_engine = selection_engine
        self.dependence_engine = dependence_engine
        self.weight_engine = weight_engine
        self.signal_engine = signal_engine
        self.consensus_engine = consensus_engine
        self.price_repo = price_repo
        self.config = config

        # Sub-motores especializados
        self.decision_engine = WalkForwardDecisionEngine(
            selection_engine=selection_engine,
            dependence_engine=dependence_engine,
            weight_engine=weight_engine,
            signal_engine=signal_engine,
            consensus_engine=consensus_engine,
            config=config
        )
        self.outcome_evaluator = ForwardOutcomeEvaluator(price_repo, config)
        self.episode_tracker = ConsensusEpisodeTracker(price_repo, config)
        self.simulator = ConsensusShadowStrategySimulator(price_repo, config.friction)
        self.baseline_engine = BaselineEngine(signal_engine, consensus_engine, price_repo, config)
        self.metrics_calculator = WalkForwardMetricsCalculator(config)

    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _compute_dataset_fingerprint(self, end_dt: datetime) -> str:
        """
        Gera um hash SHA-256 determinístico dos dados disponíveis até end_dt.
        """
        all_traders = self.replay_engine.trader_repo.list_all()
        t_ids = sorted(t.trader_id for t in all_traders if t.created_at <= end_dt)
        all_execs = self.replay_engine.execution_repo.find_all_until_as_of(end_dt)
        
        payload = {
            "traders_count": len(t_ids),
            "executions_count": len(all_execs),
            "first_exec": all_execs[0].execution_id if all_execs else None,
            "last_exec": all_execs[-1].execution_id if all_execs else None,
            "end_dt": end_dt.isoformat()
        }
        dumped = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]

    def run_walk_forward(
        self,
        symbols: Optional[Sequence[str]] = None,
        run_id: Optional[str] = None
    ) -> WalkForwardRun:
        """
        Executa a validação Walk-Forward completa e gera o WalkForwardRun consolidado.
        """
        r_id = run_id or f"RUN_{uuid.uuid4().hex[:8]}"
        now_utc = datetime.now(timezone.utc)
        start_dt = self._normalize_utc(self.config.start)
        end_dt = self._normalize_utc(self.config.end)

        config_fingerprint = self.config.compute_config_fingerprint()
        dataset_fingerprint = self._compute_dataset_fingerprint(end_dt)

        # 1. Executa Diário de Decisões Congeladas
        journal = self.decision_engine.build_decision_journal(symbols=symbols)
        first_decision_ts = journal.decisions[0].decision_as_of if journal.decisions else None
        decisions_map = {d.decision_id: d for d in journal.decisions}

        # 2. Avalia Outcomes Futuros em todos os horizontes (ALL e NON_OVERLAPPING)
        outcomes_by_horizon = self.outcome_evaluator.evaluate_all_decisions(journal.decisions)
        non_overlapping_by_h = self.outcome_evaluator.build_non_overlapping_outcomes_by_horizon(outcomes_by_horizon)

        # 3. Rastreia Episódios Direcionais
        episodes = []
        if self.config.evaluate_consensus_episodes:
            episodes = self.episode_tracker.track_all_episodes(journal.decisions_by_symbol)

        # 4. Simula Shadow Strategy para cada símbolo
        shadow_results: dict[str, ShadowStrategyResult] = {}
        if self.config.enable_shadow_strategy:
            for sym, decs in journal.decisions_by_symbol.items():
                res = self.simulator.simulate_shadow_strategy(sym, decs)
                shadow_results[sym] = res

        # 5. Executa Comparações contra Baselines (3 visões pareadas)
        baseline_comparisons: dict[str, BaselineComparisonResult] = {}
        for mode in self.config.baseline_modes:
            for sym, decs in journal.decisions_by_symbol.items():
                c_shadow = shadow_results.get(sym)
                if c_shadow is not None:
                    comp = self.baseline_engine.compare_with_baseline(mode, decs, c_shadow)
                    baseline_comparisons[f"{sym}_{mode.value}"] = comp

        # 6. Consolidação de Métricas de Eficácia e Horizon Summaries
        efficacy_summaries: dict[int, HorizonEfficacySummary] = {}
        efficacy_by_h: dict[int, dict[str, Any]] = {}
        bucket_by_h: dict[int, dict[str, Any]] = {}

        for h, all_outs in outcomes_by_horizon.items():
            non_overlap_outs = non_overlapping_by_h.get(h, [])
            summary = self.metrics_calculator.calculate_horizon_summary(
                horizon_days=h,
                all_outcomes=all_outs,
                non_overlapping_outcomes=non_overlap_outs,
                decisions_map=decisions_map,
                episodes=episodes
            )
            efficacy_summaries[h] = summary
            efficacy_by_h[h] = self.metrics_calculator.calculate_efficacy_for_horizon(all_outs, decisions_map)
            bucket_by_h[h] = self.metrics_calculator.calculate_bucket_analysis(all_outs, decisions_map)

        # 7. Métricas por Segmentos/Regimes
        segment_metrics = self.metrics_calculator.calculate_segment_metrics(outcomes_by_horizon, decisions_map)

        # 8. Isolamento de Final Holdout
        holdout_metrics: Optional[dict[str, Any]] = None
        full_period_diagnostic: Optional[dict[str, Any]] = None

        if self.config.holdout_start is not None:
            h_start = self._normalize_utc(self.config.holdout_start)
            holdout_outcomes = {
                h: [o for o in outs if self._normalize_utc(o.decision_as_of) >= h_start]
                for h, outs in outcomes_by_horizon.items()
            }
            holdout_metrics = {
                f"horizon_{h}d": self.metrics_calculator.calculate_efficacy_for_horizon(outs, decisions_map)
                for h, outs in holdout_outcomes.items()
            }
            full_period_diagnostic = {
                "diagnostic_label": "FULL_PERIOD_DIAGNOSTIC",
                "metrics_by_horizon": efficacy_by_h
            }

        # 9. Qualidade de Dados e Avisos
        data_quality = self.metrics_calculator.build_data_quality_summary(journal, outcomes_by_horizon)
        warnings = self.metrics_calculator.generate_statistical_warnings(efficacy_by_h, episodes)

        return WalkForwardRun(
            run_id=r_id,
            created_at=now_utc,
            config_fingerprint=config_fingerprint,
            dataset_fingerprint=dataset_fingerprint,
            run_purpose=self.config.run_purpose,
            experiment_name=self.config.experiment_name,
            parent_experiment_id=self.config.parent_experiment_id,
            trial_sequence_number=self.config.trial_sequence_number,
            segment_label=self.config.segment_label,
            start=start_dt,
            end=end_dt,
            warmup_start=start_dt,
            first_decision_at=first_decision_ts,
            decision_journal=journal,
            outcomes_by_horizon=outcomes_by_horizon,
            non_overlapping_outcomes_by_horizon=non_overlapping_by_h,
            episodes=episodes,
            shadow_strategy_by_symbol=shadow_results,
            baseline_comparisons=baseline_comparisons,
            efficacy_summaries_by_horizon=efficacy_summaries,
            efficacy_metrics_by_horizon=efficacy_by_h,
            bucket_metrics_by_horizon=bucket_by_h,
            segment_metrics=segment_metrics,
            holdout_metrics=holdout_metrics,
            full_period_diagnostic_metrics=full_period_diagnostic,
            data_quality_summary=data_quality,
            warnings=warnings
        )
