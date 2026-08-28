from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.selection_config import SelectionConfig
from src.evaluation.models import QualificationStatus, ScoreTrend, TraderEvaluationSnapshot
from src.selection.models import SelectionStatus, TraderSelectionDecision
from src.selection.policy import TraderSelectionPolicy


@pytest.fixture
def base_snapshot():
    return TraderEvaluationSnapshot(
        trader_id="T001",
        as_of=datetime(2026, 1, 30, tzinfo=timezone.utc),
        history_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        history_days=30.0,
        trade_count=20,
        realized_pnl=Decimal("1500.00"),
        realized_equity=Decimal("11500.00"),
        net_return_pct=15.0,
        max_drawdown_pct=5.0,
        win_rate=0.65,
        profit_factor=1.8,
        sharpe_ratio=2.1,
        sortino_ratio=2.5,
        largest_loss_pct=1.2,
        max_consecutive_losses=2,
        top_1_trade_pnl_contribution_pct=25.0,
        top_5_trades_pnl_contribution_pct=55.0,
        top_10_percent_trades_pnl_contribution_pct=35.0,
        survivor_score=80.0,
        is_qualified=True,
        qualification_status=QualificationStatus.QUALIFIED,
        score_lifetime=80.0
    )


def test_insufficient_history_remains_insufficient_data(base_snapshot):
    snap = base_snapshot.model_copy(update={
        "history_days": 5.0,
        "trade_count": 3,
        "is_qualified": False,
        "qualification_status": QualificationStatus.INSUFFICIENT_HISTORY,
        "survivor_score": 0.0
    })
    dec = TraderSelectionPolicy.evaluate_transition(snap, previous_decision=None)
    assert dec.new_status == SelectionStatus.INSUFFICIENT_DATA
    assert "INSUFFICIENT_DATA" in dec.triggered_rules[0]


def test_candidate_requires_confirmation_periods_before_selected(base_snapshot):
    cfg = SelectionConfig(min_survivor_score_candidate=65.0, min_survivor_score_selected=75.0, candidate_confirmation_periods=3)
    
    # 1ª avaliação saudável: torna-se CANDIDATE
    dec1 = TraderSelectionPolicy.evaluate_transition(base_snapshot, previous_decision=None, config=cfg)
    assert dec1.new_status == SelectionStatus.CANDIDATE
    assert dec1.consecutive_qualified_periods == 1

    # 2ª avaliação saudável: permanece CANDIDATE em confirmação
    snap2 = base_snapshot.model_copy(update={"as_of": datetime(2026, 2, 28, tzinfo=timezone.utc)})
    dec2 = TraderSelectionPolicy.evaluate_transition(snap2, previous_decision=dec1, config=cfg)
    assert dec2.new_status == SelectionStatus.CANDIDATE
    assert dec2.consecutive_qualified_periods == 2

    # 3ª avaliação saudável: atinge 3 períodos e é promovido a SELECTED
    snap3 = base_snapshot.model_copy(update={"as_of": datetime(2026, 3, 30, tzinfo=timezone.utc)})
    dec3 = TraderSelectionPolicy.evaluate_transition(snap3, previous_decision=dec2, config=cfg)
    assert dec3.new_status == SelectionStatus.SELECTED
    assert dec3.consecutive_qualified_periods == 3
    assert "PROMOTED_TO_SELECTED" in dec3.triggered_rules


