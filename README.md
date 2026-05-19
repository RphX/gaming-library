# 🎮 Game Library Tracker

Generates a unified game library from **Steam**, **Epic Games Store**, **EA App**, **GOG Galaxy** and **Ubisoft Connect** — exported as CSV and HTML.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)

## Output

- `games_library.csv` — flat list of all games with platform, app ID, playtime, genres and HLTB completion times
- `games_library.html` — visual dashboard with platform logos, stats, genre tags, interactive filters and HLTB times

## Features

- **Genre classification** — Steam genres fetched via the Steam Store API and cached locally (`genres_cache.json`); EGS/EA/Ubisoft games are cross-referenced against the Steam catalogue
- **Completion times** — main story, extra content and completionist times from [HowLongToBeat](https://howlongtobeat.com), cached locally (`hltb_cache.json`); each time links directly to the HLTB game page
- **DLC / Demo / Soundtrack detection** — auto-tagged via the Steam `type` field; manual overrides available via `MANUAL_DLCS` in `config.py`
- **Interactive filters** — filter by platform and/or genre with a single click; counter shows how many games are currently visible
- **Cross-platform deduplication** — a game owned on multiple stores is shown once with all platform logos

## Requirements

```
pip install requests browser-cookie3 howlongtobeatpy
```

> `browser-cookie3` reads Epic Games cookies from Firefox for the order history API.  
> `howlongtobeatpy` queries the HowLongToBeat API — no account required.

## Setup

1. Copy `config.example.py` to `config.py`
2. Fill in your details (see below)
3. Run `python list_games.py`

The first run fetches genres and HLTB times (~2–3 min for ~400 games). Subsequent runs use the local cache and finish in seconds.

### config.py

| Field | Description |
|---|---|
| `STEAM_API_KEY` | [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) |
| `STEAM_ID` | [steamidfinder.com](https://www.steamidfinder.com/) |
| `EGS_MANUAL_GAMES` | Games missing from your Epic order history (free claims, gifts…) |
| `UBI_MANUAL_GAMES` | Ubisoft Connect games **without** a Steam badge in the app |
| `MANUAL_DLCS` | EGS/EA DLCs not detected automatically (not listed on Steam) |

> ⚠️ Never share or commit `config.py` — it contains your personal API key.

## What's detected automatically

| Platform | Source |
|---|---|
| Steam | Steam Web API |
| Epic Games Store | Local manifests + Firefox cookies (order history API) |
| EA App | Local install files + Windows registry |
| GOG Galaxy | `galaxy-2.0.db` SQLite database (includes playtime) |
| Ubisoft Connect | Manual list in `config.py` |

## Cache files

| File | Contents | Safe to delete? |
|---|---|---|
| `genres_cache.json` | Game genres by app ID / name | Yes — rebuilt on next run (~30 s) |
| `hltb_cache.json` | HLTB completion times + URLs | Yes — rebuilt on next run (~2–3 min) |

## Notes

- Cross-platform duplicates (e.g. a game owned on both Steam and GOG) are **merged** in the HTML output — counted once with both platform logos shown.
- Steam and GOG playtime is displayed in hours. EGS and EA playtime is not accessible via any public API.
- Ubisoft Connect games showing a **Steam badge** in the app are already counted under Steam — don't add them to `UBI_MANUAL_GAMES`.
- HLTB times show the **main story** duration in the table; hover for extra content and completionist times.
