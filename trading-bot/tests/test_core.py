import unittest

from helpers import H, T0, Scripted, candles_from

from tradebot.backtest import run_backtest
from tradebot.data import synthetic, timeframe_ms
from tradebot.indicators import atr, bollinger, ema, highest, lowest, macd, rsi, sma
from tradebot.models import Candle, Position, Signal
from tradebot.optimize import optimize
from tradebot.report import render_report
from tradebot.risk import RiskConfig, check_exit, plan_entry, position_size, rebase, stop_price
from tradebot.stats import max_drawdown_pct, sharpe_ratio
from tradebot.strategies import STRATEGIES, EmaRsi, build_strategy


class IndicatorTests(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(sma([1, 2, 3, 4, 5], 3), [None, None, 2.0, 3.0, 4.0])

    def test_ema_seed_and_leading_none(self):
        self.assertEqual(ema([1, 2, 3, 4, 5], 3)[:3], [None, None, 2.0])
        self.assertAlmostEqual(ema([1, 2, 3, 4, 5], 3)[4], 4.0)
        shifted = ema([None, None, 1, 2, 3, 4, 5], 3)
        self.assertEqual(shifted[:4], [None, None, None, None])
        self.assertAlmostEqual(shifted[6], 4.0)

    def test_rsi_bounds(self):
        self.assertEqual(rsi(list(range(1, 30)), 14)[-1], 100.0)
        self.assertEqual(rsi(list(range(30, 1, -1)), 14)[-1], 0.0)
        values = [v for v in rsi([c.close for c in synthetic(500)]) if v is not None]
        self.assertTrue(all(0 <= v <= 100 for v in values))

    def test_bollinger_and_macd(self):
        mid, upper, lower = bollinger([10.0] * 25, 20, 2)
        self.assertEqual((mid[-1], upper[-1], lower[-1]), (10.0, 10.0, 10.0))
        line, signal = macd([float(i) for i in range(100)])
        self.assertGreater(line[-1], 0)
        self.assertIsNotNone(signal[-1])

    def test_channels_and_atr(self):
        self.assertEqual(highest([1, 5, 2, 3], 2), [None, 5, 5, 3])
        self.assertEqual(lowest([1, 5, 2, 3], 2), [None, 1, 2, 2])
        candles = [Candle(i, 10, 11, 9, 10, 1) for i in range(30)]
        self.assertAlmostEqual(atr(candles, 14)[-1], 2.0)

    def test_timeframe(self):
        self.assertEqual(timeframe_ms("15m"), 900_000)
        self.assertEqual(timeframe_ms("4h"), 14_400_000)
        with self.assertRaises(ValueError):
            timeframe_ms("1x")


class StrategyTests(unittest.TestCase):
    def test_all_strategies_run_and_respect_warmup(self):
        candles = synthetic(1500)
        for name, cls in STRATEGIES.items():
            strategy = cls()
            signals = strategy.signals(candles)
            self.assertEqual(len(signals), len(candles), name)
            self.assertNotIn(Signal.BUY, signals[: strategy.warmup - 1], name)
            self.assertIn(Signal.BUY, signals, name)

    def test_signals_do_not_use_future_data(self):
        candles = synthetic(800)
        for cls in STRATEGIES.values():
            full = cls().signals(candles)
            for cut in (300, 555):
                self.assertEqual(cls().signals(candles[:cut]), full[:cut], cls.name)

    def test_ema_rsi_crossover(self):
        s = EmaRsi(fast=3, slow=6, rsi_period=3, rsi_max_entry=101, rsi_exit=101)
        closes = [10.0] * 10 + [9, 8, 7, 6, 5] + [6, 8, 10, 12] + [10, 7, 4, 2]
        signals = s.signals(candles_from(closes))
        self.assertEqual(signals.index(Signal.BUY), 17)
        self.assertIn(Signal.SELL, signals[17:])

    def test_donchian_breakout(self):
        closes = [100.0] * 30 + [110.0] + [100.0] * 5 + [80.0]
        signals = build_strategy("donchian_breakout").signals(candles_from(closes, spread=0))
        self.assertEqual(signals[30], Signal.BUY)
        self.assertEqual(signals[-1], Signal.SELL)

    def test_build_strategy_validation(self):
        with self.assertRaises(ValueError):
            build_strategy("inconnue")
        with self.assertRaises(ValueError):
            build_strategy("ema_rsi", {"fats": 3})
        with self.assertRaises(ValueError):
            build_strategy("ema_rsi", {"fast": 30, "slow": 10})
        self.assertEqual(build_strategy("macd_trend", {"trend_ema": 0}).trend_ema, 0)


class RiskTests(unittest.TestCase):
    def test_position_size_risks_the_configured_share(self):
        cfg = RiskConfig(risk_per_trade=0.01, max_position_pct=1, fee_pct=0)
        qty = position_size(10_000, 100, 97, cfg)
        self.assertAlmostEqual(qty * 3, 100)  # perte au stop = 1 % de 10 000

    def test_position_size_is_capped(self):
        cfg = RiskConfig(risk_per_trade=0.05, max_position_pct=0.25, fee_pct=0)
        self.assertAlmostEqual(position_size(10_000, 100, 99, cfg) * 100, 2_500)

    def test_fees_reduce_size(self):
        no_fee = position_size(10_000, 100, 97, RiskConfig(max_position_pct=1, fee_pct=0))
        fee = position_size(10_000, 100, 97, RiskConfig(max_position_pct=1, fee_pct=0.001))
        self.assertLess(fee, no_fee)

    def test_atr_stop_and_target(self):
        cfg = RiskConfig(stop_atr_mult=2, take_profit_rr=3)
        self.assertAlmostEqual(stop_price(100, cfg, atr_value=2.5), 95)
        self.assertAlmostEqual(stop_price(100, cfg, atr_value=None), 97)  # repli sur le stop en %
        self.assertAlmostEqual(stop_price(100, cfg, atr_value=80), 97)  # stop ATR absurde ignoré
        plan = plan_entry(10_000, 10_000, 100, cfg, atr_value=2.5)
        self.assertAlmostEqual(plan.target, 115)

    def test_plan_entry_respects_free_cash(self):
        plan = plan_entry(10_000, 500, 100, RiskConfig(fee_pct=0))
        self.assertAlmostEqual(plan.qty, 5)
        self.assertIsNone(plan_entry(10_000, 0, 100, RiskConfig()))

    def test_rebase_after_slippage(self):
        plan = plan_entry(10_000, 10_000, 100, RiskConfig())
        stop, target = rebase(plan, 100, 101)
        self.assertAlmostEqual(stop, 98)
        self.assertAlmostEqual(target, 107)

    def test_check_exit_is_pessimistic_and_handles_gaps(self):
        cfg = RiskConfig()
        pos = Position("X", 1, 100, 97, 106, 100, 0)
        self.assertEqual(check_exit(pos, cfg, 100, 96, 107), ("stop-loss", 97))
        self.assertEqual(check_exit(pos, cfg, 95, 94, 96), ("stop-loss", 95))
        self.assertEqual(check_exit(pos, cfg, 108, 107, 109), ("take-profit", 108))
        self.assertIsNone(check_exit(pos, cfg, 100, 98, 105))

    def test_trailing_stop(self):
        cfg = RiskConfig(trailing_stop_pct=0.05)
        pos = Position("X", 1, 100, 97, 0, 120, 0)
        self.assertEqual(check_exit(pos, cfg, 115, 113, 115), ("stop suiveur", 114))

    def test_invalid_config(self):
        with self.assertRaises(ValueError):
            RiskConfig(risk_per_trade=0.5)
        with self.assertRaises(ValueError):
            RiskConfig.from_dict({"stop_los_pct": 0.02})


class StatsTests(unittest.TestCase):
    def test_drawdown_and_sharpe(self):
        self.assertAlmostEqual(max_drawdown_pct([100, 120, 90, 130]), 25)
        self.assertEqual(sharpe_ratio([100, 100, 100], 8760), 0.0)
        self.assertGreater(sharpe_ratio([100, 101, 102.5, 103, 104.2], 8760), 0)


class BacktestTests(unittest.TestCase):
    def test_accounting_is_consistent(self):
        r = run_backtest(synthetic(2000), build_strategy("donchian_breakout"))
        self.assertGreater(len(r.trades), 10)
        self.assertEqual(len(r.equity), 2000)
        self.assertAlmostEqual(r.final_equity, r.initial_capital + sum(t.pnl for t in r.trades), places=6)
        self.assertTrue(all(t.closed_at >= t.opened_at for t in r.trades))

    def test_no_lookahead(self):
        candles = synthetic(1500)
        strategy = build_strategy("ema_rsi")
        full = run_backtest(candles, strategy).trades
        cut = run_backtest(candles[:900], strategy).trades
        done_before_cut = [t for t in cut if t.reason != "fin du backtest"]
        self.assertEqual(done_before_cut, full[: len(done_before_cut)])

    def test_losses_are_bounded_by_risk(self):
        cfg = RiskConfig(risk_per_trade=0.01, fee_pct=0.001)
        r = run_backtest(synthetic(3000), build_strategy("ema_rsi"), cfg, slippage_pct=0)
        worst = min(t.pnl for t in r.trades)
        self.assertGreater(worst, -0.0125 * r.initial_capital * 1.5)  # marge pour les gaps

    def test_daily_circuit_breaker(self):
        # volume = signal scripté : achat (1) en 1, vente (2) en 3, nouvel achat (1) en 4
        closes = [100, 100, 100, 90, 90, 100, 100]
        volumes = [0, 1, 0, 2, 1, 0, 0]
        candles = [Candle(T0 + i * H, c, c, c, c, v) for i, (c, v) in enumerate(zip(closes, volumes))]
        cfg = RiskConfig(max_daily_loss_pct=0.01, stop_loss_pct=0.2, risk_per_trade=0.05,
                         max_position_pct=1, take_profit_rr=0, fee_pct=0)
        r = run_backtest(candles, Scripted(), cfg, slippage_pct=0)
        self.assertEqual(len(r.trades), 1)  # -2,5 % dans la journée : le 2e achat est bloqué
        no_breaker = run_backtest(candles, Scripted(), RiskConfig(**{**cfg.__dict__, "max_daily_loss_pct": 0}),
                                  slippage_pct=0)
        self.assertEqual(len(no_breaker.trades), 2)

    def test_empty(self):
        r = run_backtest([], build_strategy("ema_rsi"))
        self.assertEqual(r.final_equity, r.initial_capital)
        self.assertEqual(r.stats.trades, 0)


class OptimizeAndReportTests(unittest.TestCase):
    def test_optimize_returns_ranked_rows_with_test_stats(self):
        rows = optimize(synthetic(2500), "donchian_breakout", top=3)
        self.assertEqual(len(rows), 3)
        self.assertGreaterEqual(rows[0].score, rows[1].score)
        self.assertIsNotNone(rows[0].test)

    def test_report_is_escaped_html(self):
        r = run_backtest(synthetic(600), build_strategy("ema_rsi"), symbol="<BTC>")
        html = render_report(r, brand="Mon <Bot>")
        self.assertIn("&lt;BTC&gt;", html)
        self.assertNotIn("<BTC>", html)
        self.assertIn("<svg", html)


if __name__ == "__main__":
    unittest.main()