def test_watchlist_transition_on_score_decay_and_recovery(base_snapshot):
    cfg = SelectionConfig(
        min_survivor_score_selected=75.0,
        watchlist_score_threshold=68.0,
        suspension_score_threshold=55.0,
        watchlist_recovery_confirmation_periods=2
    )
    
    # Trader já está SELECTED
    dec_sel = TraderSelectionDecision(
        trader_id="T001",
        as_of=datetime(2026, 3, 30, tzinfo=timezone.utc),
        previous_status=SelectionStatus.CANDIDATE,
        new_status=SelectionStatus.SELECTED,
        survivor_score=80.0,
        qualification_status=QualificationStatus.QUALIFIED,
        score_trend=ScoreTrend.STABLE,
        consecutive_qualified_periods=3,
        consecutive_watchlist_periods=0
    )

    # Queda de score para 65 (abaixo de 68, mas acima de 55): vai para WATCHLIST
    snap_decay = base_snapshot.model_copy(update={
        "as_of": datetime(2026, 4, 30, tzinfo=timezone.utc),
        "survivor_score": 65.0
    })
    dec_watch = TraderSelectionPolicy.evaluate_transition(snap_decay, previous_decision=dec_sel, config=cfg)
    assert dec_watch.new_status == SelectionStatus.WATCHLIST
    assert dec_watch.consecutive_watchlist_periods == 1
    assert "SELECTED_TO_WATCHLIST" in dec_watch.triggered_rules

    # 1ª Recuperação para 78: Permanece em WATCHLIST (1/2 confirmações saudáveis)
    snap_rec_1 = base_snapshot.model_copy(update={
        "as_of": datetime(2026, 5, 30, tzinfo=timezone.utc),
        "survivor_score": 78.0
    })
    dec_rec_1 = TraderSelectionPolicy.evaluate_transition(snap_rec_1, previous_decision=dec_watch, config=cfg)
    assert dec_rec_1.new_status == SelectionStatus.WATCHLIST
    assert dec_rec_1.consecutive_recovery_periods == 1
    assert "MAINTAIN_WATCHLIST_RECOVERING" in dec_rec_1.triggered_rules

    # 2ª Recuperação consecutiva para 80: atinge 2 confirmações e volta a SELECTED
    snap_rec_2 = base_snapshot.model_copy(update={
        "as_of": datetime(2026, 6, 30, tzinfo=timezone.utc),
        "survivor_score": 80.0
    })
    dec_rec_2 = TraderSelectionPolicy.evaluate_transition(snap_rec_2, previous_decision=dec_rec_1, config=cfg)
    assert dec_rec_2.new_status == SelectionStatus.SELECTED
    assert "WATCHLIST_RECOVERED_TO_SELECTED" in dec_rec_2.triggered_rules


def test_watchlist_interrupted_recovery_resets_counter(base_snapshot):
    cfg = SelectionConfig(
        min_survivor_score_selected=75.0,
        watchlist_score_threshold=68.0,
        watchlist_recovery_confirmation_periods=2,
        suspension_trigger_periods=3
    )
    
    dec_watch = TraderSelectionDecision(
        trader_id="T001",
        as_of=datetime(2026, 4, 30, tzinfo=timezone.utc),
        previous_status=SelectionStatus.SELECTED,
        new_status=SelectionStatus.WATCHLIST,
        survivor_score=65.0,
        qualification_status=QualificationStatus.QUALIFIED,
        score_trend=ScoreTrend.STABLE,
        consecutive_watchlist_periods=1
    )

    # 1. Saudável (recuperação 1)
    s_good1 = base_snapshot.model_copy(update={"as_of": datetime(2026, 5, 30, tzinfo=timezone.utc), "survivor_score": 76.0})
    dec_good1 = TraderSelectionPolicy.evaluate_transition(s_good1, previous_decision=dec_watch, config=cfg)
    assert dec_good1.new_status == SelectionStatus.WATCHLIST
    assert dec_good1.consecutive_recovery_periods == 1

    # 2. Ruim / Interrupção (score cai para 64) -> Contador de recuperação reseta para 0
    s_bad = base_snapshot.model_copy(update={"as_of": datetime(2026, 6, 30, tzinfo=timezone.utc), "survivor_score": 64.0})
    dec_bad = TraderSelectionPolicy.evaluate_transition(s_bad, previous_decision=dec_good1, config=cfg)
    assert dec_bad.new_status == SelectionStatus.WATCHLIST
    assert dec_bad.consecutive_recovery_periods == 0

    # 3. Saudável novamente -> Começa do 1 (e NÃO do 2)
    s_good2 = base_snapshot.model_copy(update={"as_of": datetime(2026, 7, 30, tzinfo=timezone.utc), "survivor_score": 78.0})
    dec_good2 = TraderSelectionPolicy.evaluate_transition(s_good2, previous_decision=dec_bad, config=cfg)
    assert dec_good2.new_status == SelectionStatus.WATCHLIST
    assert dec_good2.consecutive_recovery_periods == 1


