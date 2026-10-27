# Guide de revente

Ce guide vous aide à vendre TradeBot de façon durable et sans vous mettre en danger juridiquement.

> Ce document donne des repères généraux ; ce n'est pas un avis juridique ni fiscal. Faites valider
> votre modèle (statut, contrats, mentions, fiscalité) par un avocat et un expert-comptable avant de
> commencer à vendre, en particulier sur la réglementation des crypto-actifs.

## 1. Ce que vous vendez : un logiciel, pas des gains

Le modèle le plus simple et le moins risqué est la **vente d'un logiciel auto-hébergé** :

- le client installe le bot sur **sa** machine ou **son** serveur ;
- il utilise **son** compte d'exchange et **ses** clés API ;
- vous ne touchez **jamais** à son argent ni à ses clés.

Vous vendez un outil, de la documentation, des mises à jour et du support, **pas une promesse de rendement**.

### Formules possibles

| Formule | Contenu | Points d'attention |
|---|---|---|
| **Licence unique** | Le bot + la documentation + X mois de mises à jour | Simple ; revenu ponctuel |
| **Abonnement** | Accès aux mises à jour, nouvelles stratégies, support | Revenu récurrent ; il faut livrer régulièrement |
| **Installation clé en main** | Vous installez le bot sur le VPS du client, en sa présence | Ne conservez aucun accès ni aucune clé ensuite |
| **Marque blanche** | Un revendeur le vend sous son nom (`brand` dans `config.toml`) | Contrat de revendeur à prévoir |
| **Formation** | Apprendre à backtester, gérer le risque, utiliser le bot | Rester pédagogique : pas de conseil personnalisé |

### Ce qu'il ne faut PAS faire sans autorisation

Ces activités relèvent de services réglementés, notamment par le règlement européen **MiCA**
(prestataires de services sur crypto-actifs, agréés par l'AMF en France) ou par le régime du conseil
en investissement :

- **gérer l'argent de vos clients**, même via leurs clés API hébergées chez vous (gestion de portefeuille pour le compte de tiers) ;
- **recevoir ou exécuter des ordres pour le compte de clients** ;
- donner des **recommandations personnalisées** (« achète tel crypto maintenant ») ;
- **conserver les fonds** ou les clés des clients.

Un **SaaS** (bot hébergé chez vous, clés des clients sur vos serveurs) peut entrer dans ces catégories.
Il fait aussi de vous une cible pour les pirates. Ne vous lancez pas dans ce modèle sans un avis juridique
et un vrai niveau de sécurité.

## 2. Communication : ce qui est interdit et ce qui inspire confiance

Le marché des « robots de trading » est rempli d'arnaques, et l'AMF publie régulièrement des mises en
garde à leur sujet. Votre meilleur argument est la **transparence**.

**À ne jamais faire :**
- promettre ou laisser entendre des gains (« +30 % par mois », « revenu passif garanti », « sans risque ») ;
  c'est une pratique commerciale trompeuse ;
- montrer uniquement les meilleurs résultats ou une période choisie sur mesure ;
- publier de faux témoignages ou des captures retouchées ;
- payer des influenceurs pour promouvoir des services sur crypto-actifs sans vérifier les règles
  applicables (loi française du 9 juin 2023 sur l'influence commerciale).

**À faire :**
- présenter le bot comme un **outil d'automatisation et de gestion du risque** ;
- montrer des backtests **complets** (rapport HTML), avec drawdown, trades perdants et comparaison au buy & hold ;
- si vous publiez des résultats réels, publier **tout** l'historique, pertes comprises, et préciser la période ;
- afficher partout : « Le trading comporte un risque de perte en capital. Les performances passées ne
  préjugent pas des performances futures. »

## 3. Documents à préparer

- **Contrat de licence utilisateur** : un modèle est dans [`LICENSE`](../LICENSE). Complétez-le et faites-le relire.
- **Conditions générales de vente** : prix, livraison, mises à jour, support, remboursement. Pour un
  contenu numérique livré immédiatement, le droit de rétractation de 14 jours ne s'applique que si le
  client y renonce expressément ; prévoyez la case à cocher.
- **Avertissement sur les risques**, à faire accepter avant l'achat.
- **Mentions légales** et **politique de confidentialité (RGPD)** si vous collectez des données clients.
- **Fiscalité** : statut (micro-entreprise, société), TVA sur les services numériques (guichet OSS pour
  les particuliers dans l'UE).

## 4. Licences des composants utilisés

| Composant | Licence | Ce qu'il faut faire |
|---|---|---|
| Python | PSF | Rien de particulier |
| ccxt | MIT | Si vous livrez ccxt avec le bot (image Docker), joignez sa notice de licence MIT. S'il est installé par le client via `pip`, rien à faire |
| Code de TradeBot | Le vôtre | Choisissez votre licence (modèle propriétaire fourni) |

## 5. Protéger votre travail

Le code Python est lisible par l'acheteur, et aucune protection n'est inviolable. Ce qui protège
réellement votre activité :

- la **valeur continue** : mises à jour, nouvelles stratégies, support réactif, communauté ;
- un **contrat de licence** qui interdit la revente et la redistribution ;
- la livraison via un **dépôt Git privé** ou un **registre Docker privé**, avec un accès par client que vous pouvez révoquer ;
- en option, une **compilation** (Nuitka) ou un **obscurcissement** (PyArmor). C'est dissuasif, pas infaillible.

Une vérification de clé de licence dans le code se contourne facilement en Python. Elle ne vaut le coup
que couplée à un service en ligne (téléchargement des mises à jour, par exemple).

## 6. Check-list avant la première vente

- [ ] Le projet est dans **son propre dépôt Git**, séparé de vos autres projets
- [ ] Tous les tests passent : `python -m unittest discover -s tests`
- [ ] Vous avez fait tourner le bot vous-même : plusieurs semaines en paper, puis en testnet
- [ ] Vous avez des backtests complets (rapports HTML) sur plusieurs paires et périodes
- [ ] Le nom commercial est choisi et ne reprend pas une marque existante (vérification INPI / EUIPO)
- [ ] Contrat de licence, CGV, avertissement des risques et mentions légales sont validés par un juriste
- [ ] Vous avez un canal de support (e-mail, Discord…) et un délai de réponse annoncé
- [ ] Les numéros de version suivent les mises à jour (`tradebot/__init__.py`) et un journal des changements existe
- [ ] Toute votre communication est relue avec la section 2 en tête

## 7. Idées d'évolutions payantes

- Nouvelles stratégies (grille, DCA, multi-unités de temps)
- Ordres stop posés directement sur l'exchange (protection même si le bot est arrêté)
- Vente à découvert sur les marchés à terme (risque plus élevé : à réserver aux utilisateurs avertis)
- Tableau de bord multi-bots, export comptable des trades
- Assistant d'installation graphique
