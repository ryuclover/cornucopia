from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from src.config.signal_config import SignalConfig
from src.domain.enums import PositionSide
from src.domain.position_tracker import PositionTracker
from src.replay.engine import TraderReplayEngine
from src.signals.models import SignalState, TraderSignal


class TraderSignalExtractor:
    """
    Extrator de Sinais e Posições Ponto no Tempo (Point-in-Time Signal Extractor).
    
    Reconstrói a posição exata de um trader para um instrumento em 'as_of' utilizando
    estritamente as execuções finalizadas até esse instante.
    
    Regras de Diferenciação:
    - Posição comprada aberta -> LONG (independente de quando foi aberta)
    - Posição vendida aberta -> SHORT (independente de quando foi aberta)
    - Posição zerada com negociação recente (<= lookback_days) -> FLAT
    - Posição zerada inativa (> lookback_days) ou nunca negociou -> NO_OPINION
    - Erro de dados ou inconsistência -> UNKNOWN
    """
    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @classmethod
    def extract_signal(
        cls,
        trader_id: str,
        symbol: str,
        as_of: datetime,
        replay_engine: TraderReplayEngine,
        config: Optional[SignalConfig] = None
    ) -> TraderSignal:
        """
        Extrai o sinal individual e auditável do trader para o instrumento em 'as_of'.
        """
        cfg = config or SignalConfig()
        as_of = cls._normalize_utc(as_of)

        inst = replay_engine.instrument_repo.get_by_symbol(symbol)
        if inst is None:
            return TraderSignal(
                trader_id=trader_id,
                symbol=symbol,
                as_of=as_of,
                signal_state=SignalState.UNKNOWN,
                reasons=[f"Instrumento {symbol} não cadastrado no repositório"]
            )

        # 1. Recupera execuções estritamente point-in-time (<= as_of)
        if hasattr(replay_engine.execution_repo, "find_by_trader_until_as_of"):
            all_execs = replay_engine.execution_repo.find_by_trader_until_as_of(trader_id, as_of)
        elif hasattr(replay_engine.execution_repo, "find_by_trader"):
            all_execs = [
                e for e in replay_engine.execution_repo.find_by_trader(trader_id)
                if cls._normalize_utc(e.timestamp) <= as_of
            ]
        else:
            all_execs = []

        symbol_execs = [e for e in all_execs if e.symbol == symbol]

        if not symbol_execs:
            return TraderSignal(
                trader_id=trader_id,
                symbol=symbol,
                as_of=as_of,
                signal_state=SignalState.NO_OPINION,
                position_side=None,
                position_quantity=Decimal("0.0"),
                normalized_exposure=0.0,
                last_execution_at=None,
                days_since_last_execution=None,
                reasons=[f"Trader {trader_id} nunca negociou {symbol} até {as_of.isoformat()} (NO_OPINION)"],
                diagnostics={"execution_count": 0}
            )

        # 2. Ordenação cronológica estrita e reconstituição da posição
        sorted_execs = sorted(symbol_execs, key=lambda e: (cls._normalize_utc(e.timestamp), e.execution_id))
        last_exec_dt = cls._normalize_utc(sorted_execs[-1].timestamp)
        days_since_last = max(0.0, (as_of - last_exec_dt).total_seconds() / 86400.0)

        tracker = PositionTracker(instrument=inst, trader_id=trader_id)
        for ex in sorted_execs:
            tracker.process_execution(ex)

        curr_pos = tracker.position

        # 3. Classificação do Sinal
        if curr_pos.side == PositionSide.LONG and curr_pos.quantity > 0:
            sig_state = SignalState.LONG
            pos_side = PositionSide.LONG
            norm_exp = 1.0
            reasons = [
                f"Posição COMPRADA ativa em {symbol}: {curr_pos.quantity} contratos/ações (PM: {curr_pos.average_entry_price})"
            ]
        elif curr_pos.side == PositionSide.SHORT and curr_pos.quantity > 0:
            sig_state = SignalState.SHORT
            pos_side = PositionSide.SHORT
            norm_exp = -1.0
            reasons = [
                f"Posição VENDIDA ativa em {symbol}: {curr_pos.quantity} contratos/ações (PM: {curr_pos.average_entry_price})"
            ]
        else:
            # Posição está zerada (FLAT): distingue FLAT de NO_OPINION via freshness lookback
            if days_since_last <= float(cfg.flat_activity_lookback_days):
                sig_state = SignalState.FLAT
                pos_side = None
                norm_exp = 0.0
                reasons = [
                    f"Posição ZERADA em {symbol} com atividade recente há {days_since_last:.1f} dias (<= lookback de {cfg.flat_activity_lookback_days}d -> FLAT)"
                ]
            else:
                sig_state = SignalState.NO_OPINION
                pos_side = None
                norm_exp = 0.0
                reasons = [
                    f"Posição zerada em {symbol} inativa há {days_since_last:.1f} dias (> lookback de {cfg.flat_activity_lookback_days}d -> NO_OPINION)"
                ]

        return TraderSignal(
            trader_id=trader_id,
            symbol=symbol,
            as_of=as_of,
            signal_state=sig_state,
            position_side=pos_side,
            position_quantity=curr_pos.quantity,
            normalized_exposure=norm_exp,
            last_execution_at=last_exec_dt,
            days_since_last_execution=round(days_since_last, 2),
            reasons=reasons,
            diagnostics={
                "execution_count": len(sorted_execs),
                "last_execution_id": sorted_execs[-1].execution_id,
                "is_open": curr_pos.side != PositionSide.FLAT,
                "closed_trades_count": len(tracker.closed_trades),
            }
        )
