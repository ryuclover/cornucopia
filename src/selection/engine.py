from datetime import datetime, timezone
from decimal import Decimal
import statistics
from typing import Optional, Sequence
from src.config.evaluation_config import EvaluationFrequency
from src.config.selection_config import SelectionConfig
from src.evaluation.engine import TraderEvaluationEngine
from src.evaluation.models import QualificationStatus
from src.selection.models import (
    SelectedCoreSnapshot,
    SelectionChurnMetric,
    SelectionStatus,
    TraderSelectionDecision,
    TraderSelectionHistory,
)
from src.selection.policy import TraderSelectionPolicy


class TraderSelectionEngine:
    """
    Motor de Seleção Formal e Gestão do Núcleo de Especialistas do Cornucopia.
    
    Conecta o TraderEvaluationEngine à TraderSelectionPolicy para produzir decisões auditáveis,
    históricos de seleção, snapshots do núcleo e métricas de rotatividade (churn).
    """
    def __init__(
        self,
        evaluation_engine: TraderEvaluationEngine,
        config: Optional[SelectionConfig] = None,
        policy: Optional[TraderSelectionPolicy] = None,
    ):
        self.evaluation_engine = evaluation_engine
        self.config = config or SelectionConfig()
        self.policy = policy or TraderSelectionPolicy()

    def _normalize_utc(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def evaluate_trader_selection(
        self,
        trader_id: str,
        as_of: datetime,
        history: Optional[TraderSelectionHistory] = None
    ) -> TraderSelectionDecision:
        """
        Avalia o estado de seleção de um trader no instante pontual 'as_of'.
        """
        as_of = self._normalize_utc(as_of)
        snapshot = self.evaluation_engine.evaluate_trader(trader_id, as_of=as_of)
        
        prev_decision = history.decisions[-1] if history and history.decisions else None
        return self.policy.evaluate_transition(
            snapshot=snapshot,
            previous_decision=prev_decision,
            config=self.config
        )

    def evaluate_selection_series(
        self,
        trader_id: str,
        start: datetime,
        end: datetime,
        frequency: Optional[EvaluationFrequency] = None
    ) -> TraderSelectionHistory:
        """
        Executa a avaliação cronológica da seleção de um trader ao longo de uma série temporal.
        """
        start = self._normalize_utc(start)
        end = self._normalize_utc(end)
        
        # Gera os snapshots de avaliação do trader na série
        snapshots = self.evaluation_engine.evaluate_series(
            trader_id=trader_id,
            start=start,
            end=end,
            frequency=frequency
        )

        decisions: list[TraderSelectionDecision] = []
        prev_dec: Optional[TraderSelectionDecision] = None

        for snap in snapshots:
            decision = self.policy.evaluate_transition(
                snapshot=snap,
                previous_decision=prev_dec,
                config=self.config
            )
            decisions.append(decision)
            prev_dec = decision

        curr_status = decisions[-1].new_status if decisions else SelectionStatus.INSUFFICIENT_DATA
        return TraderSelectionHistory(
            trader_id=trader_id,
            decisions=decisions,
            current_status=curr_status
        )

    def get_selected_core(
        self,
        as_of: datetime,
        trader_ids: Optional[list[str]] = None,
        history_start: Optional[datetime] = None,
        frequency: EvaluationFrequency = EvaluationFrequency.MONTHLY
    ) -> SelectedCoreSnapshot:
        """
        Reconstrói o núcleo de traders formalmente selecionados em 'as_of' ponto-no-tempo.
        
        Sem look-ahead bias e sem viés de sobrevivência.
        """
        as_of = self._normalize_utc(as_of)
        trader_repo = self.evaluation_engine.replay_engine.trader_repo

        if trader_ids is not None:
            active_traders = [trader_repo.get_by_id(tid) for tid in trader_ids if trader_repo.get_by_id(tid) is not None]
        else:
            active_traders = trader_repo.list_all()

        # Filtro estrito Ponto no Tempo: traders criados até as_of
        eligible_traders = [t for t in active_traders if t.created_at <= as_of]
        eligible_traders.sort(key=lambda t: t.trader_id)

        all_decisions: list[TraderSelectionDecision] = []
        selected_decisions: list[TraderSelectionDecision] = []

        for trader in eligible_traders:
            start_dt = history_start or trader.created_at
            if start_dt > as_of:
                start_dt = trader.created_at

            # Avalia a série histórica do trader até as_of para preservar a continuidade da máquina de estados
            history = self.evaluate_selection_series(
                trader_id=trader.trader_id,
                start=start_dt,
                end=as_of,
                frequency=frequency
            )
            
            if history.decisions:
                last_dec = history.decisions[-1]
                all_decisions.append(last_dec)
                if last_dec.new_status == SelectionStatus.SELECTED:
                    selected_decisions.append(last_dec)

        # Contadores por estado
        sel_count = len(selected_decisions)
        cand_count = sum(1 for d in all_decisions if d.new_status == SelectionStatus.CANDIDATE)
        watch_count = sum(1 for d in all_decisions if d.new_status == SelectionStatus.WATCHLIST)
        susp_count = sum(1 for d in all_decisions if d.new_status == SelectionStatus.SUSPENDED)
        excl_count = sum(1 for d in all_decisions if d.new_status == SelectionStatus.EXCLUDED)
        insuf_count = sum(1 for d in all_decisions if d.new_status == SelectionStatus.INSUFFICIENT_DATA)

        # Métricas agregadas de qualidade do núcleo
        if selected_decisions:
            scores = [d.survivor_score for d in selected_decisions]
            avg_score = round(statistics.mean(scores), 2)
            min_score = round(min(scores), 2)
            drawdowns = [d.metrics_summary.get("max_drawdown_pct", 0.0) for d in selected_decisions]
            avg_dd = round(statistics.mean(drawdowns), 2)
            avg_qual = 100.0  # Todos os membros selecionados estão ativos
        else:
            avg_score = 0.0
            min_score = 0.0
            avg_dd = 0.0
            avg_qual = 0.0

        return SelectedCoreSnapshot(
            as_of=as_of,
            selected_traders=selected_decisions,
            all_trader_decisions=all_decisions,
            selected_count=sel_count,
            candidate_count=cand_count,
            watchlist_count=watch_count,
            suspended_count=susp_count,
            excluded_count=excl_count,
            insufficient_data_count=insuf_count,
            average_survivor_score=avg_score,
            minimum_survivor_score=min_score,
            average_qualification_rate=avg_qual,
            average_drawdown_pct=avg_dd
        )

    @staticmethod
    def calculate_churn(
        core_before: SelectedCoreSnapshot,
        core_after: SelectedCoreSnapshot
    ) -> SelectionChurnMetric:
        """
        Calcula a rotatividade (churn) do núcleo de traders entre dois snapshots temporais.
        """
        set_before = {d.trader_id for d in core_before.selected_traders}
        set_after = {d.trader_id for d in core_after.selected_traders}

        promoted = sorted(list(set_after - set_before))
        demoted = sorted(list(set_before - set_after))
        churn_count = len(promoted) + len(demoted)
        
        total_pop = max(len(set_before) + len(set_after), 1)
        churn_rate = round(100.0 * churn_count / total_pop, 2)

        return SelectionChurnMetric(
            from_as_of=core_before.as_of,
            to_as_of=core_after.as_of,
            promoted_to_selected=promoted,
            demoted_from_selected=demoted,
            total_selected_before=len(set_before),
            total_selected_after=len(set_after),
            churn_count=churn_count,
            churn_rate_pct=churn_rate
        )

    def get_core_series(
        self,
        start: datetime,
        end: datetime,
        frequency: EvaluationFrequency = EvaluationFrequency.MONTHLY,
        trader_ids: Optional[list[str]] = None
    ) -> tuple[list[SelectedCoreSnapshot], list[SelectionChurnMetric]]:
        """
        Gera uma série cronológica de snapshots do núcleo e a evolução do churn entre períodos.
        """
        start = self._normalize_utc(start)
        end = self._normalize_utc(end)
        timestamps = self.evaluation_engine.generate_evaluation_timestamps(start, end, frequency)

        core_snapshots: list[SelectedCoreSnapshot] = []
        for ts in timestamps:
            snap = self.get_selected_core(as_of=ts, trader_ids=trader_ids, history_start=start, frequency=frequency)
            core_snapshots.append(snap)

        churn_metrics: list[SelectionChurnMetric] = []
        for i in range(len(core_snapshots) - 1):
            churn = self.calculate_churn(core_snapshots[i], core_snapshots[i + 1])
            churn_metrics.append(churn)

        return core_snapshots, churn_metrics
