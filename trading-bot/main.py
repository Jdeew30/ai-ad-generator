"""
Bot de trading — point d'entrée.

  python main.py backtest --source synthetic
  python main.py backtest --source binance --symbol BTC/USDT --interval 1h
  python main.py paper --symbol BTC/USDT --interval 1m
  python main.py live --exchange binance --symbol BTC/USDT --interval 1h   (sandbox)
"""
from __future__ import annotations

import argparse
import logging
import sys

from bot.backtest import run_backtest
from bot.broker import CcxtBroker, PaperBroker
from bot.data import Candle, fetch_binance, load_csv, synthetic
from bot.risk import RiskConfig
from bot.runner import TradingBot
from bot.strategy import MaCrossoverStrategy

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bot de trading EMA + RSI")
    sub = p.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--symbol", default="BTC/USDT")
    common.add_argument("--interval", default="1h", choices=INTERVAL_SECONDS)
    common.add_argument("--fast", type=int, default=12)
    common.add_argument("--slow", type=int, default=26)
    common.add_argument("--stop-loss", type=float, default=0.02, help="ex. 0.02 = -2 %%")
    common.add_argument("--take-profit", type=float, default=0.04, help="ex. 0.04 = +4 %%")
    common.add_argument("--risk", type=float, default=0.01, help="part du capital risquée par trade")

    bt = sub.add_parser("backtest", parents=[common], help="tester la stratégie sur l'historique")
    bt.add_argument("--source", choices=["synthetic", "binance", "csv"], default="synthetic")
    bt.add_argument("--csv", help="chemin du fichier CSV (avec --source csv)")
    bt.add_argument("--capital", type=float, default=10_000.0)
    bt.add_argument("--trades", action="store_true", help="afficher le détail des trades")

    paper = sub.add_parser("paper", parents=[common], help="trading simulé sur données réelles")
    paper.add_argument("--capital", type=float, default=10_000.0)
    paper.add_argument("--steps", type=int, help="nombre d'itérations (défaut : infini)")

    live = sub.add_parser("live", parents=[common], help="ordres RÉELS via ccxt")
    live.add_argument("--exchange", default="binance")
    live.add_argument("--real-money", action="store_true",
                      help="désactive le mode sandbox : de l'argent réel sera engagé")
    live.add_argument("--steps", type=int)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    strategy = MaCrossoverStrategy(fast=args.fast, slow=args.slow)
    risk = RiskConfig(risk_per_trade=args.risk, stop_loss_pct=args.stop_loss, take_profit_pct=args.take_profit)
    limit = max(strategy.warmup * 3, 200)

    if args.mode == "backtest":
        if args.source == "csv":
            if not args.csv:
                print("--csv est requis avec --source csv", file=sys.stderr)
                return 2
            candles = load_csv(args.csv)
        elif args.source == "binance":
            candles = fetch_binance(args.symbol, args.interval, 1000)
        else:
            candles = synthetic()
        result = run_backtest(candles, strategy, risk, args.capital)
        print(result.summary())
        if args.trades:
            for t in result.trades:
                print(f"  {t.entry_price:>12.4f} -> {t.exit_price or 0:>12.4f}  {t.pnl:+10.2f}  {t.reason}")
        return 0

    if args.mode == "paper":
        bot = TradingBot(
            PaperBroker(args.capital, risk.fee_pct),
            lambda: fetch_binance(args.symbol, args.interval, limit),
            strategy, risk, state_file=".state/paper.json",
        )
    else:
        broker = CcxtBroker(args.exchange, args.symbol, sandbox=not args.real_money)
        if args.real_money:
            answer = input(f"ATTENTION : ordres réels sur {args.exchange} {args.symbol}. Tapez OUI pour continuer : ")
            if answer.strip() != "OUI":
                print("Annulé.")
                return 1

        def fetch() -> list[Candle]:
            return [Candle(int(r[0]), *map(float, r[1:6])) for r in broker.fetch_ohlcv(args.interval, limit)]

        bot = TradingBot(broker, fetch, strategy, risk, state_file=f".state/live-{args.exchange}.json")

    # Interroger plus souvent que la durée d'une bougie pour réagir aux stops.
    bot.run(poll_seconds=min(60, INTERVAL_SECONDS[args.interval]), max_steps=args.steps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
