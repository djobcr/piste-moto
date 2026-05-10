# Piste Moto — agrégateur sorties Alès

Agrégateur des journées de roulage moto au Pôle Mécanique d'Alès. Scrape les organisateurs publics et stocke dans SQLite. Le front (à venir) deep-link vers le site de l'organisateur pour la résa.

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Commandes

```powershell
py crawl.py                          # crawl + upsert + regen dist/index.html
py render.py                         # regen dist/index.html depuis la DB existante
py -m unittest discover -s tests -v  # lance les tests unitaires
py -m http.server -d dist 8000       # sert le HTML sur http://localhost:8000
```

Le HTML statique est totalement autonome (CSS et JS inline). Tu peux ouvrir
`dist/index.html` directement dans le navigateur (`file://`) ou l'héberger
n'importe où.

## Déploiement (GitHub Pages, gratuit, 100% autonome)

Le workflow `.github/workflows/crawl.yml` lance un crawl toutes les heures et
publie automatiquement le HTML sur GitHub Pages. Setup en 5 étapes :

1. **Créer un repo GitHub** (public — Pages gratuit nécessite public).
   Depuis le dossier projet :
   ```powershell
   git init
   git branch -M main
   git add .
   git commit -m "Initial commit"
   gh repo create piste-moto --public --source=. --push
   # ou via l'UI GitHub : créer le repo puis git remote add + git push
   ```

2. **Activer GitHub Pages "from Actions"** dans le repo :
   `Settings → Pages → Source: GitHub Actions`.

3. **Permissions du workflow** : `Settings → Actions → General → Workflow
   permissions → Read and write` (nécessaire pour commit la DB).

4. **Lancer le premier run** : `Actions → Crawl & Deploy → Run workflow`
   (manuel) ou attendre l'heure pile (cron).

5. **Récupérer l'URL** : `Settings → Pages` affiche l'URL finale, du genre
   `https://tonpseudo.github.io/piste-moto/`.

Le cron tourne ensuite toutes les heures sans intervention. Si un scraper
échoue, le workflow continue et redéploie avec les autres sources (la DB
contient encore les events précédents). La DB est commitée à chaque run
qui change quelque chose, avec `[skip ci]` pour éviter une boucle.

## Requêtes SQL utiles

```sql
-- Tous les events futurs (vue créée par db.py)
SELECT date, organizer, title, price_cents/100.0 AS prix, currency, available, booking_url
FROM events_active ORDER BY date;

-- Events qui ont encore des places, triés par date
SELECT date, organizer, title FROM events_active WHERE available = 1 ORDER BY date;

-- Prix médian par organisateur
SELECT organizer, COUNT(*) AS n, AVG(price_cents)/100.0 AS prix_moyen
FROM events_active WHERE price_cents IS NOT NULL GROUP BY organizer;
```

## Organisateurs supportés

| Organisateur | Source | Tarif | Dispo | Statut |
|---|---|---|---|---|
| Pôle Mécanique MC (PMMC) | WooCommerce Store API | ✅ | binaire | ✅ |
| DB Sport / Denis Bouan | WooCommerce Store API | ✅ | binaire | ✅ |
| MotoClub DDE 34 | HTML + microdata schema.org | ✅ | binaire (InStock/OutOfstock) | ✅ |
| SuperLaps | HTML | ✅ "à partir de" | listé = dispo | ✅ |
| Team SLA | WooCommerce Store API | ✅ | binaire | ✅ |
| Spoon Racing | WooCommerce Store API | ✅ | binaire | ✅ |
| AK Racing | Odoo + microdata schema.org | ✅ TTC | listé = dispo | ✅ |
| MGB Moto | RideApp `/api/v1/events` | ✅ | **nb exact de places** | ✅ |
| First on Track | RideApp `/api/v1/events` | ✅ | **nb exact de places** | ✅ |
| Erdete | HTML Squarespace + Google Form | ✅ CHF | listé = dispo | ✅ |
| Accès Piste | HTML Drupal | ✅ | binaire (`block-img-epuise`) | ✅ |

## Structure

```
piste.db          # SQLite, généré
db.py             # schéma + upsert
crawl.py          # orchestrator (lance tous les scrapers)
scrapers/
  _common.py      # parser de date FR partagé
  pmmc.py         # un fichier par organisateur
  ...
```
