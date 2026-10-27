# Bot de trading (EMA + RSI)

Bot de trading crypto en Python, **sans dépendance** pour le backtest et la simulation
(`ccxt` est requis uniquement pour le mode live).

> ⚠️ **Avertissement** : le trading comporte un risque de perte en capital. Ce bot est un
> outil d'apprentissage. Aucune stratégie ne garantit de gains, et un bon backtest ne
> prédit pas les résultats futurs. Testez longtemps en `backtest` et `paper` avant
> d'envisager de l'argent réel, et n'engagez jamais plus que ce que vous pouvez perdre.

## Stratégie

- **Achat** : l'EMA rapide (12) croise au-dessus de l'EMA lente (26) et le RSI(14) est < 70.
- **Vente** : l'EMA rapide repasse sous l'EMA lente, ou le RSI dépasse 80.
- **Gestion du risque** :
  - stop-loss à -2 % et take-profit à +4 % ;
  - taille de position calculée pour ne risquer que 1 % du capital par trade ;
  - jamais plus de 50 % du capital dans une même position ;
  - frais de 0,1 % pris en compte.

Les signaux sont calculés sur des bougies **clôturées** et exécutés à la bougie suivante,
pour éviter tout biais d'anticipation dans le backtest.

## Installation

```bash
cd trading-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # uniquement nécessaire pour le mode live
```

## Utilisation

### 1. Backtest

```bash
python main.py backtest                                        # données fictives
python main.py backtest --source binance --symbol BTC/USDT --interval 1h
python main.py backtest --source csv --csv mes_bougies.csv --trades
python main.py backtest --fast 9 --slow 21 --stop-loss 0.03 --take-profit 0.06
```

Format CSV : `time,open,high,low,close,volume` (time en millisecondes).

### 2. Paper trading (simulation sur les prix réels de Binance)

```bash
python main.py paper --symbol BTC/USDT --interval 1m
```

Aucun ordre réel et aucune clé API. Le bot interroge le marché toutes les minutes au plus
et enregistre sa position dans `.state/paper.json`.

### 3. Live (ordres réels via ccxt)

```bash
cp .env.example .env    # renseignez vos clés API, puis :
export $(cat .env | xargs)
python main.py live --exchange binance --symbol BTC/USDT --interval 1h
```

- Par défaut, le mode live utilise le **sandbox / testnet** de l'exchange.
- `--real-money` désactive le sandbox et demande une confirmation (`OUI`).
- Créez des clés API **sans droit de retrait**, limitées à votre IP si possible.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Structure

```
bot/
  indicators.py  SMA, EMA, RSI
  strategy.py    signaux EMA + RSI
  risk.py        taille de position, stop-loss, take-profit
  data.py        bougies : CSV, API Binance publique, données fictives
  backtest.py    simulation historique + statistiques
  broker.py      PaperBroker (fictif) et CcxtBroker (réel)
  runner.py      boucle de trading en continu avec reprise après redémarrage
main.py          interface en ligne de commande
```

## Personnaliser

Pour créer votre propre stratégie, écrivez une classe qui expose `warmup`,
`signals(closes)` et `latest_signal(closes)` (voir `bot/strategy.py`), puis passez-la
à `run_backtest` ou à `TradingBot`.
