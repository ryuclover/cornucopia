from datetime import datetime, timezone
from typing import Optional, Sequence
from src.config.evaluation_config import EvaluationFrequency
from src.config.signal_config import SignalConfig
from src.evaluation.engine import TraderEvaluationEngine
from src.replay.engine import TraderReplayEngine
from src.signals.extractor import TraderSignalExtractor
from src.signals.models import TraderSignal


class TraderSignalEngine:
    """
    Motor de Extração e Gestão de Sinais Individuais dos Traders Selecionados.
    
    Orquestra:
    - Descoberta automática do universo de instrumentos ativos em 'as_of'.
    - Extração em lote de sinais para todos os membros do núcleo.
    - Séries temporais históricas de sinais por trader/ativo.
    """
    def __init__(
        self,
        replay_engine: TraderReplayEngine,
        config: Optional[SignalConfig] = None
    ):
        self.replay_engine = replay_engine
        self.config = config or SignalConfig()

    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def discover_active_symbols(
        self,
        as_of: datetime,
        trader_ids: Sequence[str],
        lookback_days: Optional[int] = None
    ) -> list[str]:
        """
        Descobre todos os instrumentos operados ou com posição ativa por qualquer trader do núcleo até 'as_of'.
        """
        as_of = self._normalize_utc(as_of)
        active_symbols = set()
        lookback = lookback_days or self.config.flat_activity_lookback_days

        for tid in trader_ids:
            if hasattr(self.replay_engine.execution_repo, "find_by_trader_until_as_of"):
                execs = self.replay_engine.execution_repo.find_by_trader_until_as_of(tid, as_of)
            elif hasattr(self.replay_engine.execution_repo, "find_by_trader"):
                execs = [e for e in self.replay_engine.execution_repo.find_by_trader(tid) if self._normalize_utc(e.timestamp) <= as_of]
            else:
                execs = []

            for e in execs:
                active_symbols.add(e.symbol)

        return sorted(list(active_symbols))

    def extract_core_signals(
        self,
        as_of: datetime,
        trader_ids: Sequence[str],
        symbols: Optional[Sequence[str]] = None,
        config: Optional[SignalConfig] = None
    ) -> dict[str, list[TraderSignal]]:
        """
        Extrai sinais de todos os traders para cada símbolo relevante em 'as_of'.
        Retorna dicionário {symbol: [TraderSignal]}.
        """
        cfg = config or self.config
        as_of = self._normalize_utc(as_of)

        target_symbols = symbols if symbols is not None else self.discover_active_symbols(as_of, trader_ids)
        result: dict[str, list[TraderSignal]] = {}

        for sym in target_symbols:
            signals_for_sym = []
            for tid in trader_ids:
                sig = TraderSignalExtractor.extract_signal(
                    trader_id=tid,
                    symbol=sym,
                    as_of=as_of,
                    replay_engine=self.replay_engine,
                    config=cfg
                )
                signals_for_sym.append(sig)
            result[sym] = signals_for_sym

        return result

    def extract_signal_series(
        self,
        trader_id: str,
        symbol: str,
        start: datetime,
        end: datetime,
        frequency: EvaluationFrequency = EvaluationFrequency.MONTHLY,
        config: Optional[SignalConfig] = None
    ) -> list[TraderSignal]:
        """
        Gera a série histórica de sinais individuais de um trader para um instrumento.
        """
        start = self._normalize_utc(start)
        end = self._normalize_utc(end)
        cfg = config or self.config

        timestamps = TraderEvaluationEngine.generate_evaluation_timestamps(start, end, frequency)
        signals: list[TraderSignal] = []

        for ts in timestamps:
            sig = TraderSignalExtractor.extract_signal(
                trader_id=trader_id,
                symbol=symbol,
                as_of=ts,
                replay_engine=self.replay_engine,
                config=cfg
            )
            signals.append(sig)

        return signals
