import unittest

from helpers import H, Harness

from tradebot.data import DAY_MS
from tradebot.risk import RiskConfig

BTC, ETH = "BTC/USDT", "ETH/USDT"


class EngineTests(unittest.TestCase):
    def test_does_not_act_on_signal_seen_at_first_start(self):
        h = Harness()
        h.step()
        h.step({BTC: 1})  # signal déjà présent au démarrage
        self.assertIsNone(h.store.get_position(BTC))

    def test_buys_with_risk_based_size_stop_and_target(self):
        h = Harness()
        h.warm()
        h.step({BTC: 1})
        pos = h.store.get_position(BTC)
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos.qty, 25)  # 1 % / 3 % = 3 333 plafonné à 25 % du capital = 2 500
        self.assertAlmostEqual(pos.stop, 97)
        self.assertAlmostEqual(pos.target, 106)
        self.assertAlmostEqual(h.broker.free_quote(), 7_500)
        self.assertTrue(any("ACHAT" in m for m in h.notifier.sent))

    def test_stop_loss_and_no_rebuy_on_same_candle(self):
        h = Harness()
        h.warm()
        h.step({BTC: 1})
        h.set_price(96)
        self.assertIsNone(h.store.get_position(BTC))
        trade = h.store.trades()[0]
        self.assertEqual(trade.reason, "stop-loss")
        self.assertAlmostEqual(trade.pnl, -100)
        h.set_price(100)  # même bougie, signal d'achat déjà traité
        self.assertIsNone(h.store.get_position(BTC))

    def test_take_profit(self):
        h = Harness()
        h.warm()
        h.step({BTC: 1})
        h.set_price(107)
        trade = h.store.trades()[0]
        self.assertEqual(trade.reason, "take-profit")
        self.assertAlmostEqual(trade.pnl, 175)
        self.assertAlmostEqual(h.broker.free_quote(), 10_175)

    def test_sell_signal(self):
        h = Harness()
        h.warm()
        h.step({BTC: 1})
        h.step({BTC: 2}, price=103)
        self.assertEqual(h.store.trades()[0].reason, "signal")
        self.assertAlmostEqual(h.store.trades()[0].pnl, 75)

    def test_trailing_stop_follows_price(self):
        h = Harness(risk=RiskConfig(trailing_stop_pct=0.05, take_profit_rr=0, fee_pct=0))
        h.warm()
        h.step({BTC: 1})
        h.set_price(120)
        self.assertAlmostEqual(h.store.get_position(BTC).highest, 120)
        h.set_price(113)
        trade = h.store.trades()[0]
        self.assertEqual(trade.reason, "stop suiveur")
        self.assertGreater(trade.pnl, 0)

    def test_max_open_positions(self):
        h = Harness(symbols=(BTC, ETH), risk=RiskConfig(max_open_positions=1, fee_pct=0))
        h.warm()
        h.step({BTC: 1, ETH: 1})
        self.assertEqual(len(h.store.positions()), 1)
        self.assertTrue(any("maximum" in e["message"] for e in h.store.events()))

    def test_pause_resume_and_close_all(self):
        h = Harness(symbols=(BTC, ETH))
        h.warm()
        h.store.push_command("pause")
        h.step({BTC: 1})
        self.assertIsNone(h.store.get_position(BTC))
        h.store.push_command("resume")
        h.step({BTC: 1, ETH: 1})
        self.assertEqual(len(h.store.positions()), 2)
        h.store.push_command("close_all")
        h.step(price=101)
        self.assertEqual(h.store.positions(), [])
        self.assertEqual({t.reason for t in h.store.trades()}, {"fermeture manuelle"})
        self.assertTrue(h.store.get("paused"))

    def test_daily_loss_circuit_breaker(self):
        risk = RiskConfig(max_daily_loss_pct=0.01, stop_loss_pct=0.1, risk_per_trade=0.02,
                          max_position_pct=1, take_profit_rr=0, fee_pct=0)
        h = Harness(risk=risk)
        h.warm()
        h.step({BTC: 1})  # 20 unités à 100
        h.set_price(94)  # -120 = -1,2 % du capital
        self.assertTrue(h.store.get("status")["halted"])
        h.step({BTC: 2}, price=94)
        h.step({BTC: 1}, price=94)
        self.assertIsNone(h.store.get_position(BTC))
        self.assertTrue(any("perte journalière" in e["message"] for e in h.store.events()))
        self.assertTrue(any("Coupe-circuit" in m for m in h.notifier.sent))

    def test_new_day_resets_breaker_and_sends_summary(self):
        h = Harness()
        h.warm()
        h.store.set("halted_day", h.engine.today())
        h.now += DAY_MS
        h.engine.tick()
        self.assertTrue(any("Bilan" in m for m in h.notifier.sent))
        self.assertFalse(h.store.get("status")["halted"])

    def test_state_survives_restart(self):
        h = Harness()
        h.warm()
        h.step({BTC: 1})
        restarted = Harness(store=h.store)
        restarted.data = h.data
        restarted.engine.data = h.data
        restarted.now = h.now
        self.assertAlmostEqual(restarted.broker.free_quote(), 7_500)
        restarted.set_price(96)
        self.assertEqual(restarted.store.trades()[0].reason, "stop-loss")
        self.assertAlmostEqual(restarted.broker.free_quote(), 9_900)

    def test_error_on_one_symbol_does_not_block_others(self):
        h = Harness(symbols=(BTC, ETH))
        h.warm()
        h.data.fail.add(ETH)
        h.step({BTC: 1, ETH: 1})
        h.engine.tick()  # nouvelle erreur dans la même heure : pas de nouvelle alerte
        self.assertIsNotNone(h.store.get_position(BTC))
        errors = [e for e in h.store.events() if e["level"] == "error"]
        self.assertEqual(len(errors), 2)
        self.assertEqual(sum("Erreur sur ETH/USDT" in m for m in h.notifier.sent), 1)  # alerte limitée

    def test_position_sold_outside_the_bot(self):
        h = Harness()
        h.warm()
        h.step({BTC: 1})
        h.broker.wallet["base"].pop(BTC)
        h.set_price(90)
        self.assertIsNone(h.store.get_position(BTC))
        self.assertEqual(h.store.trades(), [])
        self.assertTrue(any("introuvable" in m for m in h.notifier.sent))

    def test_telegram_commands(self):
        h = Harness()
        h.warm()
        h.notifier.commands = ["/pause"]
        h.engine.tick()
        self.assertTrue(h.store.get("paused"))
        h.notifier.commands = ["/statut", "/nimportequoi"]
        h.engine.tick()
        self.assertTrue(any("Capital" in m and "EN PAUSE" in m for m in h.notifier.sent))
        self.assertIn("/fermer_tout", h.notifier.sent[-1])

    def test_status_and_equity_snapshot(self):
        h = Harness()
        h.warm()
        h.step({BTC: 1})
        h.set_price(102)
        status = h.store.get("status")
        self.assertAlmostEqual(status["equity"], 10_050)
        self.assertEqual(status["positions"][0]["symbol"], BTC)
        self.assertAlmostEqual(status["positions"][0]["pnl"], 50)
        self.assertGreaterEqual(len(h.store.equity_curve()), 1)

    def test_run_loop_stops(self):
        h = Harness()
        h.settings.poll_seconds = 0
        h.engine.run(max_ticks=2)
        self.assertTrue(any("démarré" in m for m in h.notifier.sent))
        self.assertTrue(any("arrêté" in m for m in h.notifier.sent))


if __name__ == "__main__":
    unittest.main()