def test_candidate_interrupted_sequence_resets_confirmation(base_snapshot):
    cfg = SelectionConfig(min_survivor_score_candidate=65.0, min_survivor_score_selected=75.0, candidate_confirmation_periods=3)

    # Início como Candidato (confirmação 1)
    dec1 = TraderSelectionPolicy.evaluate_transition(base_snapshot, previous_decision=None, config=cfg)
    assert dec1.new_status == SelectionStatus.CANDIDATE
    assert dec1.consecutive_qualified_periods == 1

    # Confirmação 2
    s2 = base_snapshot.model_copy(update={"as_of": datetime(2026, 2, 28, tzinfo=timezone.utc)})
    dec2 = TraderSelectionPolicy.evaluate_transition(s2, previous_decision=dec1, config=cfg)
    assert dec2.consecutive_qualified_periods == 2

    # Interrupção (score cai para 62 < 65) -> Confirmação resetada para 0
    s_drop = base_snapshot.model_copy(update={"as_of": datetime(2026, 3, 30, tzinfo=timezone.utc), "survivor_score": 62.0})
    dec_drop = TraderSelectionPolicy.evaluate_transition(s_drop, previous_decision=dec2, config=cfg)
    assert dec_drop.consecutive_qualified_periods == 0
    assert dec_drop.new_status == SelectionStatus.CANDIDATE

    # Recupera para 78 -> Começa contagem do 1 novamente
    s_rec = base_snapshot.model_copy(update={"as_of": datetime(2026, 4, 30, tzinfo=timezone.utc), "survivor_score": 78.0})
    dec_rec = TraderSelectionPolicy.evaluate_transition(s_rec, previous_decision=dec_drop, config=cfg)
    assert dec_rec.consecutive_qualified_periods == 1
    assert dec_rec.new_status == SelectionStatus.CANDIDATE


def test_suspended_interrupted_recovery_resets_reentry_counter(base_snapshot):
    cfg = SelectionConfig(min_survivor_score_candidate=65.0, min_survivor_score_selected=75.0, reentry_confirmation_periods=2)
    
    dec_susp = TraderSelectionDecision(
        trader_id="T001",
        as_of=datetime(2026, 4, 30, tzinfo=timezone.utc),
        previous_status=SelectionStatus.WATCHLIST,
        new_status=SelectionStatus.SUSPENDED,
        survivor_score=50.0,
        qualification_status=QualificationStatus.DISQUALIFIED,
        score_trend=ScoreTrend.DETERIORATING,
        consecutive_recovery_periods=0
    )

    # 1. Recuperação 1/2
    s1 = base_snapshot.model_copy(update={"as_of": datetime(2026, 5, 30, tzinfo=timezone.utc), "survivor_score": 70.0})
    dec1 = TraderSelectionPolicy.evaluate_transition(s1, previous_decision=dec_susp, config=cfg)
    assert dec1.new_status == SelectionStatus.SUSPENDED
    assert dec1.consecutive_recovery_periods == 1

    # 2. Recaída (score cai para 52 < 60) -> Contador reseta para 0
    s_bad = base_snapshot.model_copy(update={"as_of": datetime(2026, 6, 30, tzinfo=timezone.utc), "survivor_score": 52.0})
    dec_bad = TraderSelectionPolicy.evaluate_transition(s_bad, previous_decision=dec1, config=cfg)
    assert dec_bad.new_status == SelectionStatus.SUSPENDED
    assert dec_bad.consecutive_recovery_periods == 0


