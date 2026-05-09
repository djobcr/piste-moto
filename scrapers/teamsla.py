"""Team SLA — site WooCommerce multi-circuit.

L'API WC Store /wp-json/wc/store/v1/products expose tous les produits (74),
on filtre ceux dont le slug commence par `circuit-ales-` pour ne garder que
les sorties Alès. Les noms contiennent du HTML (`<br>`) qu'on nettoie via
`clean_text`.
"""
from __future__ import annotations

from datetime import date

import httpx

from db import Event
from scrapers._common import (
    HTTP_TIMEOUT,
    USER_AGENT,
    clean_text,
    parse_french_date,
    wc_price_to_cents,
)

ORGANIZER = "Team SLA"
CIRCUIT = "Alès"
API_URL = "https://www.team-sla.fr/wp-json/wc/store/v1/products"
PER_PAGE = 100


def fetch(today: date | None = None) -> list[Event]:
    if today is None:
        today = date.today()

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    products: list[dict] = []
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers) as client:
        page = 1
        while True:
            resp = client.get(API_URL, params={"per_page": PER_PAGE, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            products.extend(batch)
            if len(batch) < PER_PAGE:
                break
            page += 1

    events: list[Event] = []
    for p in products:
        ev = _product_to_event(p, today=today)
        if ev is not None:
            events.append(ev)
    return events


def _product_to_event(p: dict, *, today: date) -> Event | None:
    slug = (p.get("slug") or "").lower()
    if not slug.startswith("circuit-ales-"):
        return None

    name = clean_text(p.get("name") or "")
    if not name:
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
        },
    )
