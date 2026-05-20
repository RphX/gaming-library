"""
Bibliothèque de jeux — Steam / EGS / EA App / GOG / Ubisoft Connect
Exporte la liste complète en CSV et HTML.

Configuration requise :
  - Copiez "config.example.py" en "config.py" et remplissez vos informations.
  - EGS / EA / GOG : détection automatique sur le poste local.
"""

import re
import json
import sqlite3
import csv
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("⚠️  Module 'requests' introuvable — Steam désactivé. Installez-le avec : pip install requests")

try:
    from howlongtobeatpy import HowLongToBeat
    HAS_HLTB = True
except ImportError:
    HAS_HLTB = False

try:
    from config import STEAM_API_KEY, STEAM_ID, EGS_MANUAL_GAMES, UBI_MANUAL_GAMES, MANUAL_DLCS
except ImportError:
    print("⚠️  Fichier config.py introuvable. Copiez config.example.py en config.py et remplissez vos informations.")
    STEAM_API_KEY = ""
    STEAM_ID      = ""
    EGS_MANUAL_GAMES = []
    UBI_MANUAL_GAMES = []
    MANUAL_DLCS = []
try:
    from config import GAME_STATUS
except ImportError:
    GAME_STATUS: dict[str, str] = {}


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
    db_candidates = _GOG_DB_PATHS
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
    db_candidates = _GOG_DB_PATHS
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
                SELECT DISTINCT lr.releaseKey, gp.value, COALESCE(gt.minutesInGame, 0)
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
            for (release_key, val, minutes) in cur.fetchall():
                try:
                    obj = json.loads(val)
                    name = obj.get("title") or obj.get("originalTitle")
                    if name:
                        hours = round(minutes / 60, 1) if minutes else ""
                        gog_id = release_key.replace("gog_", "") if release_key and release_key.startswith("gog_") else ""
                        games.append({"name": name, "platform": "GOG", "app_id": gog_id, "hours_played": hours})
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
#  GENRES  (Steam Store API + cache local)
# ═══════════════════════════════════════════════════════════
_GENRES_CACHE = Path(__file__).parent / "genres_cache.json"
_HLTB_CACHE   = Path(__file__).parent / "hltb_cache.json"
_GOG_DB_PATHS = [
    Path(r"C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db"),
    Path.home() / r"AppData\Local\GOG.com\Galaxy\storage\galaxy-2.0.db",
]


