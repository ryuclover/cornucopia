from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence
from src.config.evaluation_config import EvaluationFrequency
from src.config.walkforward_config import WalkForwardConfig
from src.consensus.engine import ConsensusEngine
from src.consensus.models import ConsensusDirection
from src.dependence.engine import TraderDependenceEngine
from src.evaluation.engine import TraderEvaluationEngine
from src.selection.engine import TraderSelectionEngine
from src.signals.engine import TraderSignalEngine
from src.walkforward.models import WalkForwardDecision, WalkForwardDecisionJournal
from src.weighting.engine import TraderWeightEngine
from src.weighting.models import InfeasibleWeightConstraintsError


class WalkForwardDecisionEngine:
    """
    Motor de Decisões Pontuais e Congelamento Auditável do Walk-Forward.
    
    Garante isolamento temporal estrito: em cada 'decision_as_of', executa a cadeia completa
    (Selection -> Dependence -> Weight -> Signals -> Consensus) utilizando exclusivamente
    os dados históricos disponíveis até aquele momento.
    """
    def __init__(
        self,
        selection_engine: TraderSelectionEngine,
        dependence_engine: TraderDependenceEngine,
        weight_engine: TraderWeightEngine,
        signal_engine: TraderSignalEngine,
        consensus_engine: ConsensusEngine,
        config: WalkForwardConfig
    ):
        self.selection_engine = selection_engine
        self.dependence_engine = dependence_engine
        self.weight_engine = weight_engine
        self.signal_engine = signal_engine
        self.consensus_engine = consensus_engine
        self.config = config

    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def generate_decision_timestamps(self) -> list[datetime]:
        """
        Gera a sequência cronológica de timestamps de decisão após o período de warm-up.
        """
        start = self._normalize_utc(self.config.start)
        end = self._normalize_utc(self.config.end)
        warmup_end = start + timedelta(days=self.config.warmup_days)

        all_ts = TraderEvaluationEngine.generate_evaluation_timestamps(
            start=start,
            end=end,
            frequency=self.config.decision_frequency
        )

        # Filtra timestamps estritamente a partir do fim do warm-up
        decision_ts = [ts for ts in all_ts if ts >= warmup_end]
        return decision_ts

    def evaluate_decision_at(
        self,
        as_of: datetime,
        symbols: Optional[Sequence[str]] = None
    ) -> list[WalkForwardDecision]:
        """
        Executa a cadeia analítica completa e produz decisões congeladas para todos os símbolos em 'as_of'.
        """
        as_of = self._normalize_utc(as_of)
        config_fingerprint = f"WF_{self.config.decision_frequency.value}_W{self.config.warmup_days}"

        # 1. Determina universo de símbolos alvos
        if symbols is not None:
            target_symbols = list(symbols)
        else:
            all_traders = self.selection_engine.evaluation_engine.replay_engine.trader_repo.list_all()
            all_tids = [t.trader_id for t in all_traders if t.created_at <= as_of]
            target_symbols = self.signal_engine.discover_active_symbols(as_of, all_tids)
            if not target_symbols:
                # Símbolo fallback se nenhum trader operou
                target_symbols = ["DEFAULT"]

        # 2. Reconstrói o Selected Core em as_of
        selected_core = self.selection_engine.get_selected_core(
            as_of=as_of,
            history_start=self._normalize_utc(self.config.start),
            frequency=self.config.decision_frequency
        )

        selected_tids = [td.trader_id for td in selected_core.selected_traders]
        decisions: list[WalkForwardDecision] = []

        # Se o núcleo estiver vazio em as_of, registra abstenção formal
        if not selected_tids:
            for sym in target_symbols:
                dec = WalkForwardDecision(
                    decision_id=f"{sym}_{as_of.strftime('%Y%m%d%H%M%S')}",
                    decision_as_of=as_of,
                    symbol=sym,
                    selected_trader_ids=[],
                    selected_core_count=0,
                    trader_weights={},
                    consensus_direction=ConsensusDirection.INSUFFICIENT_COVERAGE,
                    reasons=["Núcleo de traders selecionados vazio em as_of (NO_SELECTED_CORE)"],
                    config_fingerprint=config_fingerprint,
                    diagnostics={"selected_core_empty": True}
                )
                decisions.append(dec)
            return decisions

        # 3. Calcula pesos do Core em as_of
        try:
            core_weights = self.weight_engine.calculate_core_weights(
                as_of=as_of,
                selected_core=selected_core
            )
        except InfeasibleWeightConstraintsError as e:
            # Caso as restrições sejam inviáveis matematicamente, registra abstenção
            for sym in target_symbols:
                dec = WalkForwardDecision(
                    decision_id=f"{sym}_{as_of.strftime('%Y%m%d%H%M%S')}",
                    decision_as_of=as_of,
                    symbol=sym,
                    selected_trader_ids=selected_tids,
                    selected_core_count=len(selected_tids),
                    trader_weights={},
                    consensus_direction=ConsensusDirection.NO_CONSENSUS,
                    reasons=[f"Restrições de peso inviáveis em as_of: {str(e)}"],
                    config_fingerprint=config_fingerprint,
                    diagnostics={"weight_infeasible": True}
                )
                decisions.append(dec)
            return decisions

        # 4. Executa Consenso do Núcleo por Instrumento
        core_consensus = self.consensus_engine.calculate_core_consensus(
            as_of=as_of,
            core_weight_snapshot=core_weights,
            symbols=target_symbols
        )

        trader_weights_map = {tw.trader_id: tw.normalized_weight for tw in core_weights.trader_weights}

        for sym in target_symbols:
            inst_cons = core_consensus.consensus_by_instrument.get(sym)
            if inst_cons is None:
                continue

            supp_count = len(inst_cons.long_supporting_traders) if inst_cons.consensus_direction == ConsensusDirection.LONG else (
                len(inst_cons.short_supporting_traders) if inst_cons.consensus_direction == ConsensusDirection.SHORT else 0
            )
            supp_groups = inst_cons.long_supporting_group_count if inst_cons.consensus_direction == ConsensusDirection.LONG else (
                inst_cons.short_supporting_group_count if inst_cons.consensus_direction == ConsensusDirection.SHORT else 0
            )

            dec = WalkForwardDecision(
                decision_id=f"{sym}_{as_of.strftime('%Y%m%d%H%M%S')}",
                decision_as_of=as_of,
                symbol=sym,
                selected_trader_ids=selected_tids,
                selected_core_count=len(selected_tids),
                trader_weights=trader_weights_map,
                consensus_direction=inst_cons.consensus_direction,
                long_weight=inst_cons.long_weight,
                short_weight=inst_cons.short_weight,
                flat_weight=inst_cons.flat_weight,
                no_opinion_weight=inst_cons.no_opinion_weight,
                unknown_weight=inst_cons.unknown_weight,
                coverage_weight=inst_cons.coverage_weight,
                directional_weight=inst_cons.directional_weight,
                directional_agreement_long=inst_cons.directional_agreement_long,
                directional_agreement_short=inst_cons.directional_agreement_short,
                consensus_margin=inst_cons.consensus_margin,
                supporting_trader_count=supp_count,
                supporting_independent_group_count=supp_groups,
                group_direction_breakdown=inst_cons.group_direction_breakdown,
                config_fingerprint=config_fingerprint,
                reasons=inst_cons.reasons,
                triggered_rules=inst_cons.triggered_rules,
                diagnostics={
                    "total_analyzed_instruments": core_consensus.total_instruments_analyzed,
                    "core_weight_snapshot_as_of": core_weights.as_of.isoformat()
                }
            )
            decisions.append(dec)

        return decisions

    def build_decision_journal(
        self,
        symbols: Optional[Sequence[str]] = None
    ) -> WalkForwardDecisionJournal:
        """
        Executa todas as datas de decisão cronológicas e constrói o WalkForwardDecisionJournal.
        """
        timestamps = self.generate_decision_timestamps()
        all_decisions: list[WalkForwardDecision] = []
        by_symbol: dict[str, list[WalkForwardDecision]] = {}

        long_c = 0
        short_c = 0
        neut_c = 0
        no_cons_c = 0
        insuff_c = 0

        for ts in timestamps:
            decs_at_ts = self.evaluate_decision_at(ts, symbols=symbols)
            for d in decs_at_ts:
                all_decisions.append(d)
                by_symbol.setdefault(d.symbol, []).append(d)

                if d.consensus_direction == ConsensusDirection.LONG:
                    long_c += 1
                elif d.consensus_direction == ConsensusDirection.SHORT:
                    short_c += 1
                elif d.consensus_direction == ConsensusDirection.NEUTRAL:
                    neut_c += 1
                elif d.consensus_direction == ConsensusDirection.NO_CONSENSUS:
                    no_cons_c += 1
                elif d.consensus_direction == ConsensusDirection.INSUFFICIENT_COVERAGE:
                    insuff_c += 1

        return WalkForwardDecisionJournal(
            decisions=all_decisions,
            decisions_by_symbol=by_symbol,
            total_decisions=len(all_decisions),
            long_decisions=long_c,
            short_decisions=short_c,
            neutral_decisions=neut_c,
            no_consensus_decisions=no_cons_c,
            insufficient_coverage_decisions=insuff_c
        )
