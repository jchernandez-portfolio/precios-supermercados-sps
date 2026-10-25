"""Derivaciones comerciales puras sobre current/history aceptados.

``reported_regular_price`` nunca participa en el cálculo de reducción real: es
sólo un valor reportado por la fuente. La referencia válida es el
``current_price`` del periodo histórico aceptado inmediatamente anterior.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .commercial_state import CurrentCommercialOffer, OfferHistoryPeriod
from .identifiers import (
    generate_offer_id,
    generate_source_product_id,
    generate_state_hash,
)
from .models import ValidatedOffer


class CommercialPricingError(ValueError):
    """Current/history no reconcilian y no permiten derivar un ahorro seguro."""


@dataclass(frozen=True, slots=True)
class RealPriceReduction:
    """Comparación del precio actual contra el estado aceptado anterior."""

    offer_id: str
    currency: str
    current_price: Decimal | None
    previous_accepted_price: Decimal | None
    reduction_amount: Decimal | None
    reduction_ratio: Decimal | None
    is_real_reduction: bool
    current_observed_at_utc: datetime
    previous_observed_at_utc: datetime | None
    current_scrape_run_id: str
    previous_scrape_run_id: str | None


def evaluate_real_price_reduction(
    current: CurrentCommercialOffer | None,
    history: Sequence[OfferHistoryPeriod],
) -> RealPriceReduction | None:
    """Calcula ahorro real sin usar el precio regular reportado por la fuente.

    ``history`` debe ser una cadena íntegra y ordenada de periodos aceptados, con
    exactamente un periodo abierto al final y reconciliado con ``current``. Si
    existe un periodo previo, su ``current_price`` es la única referencia. Sin
    ambos precios no se inventa una reducción. Una subida o igualdad produce 0.
    """

    if current is None:
        if history:
            raise CommercialPricingError("existe histórico sin current asociado")
        return None
    if not history:
        raise CommercialPricingError("current no tiene periodo histórico abierto")

    validated = current.validated_offer
    offer = validated.offer
    _validate_deterministic_identity(validated, context="current")
    if offer.observed_at_utc != current.last_observed_at_utc:
        raise CommercialPricingError(
            "current y su evidencia discrepan en observación / última observación"
        )
    if offer.scrape_run_id != current.last_scrape_run_id:
        raise CommercialPricingError(
            "current y su evidencia discrepan en ejecución / última ejecución"
        )
    _validate_evidence_integrity(validated, context="current")

    _validate_history_chain(offer.offer_id, offer.currency, history)
    open_period = history[-1]

    if open_period.state_hash != validated.state_hash:
        raise CommercialPricingError("current y periodo abierto tienen state_hash distinto")
    if open_period.valid_from_utc != current.first_observed_at_utc:
        raise CommercialPricingError("current e histórico discrepan en apertura")
    if open_period.last_observed_at_utc != current.last_observed_at_utc:
        raise CommercialPricingError("current e histórico discrepan en última observación")
    if open_period.last_confirmed_by_scrape_run_id != current.last_scrape_run_id:
        raise CommercialPricingError("current e histórico discrepan en última ejecución")

    previous_period = history[-2] if len(history) >= 2 else None
    current_price = offer.current_price
    previous_price = (
        previous_period.validated_offer.offer.current_price
        if previous_period is not None
        else None
    )

    reduction_amount: Decimal | None = None
    reduction_ratio: Decimal | None = None
    is_real_reduction = False
    if current_price is not None and previous_price is not None:
        reduction_amount = max(previous_price - current_price, Decimal("0"))
        reduction_ratio = reduction_amount / previous_price
        is_real_reduction = reduction_amount > 0

    return RealPriceReduction(
        offer_id=offer.offer_id,
        currency=offer.currency,
        current_price=current_price,
        previous_accepted_price=previous_price,
        reduction_amount=reduction_amount,
        reduction_ratio=reduction_ratio,
        is_real_reduction=is_real_reduction,
        current_observed_at_utc=current.last_observed_at_utc,
        previous_observed_at_utc=(
            previous_period.last_observed_at_utc
            if previous_period is not None
            else None
        ),
        current_scrape_run_id=current.last_scrape_run_id,
        previous_scrape_run_id=(
            previous_period.last_confirmed_by_scrape_run_id
            if previous_period is not None
            else None
        ),
    )


def _validate_deterministic_identity(
    validated: ValidatedOffer,
    *,
    context: str,
) -> None:
    """Recalcula las identidades que una persistencia no puede redefinir."""

    offer = validated.offer
    expected_source_product_id = generate_source_product_id(
        offer.supermarket_id,
        offer.source_key_type,
        offer.source_key,
    )
    if offer.source_product_id != expected_source_product_id:
        raise CommercialPricingError(
            f"{context} contiene source_product_id no determinista"
        )
    expected_offer_id = generate_offer_id(
        offer.supermarket_id,
        offer.location_id,
        expected_source_product_id,
    )
    if offer.offer_id != expected_offer_id:
        raise CommercialPricingError(f"{context} contiene offer_id no determinista")


def _validate_evidence_integrity(
    validated: ValidatedOffer,
    *,
    context: str,
) -> None:
    """Revalida cronología y contenido tras reconciliar el wrapper persistido."""

    offer = validated.offer
    if validated.validated_at_utc < offer.observed_at_utc:
        raise CommercialPricingError(
            f"{context} contiene validated_at_utc anterior a observed_at_utc"
        )
    if generate_state_hash(offer) != validated.state_hash:
        if context == "current":
            raise CommercialPricingError("current contiene state_hash inválido")
        raise CommercialPricingError(f"{context} contiene state_hash inválido")


def _require_run_id(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CommercialPricingError(f"{field_name} no puede estar vacío")
    return value.strip()


def _validate_history_chain(
    offer_id: str,
    currency: str,
    history: Sequence[OfferHistoryPeriod],
) -> None:
    if history[-1].valid_to_utc is not None:
        raise CommercialPricingError("el último periodo histórico no está abierto")

    open_count = 0
    previous: OfferHistoryPeriod | None = None
    for index, period in enumerate(history):
        validated = period.validated_offer
        period_offer = validated.offer
        _validate_deterministic_identity(validated, context="periodo histórico")
        if period.offer_id != offer_id or period_offer.offer_id != period.offer_id:
            raise CommercialPricingError("current e histórico pertenecen a offer_id distintos")
        if period_offer.currency != currency:
            raise CommercialPricingError("current e histórico usan monedas distintas")
        if period_offer.observed_at_utc != period.valid_from_utc:
            raise CommercialPricingError("periodo y evidencia discrepan en apertura")
        opened_by = _require_run_id(
            period.opened_by_scrape_run_id,
            "opened_by_scrape_run_id",
        )
        if period_offer.scrape_run_id != opened_by:
            raise CommercialPricingError("periodo y evidencia discrepan en ejecución de apertura")
        _require_run_id(
            period.last_confirmed_by_scrape_run_id,
            "last_confirmed_by_scrape_run_id",
        )
        if validated.state_hash != period.state_hash:
            raise CommercialPricingError("periodo y ValidatedOffer tienen state_hash distinto")
        _validate_evidence_integrity(validated, context="periodo histórico")

        if period.valid_to_utc is None:
            open_count += 1
            if index != len(history) - 1:
                raise CommercialPricingError("existe un periodo histórico abierto intermedio")
            if period.closed_by_scrape_run_id is not None:
                raise CommercialPricingError("periodo histórico abierto tiene ejecución de cierre")
        else:
            if period.valid_to_utc <= period.valid_from_utc:
                raise CommercialPricingError("periodo histórico con intervalo no positivo")
            _require_run_id(
                period.closed_by_scrape_run_id,
                "closed_by_scrape_run_id",
            )

        if period.last_observed_at_utc < period.valid_from_utc:
            raise CommercialPricingError("periodo histórico con última observación inválida")
        if period.valid_to_utc is not None and period.last_observed_at_utc > period.valid_to_utc:
            raise CommercialPricingError("periodo histórico observado después de su cierre")

        if previous is not None:
            if previous.valid_to_utc != period.valid_from_utc:
                raise CommercialPricingError("los periodos históricos no son contiguos")
            if previous.closed_by_scrape_run_id != opened_by:
                raise CommercialPricingError(
                    "ejecución de cierre no coincide con la apertura del periodo siguiente"
                )
        previous = period

    if open_count != 1:
        raise CommercialPricingError("el último periodo histórico no está abierto")
