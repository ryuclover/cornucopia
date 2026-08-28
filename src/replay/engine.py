from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from src.domain.instrument import MarketInstrument
from src.domain.position import Position, PositionTracker
from src.metrics.calculator import PerformanceCalculator
from src.replay.models import TraderReplayResult
from src.scoring.survivor_v1 import SurvivorScoreV1
from src.storage.repositories.base import (
    ExecutionRepository,
    InstrumentRepository,
    MarketPriceRepository,
    TraderRepository,
)


class TraderReplayEngine:
    """
    Motor de Replay e Reconstrução Histórica Ponto-no-Tempo de Traders.
    
    Orquestra as execuções persistidas, aplica contabilidade FIFO através do PositionTracker,
    incorpora cotações históricas de mercado para marcação a mercado e gera snapshots auditáveis.
    """
    def __init__(
        self,
        trader_repo: TraderRepository,
        instrument_repo: InstrumentRepository,
        execution_repo: ExecutionRepository,
        market_price_repo: Optional[MarketPriceRepository] = None,
        scorer: Optional[SurvivorScoreV1] = None,
    ):
        self.trader_repo = trader_repo
        self.instrument_repo = instrument_repo
        self.execution_repo = execution_repo
        self.market_price_repo = market_price_repo
        self.scorer = scorer or SurvivorScoreV1()

    def replay_trader(
        self,
        trader_id: str,
        as_of: datetime,
        compute_score: bool = True
    ) -> TraderReplayResult:
        """
        Reconstrói o estado do trader estritamente até 'as_of'.
        """
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        else:
            as_of = as_of.astimezone(timezone.utc)

        trader = self.trader_repo.get_by_id(trader_id)
        if trader is None:
            raise ValueError(f"Trader '{trader_id}' não encontrado no repositório.")

        # 1. Busca execuções no banco com filtro rígido (timestamp <= as_of) e ordenação determinística
        executions = self.execution_repo.find_by_trader_until_as_of(trader_id, as_of)

        # 2. Identifica os instrumentos negociados e inicializa trackers
        symbols_traded = {e.symbol for e in executions}
        trackers: dict[str, PositionTracker] = {}

        for sym in symbols_traded:
            inst = self.instrument_repo.get_by_symbol(sym)
            if inst is None:
                raise ValueError(f"Instrumento '{sym}' não cadastrado no repositório.")
            trackers[sym] = PositionTracker(
                instrument=inst,
                trader_id=trader_id,
                initial_capital=trader.initial_capital
            )

        # 3. Processa cada execução no tracker correspondente
        for ex in executions:
            tracker = trackers[ex.symbol]
            
            # Se houver histórico de cotações entre a última execução e a atual, pode atualizar mark prices
            tracker.process_execution(ex)

        # 4. Aplica marcação a mercado no instante 'as_of' para posições em aberto
        running_unrealized_pnl = Decimal("0.0")
        has_open_position = False
        missing_market_price = False

        positions_summary: dict[str, Position] = {}
        all_closed_trades = []
        total_commission = Decimal("0.0")
        all_snapshots = []

        for sym, tracker in trackers.items():
            positions_summary[sym] = tracker.position
            all_closed_trades.extend(tracker.closed_trades)
            total_commission += tracker.position.total_commission_paid
            all_snapshots.extend(tracker.equity_history)

            # Se a posição estiver aberta, busca o último preço em ou antes de as_of
            if tracker.position.quantity > 0:
                has_open_position = True
                latest_px = self.market_price_repo.get_latest_price_until_as_of(sym, as_of) if self.market_price_repo else None
                if latest_px is not None:
                    unrealized = tracker.position.calculate_unrealized_pnl(latest_px)
                    running_unrealized_pnl += unrealized
                else:
                    # Falta cotação de mercado para marcação a mercado confiável
                    missing_market_price = True

        # Ordena todos os closed_trades cronologicamente
        all_closed_trades.sort(key=lambda t: t.exit_time)

        total_realized_pnl = sum((t.net_pnl for t in all_closed_trades), Decimal("0.0"))
        realized_equity = trader.initial_capital + total_realized_pnl

        if missing_market_price:
            total_unrealized_pnl = None
            total_equity = None
            valuation_status = "MISSING_MARKET_PRICE"
        else:
            total_unrealized_pnl = running_unrealized_pnl
            total_equity = realized_equity + total_unrealized_pnl
            valuation_status = "CONFIRMED"

        # 5. Calcula métricas de performance ponto-no-tempo
        perf = PerformanceCalculator.calculate(
            trader_id=trader_id,
            trades=all_closed_trades,
            as_of=as_of,
            initial_capital=trader.initial_capital,
            first_history_date=trader.created_at
        )

        # 6. Calcula score se solicitado
        score = self.scorer.evaluate(perf) if compute_score else None

        return TraderReplayResult(
            trader_id=trader_id,
            as_of=as_of,
            initial_capital=trader.initial_capital,
            positions=positions_summary,
            closed_trades=all_closed_trades,
            total_realized_pnl=total_realized_pnl,
            realized_equity=realized_equity,
            total_unrealized_pnl=total_unrealized_pnl,
            total_commission=total_commission,
            total_equity=total_equity,
            valuation_status=valuation_status,
            equity_snapshots=all_snapshots,
            performance=perf,
            score=score
        )
