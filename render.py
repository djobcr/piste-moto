"""Génère dist/index.html depuis la base SQLite.

Lit la vue `events_active` (futurs uniquement), groupe par mois, formate les
dates en français, et écrit un HTML statique autonome (CSS et JS inline).
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).parent
DB_PATH = ROOT / "piste.db"
TEMPLATES_DIR = ROOT / "templates"
DIST_DIR = ROOT / "dist"
OUTPUT = DIST_DIR / "index.html"

WEEKDAYS_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
MONTHS_FR_LONG = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
MONTHS_FR_SHORT = [
    "", "Jan", "Fév", "Mars", "Avr", "Mai", "Juin",
    "Juil", "Août", "Sept", "Oct", "Nov", "Déc",
]


@dataclass
class RenderedEvent:
    organizer: str
    title: str
    booking_url: str
    available: bool
    day_num: int
    month_short: str
    weekday_short: str
    price_display: str | None
    seats_label: str | None
    seats_class: str
    search_blob: str
    # Pour tri / filtres côté JS
    date_iso: str
    price_cents: int  # 0 si inconnu — placé en fin de tri par prix
    has_price: bool
    currency: str
    seats_num: int  # -1 si inconnu, 0 si complet, N si dispo précis, 9999 si "Dispo" sans nombre
    is_weekend: bool
    # Niveaux : list de dicts {raw, canonical, remaining?, max?}
    levels: list[dict]
    canonical_levels_csv: str  # tous les canoniques de l'event (info, badges)
    bookable_levels_csv: str   # niveaux où il reste réellement des places (filtre)


def render(db_path: Path = DB_PATH, output: Path = OUTPUT) -> int:
    """Renvoie le nombre d'events actifs rendus dans le HTML."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT organizer, source_id, circuit, date, title, price_cents, currency,
                   available, booking_url, levels, raw_data
            FROM events_active
            ORDER BY date, organizer
        """).fetchall()
        count_total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    events_by_month: dict[str, list[RenderedEvent]] = defaultdict(list)
    organizers: set[str] = set()

    for r in rows:
        ev = _row_to_rendered(r)
        d = date.fromisoformat(r["date"])
        month_key = f"{MONTHS_FR_LONG[d.month].capitalize()} {d.year}"
        events_by_month[month_key].append(ev)
        organizers.add(ev.organizer)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("index.html")

    html = template.render(
        events=rows,  # used for {% if not events %}
        events_by_month=events_by_month.items(),
        organizers=sorted(organizers, key=str.lower),
        count_active=len(rows),
        count_total=count_total,
        count_organizers=len(organizers),
        generated_at=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )
    output.write_text(html, encoding="utf-8")
    return len(rows)


def _row_to_rendered(r: sqlite3.Row) -> RenderedEvent:
    d = date.fromisoformat(r["date"])
    raw = json.loads(r["raw_data"] or "{}")
    levels = json.loads(r["levels"] or "[]")

    seats_label, seats_class, seats_num = _seats_display(r, raw)
    price_display = _price_display(r["price_cents"], r["currency"])

    # Pour les events sans levels structurés, on considère "open" (tous niveaux)
    # — ils passent tous les filtres niveau côté UI.
    canonical_set: set[str] = {lv.get("canonical", "autre") for lv in levels}
    if not canonical_set:
        canonical_set = {"open"}
    canonical_csv = ",".join(sorted(canonical_set))

    # `bookable_levels_csv` : sous-ensemble des niveaux où il reste des places.
    # Règle : remaining = None (info absente) → bookable ; remaining > 0 → bookable ;
    # remaining == 0 → exclu. Un event sans aucun level → "open" implicite.
    if levels:
        bookable_set: set[str] = set()
        for lv in levels:
            rem = lv.get("remaining")
            if rem is None or (isinstance(rem, int) and rem > 0):
                bookable_set.add(lv.get("canonical", "autre"))
    else:
        bookable_set = {"open"}
    bookable_csv = ",".join(sorted(bookable_set))

    search_blob = " ".join([
        r["organizer"] or "",
        r["title"] or "",
        r["circuit"] or "",
        r["date"],
        d.strftime("%d/%m/%Y"),
        MONTHS_FR_LONG[d.month],
        " ".join(lv.get("raw", "") for lv in levels),
    ]).lower()

    return RenderedEvent(
        organizer=r["organizer"],
        title=r["title"],
        booking_url=r["booking_url"] or "",
        available=bool(r["available"]),
        day_num=d.day,
        month_short=MONTHS_FR_SHORT[d.month],
        weekday_short=WEEKDAYS_FR[d.weekday()],
        price_display=price_display,
        seats_label=seats_label,
        seats_class=seats_class,
        search_blob=search_blob,
        date_iso=r["date"],
        price_cents=int(r["price_cents"]) if r["price_cents"] is not None else 0,
        has_price=r["price_cents"] is not None,
        currency=(r["currency"] or "EUR"),
        seats_num=seats_num,
        is_weekend=d.weekday() >= 5,  # samedi=5, dimanche=6
        levels=levels,
        canonical_levels_csv=canonical_csv,
        bookable_levels_csv=bookable_csv,
    )


def _seats_display(r: sqlite3.Row, raw: dict) -> tuple[str | None, str, int]:
    """(label, css class, seats_num pour tri/stats)."""
    if not r["available"]:
        return ("Complet", "out", 0)

    remaining = raw.get("remaining_seats")
    if isinstance(remaining, int):
        if remaining <= 0:
            return ("Complet", "out", 0)
        cls = "low" if remaining < 10 else ""
        return (f"{remaining} place{'s' if remaining > 1 else ''}", cls, remaining)

    # Dispo sans nombre exact
    return ("Dispo", "", 9999)


def _price_display(price_cents: int | None, currency: str | None) -> str | None:
    if price_cents is None:
        return None
    cur = currency or "EUR"
    symbol = {"EUR": "€", "CHF": "CHF", "USD": "$"}.get(cur, cur)
    amount = price_cents / 100
    if amount == int(amount):
        return f"{int(amount)} {symbol}"
    return f"{amount:.2f} {symbol}"


if __name__ == "__main__":
    n = render()
    print(f"Rendered {n} active events to {OUTPUT}")
