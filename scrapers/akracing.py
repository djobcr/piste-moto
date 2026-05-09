"""AK Racing — instance Odoo dédiée à la formation/pilotage moto.

Pas d'API JSON publique (Odoo ne l'expose pas par défaut sur ce site). Stratégie :

1. Recherche serveur : GET `/event?search=ales` filtre les events au mot-clé "ales".
2. Pour chaque event listé, fetch la fiche `/event/{slug}-{id}/register` qui contient
   des microdata schema.org (startDate, location, name) et le prix TTC.

Le prix TTC est dans `<span class="oe_currency_value">299,00</span>` du premier
ticket du form#registration_form. Le `<span itemprop="price">` à côté contient
le prix HT (caché en `d-none`) — pas ce qu'on veut pour l'utilisateur.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from db import Event
from scrapers._common import HTTP_TIMEOUT, USER_AGENT, clean_text, euros_to_cents

ORGANIZER = "AK Racing"
CIRCUIT = "Alès"
BASE_URL = "https://ak-racing1.odoo.com"
SEARCH_URL = f"{BASE_URL}/event"
SEARCH_QUERY = {"search": "ales"}

# /event/{slug}-{numericId}/register ou /event/{slug}-{numericId}
_RE_EVENT_HREF = re.compile(r"^/event/(?P<slug>[\w-]+?)-(?P<id>\d+)(?:/register)?$")


def fetch(today: date | None = None) -> list[Event]:
    if today is None:
        today = date.today()

    headers = {"User-Agent": USER_AGENT}
    events: list[Event] = []
    seen_ids: set[str] = set()

    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True) as client:
        for slug, event_id in _iter_event_links(client):
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            ev = _fetch_event_detail(client, slug=slug, event_id=event_id, today=today)
            if ev is not None:
                events.append(ev)
    return events


def _iter_event_links(client: httpx.Client):
    """Itère sur les pages de résultats de la recherche et yield (slug, id)."""
    page = 1
    while True:
        path = f"/event/page/{page}" if page > 1 else "/event"
        resp = client.get(BASE_URL + path, params=SEARCH_QUERY)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        anchors = soup.select('a[href^="/event/"]')

        any_match = False
        for a in anchors:
            href = a.get("href") or ""
            m = _RE_EVENT_HREF.match(href)
            if not m:
                continue
            slug = m.group("slug")
            if "ales" not in slug.lower():
                continue
            any_match = True
            yield slug, m.group("id")

        # Détecte la pagination Odoo : <a href="/event/page/N?...">
        next_link = soup.select_one(f'a[href*="/event/page/{page + 1}"]')
        if not next_link or not any_match:
            return
        page += 1


def _fetch_event_detail(
    client: httpx.Client,
    *,
    slug: str,
    event_id: str,
    today: date,
) -> Event | None:
    register_path = f"/event/{slug}-{event_id}/register"
    resp = client.get(BASE_URL + register_path)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Date début depuis microdata schema.org Event
    start_meta = soup.find("meta", attrs={"itemprop": "startDate"})
    if start_meta is None:
        return None
    start_value = start_meta.get("content") or ""
    parsed_dt = _parse_iso_date(start_value)
    if parsed_dt is None or parsed_dt < today:
        return None

    # Titre
    title_el = soup.find(attrs={"itemprop": "name"})
    title = clean_text(title_el.get_text(" ")) if title_el else f"Stage AK Racing {event_id}"

    # Prix TTC : premier .oe_currency_value du form d'inscription
    price_cents: int | None = None
    form = soup.find("form", id="registration_form")
    if form is not None:
        first_price = form.find("span", class_="oe_currency_value")
        if first_price is not None:
            price_cents = euros_to_cents(clean_text(first_price.get_text()))

    booking_url = urljoin(BASE_URL + "/", register_path)

    return Event(
        organizer=ORGANIZER,
        source_id=event_id,
        circuit=CIRCUIT,
        date=parsed_dt.isoformat(),
        title=title,
        price_cents=price_cents,
        currency="EUR",
        available=True,  # Odoo n'expose pas le restant publiquement
        booking_url=booking_url,
        raw_data={
            "slug": slug,
            "start_iso": start_value,
        },
    )


def _parse_iso_date(value: str) -> date | None:
    """Accepte '2026-07-26 05:00:00Z' ou '2026-07-26T05:00:00'."""
    if not value:
        return None
    cleaned = value.strip().replace("Z", "").replace("T", " ")
    try:
        return datetime.fromisoformat(cleaned).date()
    except ValueError:
        return None
