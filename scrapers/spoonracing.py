"""Spoon Racing — organisateur dédié au circuit d'Alès.

API WooCommerce Store, mais avec `currency_minor_unit=0` (price "140" = 140€)
contrairement à la plupart des shops WP. Le helper `wc_price_to_cents` gère
cette particularité.

Le shop contient aussi quelques produits non-trackday (baptêmes, adhésion,
karting), donc on filtre par préfixe nom "Alès" / "Ales".
"""
from __future__ import annotations

from datetime import date

import httpx

from db import Event, Level
from scrapers._common import (
    HTTP_TIMEOUT,
    USER_AGENT,
    clean_text,
    normalize_level,
    parse_french_date,
    wc_price_to_cents,
)

ORGANIZER = "Spoon Racing"
CIRCUIT = "Alès"
API_URL = "https://www.spoonracing.fr/wp-json/wc/store/v1/products"
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


def _is_ales_product(name: str) -> bool:
    n = name.strip().lower()
    return n.startswith("alès") or n.startswith("ales")


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
        levels=_extract_levels(p),
        raw_data={
            "id": p.get("id"),
            "slug": p.get("slug"),
            "stock_status": p.get("stock_status"),
        },
    )


def _extract_levels(p: dict) -> list[Level]:
    """Spoon Racing expose un attribut 'Groupe' avec terms ['Débutant', 'Moyen', 'Pilote'].

    Certains terms incluent les options coaching, ex:
    'Débutant + coaching 3 sessions matin (+140€)'. On déduplique sur le slug
    canonique pour garder un seul "Débutant" propre.
    """
    for attr in (p.get("attributes") or []):
        attr_name = (attr.get("name") or "").lower()
        if "groupe" in attr_name:
            terms = attr.get("terms") or []
            seen: set[str] = set()
            levels: list[Level] = []
            for t in terms:
                raw_full = (t.get("name") or "").strip()
                if not raw_full:
                    continue
                # Garde la base avant " + coaching" ou " ("
                base = raw_full.split(" + ")[0].split(" (")[0].strip()
                canon = normalize_level(base)
                if canon in seen or canon == "autre":
                    continue
                seen.add(canon)
                levels.append(Level(raw=base, canonical=canon))
            return levels
    return []
