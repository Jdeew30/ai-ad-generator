"""
Assistant de configuration pas à pas (python -m vintedbot setup).

Pour Telegram, il suffit de coller le token donné par @BotFather : l'assistant
vérifie le token, trouve tout seul l'identifiant de votre conversation et envoie
un message de test.
"""
from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

SETUP_DONE = "VINTEDBOT_SETUP_DONE"
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
WEBHOOK_RE = re.compile(r"^https://(ptb\.|canary\.)?discord(app)?\.com/api/webhooks/\d+/[\w-]+$")
PROJECT_DIR = Path(__file__).resolve().parent.parent

Ask = Callable[[str], str]
Say = Callable[[str], None]


# ------------------------------------------------------------ fichiers
def read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                values[key.strip()] = value.strip().strip("\"'")
    return values


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Met à jour (ou ajoute) des variables dans .env en conservant le reste du fichier."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if key in pending and not key.startswith("#"):
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_enabled(config: Path, section: str, enabled: bool) -> None:
    """Passe `enabled` à true/false dans la section [telegram] ou [discord] de config.toml."""
    text = config.read_text(encoding="utf-8")
    value = "true" if enabled else "false"
    pattern = re.compile(rf"(^\[{section}\][^\[]*?^enabled\s*=\s*)(true|false)", re.MULTILINE | re.DOTALL)
    if pattern.search(text):
        text = pattern.sub(rf"\g<1>{value}", text, count=1)
    else:
        text = text.rstrip() + f"\n\n[{section}]\nenabled = {value}\n"
    config.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- réseau
def telegram(token: str, method: str, opener: Callable[..., Any], **params: Any) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode() if params else None
    try:
        with opener(url, data=data, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:  # Telegram renvoie {"ok": false, "description": ...}
        try:
            return json.load(e)
        except ValueError:
            return {"ok": False, "description": f"HTTP {e.code}"}
    except OSError as e:
        return {"ok": False, "description": f"Telegram injoignable, vérifiez votre connexion internet ({e})"}


def find_chat_id(updates: dict) -> str | None:
    """Identifiant de la dernière conversation privée qui a écrit au bot."""
    for update in reversed(updates.get("result", [])):
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("id") is not None:
            return str(chat["id"])
    return None


# ------------------------------------------------------------- assistant
def setup_telegram(env: Path, config: Path, ask: Ask, say: Say, opener: Callable[..., Any]) -> bool:
    say("\n— Telegram —\n"
        "1. Dans Telegram, cherchez @BotFather et envoyez-lui : /newbot\n"
        "2. Choisissez un nom (ex. « Mes affaires Vinted ») puis un identifiant finissant par « bot ».\n"
        "3. BotFather vous donne un token qui ressemble à 123456789:AAH...")
    for _ in range(3):
        token = ask("Collez le token ici : ").strip()
        if not TOKEN_RE.match(token):
            say("❌ Ce n'est pas un token valide (format attendu : chiffres:lettres). Réessayez.")
            continue
        me = telegram(token, "getMe", opener)
        if me.get("ok"):
            break
        say(f"❌ Token non validé : {me.get('description', 'erreur inconnue')}. Réessayez.")
    else:
        return False

    username = me["result"].get("username", "votre bot")
    say(f"✅ Bot trouvé : @{username}\n"
        f"Maintenant, ouvrez Telegram, cherchez @{username}, appuyez sur « Démarrer » "
        "et envoyez-lui un message (par exemple « salut »).")
    for _ in range(3):
        ask("Quand c'est fait, appuyez sur Entrée ici… ")
        chat_id = find_chat_id(telegram(token, "getUpdates", opener))
        if chat_id:
            break
        say("⏳ Je ne vois pas encore votre message. Vérifiez que vous l'avez bien envoyé à "
            f"@{username}, puis réessayez.")
    else:
        return False

    sent = telegram(token, "sendMessage", opener, chat_id=chat_id,
                    text="✅ VintedBot est bien connecté ! Les bonnes affaires arriveront ici.")
    if not sent.get("ok"):
        say(f"❌ Envoi du message de test impossible ({sent.get('description')}).")
        return False
    write_env(env, {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id})
    set_enabled(config, "telegram", True)
    say("✅ Telegram configuré : vous devriez avoir reçu un message de test.")
    return True


def setup_discord(env: Path, config: Path, ask: Ask, say: Say, opener: Callable[..., Any]) -> bool:
    say("\n— Discord —\n"
        "Sur votre serveur : Paramètres du salon › Intégrations › Webhooks › Nouveau webhook › "
        "Copier l'URL du webhook.")
    for _ in range(3):
        url = ask("Collez l'URL du webhook ici : ").strip()
        if not WEBHOOK_RE.match(url):
            say("❌ L'URL doit ressembler à https://discord.com/api/webhooks/... Réessayez.")
            continue
        request = urllib.request.Request(
            url, method="POST", data=json.dumps({"content": "✅ VintedBot est bien connecté !"}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "VintedBot (alertes, 1.0)"})
        try:
            with opener(request, timeout=20) as resp:
                resp.read()
        except Exception as e:
            say(f"❌ Discord refuse ce webhook ({e}). Réessayez.")
            continue
        write_env(env, {"DISCORD_WEBHOOK_URL": url})
        set_enabled(config, "discord", True)
        say("✅ Discord configuré : un message de test a été posté dans le salon.")
        return True
    return False


def run_setup(config: Path, env: Path, ask: Ask = input, say: Say = print,
              opener: Callable[..., Any] = urllib.request.urlopen, if_needed: bool = False) -> int:
    if if_needed and read_env(env).get(SETUP_DONE) == "1":
        return 0
    for template, target in (("config.example.toml", config), (".env.example", env)):
        if not target.exists():
            shutil.copyfile(PROJECT_DIR / template, target)
    say("=== Configuration de VintedBot ===\n"
        "Où voulez-vous recevoir les alertes de bonnes affaires ?\n"
        "  1. Telegram (recommandé : notification sur votre téléphone avec la photo)\n"
        "  2. Discord\n"
        "  3. Seulement dans cette fenêtre")
    choice = ask("Votre choix [1] : ").strip() or "1"
    if choice == "1":
        ok = setup_telegram(env, config, ask, say, opener)
    elif choice == "2":
        ok = setup_discord(env, config, ask, say, opener)
    else:
        ok = True
        say("Les alertes s'afficheront dans cette fenêtre.")
    if not ok:
        say("\nConfiguration interrompue. Relancez-la quand vous voulez : python -m vintedbot setup")
        return 1
    write_env(env, {SETUP_DONE: "1"})
    say(f"\nC'est prêt ! Vos recherches sont dans {config.name} (PS5, Switch OLED, Steam Deck, "
        "Nike Tech Fleece...). Ouvrez-le avec le Bloc-notes pour les modifier.")
    return 0
