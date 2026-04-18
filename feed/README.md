# `feed/` — podcast hosting root

Ce dossier est la racine publique servie par GitHub Pages
(`https://b-jan.github.io/nl-ai/`).

- `rss.xml` — flux RSS iTunes-compliant, régénéré à chaque épisode.
- `episodes.json` — registre versionné des épisodes publiés (idempotence).
- `cover.jpg` — cover art 3000×3000, à fournir manuellement une fois.

Ne pas éditer `rss.xml` / `episodes.json` à la main : ils sont réécrits par
`src/tldr_podcast/main.py` et doivent rester déterministes.
