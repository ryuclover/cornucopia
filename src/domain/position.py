from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from src.domain.enums import OrderSide, PositionSide
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trade import ClosedTrade


@dataclass
class _Lot:
    """Lote interno utilizado para o algoritmo de correspondência FIFO de execuções."""
    execution_id: str
    quantity: Decimal
    price: Decimal
    timestamp: datetime
    commission_per_unit: Decimal
    max_adverse_price: Decimal = field(default=Decimal("0.0"))
    max_favorable_price: Decimal = field(default=Decimal("0.0"))

    def __post_init__(self):
        if self.max_adverse_price == Decimal("0.0"):
            self.max_adverse_price = self.price
        if self.max_favorable_price == Decimal("0.0"):
            self.max_favorable_price = self.price


@dataclass
class EquitySnapshot:
    """Snapshot temporal de patrimônio mark-to-market (realizado + não realizado) e exposição."""
    timestamp: datetime
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_equity: Decimal
    position_quantity: Decimal
    position_side: PositionSide


class Position:
    """
    Representa o estado consolidado da posição de um trader em um instrumento.
    """
    def __init__(self, trader_id: str, instrument: MarketInstrument):
        self.trader_id = trader_id
        self.instrument = instrument
        self.open_lots: deque[_Lot] = deque()
        self.realized_pnl: Decimal = Decimal("0.0")
        self.total_commission_paid: Decimal = Decimal("0.0")
        self._current_side: PositionSide = PositionSide.FLAT

    @property
    def side(self) -> PositionSide:
        """Lado atual da posição consolidada."""
        if not self.open_lots or self.quantity == 0:
            return PositionSide.FLAT
        return self._current_side

    @property
    def quantity(self) -> Decimal:
        """Quantidade total em aberto."""
        return sum((lot.quantity for lot in self.open_lots), Decimal("0.0"))

    @property
    def average_entry_price(self) -> Decimal:
        """Preço médio ponderado de entrada da quantidade atualmente em aberto."""
        qty = self.quantity
        if qty == 0:
            return Decimal("0.0")
        total_cost = sum((lot.quantity * lot.price for lot in self.open_lots), Decimal("0.0"))
        return total_cost / qty

    def calculate_unrealized_pnl(self, current_market_price: Decimal) -> Decimal:
        """Calcula o P&L não realizado da posição aberta com base no preço de mercado atual."""
        if self.side == PositionSide.FLAT or self.quantity == 0:
            return Decimal("0.0")
        is_long = (self.side == PositionSide.LONG)
        return self.instrument.calculate_pnl(
            quantity=self.quantity,
            entry_price=self.average_entry_price,
            exit_price=current_market_price,
            is_long=is_long
        )


