import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from helpers import NOW, FakeClient, listing, make_watch

from vintedbot import cli
from vintedbot.analysis import MarketStats, evaluate
from vintedbot.config import ConfigError, Costs, load_settings
from vintedbot.notify import ConsoleNotifier, DiscordNotifier, TelegramNotifier, deal_lines, eur

PROJECT = Path(__file__).resolve().parent.parent


def sample_eval():
    market = MarketStats(40, 300, 280, 320)
    return evaluate(listing(1, "PS5 slim", 180), make_watch(), market, Costs(), 10, NOW)


class NotifyTests(unittest.TestCase):
    def test_eur_format(self):
        self.assertEqual(eur(1234.5), "1 234,50 €")

    def test_deal_message(self):
        text = "\n".join(deal_lines(sample_eval(), "raison"))
        for expected in ("BONNE AFFAIRE", "180,00 €", "-40 %", "Marge estimée", "Très bon état", "🎯 raison"):
            self.assertIn(expected, text)

    def test_telegram_photo_with_button_and_text_fallback(self):
        calls = []

        def opener(url, data=None, timeout=None):
            calls.append((url.rsplit("/", 1)[1], dict(p.split("=", 1) for p in data.decode().split("&"))))
            if url.endswith("sendPhoto"):
                raise urllib.error.HTTPError(url, 400, "bad photo", {}, None)
            return io.BytesIO(b"{}")

        TelegramNotifier("T", "42", opener=opener).deal(sample_eval(), "r")
        self.assertEqual([c[0] for c in calls], ["sendPhoto", "sendMessage"])
        self.assertIn("inline_keyboard", calls[1][1]["reply_markup"].replace("%22", '"'))

    def test_discord_embed(self):
        sent = []

        def opener(request, timeout=None):
            sent.append(request)
            return io.BytesIO(b"")

        DiscordNotifier("https://discord.com/api/webhooks/x", opener=opener).deal(sample_eval(), "r")
        payload = json.loads(sent[0].data)
        self.assertEqual(payload["embeds"][0]["url"], "https://www.vinted.fr/items/1-annonce")
        self.assertIn("image", payload["embeds"][0])
        self.assertNotIn("Python-urllib", sent[0].get_header("User-agent"))

    def test_network_errors_never_crash(self):
        def opener(*a, **k):
            raise urllib.error.URLError("hors ligne")

        TelegramNotifier("T", "42", opener=opener).deal(sample_eval(), "r")
        DiscordNotifier("https://d", opener=opener).text("x")


class ConfigTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        path = Path(tempfile.mkdtemp()) / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_example_config(self):
        s = load_settings(PROJECT / "config.example.toml", env={})
        self.assertGreaterEqual(len(s.watches), 5)
        self.assertTrue(all(w.api_params for w in s.watches))

    def test_url_watch_keeps_market_complete(self):
        s = load_settings(self.write(
            '[[watch]]\nname = "J4"\npreset = "sneakers"\n'
            'url = "https://www.vinted.fr/catalog?search_text=jordan&size_ids[]=1196&price_to=150"\n'), env={})
        w = s.watches[0]
        self.assertEqual(w.max_price, 150)
        self.assertNotIn("price_to", w.api_params)  # le marché reste visible au-dessus de 150 €
        self.assertEqual(w.api_params["attribute_ids[size]"], "1196")

    def test_errors(self):
        cases = [
            '[[watch]]\nname = "a"\nquery = "x"\nmin_profitt = 3\n',
            '[[watch]]\nname = "a"\n',
            '[[watch]]\nname = "a"\nquery = "x"\npreset = "voiture"\n',
            '[[watch]]\nname = "a"\nquery = "x"\nconditions = ["comme neuf"]\n',
            '[[watch]]\nname = "a"\nquery = "x"\n[[watch]]\nname = "a"\nquery = "y"\n',
            '[general]\npoll_seconds = 5\n',
            '[general]\ndomain = "www.leboncoin.fr"\n',
            '[telegram]\nenabled = true\n',
        ]
        for text in cases:
            with self.assertRaises(ConfigError, msg=text):
                load_settings(self.write(text), env={})

    def test_secrets_from_env(self):
        s = load_settings(self.write('[telegram]\nenabled = true\n'),
                          env={"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"})
        self.assertEqual(s.telegram_token, "t")


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_analyze_prints_market_and_max_buy_price(self):
        market = [listing(i + 1, f"PS5 slim {i}", 300 + (i % 7) * 5) for i in range(30)]
        page = market + [listing(99, "PS5 slim état parfait", 200), listing(98, "Boîte vide PS5", 150)]
        fake = FakeClient([page])
        with mock.patch.object(cli, "make_client", return_value=fake):
            code, out = self.run_cli("analyze", "ps5 slim", "--preset", "console", "--min-price", "100",
                                     "--config", "absent.toml", "--env", "absent.env")
        self.assertEqual(code, 0)
        self.assertIn("32 annonces lues, 31 comparables", out)
        self.assertIn("médiane", out)
        self.assertIn("achetez à", out)
        self.assertIn("état parfait", out)
        self.assertIn('query = "ps5 slim"', out)
        self.assertEqual(fake.calls[0][0]["price_from"], "100")

    def test_check_uses_a_temporary_store(self):
        page = [listing(i + 1, f"PS5 slim {i}", 300 + i % 5) for i in range(20)] + [listing(50, "PS5 slim", 170)]
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.toml"
            cfg.write_text(f'[general]\ndata_dir = "{d}/data"\nmin_samples = 10\n'
                           '[[watch]]\nname = "PS5"\nquery = "ps5 slim"\npreset = "console"\nmin_price = 100\n',
                           encoding="utf-8")
            with mock.patch.object(cli, "make_client", return_value=FakeClient([page])):
                code, out = self.run_cli("check", "--config", str(cfg), "--env", "absent.env")
            self.assertEqual(code, 0)
            self.assertIn("Meilleures offres déjà en ligne", out)
            self.assertIn("170,00 €", out)
            self.assertFalse((Path(d) / "data").exists())

    def test_presets_init_and_test_notify(self):
        self.assertIn("sneakers", self.run_cli("presets")[1])
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                self.run_cli("init")
                self.assertTrue(Path("config.toml").exists() and Path(".env").exists())
                code, out = self.run_cli("test-notify")
                self.assertEqual(code, 0)
                self.assertIn("PÉPITE", out)
            finally:
                os.chdir(cwd)

    def test_missing_config_for_run(self):
        self.assertEqual(self.run_cli("run", "--config", "absent.toml", "--env", "absent.env")[0], 2)


class ConsoleTests(unittest.TestCase):
    def test_console_notifier(self):
        lines = []
        ConsoleNotifier(lines.append).deal(sample_eval(), "r")
        self.assertIn("🔗 https://www.vinted.fr/items/1-annonce", lines[0])


if __name__ == "__main__":
    unittest.main()
