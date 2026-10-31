"""Annonces Vinted et états des articles."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# clé -> (libellé, identifiant Vinted, coefficient de valeur par rapport à « Très bon état »)
CONDITIONS: dict[str, tuple[str, int, float]] = {
    "neuf_etiquette": ("Neuf avec étiquette", 6, 1.15),
    "neuf": ("Neuf sans étiquette", 1, 1.08),
    "tres_bon": ("Très bon état", 2, 1.00),
    "bon": ("Bon état", 3, 0.88),
    "satisfaisant": ("Satisfaisant", 4, 0.72),
}
UNKNOWN_CONDITION_FACTOR = 0.95
_CONDITION_BY_ID = {vinted_id: key for key, (_, vinted_id, _) in CONDITIONS.items()}


def normalize(text: str) -> str:
    """Minuscules, sans accents ni ponctuation : « Boîte VIDE ! » -> « boite vide »."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def condition_key(label: str | None, status_id: int | None = None) -> str:
    """Convertit le libellé d'état Vinted (FR ou EN) en clé interne."""
    t = normalize(label or "")
    if t:
        if ("neuf" in t and "avec" in t) or "with tags" in t:
            return "neuf_etiquette"
        if "neuf" in t or "new" in t:
            return "neuf"
        if "tres bon" in t or "very good" in t:
            return "tres_bon"
        if "satisfaisant" in t or "satisfactory" in t:
            return "satisfaisant"
        if "bon" in t or "good" in t:
            return "bon"
    return _CONDITION_BY_ID.get(status_id or -1, "inconnu")


def condition_label(key: str) -> str:
    return CONDITIONS[key][0] if key in CONDITIONS else "État non précisé"


def condition_factor(key: str) -> float:
    return CONDITIONS[key][2] if key in CONDITIONS else UNKNOWN_CONDITION_FACTOR


@dataclass(frozen=True)
class Listing:
    id: int
    title: str
    price: float
    currency: str = "EUR"
    total_price: float | None = None  # prix + protection acheteur, si fourni par Vinted
    condition: str = "inconnu"
    brand: str = ""
    size: str = ""
    url: str = ""
    photo_url: str = ""
    seller: str = ""
    favourites: int = 0
    created_at: int | None = None  # secondes (date de mise en ligne des photos)

    @property
    def text(self) -> str:
        """Texte normalisé utilisé pour les mots-clés."""
        return normalize(f"{self.title} {self.brand}")
