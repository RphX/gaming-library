"""
Bibliothèque de jeux — Steam / EGS / EA App / GOG / Ubisoft Connect
Exporte la liste complète en CSV et HTML.

Configuration requise :
  - Copiez "config.example.py" en "config.py" et remplissez vos informations.
  - EGS / EA / GOG : détection automatique sur le poste local.
"""

import os
import re
import json
import glob
import sqlite3
import csv
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("⚠️  Module 'requests' introuvable — Steam désactivé. Installez-le avec : pip install requests")

try:
    from config import STEAM_API_KEY, STEAM_ID, EGS_MANUAL_GAMES, UBI_MANUAL_GAMES
except ImportError:
    print("⚠️  Fichier config.py introuvable. Copiez config.example.py en config.py et remplissez vos informations.")
    STEAM_API_KEY = ""
    STEAM_ID      = ""
    EGS_MANUAL_GAMES = []
    UBI_MANUAL_GAMES = []


def _normalize_name(s: str) -> str:
    """Normalise un nom pour la déduplication (™ ® apostrophes…)"""
    s = s.lower()
    s = re.sub(r'[™®©]', '', s)
    s = s.replace('\u2019', "'").replace('\u2018', "'")
    s = re.sub(r'\s+', ' ', s).strip()
    return s


