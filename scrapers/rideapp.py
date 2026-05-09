"""RideApp — plateforme SaaS multi-organisateurs (shop.rideapp.fr).

Deux endpoints publics utilisés :
- `/api/v1/events` (paginé) : liste tous les events, filtrés client-side sur Alès
- `/api/v1/events/{id}/groups` : niveaux + places restantes par niveau

Le 2e endpoint est exclusif à RideApp et change la donne : on récupère
"Débutant 4, Moyen 0, Confirmé 0, Pilote 7" au lieu d'un seul "11 places".
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

import httpx

from db import Event, Level
from scrapers._common import (
    HTTP_TIMEOUT,
    USER_AGENT,
    clean_text,
    euros_to_cents,
    normalize_level,
)

API_URL = "https://shop.rideapp.fr/api/v1/events"
SHOP_BASE = "https://shop.rideapp.fr"
PER_PAGE = 100
CIRCUIT = "Alès"

# Variantes acceptées dans l'API pour identifier le circuit Alès
_ALES_VARIANTS = {"alès", "ales", "alès cévennes", "ales cevennes"}


def fetch(today: date | None = None) -> list[Event]:
    if today is None:
        today = date.today()

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    events: list[Event] = []
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers) as client:
        items = list(_iter_all_items(client))
        for item in items:
            ev = _item_to_event(item, today=today, client=client)
            if ev is not None:
                events.append(ev)
    return events


def _iter_all_items(client: httpx.Client) -> Iterable[dict]:
    page = 1
    while True:
        resp = client.get(API_URL, params={"page": page, "resultPerPage": PER_PAGE})
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") or []
        yield from items
        total_pages = int(data.get("totalPages") or 1)
        if page >= total_pages:
            return
        page += 1


def _is_ales_circuit(name: str | None) -> bool:
    if not name:
        return False
    return name.strip().lower() in _ALES_VARIANTS


def _item_to_event(item: dict, *, today: date, client: httpx.Client) -> Event | None:
    if not _is_ales_circuit(item.get("circuitName")):
        return None

    parsed = _parse_iso_date(item.get("startDate"))
    if parsed is None or parsed < today:
        return None

    organizer = clean_text(item.get("organizerName") or "RideApp")
    title = clean_text(item.get("name") or "")
    if not title:
        title = f"{organizer} — {parsed.isoformat()}"

    price = item.get("price")
    price_cents = euros_to_cents(price) if isinstance(price, (int, float)) else None

    remaining = item.get("remainingSeats")
    available = bool(remaining and remaining > 0)

    event_id = str(item.get("id") or "")
    event_day_id = str(item.get("eventDayId") or "")
    organizer_slug = str(item.get("organizerSlug") or "")
    booking_url = ""
    if organizer_slug and event_id:
        booking_url = f"{SHOP_BASE}/{organizer_slug}/event?eventId={event_id}"
        if event_day_id:
            booking_url += f"&eventDayId={event_day_id}"

    levels = _fetch_groups(client, event_id) if event_id else []

    return Event(
        organizer=organizer,
        source_id=event_id or f"rideapp:{organizer_slug}:{parsed.isoformat()}",
        circuit=CIRCUIT,
        date=parsed.isoformat(),
        title=title,
        price_cents=price_cents,
        currency="EUR",
        available=available,
        booking_url=booking_url or item.get("orderUrl") or SHOP_BASE,
        levels=levels,
        raw_data={
            "remaining_seats": remaining,
            "organizer_slug": organizer_slug,
            "event_day_id": event_day_id,
            "order_url_external": item.get("orderUrl"),
            "is_partner": item.get("isPartner"),
            "bundle": item.get("bundle"),
        },
    )


def _fetch_groups(client: httpx.Client, event_id: str) -> list[Level]:
    """Récupère les groupes (niveaux) pour 1 event RideApp.

    Endpoint : /api/v1/events/{event_id}/groups → liste de groupes triés par
    `displayOrder`. Tolérant aux échecs (renvoie [] si KO) pour ne pas casser
    le scraper entier sur une réponse 5xx isolée.
    """
    try:
        resp = client.get(f"{SHOP_BASE}/api/v1/events/{event_id}/groups")
        resp.raise_for_status()
        groups = resp.json()
    except (httpx.HTTPError, ValueError):
        return []

    if not isinstance(groups, list):
        return []

    # Tri par displayOrder (1 = niveau le plus haut chez RideApp).
    groups_sorted = sorted(groups, key=lambda g: g.get("displayOrder", 999))
    levels: list[Level] = []
    for g in groups_sorted:
        name = clean_text(g.get("name") or "")
        if not name:
            continue
        levels.append(Level(
            raw=name,
            canonical=normalize_level(name),
            remaining=g.get("remainingSeats") if isinstance(g.get("remainingSeats"), int) else None,
            max=g.get("maxCapacity") if isinstance(g.get("maxCapacity"), int) else None,
        ))
    return levels


def _parse_iso_date(value: str | None) -> date | None:
    """`'2026-05-13T00:00:00+00:00'` → date(2026, 5, 13)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except (ValueError, TypeError):
        return None
