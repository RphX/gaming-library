# ══════════════════════════════════════════════════════════════════
#  CONFIGURATION — Copiez ce fichier en "config.py" et remplissez
# ══════════════════════════════════════════════════════════════════

# ── Steam ─────────────────────────────────────────────────────────
# Clé API  → https://steamcommunity.com/dev/apikey
# SteamID  → https://www.steamidfinder.com/
STEAM_API_KEY = ""
STEAM_ID      = ""

# ── Epic Games Store ──────────────────────────────────────────────
# Jeux absents de l'historique d'achats (F2P, cadeaux, etc.)
# Assurez-vous d'être connecté à epicgames.com dans Firefox.
EGS_MANUAL_GAMES: list[str] = [
    # "Fortnite",
    # "Rocket League",
]

# ── Ubisoft Connect ───────────────────────────────────────────────
# Jeux sans badge "Steam" dans Ubisoft Connect → onglet Jeux.
# (Les jeux avec badge Steam sont déjà comptés dans Steam.)
UBI_MANUAL_GAMES: list[str] = [
    # "Assassin's Creed™",
    # "Rayman Origins",
]