def fetch_genres(games: list[dict]) -> list[dict]:
    """Enrichit tous les jeux avec leurs genres — Steam, GOG, EGS, EA, Ubisoft (cache local)."""
    cache: dict[str, list[str]] = {}
    if _GENRES_CACHE.exists():
        try:
            cache = json.loads(_GENRES_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass

    cache_dirty = False

    # ── Steam : appdetails par app_id ──────────────────────────────────
    to_fetch_steam = [
        g for g in games
        if g["platform"] == "Steam" and g["app_id"] and g["app_id"] not in cache
    ]
    if to_fetch_steam and HAS_REQUESTS:
        print(f"   Steam : {len(to_fetch_steam)} jeux à enrichir…")

        def _fetch_steam_one(app_id: str) -> tuple[str, list[str]]:
            try:
                resp = requests.get(
                    "https://store.steampowered.com/api/appdetails",
                    params={"appids": app_id, "filters": "basic,genres", "l": "english"},
                    timeout=10,
                )
                if resp.status_code != 200:
                    return app_id, []
                entry = resp.json().get(app_id, {})
                if not entry.get("success"):
                    return app_id, []
                d = entry.get("data", {})
                genres = [gr["description"] for gr in d.get("genres", [])]
                app_type = d.get("type", "")
                if app_type == "dlc":
                    genres = ["DLC"] + genres
                elif app_type == "demo":
                    genres = ["Demo"] + genres
                elif app_type == "music":
                    genres = ["Soundtrack"] + genres
                return app_id, genres
            except Exception:
                return app_id, []

        done = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(_fetch_steam_one, g["app_id"]): g["app_id"] for g in to_fetch_steam}
            for fut in as_completed(futs):
                app_id, genres = fut.result()
                cache[app_id] = genres
                done += 1
                if done % 40 == 0:
                    print(f"     {done}/{len(to_fetch_steam)}…")
        cache_dirty = True

    # ── GOG : DB locale GOG Galaxy (instantané, 0 appel réseau) ────────
    to_fetch_gog = [
        g for g in games
        if g["platform"] == "GOG" and g["app_id"] and g["app_id"] not in cache
    ]
    if to_fetch_gog:
        db_path = next((p for p in _GOG_DB_PATHS if p.exists()), None)
        if db_path:
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                cur = conn.cursor()
                keys = [f"gog_{g['app_id']}" for g in to_fetch_gog]
                placeholders = ",".join("?" * len(keys))
                cur.execute(
                    f"SELECT gp.releaseKey, gp.value FROM GamePieces gp "
                    f"JOIN GamePieceTypes gpt ON gp.gamePieceTypeId=gpt.id "
                    f"WHERE gpt.type='meta' AND gp.releaseKey IN ({placeholders})",
                    keys,
                )
                meta_map = {row[0]: json.loads(row[1]) for row in cur.fetchall()}
                conn.close()
                for g in to_fetch_gog:
                    rk = f"gog_{g['app_id']}"
                    cache[g["app_id"]] = meta_map.get(rk, {}).get("genres", [])
                cache_dirty = True
            except Exception:
                pass

    # ── EGS / EA / Ubisoft : Steam Store Search (parallèle) ──────────
    _OTHER = ("EGS", "EA", "Ubisoft")
    to_fetch_other = [
        g for g in games
        if g["platform"] in _OTHER
        and f"name:{_normalize_name(g['name'])}" not in cache
    ]
    if to_fetch_other and HAS_REQUESTS:
        print(f"   EGS/EA/Ubisoft : {len(to_fetch_other)} jeux à enrichir…")

        def _fetch_one(name: str) -> tuple[str, list[str], str | None]:
            ck = f"name:{_normalize_name(name)}"
            try:
                time.sleep(0.25)
                r = requests.get(
                    "https://store.steampowered.com/api/storesearch/",
                    params={"term": name, "l": "en", "cc": "US"},
                    timeout=10,
                )
                if r.status_code != 200:
                    return ck, [], None
                items = r.json().get("items", [])
                # Fallback : titre court (sans sous-titre / variante française)
                if not items:
                    short = re.split(r'\s*[:–-]\s*', name, maxsplit=1)[0].strip()
                    if short and short.lower() != name.lower():
                        r_s = requests.get(
                            "https://store.steampowered.com/api/storesearch/",
                            params={"term": short, "l": "en", "cc": "US"},
                            timeout=10,
                        )
                        if r_s.status_code == 200:
                            items = r_s.json().get("items", [])
                hit = next(
                    (it for it in items if _normalize_name(it.get("name", "")) == _normalize_name(name)),
                    items[0] if items else None,
                )
                if hit:
                    aid = str(hit["id"])
                    r2 = requests.get(
                        "https://store.steampowered.com/api/appdetails",
                        params={"appids": aid, "filters": "basic,genres", "l": "english"},
                        timeout=10,
                    )
                    if r2.status_code != 200:
                        return ck, [], None
                    d2 = r2.json().get(aid, {})
                    if d2.get("success"):
                        data2 = d2.get("data", {})
                        genres = [gr["description"] for gr in data2.get("genres", [])]
                        app_type = data2.get("type", "")
                        if app_type == "dlc":
                            genres = ["DLC"] + genres
                        elif app_type == "demo":
                            genres = ["Demo"] + genres
                        elif app_type == "music":
                            genres = ["Soundtrack"] + genres
                    else:
                        genres = []
                    return ck, genres, aid
                return ck, [], None
            except Exception:
                return ck, [], None

        done = 0
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(_fetch_one, g["name"]): g["name"] for g in to_fetch_other}
            for fut in as_completed(futs):
                ck, genres, aid = fut.result()
                cache[ck] = genres
                if aid and aid not in cache:
                    cache[aid] = genres
                done += 1
                if done % 20 == 0:
                    print(f"     {done}/{len(to_fetch_other)}…")
        cache_dirty = True

    if cache_dirty:
        _GENRES_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   ✅ Cache genres mis à jour ({len(cache)} entrées)")

    # Assignation finale
    _manual_dlc_set = {_normalize_name(n) for n in MANUAL_DLCS}
    for g in games:
        if g["platform"] in ("Steam", "GOG"):
            g["genres"] = cache.get(g["app_id"], [])
        else:
            g["genres"] = cache.get(f"name:{_normalize_name(g['name'])}", [])
        # DLC manuels (non détectés via Steam)
        if _normalize_name(g["name"]) in _manual_dlc_set:
            if "DLC" not in g["genres"]:
                g["genres"] = ["DLC"] + g["genres"]
    return games