def test_suspended_reentry_must_pass_through_candidate(base_snapshot):
    cfg = SelectionConfig(min_survivor_score_candidate=65.0, min_survivor_score_selected=75.0, reentry_confirmation_periods=2)
    
    dec_susp = TraderSelectionDecision(
        trader_id="T001",
        as_of=datetime(2026, 4, 30, tzinfo=timezone.utc),
        previous_status=SelectionStatus.WATCHLIST,
        new_status=SelectionStatus.SUSPENDED,
        survivor_score=50.0,
        qualification_status=QualificationStatus.DISQUALIFIED,
        score_trend=ScoreTrend.DETERIORATING,
        consecutive_qualified_periods=0,
        consecutive_watchlist_periods=0,
        consecutive_recovery_periods=0
    )

    # Período 1 de recuperação: ainda SUSPENDED (1/2 confirmações)
    snap_rec_1 = base_snapshot.model_copy(update={"as_of": datetime(2026, 5, 30, tzinfo=timezone.utc), "survivor_score": 76.0})
    dec_rec_1 = TraderSelectionPolicy.evaluate_transition(snap_rec_1, previous_decision=dec_susp, config=cfg)
    assert dec_rec_1.new_status == SelectionStatus.SUSPENDED
    assert dec_rec_1.consecutive_recovery_periods == 1

    # Período 2 de recuperação: atinge 2 confirmações e volta para CANDIDATE (e NÃO direto para SELECTED)
    snap_rec_2 = base_snapshot.model_copy(update={"as_of": datetime(2026, 6, 30, tzinfo=timezone.utc), "survivor_score": 78.0})
    dec_rec_2 = TraderSelectionPolicy.evaluate_transition(snap_rec_2, previous_decision=dec_rec_1, config=cfg)
    assert dec_rec_2.new_status == SelectionStatus.CANDIDATE
    assert "SUSPENDED_READMITTED_AS_CANDIDATE" in dec_rec_2.triggered_rules


def test_suspension_after_persistent_watchlist(base_snapshot):
    cfg = SelectionConfig(watchlist_score_threshold=68.0, suspension_trigger_periods=2)
    
    dec_watch_1 = TraderSelectionDecision(
        trader_id="T001",
        as_of=datetime(2026, 4, 30, tzinfo=timezone.utc),
        previous_status=SelectionStatus.SELECTED,
        new_status=SelectionStatus.WATCHLIST,
        survivor_score=65.0,
        qualification_status=QualificationStatus.QUALIFIED,
        score_trend=ScoreTrend.DETERIORATING,
        consecutive_qualified_periods=3,
        consecutive_watchlist_periods=1
    )

    # 2º período consecutivo degradado em watchlist: é SUSPENSO
    snap_decay_2 = base_snapshot.model_copy(update={
        "as_of": datetime(2026, 5, 30, tzinfo=timezone.utc),
        "survivor_score": 64.0
    })
    dec_susp = TraderSelectionPolicy.evaluate_transition(snap_decay_2, previous_decision=dec_watch_1, config=cfg)
    assert dec_susp.new_status == SelectionStatus.SUSPENDED
    assert "WATCHLIST_TO_SUSPENDED" in dec_susp.triggered_rules


def test_catastrophic_breach_moves_directly_to_excluded(base_snapshot):
    cfg = SelectionConfig(catastrophic_drawdown_pct=35.0)
    
    # Trader SELECTED sofre crash de 40% de drawdown
    dec_sel = TraderSelectionDecision(
        trader_id="T001",
        as_of=datetime(2026, 3, 30, tzinfo=timezone.utc),
        previous_status=SelectionStatus.CANDIDATE,
        new_status=SelectionStatus.SELECTED,
        survivor_score=80.0,
        qualification_status=QualificationStatus.QUALIFIED,
        score_trend=ScoreTrend.STABLE,
        consecutive_qualified_periods=3,
        consecutive_watchlist_periods=0
    )

    snap_crash = base_snapshot.model_copy(update={
        "as_of": datetime(2026, 4, 30, tzinfo=timezone.utc),
        "max_drawdown_pct": 42.0,
        "is_qualified": False,
        "qualification_status": QualificationStatus.DISQUALIFIED,
        "survivor_score": 0.0
    })
    dec_excl = TraderSelectionPolicy.evaluate_transition(snap_crash, previous_decision=dec_sel, config=cfg)
    assert dec_excl.new_status == SelectionStatus.EXCLUDED
    assert "FATAL_CATASTROPHIC_DRAWDOWN" in dec_excl.triggered_rules


def test_lucky_outlier_blocked_by_concentration(base_snapshot):
    cfg = SelectionConfig(max_top_trade_concentration_pct=60.0)
    
    # Trader com lucro concentrado em 85% no Top 1 trade
    snap_outlier = base_snapshot.model_copy(update={
        "top_1_trade_pnl_contribution_pct": 85.0
    })

    dec = TraderSelectionPolicy.evaluate_transition(snap_outlier, previous_decision=None, config=cfg)
    assert dec.new_status == SelectionStatus.INSUFFICIENT_DATA
    assert "REJECT_CANDIDATE_UNQUALIFIED" in dec.triggered_rules
