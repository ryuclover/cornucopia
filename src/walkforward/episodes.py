from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence
from src.config.walkforward_config import OutcomeClassification, WalkForwardConfig
from src.consensus.models import ConsensusDirection
from src.storage.repositories import SQLiteMarketPriceRepository
from src.walkforward.models import ConsensusEpisode, WalkForwardDecision


class ConsensusEpisodeTracker:
    """
    Rastreador e Avaliador de Episódios Direcionais Contínuos de Consenso.
    
    Agrupa sequências consecutivas de decisões com a mesma tese direcional (LONG ou SHORT),
    evitando que uma tese persistente infle artificialmente o tamanho amostral de decisões independentes,
    e detecta reversões severas diretas (DIRECT_FLIP: LONG <-> SHORT).
    """
    def __init__(
        self,
        price_repo: SQLiteMarketPriceRepository,
        config: WalkForwardConfig
    ):
        self.price_repo = price_repo
        self.config = config

    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _get_price_at_or_before(self, symbol: str, dt: datetime) -> Optional[Decimal]:
        return self.price_repo.get_latest_price_until_as_of(symbol, dt)

    def track_episodes_for_symbol(
        self,
        symbol: str,
        decisions: Sequence[WalkForwardDecision]
    ) -> list[ConsensusEpisode]:
        """
        Rastreia todos os episódios direcionais de um símbolo ao longo de suas decisões cronológicas.
        """
        if not decisions:
            return []

        sorted_decs = sorted(decisions, key=lambda d: self._normalize_utc(d.decision_as_of))
        episodes: list[ConsensusEpisode] = []

        current_episode_dir: Optional[ConsensusDirection] = None
        episode_start_dt: Optional[datetime] = None
        episode_end_dt: Optional[datetime] = None
        episode_decs: list[WalkForwardDecision] = []
        episode_counter = 1

        for i, dec in enumerate(sorted_decs):
            direction = dec.consensus_direction
            is_directional = direction in (ConsensusDirection.LONG, ConsensusDirection.SHORT)
            dt = self._normalize_utc(dec.decision_as_of)

            if current_episode_dir is None:
                if is_directional:
                    current_episode_dir = direction
                    episode_start_dt = dt
                    episode_end_dt = dt
                    episode_decs = [dec]
            else:
                if direction == current_episode_dir:
                    # Continuação do mesmo episódio
                    episode_end_dt = dt
                    episode_decs.append(dec)
                else:
                    # Término do episódio atual
                    is_flip = (
                        (current_episode_dir == ConsensusDirection.LONG and direction == ConsensusDirection.SHORT) or
                        (current_episode_dir == ConsensusDirection.SHORT and direction == ConsensusDirection.LONG)
                    )
                    
                    ep = self._finalize_episode(
                        symbol=symbol,
                        episode_num=episode_counter,
                        direction=current_episode_dir,
                        start_dt=episode_start_dt, # type: ignore
                        end_dt=episode_end_dt,     # type: ignore
                        terminated_by=direction,
                        is_direct_flip=is_flip,
                        decisions=episode_decs
                    )
                    episodes.append(ep)
                    episode_counter += 1

                    if is_directional:
                        current_episode_dir = direction
                        episode_start_dt = dt
                        episode_end_dt = dt
                        episode_decs = [dec]
                    else:
                        current_episode_dir = None
                        episode_start_dt = None
                        episode_end_dt = None
                        episode_decs = []

        # Finaliza último episódio aberto (se houver)
        if current_episode_dir is not None and episode_start_dt is not None and episode_end_dt is not None:
            ep = self._finalize_episode(
                symbol=symbol,
                episode_num=episode_counter,
                direction=current_episode_dir,
                start_dt=episode_start_dt,
                end_dt=episode_end_dt,
                terminated_by=ConsensusDirection.UNKNOWN,
                is_direct_flip=False,
                decisions=episode_decs
            )
            episodes.append(ep)

        return episodes

    def _finalize_episode(
        self,
        symbol: str,
        episode_num: int,
        direction: ConsensusDirection,
        start_dt: datetime,
        end_dt: datetime,
        terminated_by: ConsensusDirection,
        is_direct_flip: bool,
        decisions: list[WalkForwardDecision]
    ) -> ConsensusEpisode:
        entry_price = self._get_price_at_or_before(symbol, start_dt)
        exit_price = self._get_price_at_or_before(symbol, end_dt)

        raw_ret: Optional[float] = None
        signed_ret: Optional[float] = None
        outcome_class = OutcomeClassification.UNEVALUABLE

        if entry_price is not None and exit_price is not None and float(entry_price) > 0:
            raw_ret = float((exit_price - entry_price) / entry_price)
            if direction == ConsensusDirection.LONG:
                signed_ret = raw_ret
            elif direction == ConsensusDirection.SHORT:
                signed_ret = -raw_ret
            else:
                signed_ret = 0.0

            band = self.config.neutral_return_band_bps / 10000.0
            if signed_ret > band:
                outcome_class = OutcomeClassification.CORRECT
            elif signed_ret < -band:
                outcome_class = OutcomeClassification.INCORRECT
            else:
                outcome_class = OutcomeClassification.NEUTRAL_OUTCOME

        avg_margin = sum(d.consensus_margin for d in decisions) / len(decisions) if decisions else 0.0
        avg_groups = sum(d.supporting_independent_group_count for d in decisions) / len(decisions) if decisions else 0.0

        return ConsensusEpisode(
            episode_id=f"EP_{symbol}_{episode_num}_{start_dt.strftime('%Y%m%d')}",
            symbol=symbol,
            direction=direction,
            start_as_of=start_dt,
            end_as_of=end_dt,
            decision_count=len(decisions),
            terminated_by=terminated_by,
            is_direct_flip=is_direct_flip,
            entry_reference_price=entry_price,
            exit_reference_price=exit_price,
            episode_raw_return_pct=round(raw_ret * 100.0, 4) if raw_ret is not None else None,
            episode_signed_return_pct=round(signed_ret * 100.0, 4) if signed_ret is not None else None,
            episode_outcome_class=outcome_class,
            average_consensus_margin=round(avg_margin, 4),
            average_independent_groups=round(avg_groups, 2)
        )

    def track_all_episodes(
        self,
        decisions_by_symbol: dict[str, list[WalkForwardDecision]]
    ) -> list[ConsensusEpisode]:
        """
        Rastreia episódios para todos os símbolos analisados.
        """
        all_episodes: list[ConsensusEpisode] = []
        for sym, decs in decisions_by_symbol.items():
            eps = self.track_episodes_for_symbol(sym, decs)
            all_episodes.extend(eps)
        return sorted(all_episodes, key=lambda e: (e.symbol, e.start_as_of))