# ═══════════════════════════════════════════════════════════
#  HLTB  (HowLongToBeat — temps de complétion)
# ═══════════════════════════════════════════════════════════
def _fmt_h(h: float | None) -> str:
    """Formate un nombre d'heures HLTB en chaîne courte."""
    if h is None:
        return ""
    r = round(h, 1)
    return f"{int(r)}h" if r == int(r) else f"{r}h"


def _hltb_lookup(name: str) -> dict:
    """Recherche un jeu sur HLTB, retourne {main, extra, complete} ou {}."""
    def _try(query: str) -> dict | None:
        try:
            results = HowLongToBeat().search(query, similarity_case_sensitive=False)
            if results and results[0].similarity >= 0.5:
                r = results[0]
                return {
                    "main":     r.main_story,
                    "extra":    r.main_extra,
                    "complete": r.completionist,
                    "url":      r.game_web_link,
                }
        except Exception:
            pass
        return None

    result = _try(name)
    if result is None:
        short = re.split(r'\s*[:\u2013-]\s*', name, maxsplit=1)[0].strip()
        if short and short.lower() != name.lower():
            result = _try(short)
    return result or {}


def fetch_hltb(games: list[dict]) -> list[dict]:
    """Enrichit tous les jeux avec les temps de complétion HLTB (cache local)."""
    if not HAS_HLTB:
        print("   ⚠️  howlongtobeatpy manquant — pip install howlongtobeatpy")
        for g in games:
            g.setdefault("hours_main", None)
            g.setdefault("hours_extra", None)
            g.setdefault("hours_complete", None)
        return games

    cache: dict[str, dict] = {}
    if _HLTB_CACHE.exists():
        try:
            cache = json.loads(_HLTB_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Ne fetch que les jeux absents du cache
    to_fetch = [g for g in games if _normalize_name(g["name"]) not in cache]

    if to_fetch:
        print(f"   HLTB : {len(to_fetch)} jeux à enrichir…")

        def _worker(g: dict) -> tuple[str, dict]:
            return _normalize_name(g["name"]), _hltb_lookup(g["name"])

        done = 0
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(_worker, g): g for g in to_fetch}
            for fut in as_completed(futs):
                nk, data = fut.result()
                cache[nk] = data
                done += 1
                if done % 40 == 0:
                    print(f"     {done}/{len(to_fetch)}…")

        _HLTB_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   ✅ Cache HLTB mis à jour ({len(cache)} entrées)")

    for g in games:
        data = cache.get(_normalize_name(g["name"]), {})
        g["hours_main"]     = data.get("main")
        g["hours_extra"]    = data.get("extra")
        g["hours_complete"] = data.get("complete")
        g["hltb_url"]       = data.get("url")

    return games


# ═══════════════════════════════════════════════════════════
#  EXPORT CSV
# ═══════════════════════════════════════════════════════════
def export_csv(games: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "platform", "app_id", "hours_played", "genres", "hltb_main", "hltb_extra", "hltb_complete"])
        writer.writeheader()
        for g in games:
            writer.writerow({
                "name":          g["name"],
                "platform":      g["platform"],
                "app_id":        g["app_id"],
                "hours_played":  g["hours_played"],
                "genres":        ", ".join(g.get("genres", [])),
                "hltb_main":     _fmt_h(g.get("hours_main")),
                "hltb_extra":    _fmt_h(g.get("hours_extra")),
                "hltb_complete": _fmt_h(g.get("hours_complete")),
            })
    print(f"   ✅ {path.name}")


