# TradeBot — bot de trading crypto auto-hébergé

Bot de trading automatique pour le marché **spot** des cryptomonnaies, sur plus de 100 exchanges
(Binance, Kraken, Bybit, OKX, Coinbase… via [ccxt](https://github.com/ccxt/ccxt)).
Il s'installe sur votre ordinateur ou un serveur : **vos clés API ne quittent jamais votre machine**.

> ⚠️ **Avertissement.** Le trading de cryptomonnaies comporte un risque élevé de perte en capital.
> Aucun bot ni aucune stratégie ne garantit de gains, et un bon backtest ne prédit pas les résultats
> futurs. Testez longtemps en simulation, commencez avec de petits montants, et n'engagez jamais
> d'argent dont vous avez besoin.

## Sommaire

- [Fonctionnalités](#fonctionnalités)
- [Démarrage rapide](#démarrage-rapide)
- [Stratégies](#stratégies)
- [Gestion du risque](#gestion-du-risque)
- [Commandes](#commandes)
- [Passer en argent réel](#passer-en-argent-réel)
- [Telegram](#telegram)
- [Tableau de bord](#tableau-de-bord)
- [Docker et serveur](#docker-et-serveur)
- [Comment fonctionne le bot](#comment-fonctionne-le-bot)
- [Ajouter une stratégie](#ajouter-une-stratégie)
- [Tests](#tests)

Pour vendre ce bot, lisez aussi le [guide de revente](docs/GUIDE-REVENTE.md).

## Fonctionnalités

| | |
|---|---|
| **4 stratégies** | Tendance (EMA + RSI), retour à la moyenne (Bollinger + RSI), cassure (Donchian), momentum (MACD) |
| **Gestion du risque** | Taille de position calculée sur le risque, stop-loss fixe ou selon la volatilité (ATR), objectif de gain, stop suiveur |
| **Garde-fous** | Coupe-circuit de perte journalière, nombre maximal de positions, plafond par position, minimums de l'exchange vérifiés |
| **3 modes** | `paper` (argent fictif sur prix réels), `sandbox` (testnet de l'exchange), `live` (argent réel, confirmation obligatoire) |
| **Backtest** | Sans biais d'anticipation, frais et glissement inclus, rapport HTML avec courbe et liste des trades |
| **Optimisation** | Recherche des meilleurs paramètres avec validation sur une période jamais vue (détection du sur-ajustement) |
| **Multi-paires** | Plusieurs cryptos en parallèle, capital partagé |
| **Tableau de bord web** | Capital, positions, trades, journal ; boutons Pause / Reprendre / Tout fermer ; protégé par mot de passe |
| **Telegram** | Alertes d'achat/vente, bilan quotidien, erreurs ; commandes à distance (`/statut`, `/pause`, `/fermer_tout`…) |
| **Fiabilité** | Reprise après redémarrage (SQLite), une paire en erreur ne bloque pas les autres, arrêt propre |
| **Marque blanche** | Nom du produit configurable (`brand`), affiché partout |
| **Léger** | Python 3.11+, aucune dépendance pour le backtest et le mode paper ; ccxt pour le reste |

## Démarrage rapide

Il faut **Python 3.11 ou plus récent** ([python.org](https://www.python.org/downloads/)).

```bash
cd trading-bot
python3 -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -r requirements.txt

python -m tradebot init              # crée config.toml et .env
python -m tradebot backtest          # teste la stratégie sur 1 an de BTC et ETH
python -m tradebot run               # lance le bot en mode paper (argent fictif)
```

Ouvrez ensuite http://localhost:8080 : le mot de passe du tableau de bord est celui de
`DASHBOARD_PASSWORD` dans `.env`. S'il est vide, un mot de passe temporaire s'affiche au démarrage.

Sans connexion à un exchange, vous pouvez tout essayer sur un marché fictif :

```bash
python -m tradebot backtest --source synthetic --report rapport.html
```

## Stratégies

`python -m tradebot strategies` affiche la liste à jour avec tous les paramètres.

| Nom | Type | Idée | Marché favorable |
|---|---|---|---|
| `ema_rsi` | Tendance | Achat quand l'EMA rapide croise au-dessus de la lente, si le RSI n'est pas en surachat | Tendances franches |
| `bollinger_rsi` | Retour à la moyenne | Achat des excès de baisse sous la bande de Bollinger basse, dans une tendance haussière ; revente au retour sur la moyenne | Marchés sans direction, ou haussiers avec des replis |
| `donchian_breakout` | Cassure | Achat quand le prix dépasse le plus haut des N dernières bougies (méthode « Turtle ») | Grands mouvements ; beaucoup de petites pertes, quelques gros gains |
| `macd_trend` | Momentum | Croisement MACD / signal, filtré par une moyenne de tendance | Hausses régulières |

Choisir une stratégie et ses paramètres dans `config.toml` :

```toml
[strategy]
name = "donchian_breakout"

[strategy.params]
entry_period = 40
exit_period = 20
```

**Méthode conseillée :**
1. `backtest` sur au moins 1 à 2 ans et plusieurs paires.
2. `optimize` : retenez une combinaison qui reste correcte sur la période de **test**, pas seulement la meilleure à l'entraînement.
3. Plusieurs semaines en `paper`, puis en `sandbox`, avant tout argent réel.

## Gestion du risque

Section `[risk]` de `config.toml`. Ces règles s'appliquent à toutes les stratégies, en backtest comme en réel.

| Paramètre | Défaut | Rôle |
|---|---|---|
| `risk_per_trade` | `0.01` | Part du capital perdue au maximum si le stop est touché (1 %) |
| `stop_loss_pct` | `0.03` | Stop à -3 % sous le prix d'entrée |
| `stop_atr_mult` | `0` | Si > 0, stop à N × ATR sous l'entrée : plus large quand le marché est agité |
| `take_profit_rr` | `2.0` | Objectif = 2 × la distance du stop (gain visé deux fois supérieur au risque). `0` = laisser courir |
| `trailing_stop_pct` | `0` | Stop suiveur : suit le plus haut atteint à -X % |
| `max_position_pct` | `0.25` | Au plus 25 % du capital dans une même position |
| `max_open_positions` | `3` | Nombre maximal de positions simultanées |
| `max_daily_loss_pct` | `0.05` | Coupe-circuit : après -5 % dans la journée (UTC), plus aucun achat jusqu'au lendemain |
| `fee_pct` | `0.001` | Frais de l'exchange par ordre |

**Exemple.** Avec 10 000 USDT, `risk_per_trade = 0.01` et un stop à -3 %, le bot achète pour environ
3 333 USDT afin que le stop ne coûte que 100 USDT. Le plafond de 25 % ramène cet achat à 2 500 USDT.

## Commandes

| Commande | Rôle |
|---|---|
| `python -m tradebot init` | Crée `config.toml` et `.env` à partir des modèles |
| `python -m tradebot strategies` | Liste des stratégies et de leurs paramètres |
| `python -m tradebot backtest` | Backtest sur les paires de la config |
| `python -m tradebot optimize` | Recherche de paramètres avec test hors échantillon |
| `python -m tradebot download --out btc.csv` | Télécharge l'historique en CSV |
| `python -m tradebot run` | Lance le bot (paper ou live selon la config) |
| `python -m tradebot reset-paper` | Remet le portefeuille simulé à zéro |

Options utiles de `backtest` et `optimize` :

```bash
--symbol SOL/USDT --timeframe 4h --days 730          # marché et période
--strategy macd_trend --param trend_ema=100          # stratégie et paramètres
--source csv --csv btc.csv                           # vos propres données
--source synthetic                                   # marché fictif, hors ligne
--report rapport.html --trades                       # rapport HTML et détail des trades
--metric calmar                                      # critère d'optimisation : sharpe, return, calmar
```

## Passer en argent réel

Suivez ces étapes dans l'ordre. Ne sautez pas la simulation.

1. **Paper** (`mode = "paper"`) pendant au moins 2 à 4 semaines. Comparez avec le backtest.
2. **Testnet** (`mode = "live"`, `sandbox = true`) avec des clés du testnet de l'exchange
   ([Binance Spot Testnet](https://testnet.binance.vision/)). Vous vérifiez ainsi les vrais ordres, sans risque.
3. **Clés API réelles** :
   - activez uniquement le droit de **trading** ;
   - **n'activez jamais le droit de retrait** ;
   - limitez la clé à l'adresse IP de votre serveur si l'exchange le permet ;
   - copiez-les dans `.env` (`EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET`).
4. **Argent réel** (`sandbox = false`) avec un **petit montant** : le bot utilise le solde disponible
   du compte. Au lancement, il faut taper `OUI` (ou définir `CONFIRM_REAL_MONEY=OUI` sous Docker).
5. Surveillez via Telegram et le tableau de bord.

Le bot ne gère que les positions **qu'il a ouvertes lui-même**. Si vous vendez une position à la main
sur l'exchange, il le détecte et cesse de la suivre.

## Telegram

1. Sur Telegram, écrivez à **@BotFather**, envoyez `/newbot` et récupérez le **token**.
2. Écrivez à **@userinfobot** pour obtenir votre **chat_id**.
3. Envoyez un premier message à votre nouveau bot (sinon il ne peut pas vous écrire).
4. Dans `.env` : `TELEGRAM_BOT_TOKEN=…` et `TELEGRAM_CHAT_ID=…`. Dans `config.toml` : `[telegram] enabled = true`.

Vous recevez alors les achats, les ventes avec leur résultat, le bilan quotidien, les coupe-circuits et les erreurs.
Commandes disponibles, acceptées uniquement depuis votre conversation :

| Commande | Effet |
|---|---|
| `/statut` | Capital, résultat du jour, positions |
| `/pause` | Plus aucun nouvel achat ; les positions restent protégées par leurs stops |
| `/reprendre` | Reprend les achats |
| `/fermer_tout` | Vend toutes les positions au marché et met le bot en pause |

## Tableau de bord

Il affiche le capital, le résultat du jour et le résultat total, la courbe du capital, les positions
(avec leur stop), les derniers trades et le journal. Il propose aussi les boutons **Pause**,
**Reprendre** et **Tout fermer**.

- Protégé par le mot de passe `DASHBOARD_PASSWORD` (l'identifiant est libre).
- Il n'écoute que sur la machine locale (`127.0.0.1`). **Ne l'exposez pas en HTTP sur Internet.**
  Pour y accéder depuis un serveur distant, utilisez un tunnel SSH :
  ```bash
  ssh -L 8080:localhost:8080 utilisateur@votre-serveur
  # puis ouvrez http://localhost:8080 sur votre ordinateur
  ```
  Autre option : un reverse proxy HTTPS (Caddy, Nginx) devant le port 8080.

## Docker et serveur

Pour un fonctionnement 24 h/24, utilisez un petit serveur VPS (1 vCPU et 1 Go de RAM suffisent).

```bash
cp config.example.toml config.toml   # puis personnalisez
cp .env.example .env                 # puis renseignez les secrets
docker compose up -d --build
docker compose logs -f               # suivre l'activité
```

Les données (base SQLite, journaux) sont conservées dans un volume Docker. Le conteneur redémarre
automatiquement après une panne ou un redémarrage du serveur.

## Comment fonctionne le bot

- Toutes les `poll_seconds` secondes, et pour chaque paire, il lit les bougies et le dernier prix.
- Il **protège d'abord** les positions ouvertes : stop-loss, stop suiveur et objectif, au dernier prix.
- À chaque **nouvelle bougie clôturée**, il applique le signal de la stratégie. Un signal n'est
  traité qu'une fois : après un stop, il ne rachète pas sur le même signal.
- Au tout premier démarrage, il attend la prochaine clôture plutôt que d'agir sur un signal ancien.
- Les ordres sont des ordres **au marché**. Les quantités respectent la précision et les minimums de l'exchange.
- L'état (positions, trades, capital, journal) est dans `data/*.sqlite`. Une base séparée est
  utilisée par mode, donc le paper ne se mélange jamais avec le réel.

**Limites à connaître.** Le bot fait du spot uniquement, à l'achat : pas de vente à découvert, pas
d'effet de levier. Il ouvre une seule position par paire. Les stops sont surveillés par le bot, pas
posés sur l'exchange : si le bot est arrêté, les positions ne sont plus protégées. Le backtest suppose
des ordres exécutés au prix d'ouverture de la bougie suivante, avec frais et glissement ; la réalité
peut être moins favorable lors de mouvements très violents.

## Ajouter une stratégie

Dans `tradebot/strategies.py` :

```python
@register
@dataclass
class MaStrategie(Strategy):
    name: ClassVar[str] = "ma_strategie"
    label: ClassVar[str] = "Ma stratégie"
    description: ClassVar[str] = "Ce qu'elle fait, en une phrase."
    search_space: ClassVar[dict[str, list[Any]]] = {"period": [10, 20, 50]}  # pour optimize

    period: int = 20

    @property
    def warmup(self) -> int:
        return self.period + 1

    def signals(self, candles):
        closes = [c.close for c in candles]
        moyenne = sma(closes, self.period)
        return [Signal.BUY if m is not None and c > m * 1.02 else
                Signal.SELL if m is not None and c < m else Signal.HOLD
                for c, m in zip(closes, moyenne)]
```

Elle est aussitôt disponible dans `backtest`, `optimize` et `run`. Règle d'or : le signal à
l'indice `i` ne doit utiliser que les bougies `0..i`. Le test `test_signals_do_not_use_future_data`
le vérifie automatiquement pour toutes les stratégies.

## Tests

```bash
python -m unittest discover -s tests -v
```

Les tests couvrent les indicateurs, les stratégies (y compris l'absence de données futures), la
gestion du risque, la comptabilité du backtest, le moteur (achats, stops, coupe-circuit, pause,
reprise après redémarrage, erreurs réseau), les exchanges (précision, minimums, frais), la
configuration, Telegram et la sécurité du tableau de bord.

## Structure

```
tradebot/
  strategies.py   les 4 stratégies + registre
  indicators.py   SMA, EMA, RSI, Bollinger, MACD, ATR, Donchian
  risk.py         taille de position, stops, objectifs (commun backtest / réel)
  backtest.py     simulation historique
  optimize.py     recherche de paramètres avec test hors échantillon
  stats.py        rendement, drawdown, Sharpe, profit factor...
  report.py       rapport HTML
  engine.py       boucle de trading en continu
  exchange.py     portefeuille simulé + ordres réels (ccxt)
  data.py         bougies : Binance public, ccxt, CSV, fictives
  store.py        base SQLite
  notify.py       Telegram
  dashboard.py    serveur du tableau de bord (+ web/dashboard.html)
  config.py       lecture et validation de config.toml et .env
  cli.py          commandes
```