class PositionTracker:
    """
    Motor de processamento de execuções cronológicas e reconciliação de posições.
    
    Converte um fluxo de execuções (fills) em estado de posições e operações fechadas (ClosedTrades),
    suportando:
    - Entradas fracionadas / scale-in
    - Saídas parciais / scale-out
    - Reversões diretas de posição (Long -> Short ou Short -> Long)
    - Contabilidade FIFO rigorosa com preservação de IDs para auditoria
    - Rastreamento de excursão intratrade (MAE/MFE) e reconstrução da curva de patrimônio MTM.
    """
    def __init__(self, instrument: MarketInstrument, trader_id: str, initial_capital: Decimal = Decimal("10000.00")):
        self.instrument = instrument
        self.trader_id = trader_id
        self.initial_capital = initial_capital
        self.position = Position(trader_id=trader_id, instrument=instrument)
        self.closed_trades: list[ClosedTrade] = []
        self.equity_history: list[EquitySnapshot] = []
        self._last_processed_timestamp: Optional[datetime] = None

    def mark_market_price(self, current_price: Decimal, timestamp: datetime) -> EquitySnapshot:
        """
        Atualiza os extremos intratrade (MAE/MFE) para as posições abertas e registra um snapshot mark-to-market.
        """
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)

        if self.position.side == PositionSide.LONG:
            for lot in self.position.open_lots:
                if current_price < lot.max_adverse_price:
                    lot.max_adverse_price = current_price
                if current_price > lot.max_favorable_price:
                    lot.max_favorable_price = current_price
        elif self.position.side == PositionSide.SHORT:
            for lot in self.position.open_lots:
                if current_price > lot.max_adverse_price:
                    lot.max_adverse_price = current_price
                if current_price < lot.max_favorable_price:
                    lot.max_favorable_price = current_price

        unrealized = self.position.calculate_unrealized_pnl(current_price)
        total_eq = self.initial_capital + self.position.realized_pnl + unrealized
        snap = EquitySnapshot(
            timestamp=timestamp,
            realized_pnl=self.position.realized_pnl,
            unrealized_pnl=unrealized,
            total_equity=total_eq,
            position_quantity=self.position.quantity,
            position_side=self.position.side
        )
        self.equity_history.append(snap)
        return snap

    def get_equity_snapshots_as_of(self, as_of: datetime) -> list[EquitySnapshot]:
        """Retorna os snapshots de patrimônio mark-to-market registrados estritamente até a data as_of."""
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        else:
            as_of = as_of.astimezone(timezone.utc)
        return [s for s in self.equity_history if s.timestamp <= as_of]

    def calculate_drawdown_as_of(self, as_of: datetime) -> dict:
        """
        Calcula o patrimônio, unrealized P&L e drawdown histórico ponto-no-tempo até as_of.
        """
        snaps = self.get_equity_snapshots_as_of(as_of)
        if not snaps:
            return {
                "max_drawdown_amount": Decimal("0.0"),
                "max_drawdown_pct": 0.0,
                "current_equity": self.initial_capital,
                "current_unrealized_pnl": Decimal("0.0"),
                "current_realized_pnl": Decimal("0.0"),
            }
        hwm = self.initial_capital
        max_dd_amount = Decimal("0.0")
        max_dd_pct = 0.0
        for s in snaps:
            if s.total_equity > hwm:
                hwm = s.total_equity
            dd_amt = hwm - s.total_equity
            dd_pct = float(dd_amt / hwm * Decimal("100.0")) if hwm > 0 else 0.0
            if dd_amt > max_dd_amount:
                max_dd_amount = dd_amt
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
        latest = snaps[-1]
        return {
            "max_drawdown_amount": max_dd_amount,
            "max_drawdown_pct": max_dd_pct,
            "current_equity": latest.total_equity,
            "current_unrealized_pnl": latest.unrealized_pnl,
            "current_realized_pnl": latest.realized_pnl,
        }

    def process_execution(self, execution: Execution) -> list[ClosedTrade]:
        """
        Processa uma nova execução em ordem cronológica estrita.
        Retorna a lista de trades fechados (se a execução fechou parcial ou totalmente uma posição).
        """
        if execution.trader_id != self.trader_id or execution.symbol != self.instrument.symbol:
            raise ValueError(f"Execução incompatível com o tracker ({execution.trader_id}, {execution.symbol})")

        # Validação estrita de ordenação temporal (prevenção de look-ahead / inconsistência)
        if self._last_processed_timestamp and execution.timestamp < self._last_processed_timestamp:
            raise ValueError(
                f"Execução fora de ordem temporal: recebido {execution.timestamp} após {self._last_processed_timestamp}"
            )
        self._last_processed_timestamp = execution.timestamp

        # Atualiza os limites de preço dos lotes existentes com o preço desta execução antes de encerrar
        self.mark_market_price(execution.price, execution.timestamp)

        new_closed_trades: list[ClosedTrade] = []
        comm_per_unit = execution.commission / execution.quantity if execution.quantity > 0 else Decimal("0.0")

        # 1. Posição atual FLAT -> Abre nova posição
        if self.position.side == PositionSide.FLAT:
            side = PositionSide.LONG if execution.side == OrderSide.BUY else PositionSide.SHORT
            self.position._current_side = side
            self.position.open_lots.append(
                _Lot(
                    execution_id=execution.execution_id,
                    quantity=execution.quantity,
                    price=execution.price,
                    timestamp=execution.timestamp,
                    commission_per_unit=comm_per_unit,
                    max_adverse_price=execution.price,
                    max_favorable_price=execution.price
                )
            )
            self.position.total_commission_paid += execution.commission
            return new_closed_trades

        # 2. Execução no mesmo sentido da posição atual -> Aumenta posição (scale-in)
        is_same_side = (
            (self.position.side == PositionSide.LONG and execution.side == OrderSide.BUY) or
            (self.position.side == PositionSide.SHORT and execution.side == OrderSide.SELL)
        )
        if is_same_side:
            self.position.open_lots.append(
                _Lot(
                    execution_id=execution.execution_id,
                    quantity=execution.quantity,
                    price=execution.price,
                    timestamp=execution.timestamp,
                    commission_per_unit=comm_per_unit,
                    max_adverse_price=execution.price,
                    max_favorable_price=execution.price
                )
            )
            self.position.total_commission_paid += execution.commission
            return new_closed_trades

        # 3. Execução no sentido oposto -> Redução, Encerramento ou Reversão de posição
        qty_to_close = execution.quantity
        is_closing_long = (self.position.side == PositionSide.LONG)

        while qty_to_close > 0 and self.position.open_lots:
            first_lot = self.position.open_lots[0]
            matched_qty = min(qty_to_close, first_lot.quantity)

            entry_comm = first_lot.commission_per_unit * matched_qty
            exit_comm = comm_per_unit * matched_qty
            total_comm = entry_comm + exit_comm

            gross_pnl = self.instrument.calculate_pnl(
                quantity=matched_qty,
                entry_price=first_lot.price,
                exit_price=execution.price,
                is_long=is_closing_long
            )
            net_pnl = gross_pnl - total_comm

            allocated_capital = first_lot.price * matched_qty * self.instrument.contract_multiplier
            return_pct = (net_pnl / allocated_capital) if allocated_capital > 0 else Decimal("0.0")

            # Cálculo de MAE e MFE financeiros da fração fechada
            mae_pnl = self.instrument.calculate_pnl(
                quantity=matched_qty,
                entry_price=first_lot.price,
                exit_price=first_lot.max_adverse_price,
                is_long=is_closing_long
            )
            mfe_pnl = self.instrument.calculate_pnl(
                quantity=matched_qty,
                entry_price=first_lot.price,
                exit_price=first_lot.max_favorable_price,
                is_long=is_closing_long
            )

            closed_trade = ClosedTrade(
                trade_id=str(uuid4()),
                trader_id=self.trader_id,
                symbol=self.instrument.symbol,
                side=PositionSide.LONG if is_closing_long else PositionSide.SHORT,
                quantity=matched_qty,
                entry_price=first_lot.price,
                exit_price=execution.price,
                entry_time=first_lot.timestamp,
                exit_time=execution.timestamp,
                gross_pnl=gross_pnl,
                commission=total_comm,
                net_pnl=net_pnl,
                return_pct=return_pct,
                entry_execution_ids=[first_lot.execution_id],
                exit_execution_ids=[execution.execution_id],
                max_adverse_excursion=mae_pnl,
                max_favorable_excursion=mfe_pnl
            )

            new_closed_trades.append(closed_trade)
            self.closed_trades.append(closed_trade)
            self.position.realized_pnl += net_pnl
            self.position.total_commission_paid += exit_comm

            first_lot.quantity -= matched_qty
            qty_to_close -= matched_qty

            if first_lot.quantity == 0:
                self.position.open_lots.popleft()

        # Se todos os lotes foram encerrados
        if not self.position.open_lots:
            self.position._current_side = PositionSide.FLAT

        # 4. Caso de Reversão de posição (se sobrou quantidade na ordem de sentido oposto)
        if qty_to_close > 0:
            new_side = PositionSide.SHORT if is_closing_long else PositionSide.LONG
            self.position._current_side = new_side
            rem_comm = comm_per_unit * qty_to_close
            self.position.open_lots.append(
                _Lot(
                    execution_id=execution.execution_id,
                    quantity=qty_to_close,
                    price=execution.price,
                    timestamp=execution.timestamp,
                    commission_per_unit=comm_per_unit,
                    max_adverse_price=execution.price,
                    max_favorable_price=execution.price
                )
            )
            self.position.total_commission_paid += rem_comm

        return new_closed_trades