# ═══════════════════════════════════════════════════════════
#  EXPORT HTML
# ═══════════════════════════════════════════════════════════
_PLATFORM_COLOR = {
    "Steam":   "#66c0f4",
    "EGS":     "#0078f2",
    "EA":      "#ff4500",
    "GOG":     "#86328a",
    "Ubisoft": "#0070f3",
}

_PLATFORM_ICON = {
    "Steam":   "https://cdn.simpleicons.org/steam/66c0f4",
    "EGS":     "https://cdn.simpleicons.org/epicgames/0078f2",
    "EA":      "https://cdn.simpleicons.org/ea/ff4500",
    "GOG":     "https://cdn.simpleicons.org/gogdotcom/86328a",
    "Ubisoft": "https://cdn.simpleicons.org/ubisoft/0070f3",
}

_PLATFORM_PRIORITY = {"Steam": 0, "GOG": 1, "EGS": 2, "EA": 3, "Ubisoft": 4}

_STATUS_ORDER = ["a-faire", "en-cours", "termine", "non-commence"]
_STATUS_LABEL = {
    "a-faire":      "À faire",
    "non-commence": "Non commencé",
    "en-cours":     "En cours",
    "termine":      "Terminé",
}
_STATUS_COLOR = {
    "a-faire":      "#f59e0b",
    "non-commence": "#6b7280",
    "en-cours":     "#3b82f6",
    "termine":      "#10b981",
}


def _game_status(g: dict) -> str:
    """Retourne le statut de progression : override manuel en priorité, sinon auto sur le temps de jeu."""
    norm = _normalize_name(g["name"])
    for oname, ostatus in GAME_STATUS.items():
        if _normalize_name(oname) == norm:
            return ostatus
    hrs = g.get("hours_played")
    if hrs == "" or hrs == 0 or hrs is None:
        return "non-commence"
    return "en-cours"


