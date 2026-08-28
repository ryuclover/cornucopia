import csv
import io
import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from src.domain.enums import AssetClass, OrderSide, TraderStatus
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.storage.repositories.base import MarketPriceRecord


class SyntheticDataGenerator:
    """
    Gerador determinístico de dados sintéticos para testes e desenvolvimento.
    
    Utiliza semente pseudo-aleatória configurável para garantir total reprodutibilidade.
    """
    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)

    def generate_instruments(self) -> list[MarketInstrument]:
        """Gera instrumentos padrão de teste."""
        return [
            MarketInstrument(
                symbol="PETR4",
                asset_class=AssetClass.EQUITY,
                tick_size=Decimal("0.01"),
                tick_value=Decimal("0.01"),
                contract_multiplier=Decimal("1.0"),
                currency="BRL",
                description="Petrobras PN"
            ),
            MarketInstrument(
                symbol="VALE3",
                asset_class=AssetClass.EQUITY,
                tick_size=Decimal("0.01"),
                tick_value=Decimal("0.01"),
                contract_multiplier=Decimal("1.0"),
                currency="BRL",
                description="Vale ON"
            ),
            MarketInstrument(
                symbol="WIN$",
                asset_class=AssetClass.FUTURES,
                tick_size=Decimal("5.0"),
                tick_value=Decimal("1.0"),
                contract_multiplier=Decimal("0.20"),
                currency="BRL",
                description="Mini Índice Bovespa"
            ),
        ]

    def generate_traders(self, count: int = 3) -> list[Trader]:
        """Gera lista de traders com nomes e capitais configurados."""
        names = ["Alpha_Consistent", "Beta_Aggressive", "Gamma_TrendFollower", "Delta_Scalper", "Epsilon_Neutral"]
        traders = []
        base_time = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        for i in range(min(count, len(names))):
            traders.append(
                Trader(
                    trader_id=f"T_{i+1:03d}",
                    name=names[i],
                    created_at=base_time,
                    status=TraderStatus.ACTIVE,
                    initial_capital=Decimal("10000.00"),
                    metadata={"profile": names[i].split("_")[1].lower(), "seed": str(self.seed)}
                )
            )
        return traders

    def generate_executions_for_trader(
        self,
        trader_id: str,
        symbol: str = "PETR4",
        trade_count: int = 50,
        start_date: Optional[datetime] = None,
        base_price: float = 30.0,
        win_rate: float = 0.55,
        avg_gain_pct: float = 0.03,
        avg_loss_pct: float = 0.015,
    ) -> list[Execution]:
        """
        Gera uma sequência de execuções (entradas e saídas) para um trader específico.
        """
        current_time = start_date or datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        executions: list[Execution] = []
        exec_idx = 1
        current_price = base_price

        for _ in range(trade_count):
            # 1. Entrada (BUY)
            entry_price = round(current_price * (1 + self.rng.uniform(-0.01, 0.01)), 2)
            entry_time = current_time
            qty = Decimal(str(self.rng.choice([100, 200, 300])))

            executions.append(
                Execution(
                    execution_id=f"exec_{trader_id}_{exec_idx:05d}",
                    trader_id=trader_id,
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=qty,
                    price=Decimal(f"{entry_price:.2f}"),
                    timestamp=entry_time,
                    commission=Decimal("1.50")
                )
            )
            exec_idx += 1

            # 2. Saída (SELL)
            is_win = (self.rng.random() < win_rate)
            if is_win:
                exit_price = round(entry_price * (1 + self.rng.uniform(0.005, avg_gain_pct * 1.5)), 2)
            else:
                exit_price = round(entry_price * (1 - self.rng.uniform(0.005, avg_loss_pct * 1.5)), 2)

            duration_hours = self.rng.randint(1, 8)
            exit_time = entry_time + timedelta(hours=duration_hours)

            executions.append(
                Execution(
                    execution_id=f"exec_{trader_id}_{exec_idx:05d}",
                    trader_id=trader_id,
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=qty,
                    price=Decimal(f"{exit_price:.2f}"),
                    timestamp=exit_time,
                    commission=Decimal("1.50")
                )
            )
            exec_idx += 1

            # Avança o tempo para o próximo trade
            days_gap = self.rng.randint(1, 3)
            current_time = exit_time + timedelta(days=days_gap)
            current_price = exit_price

        return executions

    def generate_profile_steady_survivor(
        self,
        trader_id: str,
        symbol: str = "PETR4",
        start_date: Optional[datetime] = None,
        trade_count: int = 50
    ) -> list[Execution]:
        """Perfil STEADY_SURVIVOR: Consistência, retorno moderado, baixo drawdown e alta disciplina."""
        return self.generate_executions_for_trader(
            trader_id=trader_id,
            symbol=symbol,
            trade_count=trade_count,
            start_date=start_date,
            win_rate=0.62,
            avg_gain_pct=0.025,
            avg_loss_pct=0.012
        )

    def generate_profile_high_return_gambler(
        self,
        trader_id: str,
        symbol: str = "PETR4",
        start_date: Optional[datetime] = None,
        trade_count: int = 40
    ) -> list[Execution]:
        """Perfil HIGH_RETURN_GAMBLER: Alto retorno bruto, porém com perdas catastróficas e drawdowns > 30%."""
        current_time = start_date or datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        executions: list[Execution] = []
        exec_idx = 1
        current_price = 30.0

        for i in range(trade_count):
            entry_price = round(current_price * (1 + self.rng.uniform(-0.01, 0.01)), 2)
            entry_time = current_time
            qty = Decimal("500")

            executions.append(
                Execution(
                    execution_id=f"exec_{trader_id}_{exec_idx:05d}",
                    trader_id=trader_id,
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=qty,
                    price=Decimal(f"{entry_price:.2f}"),
                    timestamp=entry_time,
                    commission=Decimal("2.00")
                )
            )
            exec_idx += 1

            # Em alguns trades sofre perdas catastróficas (> 8% por trade) gerando desqualificação
            if i in (10, 25):
                exit_price = round(entry_price * 0.88, 2) # -12%
            elif self.rng.random() < 0.50:
                exit_price = round(entry_price * 1.08, 2) # +8%
            else:
                exit_price = round(entry_price * 0.94, 2) # -6%

            exit_time = entry_time + timedelta(hours=4)
            executions.append(
                Execution(
                    execution_id=f"exec_{trader_id}_{exec_idx:05d}",
                    trader_id=trader_id,
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=qty,
                    price=Decimal(f"{exit_price:.2f}"),
                    timestamp=exit_time,
                    commission=Decimal("2.00")
                )
            )
            exec_idx += 1
            current_time = exit_time + timedelta(days=2)
            current_price = exit_price

        return executions

    def generate_profile_deteriorating(
        self,
        trader_id: str,
        symbol: str = "PETR4",
        start_date: Optional[datetime] = None,
        total_trades: int = 60
    ) -> list[Execution]:
        """Perfil DETERIORATING: Começa muito consistente e depois degrada severamente."""
        split_idx = total_trades // 2
        start_dt = start_date or datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        
        # 1ª metade: consistente
        first_half = self.generate_executions_for_trader(
            trader_id=trader_id,
            symbol=symbol,
            trade_count=split_idx,
            start_date=start_dt,
            win_rate=0.70,
            avg_gain_pct=0.03,
            avg_loss_pct=0.01
        )
        
        # Continua a partir da última data
        last_time = first_half[-1].timestamp + timedelta(days=2)
        
        # 2ª metade: péssima (perdas consecutivas e alta taxa de erro)
        second_half = self.generate_executions_for_trader(
            trader_id=trader_id,
            symbol=symbol,
            trade_count=total_trades - split_idx,
            start_date=last_time,
            win_rate=0.15,
            avg_gain_pct=0.01,
            avg_loss_pct=0.04
        )
        
        # Ajusta IDs
        for idx, ex in enumerate(second_half, start=len(first_half) + 1):
            ex_obj = Execution(
                execution_id=f"exec_{trader_id}_{idx:05d}",
                trader_id=ex.trader_id,
                symbol=ex.symbol,
                side=ex.side,
                quantity=ex.quantity,
                price=ex.price,
                timestamp=ex.timestamp,
                commission=ex.commission
            )
            second_half[idx - len(first_half) - 1] = ex_obj

        return first_half + second_half

    def generate_profile_recovering(
        self,
        trader_id: str,
        symbol: str = "PETR4",
        start_date: Optional[datetime] = None,
        total_trades: int = 60
    ) -> list[Execution]:
        """Perfil RECOVERING: Passa por um período de drawdown intermediário e depois se recupera."""
        part = total_trades // 3
        start_dt = start_date or datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)

        # Fase 1: Bom
        p1 = self.generate_executions_for_trader(trader_id, symbol, trade_count=part, start_date=start_dt, win_rate=0.65, avg_gain_pct=0.025, avg_loss_pct=0.012)
        # Fase 2: Drawdown moderado
        t2 = p1[-1].timestamp + timedelta(days=2)
        p2 = self.generate_executions_for_trader(trader_id, symbol, trade_count=part, start_date=t2, win_rate=0.25, avg_gain_pct=0.01, avg_loss_pct=0.02)
        # Fase 3: Recuperação sólida
        t3 = p2[-1].timestamp + timedelta(days=2)
        p3 = self.generate_executions_for_trader(trader_id, symbol, trade_count=total_trades - 2 * part, start_date=t3, win_rate=0.75, avg_gain_pct=0.03, avg_loss_pct=0.01)

        combined = p1 + p2 + p3
        reindexed = []
        for idx, ex in enumerate(combined, start=1):
            reindexed.append(
                Execution(
                    execution_id=f"exec_{trader_id}_{idx:05d}",
                    trader_id=ex.trader_id,
                    symbol=ex.symbol,
                    side=ex.side,
                    quantity=ex.quantity,
                    price=ex.price,
                    timestamp=ex.timestamp,
                    commission=ex.commission
                )
            )
        return reindexed

    def generate_profile_lucky_outlier(
        self,
        trader_id: str,
        symbol: str = "PETR4",
        start_date: Optional[datetime] = None,
        trade_count: int = 40
    ) -> list[Execution]:
        """Perfil LUCKY_OUTLIER: Ganhos concentrados em 1 ou 2 operações excepcionais."""
        current_time = start_date or datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        executions: list[Execution] = []
        exec_idx = 1
        current_price = 30.0

        for i in range(trade_count):
            entry_price = current_price
            entry_time = current_time
            qty = Decimal("100")

            executions.append(
                Execution(
                    execution_id=f"exec_{trader_id}_{exec_idx:05d}",
                    trader_id=trader_id,
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=qty,
                    price=Decimal(f"{entry_price:.2f}"),
                    timestamp=entry_time,
                    commission=Decimal("1.50")
                )
            )
            exec_idx += 1

            if i == 5:
                # Trade gigante que responde por mais de 75% dos lucros
                exit_price = round(entry_price * 1.60, 2) # +60%
            else:
                # Trades normais quase neutros (pequenos ganhos e pequenas perdas)
                if self.rng.random() < 0.45:
                    exit_price = round(entry_price * 1.008, 2)
                else:
                    exit_price = round(entry_price * 0.993, 2)

            exit_time = entry_time + timedelta(hours=3)
            executions.append(
                Execution(
                    execution_id=f"exec_{trader_id}_{exec_idx:05d}",
                    trader_id=trader_id,
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=qty,
                    price=Decimal(f"{exit_price:.2f}"),
                    timestamp=exit_time,
                    commission=Decimal("1.50")
                )
            )
            exec_idx += 1
            current_time = exit_time + timedelta(days=2)
            current_price = exit_price

        return executions

    def generate_profile_insufficient_history(
        self,
        trader_id: str,
        symbol: str = "PETR4",
        start_date: Optional[datetime] = None,
        trade_count: int = 4
    ) -> list[Execution]:
        """Perfil INSUFFICIENT_HISTORY: Poucos trades (ex: 4 trades) em poucos dias."""
        return self.generate_executions_for_trader(
            trader_id=trader_id,
            symbol=symbol,
            trade_count=trade_count,
            start_date=start_date or datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
            win_rate=0.75,
            avg_gain_pct=0.02,
            avg_loss_pct=0.01
        )

    def generate_profile_mirror_trader(
        self,
        base_executions: list[Execution],
        new_trader_id: str,
        time_shift_seconds: int = 60
    ) -> list[Execution]:
        """Perfil MIRROR_TRADER: Replica quase perfeitamente as operações do trader base com ligeiro delay."""
        mirror_execs: list[Execution] = []
        for idx, ex in enumerate(base_executions, start=1):
            shifted_time = ex.timestamp + timedelta(seconds=time_shift_seconds)
            # Preço com pequena variação de slippage
            price_delta = Decimal(str(round(self.rng.uniform(-0.02, 0.02), 2)))
            new_price = max(Decimal("0.01"), ex.price + price_delta)
            mirror_execs.append(
                Execution(
                    execution_id=f"exec_{new_trader_id}_{idx:05d}",
                    trader_id=new_trader_id,
                    symbol=ex.symbol,
                    side=ex.side,
                    quantity=ex.quantity,
                    price=new_price,
                    timestamp=shifted_time,
                    commission=ex.commission
                )
            )
        return mirror_execs

    def generate_profile_independent_trader(
        self,
        trader_id: str,
        symbol: str = "WIN$",
        start_date: Optional[datetime] = None,
        trade_count: int = 40
    ) -> list[Execution]:
        """Perfil INDEPENDENT_TRADER: Opera outro ativo (ex: WIN$) em horários descorrelacionados."""
        return self.generate_executions_for_trader(
            trader_id=trader_id,
            symbol=symbol,
            trade_count=trade_count,
            start_date=start_date or datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc),
            base_price=120000.0,
            win_rate=0.58,
            avg_gain_pct=0.015,
            avg_loss_pct=0.012
        )

    def generate_profile_anti_correlated(
        self,
        base_executions: list[Execution],
        new_trader_id: str
    ) -> list[Execution]:
        """Perfil ANTI_CORRELATED_TRADER: Assume direções opostas (SELL onde o base compra, BUY onde vende)."""
        anti_execs: list[Execution] = []
        for idx, ex in enumerate(base_executions, start=1):
            opp_side = OrderSide.SELL if ex.side == OrderSide.BUY else OrderSide.BUY
            anti_execs.append(
                Execution(
                    execution_id=f"exec_{new_trader_id}_{idx:05d}",
                    trader_id=new_trader_id,
                    symbol=ex.symbol,
                    side=opp_side,
                    quantity=ex.quantity,
                    price=ex.price,
                    timestamp=ex.timestamp,
                    commission=ex.commission
                )
            )
        return anti_execs

    def generate_profile_different_timing(
        self,
        base_executions: list[Execution],
        new_trader_id: str,
        time_shift_days: int = 14
    ) -> list[Execution]:
        """Perfil SAME_INSTRUMENT_DIFFERENT_TIMING: Opera o mesmo ativo, mas semanas depois."""
        shifted_execs: list[Execution] = []
        for idx, ex in enumerate(base_executions, start=1):
            shifted_time = ex.timestamp + timedelta(days=time_shift_days)
            shifted_execs.append(
                Execution(
                    execution_id=f"exec_{new_trader_id}_{idx:05d}",
                    trader_id=new_trader_id,
                    symbol=ex.symbol,
                    side=ex.side,
                    quantity=ex.quantity,
                    price=ex.price,
                    timestamp=shifted_time,
                    commission=ex.commission
                )
            )
        return shifted_execs

    def generate_profile_different_instrument(
        self,
        base_executions: list[Execution],
        new_trader_id: str,
        new_symbol: str = "VALE3"
    ) -> list[Execution]:
        """Perfil SAME_DIRECTION_DIFFERENT_INSTRUMENT: Opera na mesma direção e timing, mas em ativo diferente."""
        diff_inst_execs: list[Execution] = []
        for idx, ex in enumerate(base_executions, start=1):
            diff_inst_execs.append(
                Execution(
                    execution_id=f"exec_{new_trader_id}_{idx:05d}",
                    trader_id=new_trader_id,
                    symbol=new_symbol,
                    side=ex.side,
                    quantity=ex.quantity,
                    price=Decimal("65.00") if ex.side == OrderSide.BUY else Decimal("67.00"),
                    timestamp=ex.timestamp,
                    commission=ex.commission
                )
            )
        return diff_inst_execs

    def generate_market_prices(
        self,
        symbol: str = "PETR4",
        start_date: Optional[datetime] = None,
        points: int = 100,
        initial_price: float = 30.0
    ) -> list[MarketPriceRecord]:
        """Gera série temporal de preços de mercado para o instrumento."""
        current_time = start_date or datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        records = []
        price = initial_price

        for i in range(points):
            price = max(1.0, round(price * (1 + self.rng.uniform(-0.015, 0.015)), 2))
            records.append(
                MarketPriceRecord(
                    symbol=symbol,
                    timestamp=current_time,
                    price=Decimal(f"{price:.2f}"),
                    source="synthetic_feed"
                )
            )
            current_time += timedelta(hours=4)

        return records

    def executions_to_csv(self, executions: list[Execution]) -> str:
        """Serializa lista de execuções para o formato canônico CSV."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["execution_id", "trader_id", "symbol", "timestamp", "side", "quantity", "price", "commission"])
        for e in executions:
            writer.writerow([
                e.execution_id,
                e.trader_id,
                e.symbol,
                e.timestamp.astimezone(timezone.utc).isoformat(),
                e.side.value,
                str(e.quantity),
                str(e.price),
                str(e.commission)
            ])
        return output.getvalue()

    def executions_to_json(self, executions: list[Execution]) -> str:
        """Serializa lista de execuções para o formato canônico JSON."""
        data = [
            {
                "execution_id": e.execution_id,
                "trader_id": e.trader_id,
                "symbol": e.symbol,
                "timestamp": e.timestamp.astimezone(timezone.utc).isoformat(),
                "side": e.side.value,
                "quantity": float(e.quantity),
                "price": float(e.price),
                "commission": float(e.commission),
                "slippage": float(e.slippage),
                "order_id": e.order_id,
                "notes": e.notes
            }
            for e in executions
        ]
        return json.dumps(data, indent=2)

