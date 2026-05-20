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

# ── DLC manuels ───────────────────────────────────────────────────
# Noms exacts de DLC/extensions non détectés automatiquement via Steam.
# Ces entrées recevront le tag « DLC » dans l'export.
MANUAL_DLCS: list[str] = [
    # "Borderlands 3 Bounty of Blood",
]

# ── Statut de progression ─────────────────────────────────────────
# Forcer le statut d'un jeu. Valeurs possibles :
#   "a-faire"      → dans votre liste à faire (pas encore acheté ou pas commencé)
#   "en-cours"     → en train d'y jouer
#   "termine"      → terminé
#   "non-commence" → pas encore commencé (utile pour réinitialiser l'auto-détection)
# Les jeux non listés sont auto-détectés à partir du temps de jeu Steam.
GAME_STATUS: dict[str, str] = {
    # "Cyberpunk 2077": "termine",
    # "Hades": "en-cours",
    # "Hollow Knight": "a-faire",
}
