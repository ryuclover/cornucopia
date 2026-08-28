from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Sequence
from src.config.walkforward_config import (
    EvaluationStatus,
    OutcomeClassification,
    WalkForwardConfig,
)
from src.consensus.models import ConsensusDirection
from src.storage.repositories import SQLiteMarketPriceRepository
from src.walkforward.models import ForwardReturnOutcome, WalkForwardDecision


class ForwardOutcomeEvaluator:
    """
    Avaliador Out-of-Sample de Desfechos e Retornos Futuros.
    
    Recebe decisões congeladas e busca estritamente dados de mercado futuros para medir
    a rentabilidade econômica em horizontes fixos (+1d, +5d, +20d), calculando retornos
    sinalizados, banda neutra, MAE e MFE.
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

    def evaluate_decision_outcome(
        self,
        decision: WalkForwardDecision,
        horizon_days: int
    ) -> ForwardReturnOutcome:
        """
        Avalia o outcome econômico futuro de uma decisão para um horizonte específico em dias.
        """
        decision_as_of = self._normalize_utc(decision.decision_as_of)
        symbol = decision.symbol

        # 1. Localiza Preço de Referência (último preço <= decision_as_of)
        ref_p = self.price_repo.get_latest_record_until_as_of(symbol, decision_as_of)
        if ref_p is None:
            return ForwardReturnOutcome(
                decision_id=decision.decision_id,
                symbol=symbol,
                decision_as_of=decision_as_of,
                horizon_days=horizon_days,
                outcome_class=OutcomeClassification.UNEVALUABLE,
                evaluation_status=EvaluationStatus.MISSING_REFERENCE_PRICE,
                diagnostics={"reason": "Nenhum preço <= decision_as_of"}
            )

        ref_price_dt = self._normalize_utc(ref_p.timestamp)
        age_seconds = (decision_as_of - ref_price_dt).total_seconds()

        # Validação de frescor do preço de referência
        if age_seconds > self.config.minimum_price_freshness_seconds:
            return ForwardReturnOutcome(
                decision_id=decision.decision_id,
                symbol=symbol,
                decision_as_of=decision_as_of,
                horizon_days=horizon_days,
                reference_price=ref_p.price,
                outcome_class=OutcomeClassification.UNEVALUABLE,
                evaluation_status=EvaluationStatus.STALE_REFERENCE_PRICE,
                diagnostics={"stale_age_days": round(age_seconds / 86400.0, 2)}
            )

        # 2. Localiza Preço Futuro no Horizonte (primeiro preço >= decision_as_of + horizon_days)
        target_future_dt = decision_as_of + timedelta(days=horizon_days)
        fut_p = self.price_repo.get_first_price_in_or_after_as_of(symbol, target_future_dt)

        if fut_p is None:
            return ForwardReturnOutcome(
                decision_id=decision.decision_id,
                symbol=symbol,
                decision_as_of=decision_as_of,
                horizon_days=horizon_days,
                reference_price=ref_p.price,
                outcome_class=OutcomeClassification.UNEVALUABLE,
                evaluation_status=EvaluationStatus.MISSING_FORWARD_PRICE,
                diagnostics={"reason": f"Nenhum preço futuro >= {target_future_dt.isoformat()}"}
            )

        fut_price_dt = self._normalize_utc(fut_p.timestamp)
        delay_seconds = (fut_price_dt - target_future_dt).total_seconds()

        if delay_seconds > self.config.maximum_future_price_delay_seconds:
            return ForwardReturnOutcome(
                decision_id=decision.decision_id,
                symbol=symbol,
                decision_as_of=decision_as_of,
                horizon_days=horizon_days,
                reference_price=ref_p.price,
                outcome_class=OutcomeClassification.UNEVALUABLE,
                evaluation_status=EvaluationStatus.MISSING_FORWARD_PRICE,
                diagnostics={"delay_days": round(delay_seconds / 86400.0, 2)}
            )

        # 3. Cálculo de Retornos Bruto e Condicionado (Signed)
        ref_val = float(ref_p.price)
        fut_val = float(fut_p.price)
        if ref_val <= 0:
            return ForwardReturnOutcome(
                decision_id=decision.decision_id,
                symbol=symbol,
                decision_as_of=decision_as_of,
                horizon_days=horizon_days,
                reference_price=ref_p.price,
                outcome_class=OutcomeClassification.UNEVALUABLE,
                evaluation_status=EvaluationStatus.INSUFFICIENT_DATA
            )

        raw_ret = (fut_val - ref_val) / ref_val
        direction = decision.consensus_direction

        if direction == ConsensusDirection.LONG:
            signed_ret = raw_ret
        elif direction == ConsensusDirection.SHORT:
            signed_ret = -raw_ret
        else:
            signed_ret = 0.0

        # 4. Classificação com Banda Neutra
        band_rate = self.config.neutral_return_band_bps / 10000.0
        direction_correct: Optional[bool] = None

        if direction in (ConsensusDirection.LONG, ConsensusDirection.SHORT):
            if signed_ret > band_rate:
                outcome_class = OutcomeClassification.CORRECT
                direction_correct = True
            elif signed_ret < -band_rate:
                outcome_class = OutcomeClassification.INCORRECT
                direction_correct = False
            else:
                outcome_class = OutcomeClassification.NEUTRAL_OUTCOME
                direction_correct = None
        else:
            outcome_class = OutcomeClassification.NEUTRAL_OUTCOME
            direction_correct = None

        # 5. Cálculo de MAE e MFE intra-período
        window_prices = self.price_repo.get_price_history_range(symbol, decision_as_of, fut_price_dt)

        mae_pct: Optional[float] = None
        mfe_pct: Optional[float] = None

        if len(window_prices) >= 2:
            prices_float = [float(p.price) for p in window_prices]
            max_p = max(prices_float)
            min_p = min(prices_float)

            if direction == ConsensusDirection.LONG:
                mfe_pct = round(((max_p - ref_val) / ref_val) * 100.0, 4)
                mae_pct = round(((min_p - ref_val) / ref_val) * 100.0, 4)
            elif direction == ConsensusDirection.SHORT:
                mfe_pct = round(((ref_val - min_p) / ref_val) * 100.0, 4)
                mae_pct = round(((ref_val - max_p) / ref_val) * 100.0, 4)

        return ForwardReturnOutcome(
            decision_id=decision.decision_id,
            symbol=symbol,
            decision_as_of=decision_as_of,
            horizon_days=horizon_days,
            outcome_as_of=fut_price_dt,
            reference_price=ref_p.price,
            future_price=fut_p.price,
            raw_return_pct=round(raw_ret * 100.0, 4),
            signed_return_pct=round(signed_ret * 100.0, 4),
            direction_correct=direction_correct,
            outcome_class=outcome_class,
            evaluation_status=EvaluationStatus.EVALUATED,
            mae_pct=mae_pct,
            mfe_pct=mfe_pct,
            diagnostics={
                "ref_price_timestamp": ref_price_dt.isoformat(),
                "future_price_timestamp": fut_price_dt.isoformat(),
                "window_price_count": len(window_prices)
            }
        )

    def evaluate_all_decisions(
        self,
        decisions: Sequence[WalkForwardDecision]
    ) -> dict[int, list[ForwardReturnOutcome]]:
        """
        Avalia todas as decisões em todos os horizontes futuros configurados.
        Retorna dicionário {horizon_days: [ForwardReturnOutcome]}.
        """
        outcomes_by_horizon: dict[int, list[ForwardReturnOutcome]] = {}
        for h in self.config.forward_horizons_days:
            outcomes_for_h = []
            for dec in decisions:
                outcome = self.evaluate_decision_outcome(dec, horizon_days=h)
                outcomes_for_h.append(outcome)
            outcomes_by_horizon[h] = outcomes_for_h

        return outcomes_by_horizon

    def extract_non_overlapping_outcomes(
        self,
        outcomes: Sequence[ForwardReturnOutcome],
        horizon_days: int
    ) -> list[ForwardReturnOutcome]:
        """
        Extrai um subconjunto determinístico e temporalmente descorrelacionado de outcomes.
        
        Regra:
        1. Ordena cronologicamente por decision_as_of (e decision_id);
        2. Seleciona a primeira observação;
        3. Apenas seleciona a próxima observação se seu decision_as_of ocorrer em ou após
           o encerramento temporal (outcome_as_of ou decision_as_of + horizon_days) do outcome anterior.
        """
        if not outcomes:
            return []

        # Agrupa por símbolo para isolar sobreposição por ativo
        by_symbol: dict[str, list[ForwardReturnOutcome]] = {}
        for o in outcomes:
            by_symbol.setdefault(o.symbol, []).append(o)

        selected_all: list[ForwardReturnOutcome] = []

        for sym, sym_outs in by_symbol.items():
            sorted_outs = sorted(sym_outs, key=lambda x: (self._normalize_utc(x.decision_as_of), x.decision_id))
            selected_sym: list[ForwardReturnOutcome] = []

            for curr in sorted_outs:
                if not selected_sym:
                    selected_sym.append(curr)
                else:
                    last = selected_sym[-1]
                    if last.outcome_as_of is not None:
                        last_end = self._normalize_utc(last.outcome_as_of)
                    else:
                        last_end = self._normalize_utc(last.decision_as_of) + timedelta(days=horizon_days)

                    curr_start = self._normalize_utc(curr.decision_as_of)
                    if curr_start >= last_end:
                        selected_sym.append(curr)

            selected_all.extend(selected_sym)

        return sorted(selected_all, key=lambda x: (self._normalize_utc(x.decision_as_of), x.symbol))

    def build_non_overlapping_outcomes_by_horizon(
        self,
        outcomes_by_horizon: dict[int, list[ForwardReturnOutcome]]
    ) -> dict[int, list[ForwardReturnOutcome]]:
        """
        Gera o mapa {horizon_days: list[ForwardReturnOutcome]} para o subconjunto non-overlapping.
        """
        res: dict[int, list[ForwardReturnOutcome]] = {}
        for h, outs in outcomes_by_horizon.items():
            res[h] = self.extract_non_overlapping_outcomes(outs, horizon_days=h)
        return res