def export_html(games: list[dict], path: Path) -> None:
    # ── Compte par plateforme ──────────────────────────────
    by_platform: dict[str, list[dict]] = {}
    for g in games:
        by_platform.setdefault(g["platform"], []).append(g)

    # ── Fusion cross-plateforme par nom normalisé ─────────
    merged: dict[str, dict] = {}
    for g in sorted(games, key=lambda x: _PLATFORM_PRIORITY.get(x["platform"], 99)):
        key = _normalize_name(g["name"])
        if key not in merged:
            merged[key] = {"name": g["name"], "platforms": [], "hours_played": "", "genres": [],
                           "hours_main": None, "hours_extra": None, "hours_complete": None, "hltb_url": None}
        m = merged[key]
        if g["platform"] not in m["platforms"]:
            m["platforms"].append(g["platform"])
        if m["hours_played"] == "" and g["hours_played"] != "":
            m["hours_played"] = g["hours_played"]
        if m["hours_main"] is None and g.get("hours_main") is not None:
            m["hours_main"]     = g["hours_main"]
            m["hours_extra"]    = g.get("hours_extra")
            m["hours_complete"] = g.get("hours_complete")
            m["hltb_url"]       = g.get("hltb_url")
        for genre in g.get("genres", []):
            if genre not in m["genres"]:
                m["genres"].append(genre)

    unified = sorted(merged.values(), key=lambda x: x["name"].lower())
    for g in unified:
        g["status"] = _game_status(g)
    total = len(unified)
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")

    def _slug(s: str) -> str:
        return re.sub(r'[^a-z0-9]', '-', s.lower()).strip('-')

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

    # ── Filtres plateforme ─────────────────────────────────
    plat_btns = '<button class="fbtn active" data-type="plat" data-val="all">Toutes</button>\n'
    for platform in ("Steam", "EGS", "EA", "GOG", "Ubisoft"):
        icon = _PLATFORM_ICON[platform]
        plat_btns += (
            f'<button class="fbtn" data-type="plat" data-val="{platform.lower()}">'
            f'<img src="{icon}" alt="" height="11" style="vertical-align:middle;margin-right:4px">'
            f'{platform}</button>\n'
        )

    # ── Filtres genre (triés par fréquence) ───────────────
    genre_count: dict[str, int] = {}
    for g in unified:
        for genre in g.get("genres", []):
            genre_count[genre] = genre_count.get(genre, 0) + 1
    all_genres = sorted(genre_count, key=lambda g: -genre_count[g])

    genre_btns = '<button class="fbtn active" data-type="genre" data-val="all">Tous</button>\n'
    for genre in all_genres:
        genre_btns += (
            f'<button class="fbtn" data-type="genre" data-val="{_slug(genre)}">'
            f'{genre} <span class="gcnt">{genre_count[genre]}</span>'
            f'</button>\n'
        )

    # ── Filtres statut ─────────────────────────────────────
    status_count: dict[str, int] = {}
    for g in unified:
        status_count[g["status"]] = status_count.get(g["status"], 0) + 1
    status_btns = '<button class="fbtn active" data-type="status" data-val="all">Tous</button>\n'
    for s in _STATUS_ORDER:
        cnt = status_count.get(s, 0)
        if cnt == 0:
            continue
        color = _STATUS_COLOR[s]
        label = _STATUS_LABEL[s]
        status_btns += (
            f'<button class="fbtn" data-type="status" data-val="{s}">'
            f'<span style="color:{color}">●</span> {label} '
            f'<span class="gcnt">{cnt}</span>'
            f'</button>\n'
        )

    # ── Lignes du tableau ──────────────────────────────────
    rows = ""
    for g in unified:
        logos = "".join(
            f'<img src="{_PLATFORM_ICON.get(p,"")}" alt="{p}" height="16" title="{p}">'
            for p in g["platforms"]
        )
        hrs = g["hours_played"]
        hrs_td = f'<td class="hrs">{hrs}h</td>' if hrs != "" else '<td class="hrs"></td>'
        genres = g.get("genres", [])
        tags = "".join(f'<span class="tag">{genre}</span>' for genre in genres)
        name_cell = g["name"] + (f'<div class="tags">{tags}</div>' if tags else "")
        plat_data  = " ".join(p.lower() for p in g["platforms"])
        genre_data = " ".join(_slug(genre) for genre in genres)
        # HLTB
        hm, he, hc = g.get("hours_main"), g.get("hours_extra"), g.get("hours_complete")
        hurl = g.get("hltb_url") or ""
        if hm is not None:
            parts = [f"Extra\u00a0{_fmt_h(he)}" if he else "", f"100\u00a0%\u00a0{_fmt_h(hc)}" if hc else ""]
            tooltip = " | ".join(p for p in parts if p)
            label = _fmt_h(hm)
            inner = f'<a href="{hurl}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">{label}</a>' if hurl else label
            hltb_td = f'<td class="hltb" title="{tooltip}">{inner}</td>'
        else:
            hltb_td = '<td class="hltb"></td>'
        status = g.get("status", "non-commence")
        gkey   = _normalize_name(g["name"])
        opts   = "".join(
            f'<option value="{s}"{" selected" if s == status else ""}>{_STATUS_LABEL[s]}</option>'
            for s in _STATUS_ORDER
        )
        status_td = (
            f'<td class="sstat">'
            f'<select class="ssel" data-game="{gkey}" data-default="{status}">'
            f'{opts}'
            f'</select>'
            f'</td>'
        )
        rows += (
            f"<tr data-plats='{plat_data}' data-genres='{genre_data}' data-status='{status}'>"
            f"<td class='plat'>{logos}</td><td>{name_cell}</td>{hrs_td}{hltb_td}{status_td}</tr>\n"
        )

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Ma bibliothèque de jeux</title>
<style>
  * {{ box-sizing: border-box; }}
  body  {{ font-family: 'Segoe UI', Arial, sans-serif; background:#111827; color:#e5e7eb;
           max-width:960px; margin:0 auto; padding:20px; }}
  h1    {{ text-align:center; color:#f9fafb; margin-bottom:4px; }}
  .ts   {{ text-align:center; color:#6b7280; font-size:.85em; margin-bottom:20px; }}
  .stats {{ display:flex; gap:16px; flex-wrap:wrap; justify-content:center;
             background:#1f2937; border-radius:10px; padding:16px; margin-bottom:16px; }}
  .stat  {{ text-align:center; border:2px solid #374151; border-radius:8px;
             padding:12px 24px; min-width:80px; display:flex; flex-direction:column;
             align-items:center; gap:6px; }}
  .num   {{ font-size:1.8em; font-weight:bold; color:#f9fafb; }}
  .lbl   {{ color:#9ca3af; font-size:.8em; }}
  .filters {{ background:#1f2937; border-radius:10px; padding:14px 16px;
               margin-bottom:12px; display:flex; flex-direction:column; gap:10px; }}
  .filter-row {{ display:flex; gap:6px; flex-wrap:wrap; align-items:center; }}
  .filter-label {{ color:#6b7280; font-size:.8em; width:74px; flex-shrink:0; }}
  .fbtn {{ background:#374151; color:#d1d5db; border:none; border-radius:20px;
            padding:4px 12px; font-size:.8em; cursor:pointer;
            transition:all .15s; white-space:nowrap; }}
  .fbtn:hover {{ background:#4b5563; }}
  .fbtn.active {{ background:#4f46e5; color:#fff; }}
  .gcnt {{ color:#9ca3af; font-size:.85em; }}
  .fbtn.active .gcnt {{ color:#c7d2fe; }}
  .count-info {{ text-align:right; color:#6b7280; font-size:.85em; margin-bottom:8px; }}
  table  {{ width:100%; border-collapse:collapse; font-size:.9em; }}
  thead th {{ background:#1f2937; padding:8px 12px; text-align:left;
              border-bottom:2px solid #374151; color:#9ca3af; font-weight:600; }}
  thead th.hrs {{ text-align:right; }}
  tbody td {{ padding:7px 12px; border-bottom:1px solid #1f2937; vertical-align:top; }}
  tbody tr:hover {{ background:#1f2937; }}
  td.plat {{ width:56px; white-space:nowrap; padding-top:9px; }}
  td.plat img {{ vertical-align:middle; margin-right:3px; }}
  td.hrs  {{ text-align:right; color:#6b7280; font-size:.85em; width:72px; padding-top:9px; }}
  td.hltb {{ text-align:right; color:#34d399; font-size:.82em; width:68px; padding-top:9px; cursor:default; }}
  thead th.hltb {{ text-align:right; }}
  td.sstat  {{ width:128px; padding:5px 8px; vertical-align:middle; }}
  thead th.sstat {{ width:128px; }}
  .ssel {{ width:100%; background:#1f2937; color:#d1d5db; border:1px solid #374151;
           border-radius:6px; padding:4px 8px; font-size:.78em; cursor:pointer;
           appearance:none; -webkit-appearance:none; transition:border-color .15s; }}
  .ssel:focus {{ outline:none; border-color:#4f46e5; }}
  .tags {{ display:flex; gap:4px; flex-wrap:wrap; margin-top:4px; }}
  .tag  {{ background:#1e3a5f; color:#93c5fd; font-size:.72em; padding:2px 8px;
            border-radius:10px; }}
</style>
</head>
<body>
<h1>🎮 Ma bibliothèque de jeux</h1>
<p class="ts">Généré le {ts}</p>
<div class="stats">
{stats_html}
</div>
<div class="filters">
  <div class="filter-row">
    <span class="filter-label">Plateforme</span>
    {plat_btns}
  </div>
  <div class="filter-row">
    <span class="filter-label">Genre</span>
    {genre_btns}
  </div>
  <div class="filter-row">
    <span class="filter-label">Statut</span>
    {status_btns}
    <button id="export-btn" class="fbtn" style="margin-left:auto;border:1px solid #374151">📋 Exporter config</button>
  </div>
</div>
<p class="count-info"><span id="vis-count">{total}</span> / {total} jeux</p>
<table>
  <thead><tr><th></th><th>Jeu</th><th class="hrs">Joué</th><th class="hltb" title="Temps principal (HowLongToBeat)">HLTB</th><th class="sstat">Statut</th></tr></thead>
  <tbody>
{rows}  </tbody>
</table>
<script>
(function() {{
  const LS  = 'gl_status_';
  const COL = {{'a-faire':'#f59e0b','non-commence':'#6b7280','en-cours':'#3b82f6','termine':'#10b981'}};

  function applyColor(sel) {{
    const c = COL[sel.value] || '#6b7280';
    sel.style.color = c;
    sel.style.borderColor = c + '66';
  }}

  // Initialise les selects depuis localStorage et branche les events
  document.querySelectorAll('.ssel').forEach(sel => {{
    const saved = localStorage.getItem(LS + sel.dataset.game);
    if (saved) {{
      sel.value = saved;
      sel.closest('tr').dataset.status = saved;
    }}
    applyColor(sel);
    sel.addEventListener('change', () => {{
      const val = sel.value;
      localStorage.setItem(LS + sel.dataset.game, val);
      sel.closest('tr').dataset.status = val;
      applyColor(sel);
      filterRows();
    }});
  }});

  // Bouton export → copie GAME_STATUS Python dans le presse-papiers
  document.getElementById('export-btn').addEventListener('click', () => {{
    const lines = [];
    document.querySelectorAll('.ssel').forEach(sel => {{
      const val = sel.value;
      const name = sel.closest('tr').querySelector('td:nth-child(2)').childNodes[0].textContent.trim();
      lines.push('    ' + JSON.stringify(name) + ': "' + val + '",');
    }});
    const txt = 'GAME_STATUS: dict[str, str] = {{\\n' + lines.join('\\n') + '\\n}}';
    const btn = document.getElementById('export-btn');
    (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject())
      .then(() => {{
        btn.textContent = '✅ Copié !';
        setTimeout(() => {{ btn.textContent = '📋 Exporter config'; }}, 2000);
      }})
      .catch(() => {{ prompt('Copiez ce texte :', txt); }});
  }});

  let activePlat   = 'all';
  let activeGenre  = 'all';
  let activeStatus = 'all';

  function filterRows() {{
    let visible = 0;
    document.querySelectorAll('tbody tr').forEach(row => {{
      const plats  = (row.dataset.plats  || '').split(' ');
      const genres = (row.dataset.genres || '').split(' ');
      const status =  row.dataset.status || '';
      const ok = (activePlat   === 'all' || plats.includes(activePlat)) &&
                 (activeGenre  === 'all' || genres.includes(activeGenre)) &&
                 (activeStatus === 'all' || status === activeStatus);
      row.style.display = ok ? '' : 'none';
      if (ok) visible++;
    }});
    document.getElementById('vis-count').textContent = visible;
  }}

  document.querySelectorAll('.fbtn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      const type = btn.dataset.type;
      const val  = btn.dataset.val;
      if (!type) return;
      if (type === 'plat') {{
        activePlat  = (activePlat  === val || val === 'all') ? 'all' : val;
        document.querySelectorAll('.fbtn[data-type="plat"]').forEach(b =>
          b.classList.toggle('active', activePlat === 'all' ? b.dataset.val === 'all' : b.dataset.val === activePlat)
        );
      }} else if (type === 'genre') {{
        activeGenre = (activeGenre === val || val === 'all') ? 'all' : val;
        document.querySelectorAll('.fbtn[data-type="genre"]').forEach(b =>
          b.classList.toggle('active', activeGenre === 'all' ? b.dataset.val === 'all' : b.dataset.val === activeGenre)
        );
      }} else {{
        activeStatus = (activeStatus === val || val === 'all') ? 'all' : val;
        document.querySelectorAll('.fbtn[data-type="status"]').forEach(b =>
          b.classList.toggle('active', activeStatus === 'all' ? b.dataset.val === 'all' : b.dataset.val === activeStatus)
        );
      }}
      filterRows();
    }});
  }});
}})();
</script>
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

    print("🏷️  Genres Steam…")
    all_games = fetch_genres(all_games)

    print("⏱️  HowLongToBeat…")
    all_games = fetch_hltb(all_games)

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
