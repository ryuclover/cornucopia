from typing import Optional
from src.config.selection_config import SelectionConfig
from src.evaluation.models import QualificationStatus, ScoreTrend, TraderEvaluationSnapshot
from src.selection.models import SelectionStatus, TraderSelectionDecision


class TraderSelectionPolicy:
    """
    Máquina de estados e regras determinísticas da política de seleção do núcleo de especialistas.
    
    Aplica histerese, auditoria estrita e proteção contra antecipação (look-ahead bias).
    """
    @staticmethod
    def evaluate_transition(
        snapshot: TraderEvaluationSnapshot,
        previous_decision: Optional[TraderSelectionDecision] = None,
        config: Optional[SelectionConfig] = None,
    ) -> TraderSelectionDecision:
        """
        Calcula a transição de estado de um trader para a data snapshot.as_of.
        """
        cfg = config or SelectionConfig()
        prev_status = previous_decision.new_status if previous_decision else SelectionStatus.INSUFFICIENT_DATA
        prev_qual_periods = previous_decision.consecutive_qualified_periods if previous_decision else 0
        prev_watch_periods = previous_decision.consecutive_watchlist_periods if previous_decision else 0
        prev_rec_periods = previous_decision.consecutive_recovery_periods if previous_decision else 0
        
        reasons: list[str] = []
        triggered_rules: list[str] = []
        
        new_status = prev_status
        consec_qual = prev_qual_periods
        consec_watch = prev_watch_periods
        consec_rec = prev_rec_periods
        
        # 1. Verificação de violação catastrófica (Gatilho Fatal -> EXCLUDED)
        if snapshot.max_drawdown_pct >= cfg.catastrophic_drawdown_pct:
            new_status = SelectionStatus.EXCLUDED
            reasons.append(
                f"Violação catastrófica de risco: Drawdown máximo de {snapshot.max_drawdown_pct:.1f}% "
                f"excedeu o limite severo de {cfg.catastrophic_drawdown_pct:.1f}%."
            )
            triggered_rules.append("FATAL_CATASTROPHIC_DRAWDOWN")
            return TraderSelectionDecision(
                trader_id=snapshot.trader_id,
                as_of=snapshot.as_of,
                previous_status=prev_status,
                new_status=new_status,
                survivor_score=snapshot.survivor_score,
                qualification_status=snapshot.qualification_status,
                score_trend=getattr(snapshot, "score_trend", ScoreTrend.INSUFFICIENT_DATA),
                consecutive_qualified_periods=0,
                consecutive_watchlist_periods=0,
                consecutive_recovery_periods=0,
                reasons=reasons,
                triggered_rules=triggered_rules,
                metrics_summary={
                    "survivor_score": snapshot.survivor_score,
                    "max_drawdown_pct": snapshot.max_drawdown_pct,
                    "trade_count": snapshot.trade_count,
                    "history_days": snapshot.history_days
                }
            )

        # 2. Máquina de Estados Baseada no Estado Anterior
        if prev_status == SelectionStatus.EXCLUDED:
            new_status = SelectionStatus.EXCLUDED
            reasons.append("Trader previamente excluído por violação grave de risco.")
            triggered_rules.append("MAINTAIN_EXCLUDED")
            consec_qual = 0
            consec_watch = 0
            consec_rec = 0

        elif prev_status == SelectionStatus.INSUFFICIENT_DATA:
            if snapshot.qualification_status == QualificationStatus.INSUFFICIENT_HISTORY:
                new_status = SelectionStatus.INSUFFICIENT_DATA
                reasons.append(f"Histórico ainda insuficiente ({snapshot.history_days:.1f} dias, {snapshot.trade_count} trades).")
                triggered_rules.append("MAINTAIN_INSUFFICIENT_DATA")
                consec_qual = 0
                consec_rec = 0
            elif (
                snapshot.is_qualified and
                snapshot.survivor_score >= cfg.min_survivor_score_candidate and
                snapshot.top_1_trade_pnl_contribution_pct <= cfg.max_top_trade_concentration_pct
            ):
                consec_qual = 1
                consec_rec = 0
                if consec_qual >= cfg.candidate_confirmation_periods and snapshot.survivor_score >= cfg.min_survivor_score_selected:
                    new_status = SelectionStatus.SELECTED
                    reasons.append(
                        f"Atingiu maturidade e critérios com score {snapshot.survivor_score:.1f} (>= {cfg.min_survivor_score_selected:.1f}). Promovido a SELECTED."
                    )
                    triggered_rules.append("PROMOTED_TO_SELECTED")
                else:
                    new_status = SelectionStatus.CANDIDATE
                    reasons.append(
                        f"Atingiu critérios mínimos de maturidade com score de {snapshot.survivor_score:.1f} "
                        f"(>= {cfg.min_survivor_score_candidate:.1f}) e perfil saudável. Admitido como CANDIDATE."
                    )
                    triggered_rules.append("ADMIT_CANDIDATE")
            else:
                new_status = SelectionStatus.INSUFFICIENT_DATA
                reasons.append("Não atendeu aos critérios para candidatura (score insuficiente ou desqualificado).")
                triggered_rules.append("REJECT_CANDIDATE_UNQUALIFIED")
                consec_qual = 0
                consec_rec = 0

        elif prev_status == SelectionStatus.CANDIDATE:
            # Verifica se continua com bom desempenho
            is_healthy_candidate = (
                snapshot.is_qualified and
                snapshot.survivor_score >= cfg.min_survivor_score_candidate and
                snapshot.max_drawdown_pct <= cfg.max_allowed_drawdown_pct and
                snapshot.top_1_trade_pnl_contribution_pct <= cfg.max_top_trade_concentration_pct
            )
            
            if is_healthy_candidate:
                consec_qual += 1
                if (
                    consec_qual >= cfg.candidate_confirmation_periods and
                    snapshot.survivor_score >= cfg.min_survivor_score_selected
                ):
                    new_status = SelectionStatus.SELECTED
                    reasons.append(
                        f"Promovido a SELECTED após confirmação consistente de {consec_qual} períodos "
                        f"com score {snapshot.survivor_score:.1f} (>= {cfg.min_survivor_score_selected:.1f})."
                    )
                    triggered_rules.append("PROMOTED_TO_SELECTED")
                else:
                    new_status = SelectionStatus.CANDIDATE
                    reasons.append(
                        f"Permanece como CANDIDATE em período probatório ({consec_qual}/{cfg.candidate_confirmation_periods} confirmações)."
                    )
                    triggered_rules.append("MAINTAIN_CANDIDATE_CONFIRMING")
            else:
                consec_qual = 0  # Quebra de consecutividade: reseta contador!
                if not snapshot.is_qualified or snapshot.survivor_score < cfg.suspension_score_threshold:
                    new_status = SelectionStatus.SUSPENDED
                    reasons.append(
                        f"Candidato suspenso por perda de qualificação ou score muito baixo ({snapshot.survivor_score:.1f} < {cfg.suspension_score_threshold:.1f})."
                    )
                    triggered_rules.append("CANDIDATE_FAILED_TO_SUSPENDED")
                elif snapshot.top_1_trade_pnl_contribution_pct > cfg.max_top_trade_concentration_pct:
                    new_status = SelectionStatus.CANDIDATE
                    reasons.append(
                        f"Concentração excessiva no Top 1 trade ({snapshot.top_1_trade_pnl_contribution_pct:.1f}% > {cfg.max_top_trade_concentration_pct:.1f}%). Confirmação resetada."
                    )
                    triggered_rules.append("BLOCKED_BY_PROFIT_CONCENTRATION")
                else:
                    new_status = SelectionStatus.CANDIDATE
                    reasons.append("Desempenho abaixo do threshold de candidatura neste período; confirmação resetada para 0.")
                    triggered_rules.append("CANDIDATE_CONFIRMATION_RESET")

        elif prev_status == SelectionStatus.SELECTED:
            # Histerese: só vai para WATCHLIST se score cair abaixo de watchlist_score_threshold (< 63) ou drawdown > 20%
            is_score_decaying = snapshot.survivor_score < cfg.watchlist_score_threshold
            is_drawdown_high = snapshot.max_drawdown_pct > cfg.max_allowed_drawdown_pct
            
            if snapshot.survivor_score < cfg.suspension_score_threshold or not snapshot.is_qualified:
                new_status = SelectionStatus.SUSPENDED
                consec_qual = 0
                consec_watch = 0
                consec_rec = 0
                reasons.append(
                    f"Membro SELECTED suspenso imediatamente por queda abrupta de score ({snapshot.survivor_score:.1f} < {cfg.suspension_score_threshold:.1f}) ou desqualificação."
                )
                triggered_rules.append("SELECTED_TO_SUSPENDED_DIRECT")
            elif is_score_decaying or is_drawdown_high:
                new_status = SelectionStatus.WATCHLIST
                consec_watch = 1
                consec_rec = 0
                reasons.append(
                    f"Membro SELECTED movido para WATCHLIST: score {snapshot.survivor_score:.1f} "
                    f"(limiar: {cfg.watchlist_score_threshold:.1f}) ou drawdown {snapshot.max_drawdown_pct:.1f}% (limiar: {cfg.max_allowed_drawdown_pct:.1f}%)."
                )
                triggered_rules.append("SELECTED_TO_WATCHLIST")
            else:
                new_status = SelectionStatus.SELECTED
                consec_watch = 0
                consec_rec = 0
                reasons.append(f"Membro SELECTED estável e em conformidade (score {snapshot.survivor_score:.1f}, drawdown {snapshot.max_drawdown_pct:.1f}%).")
                triggered_rules.append("MAINTAIN_SELECTED")

        elif prev_status == SelectionStatus.WATCHLIST:
            # Avalia se a observação atual é saudável para recuperação
            is_healthy_recovery = (
                snapshot.is_qualified and
                snapshot.survivor_score >= cfg.min_survivor_score_selected and
                snapshot.max_drawdown_pct <= cfg.max_allowed_drawdown_pct and
                snapshot.top_1_trade_pnl_contribution_pct <= cfg.max_top_trade_concentration_pct
            )

            if is_healthy_recovery:
                consec_rec += 1
                if consec_rec >= cfg.watchlist_recovery_confirmation_periods:
                    new_status = SelectionStatus.SELECTED
                    consec_watch = 0
                    consec_rec = 0
                    reasons.append(
                        f"Trader recuperou estabilidade em WATCHLIST após {cfg.watchlist_recovery_confirmation_periods} "
                        f"avaliações consecutivas saudáveis e retornou a SELECTED com score {snapshot.survivor_score:.1f}."
                    )
                    triggered_rules.append("WATCHLIST_RECOVERED_TO_SELECTED")
                else:
                    new_status = SelectionStatus.WATCHLIST
                    reasons.append(
                        f"Trader em recuperação em WATCHLIST ({consec_rec}/{cfg.watchlist_recovery_confirmation_periods} "
                        f"confirmações saudáveis consecutivas necessárias para SELECTED)."
                    )
                    triggered_rules.append("MAINTAIN_WATCHLIST_RECOVERING")
            else:
                # Quebra de sequência de recuperação: reseta consec_rec!
                consec_rec = 0
                consec_watch += 1
                if (
                    not snapshot.is_qualified or
                    snapshot.survivor_score < cfg.suspension_score_threshold or
                    consec_watch >= cfg.suspension_trigger_periods
                ):
                    new_status = SelectionStatus.SUSPENDED
                    consec_watch = 0
                    consec_qual = 0
                    reasons.append(
                        f"Deterioração persistente em WATCHLIST por {prev_watch_periods + 1} períodos ou score crítico ({snapshot.survivor_score:.1f}). Movido para SUSPENDED."
                    )
                    triggered_rules.append("WATCHLIST_TO_SUSPENDED")
                else:
                    new_status = SelectionStatus.WATCHLIST
                    reasons.append(f"Permanece em WATCHLIST sob observação ({consec_watch}/{cfg.suspension_trigger_periods} períodos).")
                    triggered_rules.append("MAINTAIN_WATCHLIST")

        elif prev_status == SelectionStatus.SUSPENDED:
            # Reentrada: Precisa demonstrar recuperação e passar por CANDIDATE antes de SELECTED
            is_recovering = (
                snapshot.is_qualified and
                snapshot.survivor_score >= cfg.min_survivor_score_candidate and
                snapshot.max_drawdown_pct <= cfg.max_allowed_drawdown_pct and
                snapshot.top_1_trade_pnl_contribution_pct <= cfg.max_top_trade_concentration_pct
            )
            
            if is_recovering:
                consec_rec += 1
                if consec_rec >= cfg.reentry_confirmation_periods:
                    new_status = SelectionStatus.CANDIDATE
                    consec_qual = 1
                    consec_rec = 0
                    reasons.append(
                        f"Trader suspenso demonstrou recuperação estável por {cfg.reentry_confirmation_periods} períodos consecutivos e foi readmitido como CANDIDATE."
                    )
                    triggered_rules.append("SUSPENDED_READMITTED_AS_CANDIDATE")
                else:
                    new_status = SelectionStatus.SUSPENDED
                    reasons.append(
                        f"Trader suspenso em recuperação ({consec_rec}/{cfg.reentry_confirmation_periods} confirmações consecutivas para reentrada como CANDIDATE)."
                    )
                    triggered_rules.append("MAINTAIN_SUSPENDED_RECOVERING")
            else:
                new_status = SelectionStatus.SUSPENDED
                consec_rec = 0  # Quebra de consecutividade: reseta contador de reentrada!
                reasons.append("Permanece suspenso (sem recuperação consecutiva de critérios mínimos).")
                triggered_rules.append("MAINTAIN_SUSPENDED")

        return TraderSelectionDecision(
            trader_id=snapshot.trader_id,
            as_of=snapshot.as_of,
            previous_status=prev_status,
            new_status=new_status,
            survivor_score=snapshot.survivor_score,
            qualification_status=snapshot.qualification_status,
            score_trend=getattr(snapshot, "score_trend", ScoreTrend.INSUFFICIENT_DATA),
            consecutive_qualified_periods=consec_qual,
            consecutive_watchlist_periods=consec_watch,
            consecutive_recovery_periods=consec_rec,
            reasons=reasons,
            triggered_rules=triggered_rules,
            metrics_summary={
                "survivor_score": snapshot.survivor_score,
                "net_return_pct": snapshot.net_return_pct,
                "max_drawdown_pct": snapshot.max_drawdown_pct,
                "win_rate": snapshot.win_rate,
                "profit_factor": snapshot.profit_factor,
                "trade_count": snapshot.trade_count,
                "top_1_trade_pnl_contribution_pct": snapshot.top_1_trade_pnl_contribution_pct
            }
        )
