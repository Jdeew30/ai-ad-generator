# VintedBot — alertes de bonnes affaires pour l'achat-revente

VintedBot surveille Vinted en continu et vous envoie sur **Telegram** ou **Discord**, en quelques
secondes, les annonces **nettement sous le prix du marché**, avec la **marge estimée** une fois tous
les frais déduits. Il couvre les consoles, les jeux vidéo, la high-tech, les vêtements de marque,
les sneakers et les cartes à collectionner.

```
✅ BONNE AFFAIRE · PS5 Slim · 65/100
Console PS5 Slim + 2 manettes
💶 215,00 € · Très bon état · Sony
📊 Valeur ≈ 334,50 € (médiane de 92 annonces) → -36 %
💰 Marge estimée ≈ +67 € (revente ≈ 301,05 €, coût total ≈ 234,45 € avec protection et port)
⏱ il y a 3 min · ❤️ 2 favori(s) · 👤 vendeur
🎯 36 % sous le marché et ≈ 67 € de marge
[ Voir l'annonce sur Vinted ]
```

## Sommaire

- [Démarrage rapide](#démarrage-rapide)
- [Configurer vos recherches](#configurer-vos-recherches)
- [Comment le bot repère une bonne affaire](#comment-le-bot-repère-une-bonne-affaire)
- [Profils d'expert](#profils-dexpert)
- [Notifications](#notifications)
- [Commandes](#commandes)
- [Guide de l'acheteur-revendeur](#guide-de-lacheteur-revendeur)
- [Faire tourner le bot 24 h/24](#faire-tourner-le-bot-24-h24)
- [Limites et précautions](#limites-et-précautions)

## Démarrage rapide

### En double-clic (le plus simple)

1. Installez **Python 3.11 ou plus récent** depuis [python.org](https://www.python.org/downloads/).
   Sous Windows, **cochez « Add python.exe to PATH »** pendant l'installation.
2. Double-cliquez sur **`Lancer VintedBot (Windows).bat`** ou **`Lancer VintedBot (Mac).command`**.
   Sur Mac, la première fois, faites un clic droit puis « Ouvrir » si macOS bloque le fichier.
3. L'assistant vous guide : choisissez Telegram, puis collez le token donné par **@BotFather**.
   Il trouve tout seul votre conversation et vous envoie un message de test.
4. Le bot démarre. **Laissez la fenêtre ouverte** : les alertes arrivent sur votre téléphone.
   Pour arrêter, fermez la fenêtre. Pour relancer, double-cliquez à nouveau.

Pour modifier vos recherches, ouvrez `config.toml` avec le Bloc-notes ou TextEdit.

### En ligne de commande

Aucune autre installation que Python n'est nécessaire.

```bash
cd vinted-bot
python -m vintedbot setup                     # assistant : config, Telegram ou Discord
python -m vintedbot init                      # (ou) crée seulement config.toml et .env
python -m vintedbot doctor                    # vérifie que Vinted répond
python -m vintedbot analyze "switch oled" --preset console --min-price 100
python -m vintedbot check                     # marché + meilleures offres actuelles de vos recherches
python -m vintedbot run                       # surveillance en continu
```

**Commencez par `analyze`.** C'est une étude de marché instantanée : prix médian, prix par état,
prix d'achat maximum pour atteindre votre marge, meilleures offres en ligne. Elle se termine par le
bloc à copier dans `config.toml`.

## Configurer vos recherches

Chaque recherche est un bloc `[[watch]]` dans `config.toml`. Le fichier d'exemple en contient
plusieurs : PS5, Switch OLED, Steam Deck, Zelda, Nike Tech Fleece, Carhartt, AirPods.

```toml
[[watch]]
name = "PS5 Slim"
query = "ps5 slim"         # ou url = "…" : une recherche Vinted copiée depuis votre navigateur
preset = "console"         # profil d'expert (voir plus bas)
min_price = 150            # écarte jeux, manettes, façades vendus seuls
max_price = 380            # plafond des alertes
min_discount = 0.25        # au moins -25 % sous le marché…
min_profit = 40            # …et au moins 40 € de marge nette estimée
```

| Clé | Rôle |
|---|---|
| `query` / `url` | Mots-clés, **ou** URL d'une recherche Vinted avec tous vos filtres (taille, marque, état, catégorie). C'est pratique pour les vêtements et les sneakers |
| `preset` | Profil d'expert : `general`, `console`, `jeu_video`, `smartphone`, `audio`, `vetement`, `sneakers`, `cartes_tcg` |
| `min_price` | **Important** : écarte les accessoires et les annonces hors sujet, ce qui fiabilise le prix du marché |
| `max_price` | Plafond des alertes. Le bot continue d'apprendre le marché au-dessus |
| `min_discount`, `min_profit` | Seuils d'alerte : décote par rapport au marché et marge nette en euros |
| `target_price` | Alerte immédiate dès que le prix est inférieur ou égal, même sans historique |
| `market_price` | Valeur du marché fixée à la main (sinon apprise automatiquement) |
| `include` / `exclude` | Mots obligatoires (tous) / interdits (un seul suffit) |
| `conditions` | États acceptés : `neuf_etiquette`, `neuf`, `tres_bon`, `bon`, `satisfaisant` |
| `red_flags` | Mots à signaler en plus de ceux du profil |
| `allow_defective` | `true` pour recevoir aussi les articles HS (réparateurs) |
| `shipping` | Port à l'achat pour cette recherche |
| `poll_seconds`, `enabled` | Fréquence propre, activation |

**Bien choisir une recherche.** Le prix du marché appris n'a de sens que si les annonces se
ressemblent. « PS5 Slim » ou « Zelda Tears of the Kingdom » fonctionnent bien. « Jeux Switch » ne
fonctionne pas : un jeu à 5 € et un à 60 € n'ont rien de comparable. Pour une catégorie hétérogène,
utilisez `target_price`.

## Comment le bot repère une bonne affaire

1. **Filtres d'expert** : il écarte les boîtes vides, les accessoires vendus seuls (« coque pour… »,
   « manette seule »), les contrefaçons, les annonces « je recherche » et les articles HS. Les lots
   légitimes, comme « PS5 + support + 2 manettes », sont conservés.
2. **Prix du marché appris** : c'est la médiane des annonces comparables vues sur 30 jours, sans les
   valeurs aberrantes. Au premier lancement, le bot lit environ 150 annonces pour démarrer. Il lui
   faut au moins 15 annonces comparables (`min_samples`) avant d'alerter sur la décote.
3. **Ajustement selon l'état** : valeur de référence × 1,15 pour du neuf avec étiquette, × 0,88 pour
   un bon état, × 0,72 pour un état satisfaisant.
4. **Coût réel d'achat** : prix, plus protection acheteur (le total exact donné par Vinted, sinon
   0,70 € + 5 %), plus le port. Le port dépend du profil : environ 8 € pour une console, 4 € pour un
   vêtement.
5. **Revente réaliste** : valeur du marché moins 10 % (`resale_haircut`). Une annonce affichée
   n'est pas une vente conclue, et vous devrez sans doute accepter une offre.
6. **Score sur 100** : décote (jusqu'à 60 points), rentabilité (25), fraîcheur de l'annonce (10) et
   état neuf (5). Chaque signal d'alerte (« drift », « rayures », « taché »…) retire 15 points.

| Étiquette | Signification |
|---|---|
| 🔥 **PÉPITE** | Score ≥ 75 : à saisir immédiatement |
| ✅ **BONNE AFFAIRE** | Score ≥ 55 |
| 👀 **À SURVEILLER** | Seuils atteints, mais score modeste (ou simple prix cible atteint) |
| ⚠️ **À VÉRIFIER** | Trop beau pour être vrai (plus de 65 % sous le marché), contrefaçon possible ou article HS. Toujours classée après les vraies affaires |

Les annonces qui baissent leur prix d'au moins 3 % sont réévaluées : vous êtes prévenu si une
annonce devient une affaire.

## Profils d'expert

`python -m vintedbot presets` les affiche tous.

| Profil | Écarte | Signale | Port |
|---|---|---|---|
| `console` | boîte vide, manette seule, « coque pour… », skins, cartes cadeaux, HS | drift, banni, sans manette, rayures, surchauffe | 8 € |
| `jeu_video` | boîtier vide, jaquette ou notice seule, codes seuls, DLC, HS | rayé, sans notice, loose, copie | 3,50 € |
| `smartphone` | « coque pour… », verre trempé, pièces détachées, HS | iCloud, blacklist, Face ID, opérateur | 4,50 € |
| `audio` | contrefaçons, écouteur seul, boîtier seul, HS | son faible, sans boîte, sans facture | 4 € |
| `vetement` | contrefaçons (réplique, inspired, dupe…) | taché, troué, bouloché, « façon » | 4 € |
| `sneakers` | contrefaçons, boîte ou lacets seuls | sans boîte, usées, semelle décollée | 6 € |
| `cartes_tcg` | proxys, customs, fan made | abîmée, pliée, jouée | 3 € |
| `general` | annonces « recherche », HS | contrefaçons | 4,50 € |

## Notifications

**Telegram** : vous recevez la photo de l'annonce avec un bouton « Voir l'annonce ».
1. Écrivez à **@BotFather**, envoyez `/newbot` et récupérez le token.
2. Écrivez à **@userinfobot** pour obtenir votre chat_id.
3. Envoyez un premier message à votre bot.
4. Dans `.env`, renseignez `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`. Dans `config.toml`, mettez
   `[telegram] enabled = true`.

**Discord** : sur un salon, ouvrez Paramètres › Intégrations › Webhooks › Nouveau webhook et copiez
l'URL dans `DISCORD_WEBHOOK_URL`. Dans `config.toml`, mettez `[discord] enabled = true`.

Testez ensuite avec `python -m vintedbot test-notify`.

## Commandes

| Commande | Rôle |
|---|---|
| `python -m vintedbot setup` | Assistant : crée la configuration et connecte Telegram ou Discord |
| `python -m vintedbot init` | Crée `config.toml` et `.env` |
| `python -m vintedbot doctor` | Vérifie la connexion à Vinted et l'analyse des annonces |
| `python -m vintedbot analyze "produit"` | Étude de marché et prix d'achat maximum (`--preset`, `--min-price`, `--min-profit`, `--include`, `--exclude`, `--pages`) |
| `python -m vintedbot check [--watch NOM]` | Un passage à blanc : marché et meilleures offres actuelles, sans rien enregistrer |
| `python -m vintedbot run` | Surveillance en continu |
| `python -m vintedbot test-notify` | Envoie une alerte de démonstration |
| `python -m vintedbot stats` | Ce que le bot a appris pour chaque recherche |
| `python -m vintedbot presets` | Liste des profils |

## Guide de l'acheteur-revendeur

### Acheter

- **La vitesse fait tout.** Une vraie pépite part en quelques minutes. Gardez les notifications
  actives et préparez un message type pour le vendeur.
- **Négociez.** Le bouton « Faire une offre » à 10–20 % sous le prix passe souvent, surtout sur les
  annonces de plus de quelques jours. Achetez en lot chez un même vendeur pour payer un seul port.
- **Vérifiez le vendeur** : nombre d'évaluations, ancienneté, autres articles. Un compte neuf qui
  vend une console à -60 % est un signal d'alarme.
- **Payez uniquement via Vinted**, jamais par virement, PayPal « entre proches » ou WhatsApp.
  Hors de Vinted, vous perdez la protection acheteur. C'est l'arnaque la plus fréquente.
- **À réception**, vérifiez tout avant de confirmer que la commande est conforme. Vinted laisse un
  court délai (2 jours) pour signaler un problème ; une fois validée, la vente est définitive.

### Vérifications par catégorie

- **Consoles** : demandez une **vidéo de la console allumée** et le numéro de série. Sur Switch,
  vérifiez le *drift* des Joy-Con et le modèle (V1, V2, OLED). Sur PS5, distinguez lecteur et
  Digital, et standard et Slim. Comptez un port de 7 à 10 € pour un colis lourd.
- **Jeux vidéo** : un jeu rétro **complet en boîte** vaut souvent bien plus qu'une cartouche
  seule. Méfiez-vous des **cartouches contrefaites** (Pokémon GBA/DS très copiés : étiquette,
  poids, puce). Vérifiez la région (PAL).
- **Smartphones** : exigez l'appareil **déconnecté d'iCloud ou du compte Google**. Vérifiez que
  l'IMEI n'est pas sur liste noire, et l'état de la batterie (capture d'écran).
- **AirPods et casques** : c'est la catégorie la plus contrefaite. Comparez le numéro de série sur
  le site du fabricant. Un prix 40 % sous le marché pour du « neuf » est presque toujours une copie.
- **Vêtements de marque** : demandez des photos des étiquettes, des coutures et des logos. Les
  mesures à plat valent mieux que la taille indiquée. La saison compte : achetez les doudounes au
  printemps et revendez-les en octobre.
- **Sneakers** : boîte d'origine, étiquette de taille intérieure, *legit check* pour les modèles
  recherchés (Jordan, Dunk, Yeezy).

### Revendre

- Photos lumineuses sur fond neutre, défauts compris : la transparence évite les litiges.
- Titre avec les mots que les acheteurs tapent : marque, modèle, taille, état (« PS5 Slim lecteur
  1 To + 2 manettes »).
- Affichez 5 à 10 % au-dessus de votre prix visé pour laisser de la place aux offres.

### Règles et fiscalité (France)

- **Vendre ses propres affaires d'occasion** reste en principe non imposable. **Acheter pour
  revendre avec bénéfice est une activité commerciale** : elle doit être déclarée, par exemple en
  micro-entreprise, dès qu'elle devient habituelle.
- Depuis 2023 (directive européenne DAC7), Vinted transmet à l'administration fiscale les données
  des vendeurs qui dépassent **30 ventes ou 2 000 € par an**.
- Vinted est destiné aux particuliers. Les professionnels doivent passer par **Vinted Pro**.
- En cas de doute, faites-vous conseiller (expert-comptable, URSSAF, service des impôts).

## Faire tourner le bot 24 h/24

Sur un petit serveur (VPS) ou un Raspberry Pi :

```bash
cp config.example.toml config.toml && cp .env.example .env   # puis personnalisez
docker compose up -d --build
docker compose logs -f
```

Sans Docker : `nohup python -m vintedbot run &`, ou un service systemd.

## Limites et précautions

- **Conditions d'utilisation de Vinted.** Vinted ne propose pas d'API publique, et ses conditions
  d'utilisation n'autorisent pas la collecte automatisée de données. Le bot reste volontairement
  discret : il lit seulement les résultats de recherche publics, **sans compte**, au rythme d'une
  requête toutes les 3 secondes au plus. Il ne fait **aucun achat ni message automatique**. Vinted
  peut tout de même bloquer temporairement votre adresse IP ; le bot ralentit alors tout seul. Vous
  l'utilisez sous votre responsabilité.
- **Si Vinted change son site**, la recherche peut cesser de fonctionner. `python -m vintedbot doctor`
  indique où ça coince. Les adresses et paramètres sont regroupés en haut de `vintedbot/client.py`.
- **Ses estimations ne valent pas une expertise.** La valeur de marché est calculée à partir des
  prix **demandés**, pas des prix de vente réels. Comparez avec les objets *vendus* sur eBay pour
  les achats importants.
- La description complète de l'annonce n'est pas analysée, seulement le titre : Vinted bloque
  l'accès automatisé aux pages de détail. Lisez toujours l'annonce avant d'acheter.

## Tests

```bash
python -m unittest discover -s tests -v
```

Les tests couvrent les filtres d'expert (les lots légitimes sont gardés, les accessoires écartés),
le calcul du marché, des marges et des scores, le client Vinted (session, renouvellement, blocage,
anciens et nouveaux formats d'annonce), la surveillance (démarrage, nouvelles annonces, baisses de
prix, pas de doublons, pause en cas de blocage), les notifications et la configuration.
