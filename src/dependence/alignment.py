from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence
from src.config.dependence_config import DependenceConfig
from src.config.evaluation_config import EvaluationFrequency
from src.dependence.models import TraderTimeSeriesFrame
from src.domain.enums import PositionSide
from src.domain.execution import Execution
from src.domain.trade import ClosedTrade


class TimeSeriesAligner:
    """
    Sincronizador e normalizador temporal de execuções, trades e exposições de traders.
    
    Gera grades temporais alinhadas (ex: diárias) ponto-no-tempo sem antecipação de dados.
    """
    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @classmethod
    def generate_calendar_buckets(
        cls,
        start: datetime,
        end: datetime,
        frequency: EvaluationFrequency = EvaluationFrequency.DAILY
    ) -> list[datetime]:
        """Gera a lista cronológica de buckets temporais."""
        start = cls._normalize_utc(start)
        end = cls._normalize_utc(end)

        step = timedelta(days=1)
        if frequency == EvaluationFrequency.WEEKLY:
            step = timedelta(days=7)
        elif frequency == EvaluationFrequency.MONTHLY:
            step = timedelta(days=30)

        buckets = []
        current = start
        while current <= end:
            buckets.append(current)
            current += step
        if not buckets or buckets[-1] < end:
            buckets.append(end)
        return buckets

    @classmethod
    def build_trader_time_series(
        cls,
        trader_id: str,
        trades: Sequence[ClosedTrade],
        executions: Sequence[Execution],
        as_of: datetime,
        window_days: int,
        initial_capital: Decimal,
        frequency: EvaluationFrequency = EvaluationFrequency.DAILY
    ) -> list[TraderTimeSeriesFrame]:
        """
        Constrói a série temporal normalizada de um trader dentro da janela de análise [as_of - window_days, as_of].
        """
        as_of = cls._normalize_utc(as_of)
        start_date = as_of - timedelta(days=window_days)
        buckets = cls.generate_calendar_buckets(start_date, as_of, frequency)

        # Filtro estrito Ponto no Tempo
        valid_trades = [t for t in trades if t.exit_time <= as_of and t.exit_time >= start_date]
        valid_execs = [e for e in executions if e.timestamp <= as_of and e.timestamp >= start_date]

        frames: list[TraderTimeSeriesFrame] = []
        capital_float = float(initial_capital) if initial_capital > Decimal("0.0") else 10000.0

        for i in range(len(buckets)):
            bucket_end = buckets[i]
            bucket_start = buckets[i - 1] if i > 0 else start_date

            # Trades que finalizaram dentro deste bucket
            bucket_trades = [t for t in valid_trades if bucket_start < t.exit_time <= bucket_end]
            bucket_pnl = sum((t.net_pnl for t in bucket_trades), Decimal("0.0"))
            bucket_return_pct = (float(bucket_pnl) / capital_float) * 100.0

            # Execuções ocorridas no bucket
            bucket_execs = [e for e in valid_execs if bucket_start < e.timestamp <= bucket_end]

            # Mapeia direções de posições e atividade por símbolo
            pos_directions: dict[str, float] = {}
            for t in valid_trades:
                # Se o trade estava aberto durante o bucket
                if t.entry_time <= bucket_end and t.exit_time >= bucket_start:
                    dir_val = 1.0 if t.side == PositionSide.LONG else -1.0
                    pos_directions[t.symbol] = dir_val

            is_active = len(bucket_trades) > 0 or len(bucket_execs) > 0 or len(pos_directions) > 0

            frames.append(
                TraderTimeSeriesFrame(
                    timestamp=bucket_end,
                    net_return=bucket_return_pct,
                    net_pnl=bucket_pnl,
                    gross_exposure=Decimal(str(sum(abs(d) for d in pos_directions.values()))),
                    position_directions=pos_directions,
                    is_active=is_active
                )
            )

        return frames

    @classmethod
    def align_pair_series(
        cls,
        series_a: list[TraderTimeSeriesFrame],
        series_b: list[TraderTimeSeriesFrame]
    ) -> tuple[list[TraderTimeSeriesFrame], list[TraderTimeSeriesFrame]]:
        """
        Sincroniza e alinha dois frames temporais nas mesmas datas/timestamps.
        """
        map_a = {f.timestamp: f for f in series_a}
        map_b = {f.timestamp: f for f in series_b}

        common_timestamps = sorted(list(set(map_a.keys()) & set(map_b.keys())))
        aligned_a = [map_a[ts] for ts in common_timestamps]
        aligned_b = [map_b[ts] for ts in common_timestamps]

        return aligned_a, aligned_b
