# TLDR AI FR — podcast quotidien façon NotebookLM

Pipeline automatisé qui transforme chaque édition de la newsletter
[TLDR AI](https://tldr.tech/ai) en un épisode de podcast en français (dialogue
2 voix façon NotebookLM) et le publie sur Spotify via un flux RSS
iTunes-compliant.

## Architecture

```
Claude Code Routine (17h00 Europe/Paris, daily)
        │ clone repo + run python -m tldr_podcast.main
        ▼
  1. Scrape tldr.tech/ai/YYYY-MM-DD → top 6 articles
  2. NotebookLM Enterprise API : create notebook, add web sources,
     start audio overview FR (~6 min), poll, download MP3
  3. Upload MP3 → GitHub Release (tag = ep-YYYY-MM-DD)
  4. Append episode → feed/episodes.json
  5. Regenerate feed/rss.xml (feedgen + iTunes extension)
  6. git commit + push sur main
        │
        ▼
  GitHub Pages sert feed/rss.xml
  GitHub Releases sert les MP3 (URL dans <enclosure>)
        │
        ▼
  Spotify for Creators re-poll le RSS → publie l'épisode
```

Aucun serveur à maintenir. Hébergement : GitHub Releases (MP3) +
GitHub Pages (RSS). Scheduler : Claude Code Routines.

## Coûts estimés

- NotebookLM Enterprise : 9 $/licence/mois (1 suffit)
- Gemini API tokens par audio overview : ~0,05–0,15 $/épisode
- GitHub Releases + Pages : gratuit
- Spotify for Creators : gratuit
- Claude Code Routines : inclus dans le plan Pro/Max/Team
- **Total ≈ 11–14 $/mois**

## Setup manuel (one-time)

1. **Licence NotebookLM Enterprise.** Dans l'admin Google Workspace, attribue
   une licence NotebookLM Enterprise (9 $/mois) à l'utilisateur associé au
   service-account que tu vas créer ci-dessous.

2. **Service-account GCP.**
   ```bash
   gcloud iam service-accounts create tldr-podcast-bot
   gcloud projects add-iam-policy-binding $GCP_PROJECT_ID \
     --member="serviceAccount:tldr-podcast-bot@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/notebooklm.user"
   gcloud projects add-iam-policy-binding $GCP_PROJECT_ID \
     --member="serviceAccount:tldr-podcast-bot@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/aiplatform.user"
   gcloud iam service-accounts keys create sa.json \
     --iam-account="tldr-podcast-bot@$GCP_PROJECT_ID.iam.gserviceaccount.com"
   base64 -w0 sa.json > sa.json.b64   # value for GCP_SA_JSON
   ```

3. **GitHub fine-grained PAT.** Scope : repository `b-jan/nl-ai`, permissions
   `Contents: Read and write`. C'est ce même token qui crée les releases et
   pousse le commit RSS.

4. **Activer GitHub Pages.** Repo → Settings → Pages → Source : `main` branch,
   folder `/feed`. URL publique : `https://b-jan.github.io/nl-ai/rss.xml`.

5. **Cover art.** Commit dans `feed/cover.jpg` un JPEG/PNG RGB 3000×3000
   (< 500 KB).

6. **Email owner.** Hardcode ou mets dans l'env `PODCAST_OWNER_EMAIL` une
   adresse mail que tu lis (Spotify enverra un code de vérif dessus).

7. **Dry-run local** — vérifie le bout-à-bout sans rien publier :
   ```bash
   pip install -e .
   export GCP_PROJECT_ID=... GCP_LOCATION=us-central1
   export GCP_SA_JSON="$(cat sa.json.b64)"
   export GITHUB_TOKEN=... PODCAST_OWNER_EMAIL=...
   python scripts/dry_run.py --date 2026-04-17 > /tmp/preview.rss.xml
   ```
   Écoute le MP3 dans `./out/` et valide le RSS sur
   <https://castfeedvalidator.com/>.

8. **Premier run pilote** (manuel) — génère un vrai épisode sur `main` :
   ```bash
   python -m tldr_podcast.main --date 2026-04-17
   ```
   Vérifie que la release GitHub existe, que `feed/rss.xml` est servi par
   Pages, et que le RSS passe le validator.

9. **Soumission Spotify.** Sur <https://podcasters.spotify.com> → Add a podcast
   → "I already have a podcast" → coller `https://b-jan.github.io/nl-ai/rss.xml`.
   Spotify t'envoie un code à l'email `itunes:owner` pour valider. Ensuite
   chaque nouvel `<item>` est ingéré automatiquement (polling Spotify toutes
   les quelques heures ; un bouton "Refresh" force un re-poll immédiat).

10. **Configure la Routine Claude Code** via <https://claude.ai/code/routines> :
    - Schedule : `0 17 * * *` (Europe/Paris)
    - Repo : `b-jan/nl-ai` branche `main`
    - Command : `pip install -e . && python -m tldr_podcast.main`
    - Secrets : `GCP_PROJECT_ID`, `GCP_LOCATION`, `GCP_SA_JSON`,
      `GITHUB_TOKEN`, `PODCAST_OWNER_EMAIL`

## Variables d'environnement

| Variable | Obligatoire | Défaut | Rôle |
| --- | --- | --- | --- |
| `GCP_PROJECT_ID` | oui | — | project id GCP |
| `GCP_LOCATION` | non | `us-central1` | région NotebookLM |
| `GCP_SA_JSON` | oui | — | JSON service-account (base64 ou brut) |
| `GITHUB_TOKEN` | oui | — | PAT fine-grained scoped au repo |
| `GITHUB_OWNER` | non | `b-jan` | owner du repo |
| `GITHUB_REPO` | non | `nl-ai` | nom du repo |
| `GITHUB_PAGES_BASE_URL` | non | `https://b-jan.github.io/nl-ai` | base publique Pages |
| `PODCAST_TITLE` | non | `TLDR AI — Résumé quotidien` | titre du show |
| `PODCAST_AUTHOR` | non | `TLDR AI FR` | auteur iTunes |
| `PODCAST_OWNER_EMAIL` | oui | — | email verif Spotify |
| `PODCAST_DESCRIPTION` | non | voir `config.py` | description channel |
| `PODCAST_LANGUAGE` | non | `fr` | langue RSS |
| `PODCAST_CATEGORY` | non | `Technology` | catégorie iTunes |

## Structure du repo

```
/
├── pyproject.toml
├── README.md
├── src/tldr_podcast/
│   ├── main.py          # orchestration : run_daily(date)
│   ├── config.py        # pydantic settings depuis env
│   ├── tldr_scraper.py  # fetch_issue(date) -> Issue
│   ├── notebooklm.py    # create notebook, add sources, audio overview, download
│   ├── release.py       # GitHub Release + asset upload
│   ├── feed.py          # RSS iTunes-compliant
│   └── state.py         # episodes.json (idempotence)
├── feed/
│   ├── rss.xml          # servi par GitHub Pages
│   ├── episodes.json    # registre versionné
│   └── cover.jpg        # 3000×3000, à fournir
└── scripts/dry_run.py   # run local sans commit
```

## Gestion d'erreurs

- **404 TLDR AI** (week-end, jour férié US) → exit 0 propre.
- **< 3 articles extraits** → exit ≠ 0 pour logger l'anomalie.
- **Timeout polling NotebookLM** (>25 min) → raise, pas de publication partielle.
- **Idempotence** : `episodes.json` indexé par date ; rerun sur la même date est no-op.
- `feed.rebuild` = fonction pure de `episodes.json`, déterministe.
