"""DB Sport (Denis Bouan) — école/structure de pilotage moto.

Source : API WooCommerce Store v1 publique. Renvoie 100+ produits dont seulement
quelques-uns sont des journées Alès. On filtre par préfixe `ALES ` ou `Ales ` dans
le nom (plus robuste que par catégorie : 4 produits Alès n'ont pas la catégorie
`inscription-circuit-ales` mais ont bien le préfixe dans le nom).

On ignore les éditions passées : seul l'avenir nous intéresse côté agrégateur.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

import httpx

from db import Event
from scrapers._common import (
    HTTP_TIMEOUT,
    USER_AGENT,
    clean_text,
    parse_french_date,
    wc_price_to_cents,
)

ORGANIZER = "DB Sport"
CIRCUIT = "Alès"
API_URL = "https://denisbouan.fr/wp-json/wc/store/v1/products"
PER_PAGE = 100


def fetch(today: date | None = None) -> list[Event]:
    """Récupère toutes les journées Alès à venir chez DB Sport.

    `today` est injectable pour faciliter les tests; défaut = aujourd'hui.
    """
    if today is None:
        today = date.today()

    products = list(_iter_all_products())

    events: list[Event] = []
    for p in products:
        ev = _product_to_event(p, today=today)
        if ev is not None:
            events.append(ev)
    return events


def _iter_all_products() -> Iterable[dict]:
    """Pagine sur l'API jusqu'à épuisement."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers) as client:
        page = 1
        while True:
            resp = client.get(API_URL, params={"per_page": PER_PAGE, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                return
            yield from batch
            if len(batch) < PER_PAGE:
                return
            page += 1


def _is_ales_product(name: str) -> bool:
    """`ALES Samedi 15 août` ou `Ales – Lundi 7 avril` → True. `Stage Route-Circuit` → False."""
    n = name.strip().lower()
    return n.startswith("ales ") or n.startswith("ales-") or n.startswith("ales–") or n.startswith("ales—")


def _product_to_event(p: dict, *, today: date) -> Event | None:
    name = clean_text(p.get("name") or "")
    if not _is_ales_product(name):
        return None

    parsed = parse_french_date(name)
    if parsed is None or parsed < today:
        return None

    prices = p.get("prices") or {}
    price_cents = wc_price_to_cents(prices.get("price"), prices.get("currency_minor_unit"))

    return Event(
        organizer=ORGANIZER,
        source_id=str(p["id"]),
        circuit=CIRCUIT,
        date=parsed.isoformat(),
        title=name,
        price_cents=price_cents,
        currency=prices.get("currency_code") or "EUR",
        available=bool(p.get("is_in_stock", True)),
        booking_url=p.get("permalink") or "",
        raw_data={
            "id": p.get("id"),
            "slug": p.get("slug"),
            "stock_status": p.get("stock_status"),
            "categories": [c.get("slug") for c in (p.get("categories") or [])],
        },
    )
