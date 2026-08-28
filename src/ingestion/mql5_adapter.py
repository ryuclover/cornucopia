import csv
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Optional, Sequence
from src.domain.enums import AssetClass, OrderSide, TraderStatus
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.storage.repositories.base import MarketPriceRecord


class MQL5SignalsIngestionAdapter:
    """
    Adaptador de Ingestão e Normalização para Históricos de Sinais MQL5 e Declarações MetaTrader (MT4/MT5).
    
    Transforma tickets de negociação (Deal/Order tickets com timestamps de abertura e fechamento)
    em pares atômicos de 'Execution' compatíveis com o domínio estrito do Cornucopia,
    preservando metadados de proveniência completos e auditáveis.
    
    Suporta:
    - Declarações brutas CSV nativas do MetaTrader 4 (.history.csv, Detailed Statement)
    - Declarações brutas CSV nativas do MetaTrader 5 (.positions.csv, History Deals)
    - Arquivos estruturados JSON de Signals
    """
    def __init__(self, default_currency: str = "USD"):
        self.default_currency = default_currency

    @staticmethod
    def _parse_flexible_datetime(dt_str: str) -> datetime:
        """
        Interpreta timestamps nos formatos padrão MetaTrader (YYYY.MM.DD HH:MM:SS, YYYY-MM-DD HH:MM:SS, ISO).
        """
        cleaned = dt_str.strip().replace("Z", "+00:00")
        for fmt in (
            "%Y.%m.%d %H:%M:%S",
            "%Y.%m.%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%d.%m.%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S"
        ):
            try:
                dt = datetime.strptime(cleaned, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        try:
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            raise ValueError(f"Não foi possível interpretar o timestamp MetaTrader: '{dt_str}'")

    @staticmethod
    def _map_instrument(symbol: str) -> MarketInstrument:
        sym = symbol.strip().upper()
        if sym.startswith("PETR") or sym.startswith("VALE") or sym.startswith("ITUB") or sym.startswith("BBDC"):
            asset_cls = AssetClass.EQUITY
            curr = "BRL"
            tick_s = Decimal("0.01")
            mult = Decimal("1.0")
        elif sym.startswith("WIN") or sym.startswith("WDO"):
            asset_cls = AssetClass.FUTURES
            curr = "BRL"
            tick_s = Decimal("5.0") if sym.startswith("WIN") else Decimal("0.5")
            mult = Decimal("0.20") if sym.startswith("WIN") else Decimal("10.0")
        else:
            asset_cls = AssetClass.FOREX
            curr = "USD"
            tick_s = Decimal("0.00001") if "JPY" not in sym else Decimal("0.001")
            mult = Decimal("1.0")

        return MarketInstrument(
            symbol=sym,
            asset_class=asset_cls,
            tick_size=tick_s,
            tick_value=tick_s,
            contract_multiplier=mult,
            currency=curr
        )

    def parse_csv_statement(
        self,
        file_path: Path | str,
        signal_id: str,
        trader_name: Optional[str] = None,
        initial_deposit: Decimal = Decimal("10000.00")
    ) -> tuple[Trader, list[Execution], list[MarketInstrument], dict[str, Any]]:
        """
        Lê arquivo CSV nativo exportado pelo MetaTrader (MT4 .history.csv ou MT5 .positions.csv).
        """
        path = Path(file_path)
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = [line.strip() for line in f if line.strip()]

        if not lines:
            raise ValueError(f"Arquivo CSV vazio: {file_path}")

        # Detecta delimitador (, ou ; ou \t)
        header_line = lines[0]
        delimiter = ","
        if "\t" in header_line:
            delimiter = "\t"
        elif ";" in header_line:
            delimiter = ";"

        reader = csv.reader(lines, delimiter=delimiter)
        raw_header = [col.strip().lower() for col in next(reader)]

        # Mapeamento de colunas flexível para MT4 e MT5
        col_map = {}
        price_cols = []
        time_cols = []

        for idx, col in enumerate(raw_header):
            if "ticket" in col or "position" in col or "deal" in col or "order" in col:
                if "ticket" not in col_map:
                    col_map["ticket"] = idx
            elif "symbol" in col or "item" in col:
                col_map["symbol"] = idx
            elif "type" in col or "direction" in col:
                col_map["type"] = idx
            elif "size" in col or "volume" in col or "lots" in col or "quantity" in col:
                col_map["size"] = idx
            elif "price" in col:
                price_cols.append(idx)
            elif "time" in col or "date" in col:
                time_cols.append(idx)
            elif "profit" in col:
                col_map["profit"] = idx
            elif "commission" in col:
                col_map["commission"] = idx
            elif "swap" in col:
                col_map["swap"] = idx

        # Se houver 2 colunas de tempo (Open Time e Close Time)
        if len(time_cols) >= 2:
            col_map["open_time"] = time_cols[0]
            col_map["close_time"] = time_cols[1]
        elif len(time_cols) == 1:
            col_map["open_time"] = time_cols[0]
            col_map["close_time"] = time_cols[0]

        # Se houver 2 colunas de preço (Open Price e Close Price)
        if len(price_cols) >= 2:
            col_map["open_price"] = price_cols[0]
            col_map["close_price"] = price_cols[1]
        elif len(price_cols) == 1:
            col_map["open_price"] = price_cols[0]
            col_map["close_price"] = price_cols[0]

        executions: list[Execution] = []
        instruments_map: dict[str, MarketInstrument] = {}
        reported_profit_sum = Decimal("0.0")
        trades_count = 0
        earliest_time = None

        trader_id = f"MQL5_{signal_id}"
        t_name = trader_name or f"MQL5 Signal {signal_id}"

        for row_idx, row in enumerate(reader, start=2):
            if not row or len(row) < 5:
                continue

            # Ignora linhas de cabeçalho intermediário ou sumários de balanço
            first_val = row[0].strip().lower()
            if "summary" in first_val or "total" in first_val or "balance" in first_val or "credit" in first_val:
                continue

            try:
                ticket = row[col_map.get("ticket", 0)].strip()
                sym = row[col_map["symbol"]].strip().upper()
                trade_type = row[col_map["type"]].strip().lower()

                if "buy" in trade_type:
                    open_side = OrderSide.BUY
                    close_side = OrderSide.SELL
                elif "sell" in trade_type:
                    open_side = OrderSide.SELL
                    close_side = OrderSide.BUY
                else:
                    # Ignora depósitos, saques ou balanços
                    continue

                size_str = row[col_map["size"]].replace(",", ".")
                open_p_str = row[col_map["open_price"]].replace(",", ".")
                close_p_str = row[col_map["close_price"]].replace(",", ".")

                vol = Decimal(size_str)
                open_p = Decimal(open_p_str)
                close_p = Decimal(close_p_str)

                open_t = self._parse_flexible_datetime(row[col_map["open_time"]])
                close_t = self._parse_flexible_datetime(row[col_map["close_time"]])

                if earliest_time is None or open_t < earliest_time:
                    earliest_time = open_t

                comm_str = row[col_map["commission"]].replace(",", ".") if "commission" in col_map and col_map["commission"] < len(row) else "0.0"
                swap_str = row[col_map["swap"]].replace(",", ".") if "swap" in col_map and col_map["swap"] < len(row) else "0.0"
                profit_str = row[col_map["profit"]].replace(",", ".") if "profit" in col_map and col_map["profit"] < len(row) else "0.0"

                tot_comm = abs(Decimal(comm_str))
                half_comm = tot_comm / Decimal("2.0") if tot_comm > 0 else Decimal("0.0")
                swap_val = Decimal(swap_str)
                reported_profit = Decimal(profit_str)
                reported_profit_sum += reported_profit

                if sym not in instruments_map:
                    instruments_map[sym] = self._map_instrument(sym)

                # 1. Open Execution
                open_exec = Execution(
                    execution_id=f"MQL5_{signal_id}_{ticket}_OPEN",
                    trader_id=trader_id,
                    symbol=sym,
                    side=open_side,
                    quantity=vol,
                    price=open_p,
                    timestamp=open_t,
                    commission=half_comm,
                    slippage=Decimal("0.0"),
                    order_id=ticket,
                    notes=json.dumps({
                        "source": "METATRADER_CSV_STATEMENT",
                        "stage": "OPEN",
                        "ticket": ticket,
                        "signal_id": signal_id
                    })
                )
                executions.append(open_exec)

                # 2. Close Execution
                close_exec = Execution(
                    execution_id=f"MQL5_{signal_id}_{ticket}_CLOSE",
                    trader_id=trader_id,
                    symbol=sym,
                    side=close_side,
                    quantity=vol,
                    price=close_p,
                    timestamp=close_t,
                    commission=half_comm,
                    slippage=Decimal("0.0"),
                    order_id=ticket,
                    notes=json.dumps({
                        "source": "METATRADER_CSV_STATEMENT",
                        "stage": "CLOSE",
                        "ticket": ticket,
                        "signal_id": signal_id,
                        "original_profit": float(reported_profit),
                        "original_swap": float(swap_val)
                    })
                )
                executions.append(close_exec)
                trades_count += 1

            except Exception as e:
                # Linhas não compatíveis com trade (ex: comentários ou cabeçalhos secundários)
                continue

        trader = Trader(
            trader_id=trader_id,
            name=t_name,
            created_at=earliest_time or datetime.now(timezone.utc),
            status=TraderStatus.ACTIVE,
            initial_capital=initial_deposit
        )

        executions.sort(key=lambda x: (x.timestamp, x.execution_id))
        audit = {
            "source_file": str(path),
            "signal_id": signal_id,
            "trades_count": trades_count,
            "executions_count": len(executions),
            "reported_profit_sum": float(reported_profit_sum),
            "symbols": list(instruments_map.keys()),
            "first_timestamp": executions[0].timestamp.isoformat() if executions else None,
            "last_timestamp": executions[-1].timestamp.isoformat() if executions else None
        }

        return trader, executions, list(instruments_map.values()), audit

    def parse_raw_signal_file(self, file_path: Path | str) -> tuple[Trader, list[Execution], list[MarketInstrument]]:
        """
        Lê arquivo JSON de sinal MQL5 e extrai Trader, Executions e Instruments.
        """
        path = Path(file_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        signal_id = str(data["signal_id"])
        trader_id = f"MQL5_{signal_id}"
        trader_name = data.get("name", f"MQL5 Signal {signal_id}")
        initial_dep = Decimal(str(data.get("initial_deposit", 10000.0)))

        deals = data.get("deals", [])
        if deals:
            first_open = min(self._parse_flexible_datetime(d["open_time"]) for d in deals)
        else:
            first_open = datetime.now(timezone.utc)

        trader = Trader(
            trader_id=trader_id,
            name=trader_name,
            created_at=first_open,
            status=TraderStatus.ACTIVE,
            initial_capital=initial_dep
        )

        executions: list[Execution] = []
        instruments_map: dict[str, MarketInstrument] = {}

        for d in deals:
            ticket = str(d["ticket"])
            sym = str(d["symbol"]).upper()

            if sym not in instruments_map:
                instruments_map[sym] = self._map_instrument(sym)

            deal_type = d["type"].upper()
            open_side = OrderSide.BUY if deal_type == "BUY" else OrderSide.SELL
            close_side = OrderSide.SELL if deal_type == "BUY" else OrderSide.BUY

            vol = Decimal(str(d["volume"]))
            open_p = Decimal(str(d["open_price"]))
            close_p = Decimal(str(d["close_price"]))
            open_t = self._parse_flexible_datetime(d["open_time"])
            close_t = self._parse_flexible_datetime(d["close_time"])

            tot_comm = abs(Decimal(str(d.get("commission", 0.0))))
            half_comm = tot_comm / Decimal("2.0") if tot_comm > 0 else Decimal("0.0")

            # 1. Open Execution
            open_exec = Execution(
                execution_id=f"MQL5_{signal_id}_{ticket}_OPEN",
                trader_id=trader_id,
                symbol=sym,
                side=open_side,
                quantity=vol,
                price=open_p,
                timestamp=open_t,
                commission=half_comm,
                slippage=Decimal("0.0"),
                order_id=ticket,
                notes=json.dumps({
                    "source": "MQL5_SIGNALS_JSON",
                    "stage": "OPEN",
                    "ticket": ticket,
                    "signal_id": signal_id
                })
            )
            executions.append(open_exec)

            # 2. Close Execution
            close_exec = Execution(
                execution_id=f"MQL5_{signal_id}_{ticket}_CLOSE",
                trader_id=trader_id,
                symbol=sym,
                side=close_side,
                quantity=vol,
                price=close_p,
                timestamp=close_t,
                commission=half_comm,
                slippage=Decimal("0.0"),
                order_id=ticket,
                notes=json.dumps({
                    "source": "MQL5_SIGNALS_JSON",
                    "stage": "CLOSE",
                    "ticket": ticket,
                    "signal_id": signal_id,
                    "original_profit": d.get("profit", 0.0),
                    "original_swap": d.get("swap", 0.0)
                })
            )
            executions.append(close_exec)

        executions.sort(key=lambda x: (x.timestamp, x.execution_id))
        return trader, executions, list(instruments_map.values())

    def process_directory(
        self,
        raw_dir: Path | str,
        output_dir: Path | str
    ) -> dict[str, Any]:
        """
        Processa todos os arquivos JSON e CSV em raw_dir e exporta datasets normalizados em CSV.
        """
        raw_path = Path(raw_dir)
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        all_traders: list[Trader] = []
        all_executions: list[Execution] = []
        all_instruments: dict[str, MarketInstrument] = {}
        all_prices: list[MarketPriceRecord] = []

        # 1. Processa JSONs
        raw_json_files = list(raw_path.glob("trader_*.json"))
        for rf in sorted(raw_json_files):
            trader, execs, insts = self.parse_raw_signal_file(rf)
            all_traders.append(trader)
            all_executions.extend(execs)
            for inst in insts:
                all_instruments[inst.symbol] = inst

        # 2. Processa CSVs MetaTrader
        raw_csv_files = list(raw_path.glob("*.csv"))
        for cf in sorted(raw_csv_files):
            # Extrai signal_id do nome do arquivo se possível
            sig_match = re.search(r"\d+", cf.name)
            sig_id = sig_match.group(0) if sig_match else cf.stem
            trader, execs, insts, _ = self.parse_csv_statement(cf, signal_id=sig_id)
            all_traders.append(trader)
            all_executions.extend(execs)
            for inst in insts:
                all_instruments[inst.symbol] = inst

        # 3. Exporta traders.csv
        traders_csv = out_path / "traders.csv"
        with open(traders_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["trader_id", "name", "created_at", "status", "initial_capital"])
            for t in all_traders:
                writer.writerow([t.trader_id, t.name, t.created_at.isoformat(), t.status.value, str(t.initial_capital)])

        # 4. Exporta executions.csv
        execs_csv = out_path / "executions.csv"
        with open(execs_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["execution_id", "trader_id", "symbol", "side", "quantity", "price", "timestamp", "commission", "slippage", "order_id", "notes"])
            for ex in all_executions:
                writer.writerow([
                    ex.execution_id, ex.trader_id, ex.symbol, ex.side.value,
                    str(ex.quantity), str(ex.price), ex.timestamp.isoformat(),
                    str(ex.commission), str(ex.slippage), ex.order_id or "", ex.notes or ""
                ])

        # 5. Exporta instruments.csv
        inst_csv = out_path / "instruments.csv"
        with open(inst_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["symbol", "asset_class", "tick_size", "tick_value", "contract_multiplier", "currency"])
            for inst in all_instruments.values():
                writer.writerow([inst.symbol, inst.asset_class.value, str(inst.tick_size), str(inst.tick_value), str(inst.contract_multiplier), inst.currency])

        return {
            "traders_count": len(all_traders),
            "executions_count": len(all_executions),
            "instruments_count": len(all_instruments),
            "files_generated": [
                str(traders_csv),
                str(execs_csv),
                str(inst_csv)
            ]
        }
