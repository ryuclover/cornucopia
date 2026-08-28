from typing import Any, Sequence
from src.consensus.models import (
    ConsensusDirection,
    ConsensusTurnoverMetric,
    CoreConsensusSnapshot,
)


class ConsensusDiagnosticsCalculator:
    """
    Calculador de Diagnósticos de Estabilidade, Rotação e Flips Direcionais de Consenso.
    """
    @classmethod
    def calculate_turnover(
        cls,
        snap_prev: CoreConsensusSnapshot,
        snap_curr: CoreConsensusSnapshot
    ) -> ConsensusTurnoverMetric:
        """
        Calcula o turnover de consenso entre dois snapshots temporais consecutivos.
        """
        all_symbols = sorted(list(set(snap_prev.instruments) | set(snap_curr.instruments)))
        changes: dict[str, tuple[ConsensusDirection, ConsensusDirection]] = {}
        flips = 0

        for sym in all_symbols:
            d_prev = snap_prev.consensus_by_instrument[sym].consensus_direction if sym in snap_prev.consensus_by_instrument else ConsensusDirection.UNKNOWN
            d_curr = snap_curr.consensus_by_instrument[sym].consensus_direction if sym in snap_curr.consensus_by_instrument else ConsensusDirection.UNKNOWN

            if d_prev != d_curr:
                changes[sym] = (d_prev, d_curr)
                # Verifica reversão direta severa (flip)
                if (d_prev == ConsensusDirection.LONG and d_curr == ConsensusDirection.SHORT) or \
                   (d_prev == ConsensusDirection.SHORT and d_curr == ConsensusDirection.LONG):
                    flips += 1

        return ConsensusTurnoverMetric(
            from_as_of=snap_prev.as_of,
            to_as_of=snap_curr.as_of,
            direction_changes_count=len(changes),
            flips_count=flips,
            changes_by_instrument=changes
        )

    @classmethod
    def calculate_longitudinal_stability(
        cls,
        snapshots: Sequence[CoreConsensusSnapshot]
    ) -> dict[str, Any]:
        """
        Calcula métricas agregadas de distribuição de estados ao longo de uma série histórica.
        """
        if not snapshots:
            return {}

        total_obs = sum(s.total_instruments_analyzed for s in snapshots)
        if total_obs == 0:
            return {"total_observations": 0}

        long_tot = sum(s.long_consensus_count for s in snapshots)
        short_tot = sum(s.short_consensus_count for s in snapshots)
        neutral_tot = sum(s.neutral_count for s in snapshots)
        no_cons_tot = sum(s.no_consensus_count for s in snapshots)
        insuff_tot = sum(s.insufficient_coverage_count for s in snapshots)

        return {
            "total_snapshots": len(snapshots),
            "total_instrument_observations": total_obs,
            "long_consensus_rate_pct": round((long_tot / total_obs) * 100.0, 2),
            "short_consensus_rate_pct": round((short_tot / total_obs) * 100.0, 2),
            "neutral_rate_pct": round((neutral_tot / total_obs) * 100.0, 2),
            "no_consensus_rate_pct": round((no_cons_tot / total_obs) * 100.0, 2),
            "insufficient_coverage_rate_pct": round((insuff_tot / total_obs) * 100.0, 2),
        }
