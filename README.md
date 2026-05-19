# 🎮 Game Library Tracker

Generates a unified game library from **Steam**, **Epic Games Store**, **EA App**, **GOG Galaxy** and **Ubisoft Connect** — exported as CSV and HTML.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)

## Output

- `games_library.csv` — flat list of all games with platform, app ID and playtime
- `games_library.html` — visual dashboard with platform logos, stats and unified table

## Requirements

```
pip install requests browser-cookie3
```

> `browser-cookie3` is used to read Epic Games cookies from Firefox for the order history API.

## Setup

1. Copy `config.example.py` to `config.py`
2. Fill in your details (see below)
3. Run `python list_games.py`

### config.py

| Field | How to get it |
|---|---|
| `STEAM_API_KEY` | [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) |
| `STEAM_ID` | [steamidfinder.com](https://www.steamidfinder.com/) |
| `EGS_MANUAL_GAMES` | Games not in your Epic order history (free claims, gifts…) |
| `UBI_MANUAL_GAMES` | Ubisoft Connect games **without** a Steam badge in the app |

> ⚠️ Never share or commit `config.py` — it contains your personal API key.

## What's detected automatically

| Platform | Source |
|---|---|
| Steam | Steam Web API |
| Epic Games Store | Local manifests + Firefox cookies (order history API) |
| EA App | Local install files + Windows registry |
| GOG Galaxy | `galaxy-2.0.db` SQLite database (includes playtime) |
| Ubisoft Connect | Manual list in `config.py` |

## Notes

- Cross-platform duplicates (e.g. a game owned on both Steam and GOG) are **merged** in the HTML output — counted once with both platform logos shown.
- Steam and GOG playtime is displayed in hours. EGS and EA playtime is not accessible via any public API.
- Ubisoft Connect games showing a **Steam badge** in the app are already counted under Steam — don't add them to `UBI_MANUAL_GAMES`.