# ═══════════════════════════════════════════════════════════
#  STEAM
# ═══════════════════════════════════════════════════════════
def get_steam_games() -> list[dict]:
    if not HAS_REQUESTS:
        return []
    if not STEAM_API_KEY or not STEAM_ID:
        print("   ⚠️  STEAM_API_KEY ou STEAM_ID non renseigné — Steam ignoré.")
        return []

    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"
    params = {
        "key": STEAM_API_KEY,
        "steamid": STEAM_ID,
        "include_appinfo": 1,
        "include_played_free_games": 1,
        "format": "json",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        games_raw = resp.json().get("response", {}).get("games", [])
    except Exception as e:
        print(f"   ⚠️  Steam API : {e}")
        return []

    return sorted(
        [
            {
                "name": g.get("name", "?"),
                "platform": "Steam",
                "app_id": str(g.get("appid", "")),
                "hours_played": round(g.get("playtime_forever", 0) / 60, 1),
            }
            for g in games_raw
        ],
        key=lambda x: x["name"].lower(),
    )


# ═══════════════════════════════════════════════════════════
#  EPIC GAMES STORE
# ═══════════════════════════════════════════════════════════

def _get_egs_api_games() -> list[str]:
    """Récupère tout l'historique d'achats EGS via les cookies Firefox."""
    if not HAS_REQUESTS:
        return []
    try:
        import browser_cookie3
    except ImportError:
        print("   ⚠️  browser-cookie3 non installé : pip install browser-cookie3")
        return []

    try:
        cj = browser_cookie3.firefox(domain_name=".epicgames.com")
    except Exception as e:
        print(f"   ⚠️  Cookies Firefox Epic Games inaccessibles : {e}")
        return []

    session = requests.Session()
    session.cookies.update(cj)
    # En-têtes pour simuler une requête navigateur
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.epicgames.com/account/transactions",
    })

    games: list[str] = []
    next_token = ""
    while True:
        try:
            resp = session.get(
                "https://www.epicgames.com/account/v2/payment/ajaxGetOrderHistory",
                params={"sortDir": "DESC", "sortBy": "DATE",
                        "nextPageToken": next_token, "locale": "fr"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"   ⚠️  EGS API : {e}")
            break

        for order in data.get("orders", []):
            for item in order.get("items", []):
                desc = item.get("description", "").strip()
                if desc:
                    games.append(desc)

        next_token = data.get("nextPageToken", "")
        if not next_token:
            break

    return games


def get_egs_games() -> list[dict]:
    manifest_dir = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests")
    if not manifest_dir.exists():
        print("   ⚠️  EGS : dossier Manifests introuvable (jeux installés non détectés).")

    games: list[dict] = []
    seen_norm: set[str] = set()   # clés normalisées pour déduplication

    def _add(name: str, app_id: str = "") -> None:
        key = _normalize_name(name)
        if key not in seen_norm:
            seen_norm.add(key)
            games.append({"name": name, "platform": "EGS", "app_id": app_id, "hours_played": ""})

    # ── 1. Manifests locaux (jeux installés) ──────────────
    for item in manifest_dir.glob("*.item") if manifest_dir.exists() else []:
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
            name = data.get("DisplayName") or data.get("AppName", "")
            app_name = data.get("AppName", "")
            if name and not app_name.startswith(("UE_", "UEPrereqPack")):
                _add(name, app_name)
        except Exception:
            pass

    # ── 2. API Epic Games (cookies Firefox) ───────────────
    api_games = _get_egs_api_games()
    for name in api_games:
        _add(name)
    if api_games:
        print(f"   ✔  API Epic (Firefox) : {len(api_games)} entrées")

    # ── 3. GOG Galaxy (si EGS intégré) ────────────────────
    db_candidates = [
        Path(r"C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db"),
        Path.home() / r"AppData\Local\GOG.com\Galaxy\storage\galaxy-2.0.db",
    ]
    db_path = next((p for p in db_candidates if p.exists()), None)
    if db_path:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT gp.value
                FROM GamePieces gp
                JOIN GamePieceTypes gpt ON gp.gamePieceTypeId = gpt.id
                WHERE gpt.type = 'originalTitle'
                  AND gp.releaseKey LIKE 'epicgames_%'
            """)
            for (val,) in cur.fetchall():
                try:
                    obj = json.loads(val)
                    name = obj.get("title") or obj.get("originalTitle")
                    if name:
                        _add(name)
                except Exception:
                    pass
            conn.close()
        except Exception:
            pass

    # ── 4. Liste manuelle (jeux absents de toutes les sources) ─
    for name in EGS_MANUAL_GAMES:
        _add(name)

    return sorted(games, key=lambda x: x["name"].lower())


# ═══════════════════════════════════════════════════════════
#  EA APP  (installés localement + registre Windows)
# ═══════════════════════════════════════════════════════════
_EA_NOISE = (
    "Redistribut", "DirectX", "Visual C++", "Runtime",
    "Microsoft", "PhysX", ".NET", "OpenAL",
)

def _is_ea_noise(name: str) -> bool:
    return any(kw.lower() in name.lower() for kw in _EA_NOISE)

def get_ea_games() -> list[dict]:
    games: list[dict] = []
    seen: set[str] = set()

    # ── 1. Fichiers d'installation EA App ──────────────────
    ea_install_roots = [
        Path(r"C:\ProgramData\Electronic Arts\EA Desktop\InstallData"),
        Path(r"C:\Program Files\EA Games"),
        Path(r"C:\Program Files (x86)\EA Games"),
    ]
    for root in ea_install_roots:
        if not root.exists():
            continue
        for xml_file in root.rglob("installerdata.xml"):
            try:
                tree = ET.parse(xml_file)
                for tag in ("gameName", "name", "title"):
                    node = tree.getroot().find(f".//{tag}")
                    if node is not None and node.text:
                        name = node.text.strip()
                        if name and name not in seen and not _is_ea_noise(name):
                            seen.add(name)
                            games.append({"name": name, "platform": "EA", "app_id": "", "hours_played": ""})
                        break
            except Exception:
                pass

    # ── 2. Registre Windows ────────────────────────────────
    try:
        import winreg
        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, reg_path in reg_paths:
            try:
                key = winreg.OpenKey(hive, reg_path)
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub_name = winreg.EnumKey(key, i)
                    sub = winreg.OpenKey(key, sub_name)
                    try:
                        publisher = winreg.QueryValueEx(sub, "Publisher")[0]
                        if not any(ea in publisher for ea in ("Electronic Arts", "EA Games", "EA Swiss")):
                            continue
                        display_name = winreg.QueryValueEx(sub, "DisplayName")[0]
                        if display_name and display_name not in seen and not _is_ea_noise(display_name):
                            seen.add(display_name)
                            games.append({
                                "name": display_name,
                                "platform": "EA",
                                "app_id": "",
                                "hours_played": "",
                            })
                    except FileNotFoundError:
                        pass
                    winreg.CloseKey(sub)
                except Exception:
                    pass
            winreg.CloseKey(key)
    except ImportError:
        pass

    if not games:
        print("   ⚠️  EA App : aucun jeu détecté (EA App non installé ou bibliothèque vide).")

    return sorted(games, key=lambda x: x["name"].lower())


# ═══════════════════════════════════════════════════════════
#  GOG GALAXY
# ═══════════════════════════════════════════════════════════
def get_gog_games() -> list[dict]:
    db_candidates = [
        Path(r"C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db"),
        Path.home() / r"AppData\Local\GOG.com\Galaxy\storage\galaxy-2.0.db",
    ]
    db_path = next((p for p in db_candidates if p.exists()), None)
    if db_path is None:
        print("   ⚠️  GOG Galaxy : base de données introuvable.")
        return []

    games: list[dict] = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()

        # Jeux GOG possédés, sans DLC ni extras
        try:
            cur.execute("""
                SELECT DISTINCT gp.value, COALESCE(gt.minutesInGame, 0)
                FROM LibraryReleases lr
                JOIN LicensedReleases lic ON lr.id = lic.libraryId
                JOIN GamePieces gp ON gp.releaseKey = lr.releaseKey
                JOIN GamePieceTypes gpt ON gp.gamePieceTypeId = gpt.id
                LEFT JOIN ReleaseProperties rp ON rp.releaseKey = lr.releaseKey
                LEFT JOIN GameTimes gt ON gt.releaseKey = lr.releaseKey
                WHERE lic.isOwned = 1
                  AND gpt.type = 'originalTitle'
                  AND lr.releaseKey LIKE 'gog_%'
                  AND (rp.isDlc = 0 OR rp.isDlc IS NULL)
                  AND (rp.isVisibleInLibrary = 1 OR rp.isVisibleInLibrary IS NULL)
            """)
            for (val, minutes) in cur.fetchall():
                try:
                    obj = json.loads(val)
                    name = obj.get("title") or obj.get("originalTitle")
                    if name:
                        hours = round(minutes / 60, 1) if minutes else ""
                        games.append({"name": name, "platform": "GOG", "app_id": "", "hours_played": hours})
                except Exception:
                    pass
        except Exception:
            pass

        # Fallback : table Products (ancienne structure)
        if not games:
            try:
                cur.execute("SELECT title FROM Products WHERE isOwned = 1")
                for (name,) in cur.fetchall():
                    if name:
                        games.append({"name": name, "platform": "GOG", "app_id": "", "hours_played": ""})
            except Exception:
                pass

        conn.close()
    except Exception as e:
        print(f"   ⚠️  GOG Galaxy : erreur lecture BDD — {e}")

    if not games:
        print("   ⚠️  GOG Galaxy : aucun jeu trouvé dans la BDD.")

    return sorted(games, key=lambda x: x["name"].lower())


# ═══════════════════════════════════════════════════════════
#  UBISOFT CONNECT
# ═══════════════════════════════════════════════════════════
def get_ubisoft_games() -> list[dict]:
    """Retourne les jeux Ubisoft Connect natifs (hors jeux Steam liés)."""
    games = []
    for name in UBI_MANUAL_GAMES:
        games.append({"platform": "Ubisoft", "name": name, "app_id": "", "hours_played": ""})
    return games


# ═══════════════════════════════════════════════════════════
#  EXPORT CSV
# ═══════════════════════════════════════════════════════════
def export_csv(games: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "platform", "app_id", "hours_played"])
        writer.writeheader()
        writer.writerows(games)
    print(f"   ✅ {path.name}")


# ═══════════════════════════════════════════════════════════
#  EXPORT HTML
# ═══════════════════════════════════════════════════════════
_PLATFORM_COLOR = {
    "Steam":   "#66c0f4",
    "EGS":     "#c97bf7",
    "EA":      "#ff6a3d",
    "GOG":     "#7b68ee",
    "Ubisoft": "#0070f3",
}

_PLATFORM_ICON = {
    "Steam":   "https://cdn.simpleicons.org/steam/66c0f4",
    "EGS":     "https://cdn.simpleicons.org/epicgames/c97bf7",
    "EA":      "https://cdn.simpleicons.org/ea/ff6a3d",
    "GOG":     "https://cdn.simpleicons.org/gogdotcom/7b68ee",
    "Ubisoft": "https://cdn.simpleicons.org/ubisoft/0070f3",
}

_PLATFORM_PRIORITY = {"Steam": 0, "GOG": 1, "EGS": 2, "EA": 3, "Ubisoft": 4}


def export_html(games: list[dict], path: Path) -> None:
    # Compte par plateforme (pour la barre de stats)
    by_platform: dict[str, list[dict]] = {}
    for g in games:
        by_platform.setdefault(g["platform"], []).append(g)

    # Fusion cross-plateforme par nom normalisé (Steam en priorité)
    merged: dict[str, dict] = {}
    for g in sorted(games, key=lambda x: _PLATFORM_PRIORITY.get(x["platform"], 99)):
        key = _normalize_name(g["name"])
        if key not in merged:
            merged[key] = {"name": g["name"], "platforms": [], "hours_played": ""}
        m = merged[key]
        if g["platform"] not in m["platforms"]:
            m["platforms"].append(g["platform"])
        if m["hours_played"] == "" and g["hours_played"] != "":
            m["hours_played"] = g["hours_played"]

    unified = sorted(merged.values(), key=lambda x: x["name"].lower())
    total = len(unified)
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")

    # ── Barre de stats ─────────────────────────────────────
    stats_html = (
        f'<div class="stat">'
        f'<div class="num">{total}</div>'
        f'<div class="lbl">Total</div>'
        f'</div>\n'
    )
    for platform in ("Steam", "EGS", "EA", "GOG", "Ubisoft"):
        count = len(by_platform.get(platform, []))
        color = _PLATFORM_COLOR[platform]
        icon  = _PLATFORM_ICON[platform]
        stats_html += (
            f'<div class="stat" style="border-color:{color}">'
            f'<img src="{icon}" alt="{platform}" height="32" title="{platform}">'
            f'<div class="num" style="color:{color}">{count}</div>'
            f'</div>\n'
        )

    # ── Tableau unifié ─────────────────────────────────────
    rows = ""
    for g in unified:
        logos = "".join(
            f'<img src="{_PLATFORM_ICON.get(p,"")}" alt="{p}" height="16" title="{p}">'
            for p in g["platforms"]
        )
        hrs = g["hours_played"]
        hrs_td = f'<td class="hrs">{hrs}h</td>' if hrs != "" else '<td class="hrs"></td>'
        rows += f"<tr><td class='plat'>{logos}</td><td>{g['name']}</td>{hrs_td}</tr>\n"

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Ma bibliothèque de jeux</title>
<style>
  * {{ box-sizing: border-box; }}
  body  {{ font-family: 'Segoe UI', Arial, sans-serif; background:#111827; color:#e5e7eb;
           max-width:900px; margin:0 auto; padding:20px; }}
  h1    {{ text-align:center; color:#f9fafb; margin-bottom:4px; }}
  .ts   {{ text-align:center; color:#6b7280; font-size:.85em; margin-bottom:20px; }}
  .stats {{ display:flex; gap:16px; flex-wrap:wrap; justify-content:center;
             background:#1f2937; border-radius:10px; padding:16px; margin-bottom:30px; }}
  .stat  {{ text-align:center; border:2px solid #374151; border-radius:8px;
             padding:12px 24px; min-width:80px; display:flex; flex-direction:column;
             align-items:center; gap:6px; }}
  .num   {{ font-size:1.8em; font-weight:bold; color:#f9fafb; }}
  .lbl   {{ color:#9ca3af; font-size:.8em; }}
  table  {{ width:100%; border-collapse:collapse; font-size:.9em; }}
  thead th {{ background:#1f2937; padding:8px 12px; text-align:left;
              border-bottom:2px solid #374151; color:#9ca3af; font-weight:600; }}
  thead th.hrs {{ text-align:right; }}
  tbody td {{ padding:7px 12px; border-bottom:1px solid #1f2937; }}
  tbody tr:hover {{ background:#1f2937; }}
  td.plat {{ width:56px; white-space:nowrap; }}
  td.plat img {{ vertical-align:middle; margin-right:3px; }}
  td.hrs  {{ text-align:right; color:#6b7280; font-size:.85em; width:72px; }}
</style>
</head>
<body>
<h1>🎮 Ma bibliothèque de jeux</h1>
<p class="ts">Généré le {ts}</p>
<div class="stats">
{stats_html}
</div>
<table>
  <thead><tr><th></th><th>Jeu</th><th class="hrs">Heures</th></tr></thead>
  <tbody>
{rows}  </tbody>
</table>
</body>
</html>"""

    path.write_text(html, encoding="utf-8")
    print(f"   ✅ {path.name}")


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════
def main() -> None:
    print("🎮 Récupération de votre bibliothèque de jeux…\n")

    all_games: list[dict] = []

    print("📦 Steam…")
    s = get_steam_games()
    all_games.extend(s)
    print(f"   → {len(s)} jeux")

    print("📦 Epic Games Store…")
    e = get_egs_games()
    all_games.extend(e)
    print(f"   → {len(e)} jeux")

    print("📦 EA App…")
    ea = get_ea_games()
    all_games.extend(ea)
    print(f"   → {len(ea)} jeux")

    print("📦 GOG Galaxy…")
    g = get_gog_games()
    all_games.extend(g)
    print(f"   → {len(g)} jeux")

    print("📦 Ubisoft Connect…")
    u = get_ubisoft_games()
    all_games.extend(u)
    print(f"   → {len(u)} jeux")

    print(f"\n🎮 Total : {len(all_games)} jeux\n")

    if not all_games:
        print("Aucun jeu trouvé. Vérifiez la configuration.")
        return

    out_dir = Path(__file__).parent

    print("💾 Export…")
    export_csv(all_games, out_dir / "games_library.csv")
    export_html(all_games, out_dir / "games_library.html")

    print("\nTerminé !")


if __name__ == "__main__":
    main()
