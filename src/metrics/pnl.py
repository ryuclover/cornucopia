import math
from decimal import Decimal
from typing import Sequence
from src.domain.trade import ClosedTrade


def calculate_top_n_pnl_contribution(trades: Sequence[ClosedTrade], n: int) -> float:
    """
    Calcula a contribuição percentual dos N maiores trades vencedores sobre o lucro bruto total.
    """
    if n <= 0:
        return 0.0
    wins = [t.net_pnl for t in trades if t.net_pnl > 0]
    total_gain = sum(wins, Decimal("0.0"))
    if total_gain <= 0:
        return 0.0
    sorted_wins = sorted(wins, reverse=True)
    top_sum = sum(sorted_wins[:n], Decimal("0.0"))
    return float(top_sum / total_gain * Decimal("100.0"))


def calculate_top_pct_pnl_contribution(trades: Sequence[ClosedTrade], pct: float) -> float:
    """
    Calcula a contribuição percentual dos Top X% trades vencedores sobre o lucro bruto total.
    """
    if pct <= 0.0:
        return 0.0
    wins = [t.net_pnl for t in trades if t.net_pnl > 0]
    total_wins = len(wins)
    if total_wins == 0:
        return 0.0
    count = max(1, math.ceil(total_wins * (pct / 100.0)))
    return calculate_top_n_pnl_contribution(trades, n=count)


def compute_pnl_summary(trades: Sequence[ClosedTrade], top_n_count: int = 3) -> dict:
    """
    Calcula estatísticas de P&L, ganhos médios, perdas médias, sequências,
    contagens e métricas de concentração dos principais trades.
    """
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "scratch_trades": 0,
            "gross_pnl": Decimal("0.0"),
            "total_commission": Decimal("0.0"),
            "net_pnl": Decimal("0.0"),
            "win_rate": 0.0,
            "avg_win": Decimal("0.0"),
            "avg_loss": Decimal("0.0"),
            "payoff_ratio": 0.0,
            "profit_factor": 0.0,
            "largest_win": Decimal("0.0"),
            "largest_loss": Decimal("0.0"),
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
            "top_1_trade_pnl_contribution_pct": 0.0,
            "top_n_trades_pnl_contribution_pct": 0.0,
            "top_5_trades_pnl_contribution_pct": 0.0,
            "top_10_percent_trades_pnl_contribution_pct": 0.0,
        }

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    scratches = [t for t in trades if t.net_pnl == 0]

    gross_pnl = sum((t.gross_pnl for t in trades), Decimal("0.0"))
    total_commission = sum((t.commission for t in trades), Decimal("0.0"))
    net_pnl = sum((t.net_pnl for t in trades), Decimal("0.0"))

    win_count = len(wins)
    loss_count = len(losses)
    scratch_count = len(scratches)
    win_rate = win_count / total_trades if total_trades > 0 else 0.0

    total_gross_gain = sum((t.net_pnl for t in wins), Decimal("0.0"))
    total_gross_loss = abs(sum((t.net_pnl for t in losses), Decimal("0.0")))

    avg_win = (total_gross_gain / Decimal(win_count)) if win_count > 0 else Decimal("0.0")
    avg_loss = (total_gross_loss / Decimal(loss_count)) if loss_count > 0 else Decimal("0.0")

    payoff_ratio = float(avg_win / avg_loss) if avg_loss > 0 else (float("inf") if avg_win > 0 else 0.0)
    profit_factor = float(total_gross_gain / total_gross_loss) if total_gross_loss > 0 else (float("inf") if total_gross_gain > 0 else 0.0)

    largest_win = max((t.net_pnl for t in wins), default=Decimal("0.0"))
    largest_loss = abs(min((t.net_pnl for t in losses), default=Decimal("0.0")))

    # Métricas de concentração
    top_1_contribution = calculate_top_n_pnl_contribution(trades, n=1)
    top_n_contribution = calculate_top_n_pnl_contribution(trades, n=top_n_count)
    top_5_contribution = calculate_top_n_pnl_contribution(trades, n=5)
    top_10_pct_contribution = calculate_top_pct_pnl_contribution(trades, pct=10.0)

    # Sequências consecutivas
    max_cons_wins = 0
    cur_cons_wins = 0
    max_cons_losses = 0
    cur_cons_losses = 0

    for t in trades:
        if t.net_pnl > 0:
            cur_cons_wins += 1
            cur_cons_losses = 0
        elif t.net_pnl < 0:
            cur_cons_losses += 1
            cur_cons_wins = 0
        else:
            # Neutro não quebra nem incrementa diretamente sequências estritas
            pass
        
        max_cons_wins = max(max_cons_wins, cur_cons_wins)
        max_cons_losses = max(max_cons_losses, cur_cons_losses)

    return {
        "total_trades": total_trades,
        "winning_trades": win_count,
        "losing_trades": loss_count,
        "scratch_trades": scratch_count,
        "gross_pnl": gross_pnl,
        "total_commission": total_commission,
        "net_pnl": net_pnl,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "profit_factor": profit_factor,
        "largest_win": largest_win,
        "largest_loss": largest_loss,
        "max_consecutive_wins": max_cons_wins,
        "max_consecutive_losses": max_cons_losses,
        "top_1_trade_pnl_contribution_pct": top_1_contribution,
        "top_n_trades_pnl_contribution_pct": top_n_contribution,
        "top_5_trades_pnl_contribution_pct": top_5_contribution,
        "top_10_percent_trades_pnl_contribution_pct": top_10_pct_contribution,
    }
