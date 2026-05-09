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
WEEKDAYS_FR_ABBREV = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]
MONTHS_FR_LONG = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
MONTHS_FR_SHORT = [
    "", "Jan", "Fév", "Mars", "Avr", "Mai", "Juin",
    "Juil", "Août", "Sept", "Oct", "Nov", "Déc",
]

# Ordre d'affichage canonique des niveaux : du plus accessible au plus élevé.
# Permet d'afficher les badges dans le même ordre sur toutes les cards, quelle
# que soit la source (RideApp / PMMC / Spoon / etc. ont chacun leur ordre).
_LEVEL_ORDER = {
    "debutant":      1,
    "intermediaire": 2,
    "confirme":      3,
    "expert":        4,
    "open":          5,
    "side_car":      6,
    "vip":           7,
    "autre":         99,
}


@dataclass
class RenderedEvent:
    organizer: str
    title: str
    booking_url: str
    available: bool
    day_num: int
    month_short: str
    weekday_short: str
    weekday_long: str          # "Mer." pour bandeau date "Mer. 13 MAI."
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
    # Niveaux
    levels: list[dict]
    canonical_levels_csv: str
    bookable_levels_csv: str
    # Logo : URL absolue si dispo (RideApp), sinon vide (fallback initiales en CSS)
    organizer_logo_url: str
    organizer_initials: str    # 2 lettres pour le fallback ("MG", "DD", etc.)
    organizer_color: str       # couleur HSL stable pour le fallback


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
    # Tri stable par ordre canonique pour cohérence visuelle entre toutes les cards
    levels.sort(key=lambda lv: _LEVEL_ORDER.get(lv.get("canonical", "autre"), 99))

    organizer_logo_url = raw.get("organizer_logo_url") or ""
    organizer_initials = _initials_for(r["organizer"] or "")
    organizer_color = _color_for(r["organizer"] or "")

    seats_label, seats_class, seats_num = _seats_display(r, raw)
    price_display = _price_display(r["price_cents"], r["currency"])

    # Pour les events sans levels structurés, on considère "open" (tous niveaux)
    # — ils passent tous les filtres niveau côté UI.
    canonical_set: set[str] = {lv.get("canonical", "autre") for lv in levels}
    if not canonical_set:
        canonical_set = {"open"}
    canonical_csv = ",".join(sorted(canonical_set))

    # `bookable_levels_csv` : niveaux qu'on peut encore réserver.
    # Priorité des signaux par level :
    #   1. `remaining` int       (RideApp)         → bookable si > 0
    #   2. `is_in_stock` bool    (PMMC, Spoon)     → bookable si True
    #   3. ni l'un ni l'autre    (SuperLaps, etc.) → bookable (fallback prudent)
    # Event sans aucun level (DDE 34, AK Racing, etc.) → "open" implicite.
    if levels:
        bookable_set: set[str] = set()
        for lv in levels:
            canon = lv.get("canonical", "autre")
            rem = lv.get("remaining")
            in_stock = lv.get("is_in_stock")
            if rem is not None:
                if rem > 0:
                    bookable_set.add(canon)
            elif in_stock is not None:
                if in_stock:
                    bookable_set.add(canon)
            else:
                bookable_set.add(canon)
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
        weekday_long=WEEKDAYS_FR_ABBREV[d.weekday()],
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
        organizer_logo_url=organizer_logo_url,
        organizer_initials=organizer_initials,
        organizer_color=organizer_color,
    )


def _initials_for(name: str) -> str:
    """Renvoie 2 lettres uppercase pour servir d'avatar fallback."""
    if not name:
        return "?"
    parts = [p for p in name.replace("-", " ").replace("/", " ").split() if p and p[0].isalnum()]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return name[:2].upper()


def _color_for(name: str) -> str:
    """Hash → teinte HSL stable pour différencier visuellement chaque organisateur."""
    if not name:
        return "200deg"
    h = 0
    for c in name:
        h = (h * 31 + ord(c)) % 360
    return f"{h}deg"


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
