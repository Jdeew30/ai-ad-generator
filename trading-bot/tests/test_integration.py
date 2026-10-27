import base64
import contextlib
import io
import json
import math
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (coupe les logs)

from tradebot.cli import main
from tradebot.config import ConfigError, load_dotenv, load_settings
from tradebot.dashboard import Dashboard
from tradebot.data import fetch_history, load_csv, save_csv, synthetic
from tradebot.exchange import CcxtBroker, PaperBroker
from tradebot.models import Trade
from tradebot.notify import TelegramNotifier
from tradebot.store import Store

PROJECT = Path(__file__).resolve().parent.parent


class FakeExchange:
    """Imite l'API ccxt : précision à 3 décimales, minimums, frais prélevés en crypto à l'achat."""

    def __init__(self) -> None:
        self.balance = {"USDT": 1000.0, "BTC": 0.0}
        self.orders: list[tuple[str, float]] = []

    def fetch_balance(self) -> dict:
        return {"free": dict(self.balance)}

    def market(self, symbol: str) -> dict:
        return {"limits": {"amount": {"min": 0.001}, "cost": {"min": 10}}}

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        value = math.floor(amount * 1000) / 1000
        if value <= 0:
            raise ValueError("amount must be greater than minimum amount precision")
        return f"{value:.3f}"

    def create_market_buy_order(self, symbol: str, amount: float) -> dict:
        self.orders.append(("buy", amount))
        fee = amount * 0.001
        self.balance["BTC"] += amount - fee
        return {"id": "b1", "filled": amount, "average": 100.0, "fees": [{"currency": "BTC", "cost": fee}]}

    def create_market_sell_order(self, symbol: str, amount: float) -> dict:
        self.orders.append(("sell", amount))
        return {"id": "s1", "filled": None, "average": None}

    def fetch_order(self, order_id: str, symbol: str) -> dict:
        return {"id": order_id, "filled": self.orders[-1][1], "average": 110.0,
                "fee": {"currency": "USDT", "cost": 0.5}}


class BrokerTests(unittest.TestCase):
    def test_paper_broker_fees_slippage_and_persistence(self):
        saved = {}
        b = PaperBroker(1000, fee_pct=0.001, slippage_pct=0.01, on_change=saved.update)
        fill = b.buy("BTC/USDT", 5, 100)
        self.assertAlmostEqual(fill.price, 101)
        self.assertAlmostEqual(b.free_quote(), 1000 - 505 - 0.505)
        b.sell("BTC/USDT", 5, 100)
        self.assertEqual(b.free_base("BTC/USDT"), 0)
        self.assertAlmostEqual(saved["quote"], b.free_quote())
        self.assertIsNotNone(b.check_order("BTC/USDT", 0.01, 100))
        with self.assertRaises(ValueError):
            b.buy("BTC/USDT", 100, 100)

    def test_ccxt_buy_nets_out_fee_paid_in_crypto(self):
        ex = FakeExchange()
        fill = CcxtBroker(ex, "USDT").buy("BTC/USDT", 1.23456, 100)
        self.assertEqual(ex.orders, [("buy", 1.234)])
        self.assertAlmostEqual(fill.qty, 1.234 * 0.999)
        self.assertAlmostEqual(fill.fee, 1.234 * 0.001 * 100)

    def test_ccxt_sell_never_exceeds_balance_and_fetches_fill(self):
        ex = FakeExchange()
        ex.balance["BTC"] = 0.5
        fill = CcxtBroker(ex, "USDT").sell("BTC/USDT", 0.6, 100)
        self.assertEqual(ex.orders, [("sell", 0.5)])
        self.assertEqual((fill.qty, fill.price, fill.fee), (0.5, 110.0, 0.5))

    def test_ccxt_minimums(self):
        broker = CcxtBroker(FakeExchange(), "USDT")
        self.assertIsNone(broker.check_order("BTC/USDT", 1, 100))
        self.assertIn("minimum", broker.check_order("BTC/USDT", 0.05, 100))
        self.assertIn("refusée", broker.check_order("BTC/USDT", 0.0001, 100))


class DataTests(unittest.TestCase):
    def test_fetch_history_pages_and_drops_forming_candle(self):
        candles = synthetic(2500, timeframe="1h")

        class Source:
            calls = 0

            def fetch_ohlcv(self, symbol, timeframe, limit=500, since=None):
                Source.calls += 1
                return [c for c in candles if c.time >= since][:limit]

        now = candles[-1].time + 1_800_000  # la dernière bougie est en cours
        out = fetch_history(Source(), "X", "1h", days=2500 / 24, now_ms=now)
        self.assertEqual(out, candles[:-1])
        self.assertGreaterEqual(Source.calls, 3)

    def test_csv_round_trip(self):
        data = synthetic(20)
        with tempfile.TemporaryDirectory() as d:
            save_csv(data, Path(d) / "x.csv")
            self.assertEqual(load_csv(Path(d) / "x.csv"), data)


class ConfigTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        d = tempfile.mkdtemp()
        path = Path(d) / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_example_config_is_valid(self):
        s = load_settings(PROJECT / "config.example.toml", env={})
        self.assertEqual(s.mode_label, "PAPER")
        self.assertEqual(s.quote, "USDT")

    def test_typos_are_rejected(self):
        for text in ('[risk]\nmax_daily_los_pct = 0.1\n', '[trading]\nsymbol = ["BTC/USDT"]\n',
                     '[strategie]\nname = "x"\n', '[strategy]\nname = "ema_rsi"\n[strategy.params]\nfats = 3\n'):
            with self.assertRaises(ConfigError, msg=text):
                load_settings(self.write(text), env={})

    def test_secrets_and_live_requirements(self):
        path = self.write('[exchange]\nmode = "live"\n')
        with self.assertRaises(ConfigError):
            load_settings(path, env={})
        s = load_settings(path, env={"EXCHANGE_API_KEY": "k", "EXCHANGE_API_SECRET": "s"})
        self.assertEqual((s.api_key, s.mode_label), ("k", "SANDBOX"))
        self.assertEqual(load_settings(path, env={}, for_trading=False).mode, "live")  # backtest sans clés

    def test_mixed_quotes_rejected(self):
        with self.assertRaises(ConfigError):
            load_settings(self.write('[trading]\nsymbols = ["BTC/USDT", "ETH/EUR"]\n'), env={})

    def test_dotenv(self):
        path = Path(tempfile.mkdtemp()) / ".env"
        path.write_text('# commentaire\nTB_TEST_A="valeur a"\nexport TB_TEST_B=b\n', encoding="utf-8")
        with mock.patch.dict(os.environ, {"TB_TEST_B": "déjà là"}):
            load_dotenv(path)
            self.assertEqual(os.environ["TB_TEST_A"], "valeur a")
            self.assertEqual(os.environ["TB_TEST_B"], "déjà là")
        os.environ.pop("TB_TEST_A", None)


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(args))
        return code, out.getvalue()

    def test_backtest_with_report(self):
        with tempfile.TemporaryDirectory() as d:
            report = Path(d) / "r.html"
            code, out = self.run_cli("backtest", "--config", "absent.toml", "--env", "absent.env",
                                     "--source", "synthetic", "--strategy", "donchian_breakout",
                                     "--param", "entry_period=30", "--report", str(report))
            self.assertEqual(code, 0)
            self.assertIn("entry_period=30", out)
            self.assertIn("<svg", report.read_text(encoding="utf-8"))

    def test_optimize_and_strategies(self):
        code, out = self.run_cli("optimize", "--config", "absent.toml", "--env", "absent.env",
                                 "--source", "synthetic", "--strategy", "ema_rsi", "--top", "2")
        self.assertEqual(code, 0)
        self.assertIn("TEST", out)
        self.assertIn("donchian_breakout", self.run_cli("strategies")[1])

    def test_init_creates_files_once(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                self.run_cli("init")
                self.assertTrue(Path("config.toml").exists() and Path(".env").exists())
                self.assertIn("existe déjà", self.run_cli("init")[1])
            finally:
                os.chdir(cwd)

    def test_bad_config_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.toml"
            cfg.write_text("[risk]\nrisk_per_trade = 0.9\n", encoding="utf-8")
            self.assertEqual(self.run_cli("backtest", "--config", str(cfg), "--source", "synthetic")[0], 2)


class TelegramTests(unittest.TestCase):
    def test_send_and_commands_only_from_owner(self):
        calls = []

        def opener(url, data=None, timeout=None):
            calls.append((url, data))
            payload = {"result": []}
            if url.endswith("getUpdates"):
                payload["result"] = [
                    {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/pause@MonBot"}},
                    {"update_id": 6, "message": {"chat": {"id": 666}, "text": "/fermer_tout"}},
                ]
            return io.BytesIO(json.dumps(payload).encode())

        t = TelegramNotifier("TOKEN", "42", opener=opener)
        t.send("bonjour")
        self.assertIn("sendMessage", calls[0][0])
        self.assertEqual(t.poll_commands(), ["/pause"])
        t.poll_commands()
        self.assertIn(b"offset=7", calls[-1][1])

    def test_network_errors_are_swallowed(self):
        def opener(*a, **k):
            raise urllib.error.URLError("hors ligne")

        t = TelegramNotifier("TOKEN", "42", opener=opener)
        t.send("x")
        self.assertEqual(t.poll_commands(), [])


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.store.set("status", {"equity": 10_000, "quote": "USDT", "positions": []})
        self.store.add_trade(Trade("BTC/USDT", 1, 100, 110, 0, 1, 10, "signal"))
        self.dash = Dashboard(self.store, "s3cret", "127.0.0.1", 0, brand="Mon<Bot>").start()

    def tearDown(self):
        self.dash.stop()

    def request(self, path, password="s3cret", method="GET", body=None, headers=None):
        req = urllib.request.Request(self.dash.url + path, method=method, data=body, headers=headers or {})
        if password is not None:
            token = base64.b64encode(f"admin:{password}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_requires_password(self):
        self.assertEqual(self.request("/api/summary", password=None)[0], 401)
        self.assertEqual(self.request("/api/summary", password="faux")[0], 401)

    def test_pages_and_api(self):
        code, body = self.request("/")
        self.assertEqual(code, 200)
        self.assertIn(b"Mon&lt;Bot&gt;", body)
        summary = json.loads(self.request("/api/summary")[1])
        self.assertEqual((summary["trades"], summary["realized_pnl"]), (1, 10))
        self.assertEqual(len(json.loads(self.request("/api/trades")[1])), 1)

    def test_commands_need_csrf_header_and_valid_name(self):
        body = json.dumps({"command": "pause"}).encode()
        self.assertEqual(self.request("/api/command", method="POST", body=body)[0], 403)
        headers = {"X-Requested-With": "tradebot", "Content-Type": "application/json"}
        bad = json.dumps({"command": "retirer_tout"}).encode()
        self.assertEqual(self.request("/api/command", method="POST", body=bad, headers=headers)[0], 400)
        self.assertEqual(self.request("/api/command", method="POST", body=body, headers=headers)[0], 200)
        self.assertEqual(self.store.pop_commands(), ["pause"])


if __name__ == "__main__":
    unittest.main()
