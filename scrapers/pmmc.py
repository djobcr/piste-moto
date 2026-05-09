"""Pôle Mécanique MC — moto club du circuit, vend aussi des trackdays.

Source : API WooCommerce Store v1 (publique, JSON).
Tous les produits du shop sont des journées de roulage Alès, donc pas de filtre.
"""
from __future__ import annotations

import httpx

from db import Event, Level
from scrapers._common import normalize_level, parse_french_date, wc_price_to_cents

ORGANIZER = "PMMC"
CIRCUIT = "Alès"
API_URL = "https://polemecanique-mc.com/wp-json/wc/store/v1/products"
TIMEOUT = 20.0


def fetch() -> list[Event]:
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "piste-moto-aggregator/0.1"}) as client:
        resp = client.get(API_URL, params={"per_page": 100})
        resp.raise_for_status()
        products = resp.json()

    events: list[Event] = []
    for p in products:
        ev = _product_to_event(p)
        if ev is not None:
            events.append(ev)
    return events


def _product_to_event(p: dict) -> Event | None:
    name = p.get("name") or ""
    parsed = parse_french_date(name)
    if parsed is None:
        # Pas une journée datée (improbable ici, mais garde-fou)
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
            "categories": [c.get("slug") for c in (p.get("categories") or [])],
        },
    )


def _extract_levels(p: dict) -> list[Level]:
    """PMMC expose un attribut 'Catégorie' avec terms ['Débutant','Initié',...].

    Pas d'info de places par niveau (juste la liste). On déduplique au cas où.
    """
    for attr in (p.get("attributes") or []):
        attr_name = (attr.get("name") or "").lower()
        if "categorie" in attr_name or "catégorie" in attr_name:
            terms = attr.get("terms") or []
            seen_canonical: set[str] = set()
            levels: list[Level] = []
            for t in terms:
                raw = (t.get("name") or "").strip()
                if not raw:
                    continue
                canon = normalize_level(raw)
                if canon in seen_canonical:
                    continue
                seen_canonical.add(canon)
                levels.append(Level(raw=raw, canonical=canon))
            return levels
    return []
