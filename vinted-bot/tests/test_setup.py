import io
import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (coupe les logs)

from vintedbot.config import load_settings
from vintedbot.setup_wizard import SETUP_DONE, read_env, run_setup, set_enabled, write_env

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1"
PROJECT = Path(__file__).resolve().parent.parent


class FakeTelegram:
    """Imite l'API Telegram ; le message de l'utilisateur n'apparaît qu'après `user_writes`."""

    def __init__(self, user_writes_after: int = 0, token_ok: bool = True) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.polls = 0
        self.user_writes_after = user_writes_after
        self.token_ok = token_ok

    def __call__(self, url, data=None, timeout=None):
        if not isinstance(url, str):  # requête Discord
            self.calls.append(("discord", json.loads(url.data)))
            return io.BytesIO(b"")
        method = url.rsplit("/", 1)[1]
        params = dict(p.split("=", 1) for p in data.decode().split("&")) if data else {}
        self.calls.append((method, params))
        if method == "getMe":
            body = {"ok": True, "result": {"username": "MesAffairesBot"}} if self.token_ok else \
                {"ok": False, "description": "Unauthorized"}
        elif method == "getUpdates":
            self.polls += 1
            result = [{"update_id": 1, "message": {"chat": {"id": 4242}, "text": "salut"}}] \
                if self.polls > self.user_writes_after else []
            body = {"ok": True, "result": result}
        else:
            body = {"ok": True}
        return io.BytesIO(json.dumps(body).encode())


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.config, self.env = self.dir / "config.toml", self.dir / ".env"
        self.said: list[str] = []

    def run_wizard(self, answers, opener, **kwargs):
        replies = iter(answers)
        return run_setup(self.config, self.env, ask=lambda _: next(replies), say=self.said.append,
                         opener=opener, **kwargs)

    def test_telegram_finds_chat_id_by_itself(self):
        tg = FakeTelegram(user_writes_after=1)  # l'utilisateur oublie d'écrire au premier essai
        code = self.run_wizard(["1", TOKEN, "", ""], tg)
        self.assertEqual(code, 0)
        env = read_env(self.env)
        self.assertEqual((env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"], env[SETUP_DONE]),
                         (TOKEN, "4242", "1"))
        settings = load_settings(self.config, env=env)
        self.assertTrue(settings.telegram_enabled)
        self.assertIn("sendMessage", [c[0] for c in tg.calls])
        self.assertTrue(any("@MesAffairesBot" in s for s in self.said))

    def test_bad_token_is_asked_again(self):
        code = self.run_wizard(["", "pas-un-token", TOKEN, ""], FakeTelegram())
        self.assertEqual(code, 0)
        self.assertTrue(any("pas un token valide" in s for s in self.said))

    def test_rejected_token_gives_up_after_three_tries(self):
        code = self.run_wizard(["1", TOKEN, TOKEN, TOKEN], FakeTelegram(token_ok=False))
        self.assertEqual(code, 1)
        self.assertNotIn(SETUP_DONE, read_env(self.env))

    def test_no_internet_is_explained(self):
        def offline(*a, **k):
            raise OSError("Network is unreachable")

        self.assertEqual(self.run_wizard(["1", TOKEN, TOKEN, TOKEN], offline), 1)
        self.assertTrue(any("vérifiez votre connexion internet" in s for s in self.said))

    def test_discord(self):
        tg = FakeTelegram()
        hook = "https://discord.com/api/webhooks/123/abc-DEF_9"
        self.assertEqual(self.run_wizard(["2", "https://exemple.com", hook], tg), 0)
        self.assertEqual(read_env(self.env)["DISCORD_WEBHOOK_URL"], hook)
        self.assertEqual(tg.calls[0][0], "discord")
        self.assertTrue(load_settings(self.config, env=read_env(self.env)).discord_enabled)

    def test_console_only_and_if_needed(self):
        self.assertEqual(self.run_wizard(["3"], FakeTelegram()), 0)
        self.said.clear()
        self.assertEqual(self.run_wizard([], FakeTelegram(), if_needed=True), 0)
        self.assertEqual(self.said, [])  # déjà configuré : rien demandé

    def test_file_helpers_keep_other_content(self):
        self.env.write_text("# commentaire\nTELEGRAM_BOT_TOKEN=\nAUTRE=1\n", encoding="utf-8")
        write_env(self.env, {"TELEGRAM_BOT_TOKEN": "x", "NOUVEAU": "y"})
        self.assertEqual(self.env.read_text(encoding="utf-8"),
                         "# commentaire\nTELEGRAM_BOT_TOKEN=x\nAUTRE=1\nNOUVEAU=y\n")
        self.config.write_text((PROJECT / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
        set_enabled(self.config, "discord", True)
        settings = load_settings(self.config, env={"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/a"})
        self.assertTrue(settings.discord_enabled)
        self.assertFalse(settings.telegram_enabled)


class LauncherTests(unittest.TestCase):
    def test_launchers_exist_and_use_setup_then_run(self):
        bat = (PROJECT / "Lancer VintedBot (Windows).bat").read_bytes()
        self.assertIn(b"\r\n", bat)
        self.assertTrue(bat.isascii())
        mac = PROJECT / "Lancer VintedBot (Mac).command"
        self.assertTrue(mac.stat().st_mode & 0o111)
        for text in (bat.decode(), mac.read_text(encoding="utf-8")):
            self.assertLess(text.index("setup --if-needed"), text.index("vintedbot run"))


if __name__ == "__main__":
    unittest.main()
