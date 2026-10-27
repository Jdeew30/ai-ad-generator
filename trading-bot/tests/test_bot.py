import json
import tempfile
import unittest
from pathlib import Path

from bot.backtest import run_backtest
from bot.broker import PaperBroker
from bot.data import Candle, load_csv, save_csv, synthetic
from bot.indicators import ema, rsi, sma
from bot.risk import RiskConfig, exit_levels, position_size
from bot.runner import TradingBot
from bot.strategy import MaCrossoverStrategy, Signal


def candles_from(closes: list[float]) -> list[Candle]:
    return [Candle(i, c, c * 1.001, c * 0.999, c, 1.0) for i, c in enumerate(closes)]


class IndicatorTests(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(sma([1, 2, 3, 4, 5], 3), [None, None, 2.0, 3.0, 4.0])

    def test_ema_seeded_by_sma(self):
        out = ema([1, 2, 3, 4, 5], 3)
        self.assertEqual(out[:3], [None, None, 2.0])
        self.assertAlmostEqual(out[3], 3.0)
        self.assertAlmostEqual(out[4], 4.0)

    def test_rsi_bounds(self):
        self.assertEqual(rsi(list(range(1, 30)), 14)[-1], 100.0)
        self.assertEqual(rsi(list(range(30, 1, -1)), 14)[-1], 0.0)
        for v in rsi([c.close for c in synthetic(300)]):
            if v is not None:
                self.assertTrue(0 <= v <= 100)


class StrategyTests(unittest.TestCase):
    def test_buy_on_cross_up_then_sell_on_cross_down(self):
        s = MaCrossoverStrategy(fast=3, slow=6, rsi_period=3, rsi_overbought=101, rsi_exit=101)
        closes = [10.0] * 10 + [9, 8, 7, 6, 5] + [6, 8, 10, 12] + [10, 7, 4, 2]
        sig = s.signals(closes)
        self.assertIn(Signal.BUY, sig)
        self.assertIn(Signal.SELL, sig[sig.index(Signal.BUY):])

    def test_rejects_bad_periods(self):
        with self.assertRaises(ValueError):
            MaCrossoverStrategy(fast=26, slow=12)


class RiskTests(unittest.TestCase):
    def test_position_size_respects_risk_and_cap(self):
        cfg = RiskConfig(risk_per_trade=0.01, stop_loss_pct=0.02, max_position_pct=0.5, fee_pct=0)
        # 1 % de 10 000 / 2 % = 5 000 de notionnel, sous le plafond de 5 000
        self.assertAlmostEqual(position_size(10_000, 100, cfg), 50)
        tight = RiskConfig(risk_per_trade=0.05, stop_loss_pct=0.01, max_position_pct=0.5, fee_pct=0)
        self.assertAlmostEqual(position_size(10_000, 100, tight), 50)  # plafonné

    def test_exit_levels(self):
        stop, target = exit_levels(100, RiskConfig(stop_loss_pct=0.02, take_profit_pct=0.04))
        self.assertAlmostEqual(stop, 98)
        self.assertAlmostEqual(target, 104)


class BacktestTests(unittest.TestCase):
    def test_runs_and_accounts_consistently(self):
        r = run_backtest(synthetic(1500))
        self.assertGreater(len(r.trades), 0)
        self.assertEqual(len(r.equity_curve), 1500)
        self.assertAlmostEqual(r.final_equity, r.initial_capital + sum(t.pnl for t in r.trades), places=6)
        self.assertTrue(all(t.exit_price is not None for t in r.trades))

    def test_stop_loss_caps_loss(self):
        s = MaCrossoverStrategy(fast=3, slow=6, rsi_period=3, rsi_overbought=101, rsi_exit=101)
        # signal d'achat à la clôture de l'indice 17, entrée à 10, puis chute à 6
        closes = [10.0] * 10 + [9, 8, 7, 6, 5] + [6, 8, 10] + [10.0] + [6.0] * 5
        r = run_backtest(candles_from(closes), s, RiskConfig(fee_pct=0))
        self.assertEqual(r.trades[0].reason, "stop-loss")

    def test_empty(self):
        r = run_backtest([])
        self.assertEqual(r.final_equity, r.initial_capital)


class PaperBrokerTests(unittest.TestCase):
    def test_round_trip(self):
        b = PaperBroker(1000, fee_pct=0.001)
        b.market_buy(5, 100)
        self.assertAlmostEqual(b.cash, 1000 - 500 - 0.5)
        b.market_sell(5, 110)
        self.assertAlmostEqual(b.cash, 499.5 + 550 - 0.55)
        self.assertEqual(b.base, 0)

    def test_insufficient_funds(self):
        with self.assertRaises(ValueError):
            PaperBroker(100).market_buy(10, 100)


class RunnerTests(unittest.TestCase):
    def test_buys_then_hits_take_profit_and_persists_state(self):
        s = MaCrossoverStrategy(fast=3, slow=6, rsi_period=3, rsi_overbought=101, rsi_exit=101)
        base = [10.0] * 10 + [9, 8, 7, 6, 5] + [6, 8, 10, 12]
        feed = {"closes": base + [12.0]}  # dernière bougie = en cours
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            bot = TradingBot(PaperBroker(10_000), lambda: candles_from(feed["closes"]), s, state_file=state)

            # avance jusqu'au croisement
            actions = []
            for n in range(len(base) - 3, len(base)):
                feed["closes"] = base[:n] + [base[n - 1]]
                actions.append(bot.step())
            self.assertTrue(any(a.startswith("ACHAT") for a in actions), actions)
            self.assertIsNotNone(json.loads(state.read_text())["position"])

            # reprise après redémarrage, puis prix au-dessus de l'objectif
            bot2 = TradingBot(PaperBroker(10_000), lambda: candles_from(feed["closes"]), s, state_file=state)
            self.assertIsNotNone(bot2.position)
            bot2.broker.base = bot2.position.qty
            feed["closes"] = base + [bot2.position.target * 1.01]
            self.assertIn("take-profit", bot2.step())
            self.assertIsNone(json.loads(state.read_text())["position"])

    def test_waits_for_warmup(self):
        bot = TradingBot(PaperBroker(), lambda: candles_from([1.0] * 5))
        self.assertTrue(bot.step().startswith("attente"))


class CsvTests(unittest.TestCase):
    def test_round_trip(self):
        data = synthetic(10)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.csv"
            save_csv(data, path)
            self.assertEqual(load_csv(path), data)


if __name__ == "__main__":
    unittest.main()
