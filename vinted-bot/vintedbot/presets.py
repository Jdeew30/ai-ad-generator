"""
Profils d'expert par catégorie.

- `exclude` : annonces écartées d'office (accessoire vendu seul, boîte vide, contrefaçon...).
  Elles fausseraient aussi le calcul du prix du marché.
- `red_flags` : annonces gardées mais signalées (état douteux, risque d'arnaque) ; le score baisse.

Les exclusions visent des formulations précises (« coque pour », « manette seule ») et non
les mots seuls, pour ne pas écarter les lots légitimes comme « PS5 + support + 2 manettes ».
Pour écarter les accessoires bon marché, réglez aussi `min_price` dans la recherche.
"""
from __future__ import annotations

from dataclasses import dataclass

COUNTERFEIT = (
    "replique", "replica", "fake", "contrefacon", "dupe", "inspired", "1 1", "master copy",
    "imitation", "non authentique", "pas authentique", "pas original", "non original",
)

BROKEN = (
    "hs", "en panne", "panne", "pour pieces", "pour piece", "ne fonctionne pas", "ne fonctionne plus",
    "ne s allume pas", "ne s allume plus", "defectueux", "defectueuse", "fissure", "fissuree",
    "a reparer", "bloque", "bloquee", "ecran casse", "ecran fissure",
)

WANTED = ("recherche", "cherche", "je cherche", "echange uniquement")

ACCESSORY_FOR = tuple(f"{a} pour" for a in (
    "coque", "etui", "housse", "sacoche", "pochette", "protection", "chargeur", "cable", "support",
    "socle", "skin", "sticker", "grip", "facade", "station de charge", "verre trempe",
))


@dataclass(frozen=True)
class Preset:
    label: str
    description: str
    exclude: tuple[str, ...] = ()
    red_flags: tuple[str, ...] = ()
    shipping: float | None = None  # port typique pour cette catégorie (colis lourd = plus cher)
    # Article déclaré HS : sa valeur n'a rien à voir avec celle du marché « qui fonctionne ».
    # Écarté par défaut (alertes et calcul du marché), sauf `allow_defective = true` (réparateurs).
    defective: tuple[str, ...] = BROKEN


PRESETS: dict[str, Preset] = {
    "general": Preset(
        "Général", "Signale les contrefaçons ; écarte les articles HS et les annonces « recherche ».",
        exclude=WANTED,
        red_flags=COUNTERFEIT,
    ),
    "console": Preset(
        "Consoles", "PS5, Switch, Xbox, Steam Deck... Écarte boîtes vides et accessoires vendus seuls.",
        exclude=WANTED + ACCESSORY_FOR + (
            "boite vide", "boite seule", "carton seul", "carton vide", "juste la boite", "emballage seul",
            "manette seule", "juste la manette", "uniquement la manette", "chargeur seul", "cable seul",
            "alimentation seule", "dock seul", "lecteur seul", "carte mere", "verre trempe", "skin",
            "sticker", "autocollant", "carte cadeau", "code seul",
        ),
        red_flags=COUNTERFEIT + (
            "drift", "banni", "ban", "sans manette", "sans cable", "sans alimentation", "rayure", "rayee",
            "surchauffe", "bruyante", "reparee", "repare", "manque",
        ),
        shipping=8.0,
    ),
    "jeu_video": Preset(
        "Jeux vidéo", "Jeux physiques. Écarte boîtiers vides, jaquettes et codes seuls.",
        exclude=WANTED + ("boitier vide", "boitier seul", "jaquette seule", "notice seule", "sans jeu",
                          "code seul", "code de telechargement", "code uniquement", "dlc", "steelbook vide"),
        red_flags=("raye", "rayures", "sans notice", "loose", "cartouche seule", "copie"),
        shipping=3.5,
    ),
    "smartphone": Preset(
        "Smartphones / tablettes", "iPhone, Samsung, iPad... Attention aux appareils bloqués.",
        exclude=WANTED + ACCESSORY_FOR + ("boite vide", "boite seule", "piece detachee", "pieces detachees",
                                          "ecran de remplacement", "chargeur seul", "cable seul"),
        red_flags=COUNTERFEIT + ("icloud", "compte google", "blacklist", "batterie faible", "face id",
                                 "touch id", "sim lock", "operateur"),
        shipping=4.5,
    ),
    "audio": Preset(
        "Audio (AirPods, casques)", "Les AirPods sont très contrefaits : un prix trop bas impose la prudence.",
        exclude=WANTED + COUNTERFEIT + ACCESSORY_FOR + (
            "boitier seul", "boitier de charge seul", "ecouteur gauche", "ecouteur droit",
            "embouts", "boite vide",
        ),
        red_flags=("son faible", "gresille", "batterie faible", "sans boite", "sans facture"),
        shipping=4.0,
    ),
    "vetement": Preset(
        "Vêtements de marque", "Ralph Lauren, Carhartt, The North Face, Stone Island... Écarte les contrefaçons.",
        exclude=WANTED + COUNTERFEIT,
        red_flags=("tache", "tachee", "taches", "trou", "troue", "trouee", "dechire", "dechiree", "abime",
                   "abimee", "bouloche", "boulochee", "decolore", "decoloree", "defaut", "facon"),
        defective=(),
        shipping=4.0,
    ),
    "sneakers": Preset(
        "Sneakers", "Jordan, Nike, New Balance, Salomon... Les contrefaçons sont très fréquentes.",
        exclude=WANTED + COUNTERFEIT + ("lacets seuls", "semelles seules", "boite seule", "boite vide"),
        red_flags=("sans boite", "usee", "usees", "semelle decollee", "tache", "taches", "crease",
                   "defaut", "facon"),
        defective=(),
        shipping=6.0,
    ),
    "cartes_tcg": Preset(
        "Cartes Pokémon / TCG", "Écarte proxys et cartes custom ; signale les cartes abîmées.",
        exclude=WANTED + COUNTERFEIT + ("proxy", "custom", "fan made", "fanmade", "orica"),
        red_flags=("abime", "abimee", "pliee", "pli", "rayee", "jouee", "played"),
        defective=(),
        shipping=3.0,
    ),
}
