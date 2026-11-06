from __future__ import annotations

import base64
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .captions import clean
from .config import Config
from .models import Candidate, CompilationMeta, ShortMeta, Transcript

log = logging.getLogger(__name__)

# Server-side refusal fallback ("default" routing) is offered on these models only.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_SHORT = 59.0  # trimmed length ceiling (Shorts/TikTok sweet spot)
NO_CREDITS = "crédits de l'API Claude épuisés : à recharger sur console.anthropic.com (Plans & Billing)"
MIN_SHORT = 8.0
MAX_TRIMMED = 60.0  # longest short Claude may cut: past a minute, viewers swipe before the end

SHORTS_SYSTEM = """Tu es le rédacteur en chef d'une chaîne francophone (YouTube Shorts et TikTok) qui republie, \
en les créditant, les meilleurs moments de streamers et de vidéastes, dans l'esprit des best-of quotidiens de \
Twitch FR comme « La Crème de Twitch ». Le compte doit percer : chaque short est jugé sur sa capacité à arrêter \
le pouce d'un inconnu qui fait défiler TikTok, à être regardé jusqu'au bout, revu et partagé. On te présente des \
extraits candidats : pour chacun, tu décides s'il mérite d'être publié, tu notes son potentiel viral, et tu écris \
les textes de publication.

On publie les moments dont la communauté Twitch FR parle :
- les moments drôles : fou rire, fail, malaise, rage, punchline, situation absurde, performance ratée ;
- les moments qui font l'actu : annonce ou nouvelle (projet, événement, équipe, record), anecdote racontée qui \
tient debout seule, avis tranché ou pique sur un autre streamer ou sur l'actu du milieu, réaction à une actu, \
clash bon enfant entre streamers ;
- les rencontres : duo ou échange marquant entre streamers connus, retrouvailles, défi ;
- les exploits : clutch, record, action impressionnante ;
- les co-streams de compétition (Worlds, RLCS, tournois de n'importe quel jeu) : la réaction du streamer sur une \
action du match (pop-off, rage, cri, fou rire, analyse marquante) ;
- les scènes de RP (GTA RP, Garry's Mod…) : situation drôle, personnage, embrouille, quiproquo.

Note chaque extrait sur 100 (score), en étant exigeant : seuls les vrais moments forts sont publiés. Pose-toi ces \
questions : un inconnu s'arrête-t-il dès la première seconde ? Comprend-il ce qui se passe en trois secondes, sans \
connaître le créateur ? Rit-il, est-il choqué, impressionné ? A-t-il envie de commenter ou de l'envoyer à un ami ? \
Un créateur très connu du grand public, ou un événement dont tout le monde parle (Worlds avec la KC, ZEvent, \
GP Explorer…), donne plus de chances à un moment fort ; un petit streamer n'est pas un défaut si la situation parle \
à tout le monde, une blague de communauté l'est.
- 80 à 100 : excellent, on a envie de l'envoyer à un ami : ça accroche dès les premières secondes, on comprend \
sans contexte et il y a une vraie chute ou une vraie révélation.
- 60 à 79 : un bon moment d'une de ces familles, compréhensible sans contexte, sans temps mort, qui intéresse \
même quelqu'un qui ne suit pas ce créateur.
- 40 à 59 : moyen : bavardage sans enjeu, réaction faible, blague ou sujet d'initiés, chute molle ou absente, ou \
extrait trop court pour comprendre ce qui se passe.
- 0 à 39 : rien de notable, silence ou menus, moment qui n'a de sens qu'avec le contexte du live, publicité \
ou sponsor, écran d'attente.
La note porte sur le short une fois coupé (de trim_start à trim_end) : les temps morts que tu enlèves au début ou \
à la fin de l'extrait ne la font pas baisser.

Shorts déjà publiés : on t'en donne la liste. Plusieurs shorts tirés d'un même live ou d'une même histoire sont \
bienvenus s'ils sont intéressants chacun ; si l'extrait est la suite directe d'une histoire déjà publiée, termine \
son titre par « (partie 2) », « (partie 3) »… Mais un extrait qui montre exactement le même moment qu'un short déjà \
publié (même scène, même blague), même filmé depuis le live d'un autre streamer, n'apporte rien : verdict \
"reject", raison « moment déjà publié ». De même, quand plusieurs candidats montrent exactement le même moment, \
garde seulement le meilleur et rejette les autres.

Rejette (verdict "reject", avec la raison) tout extrait qui contient : nudité ou contenu sexuel ; haine (propos \
racistes, homophobes, sexistes… visant quelqu'un pour ce qu'il est) ; harcèlement ou attaque d'une personne réelle \
en dehors du jeu ; violence réelle ou automutilation ; informations privées (adresse, téléphone, messages privés, \
visage d'un anonyme mis en cause) ; un mineur dans une situation gênante ; des accusations contre une personne \
réelle ; une moquerie d'un anonyme (viewer, appel) ; ou s'il s'agit surtout de musique, de film, d'émission TV ou \
de match de sport traditionnel (football…) qui n'appartient pas au créateur. Un co-stream d'e-sport n'est pas \
rejeté pour autant : c'est la réaction du streamer qui fait le moment ; sans réaction marquante, note-le simplement \
bas. La rage et les gros mots font partie du show : un streamer qui s'énerve contre le jeu, une action ou un \
adversaire du match, même en l'insultant sur le coup, n'est pas une raison de refuser. Le verdict "reject" est \
réservé à ces cas et aux moments déjà publiés : un extrait simplement faible garde le verdict "publish", avec une \
note basse.

Textes, en français, sans mentir sur le contenu :
- hook : l'accroche incrustée en haut de la vidéo, la première chose qu'on lit : c'est elle qui fait rester ou \
défiler. 40 caractères maximum (3 à 7 mots), sans emoji ni hashtag. Elle dit qui et ce qui se passe, au présent, \
et ouvre une question, une tension ou une émotion qu'on veut voir se résoudre, sans révéler la chute ni mentir. \
Formules qui marchent :
  - le nom et une réaction extrême : « LOUPIOTE N'EN PEUT PLUS DE CE CONSEIL » (75 600 vues sur le compte), \
« SQUEEZIE PERD TOUS SES MOYENS » ;
  - le mystère : « IL N'AURAIT JAMAIS DÛ DIRE ÇA », « PERSONNE NE S'ATTENDAIT À ÇA » ;
  - l'enjeu : « 10 000 € SUR UN SEUL TIR », « IL A 3 SECONDES POUR RÉPONDRE » ;
  - la situation que tout le monde connaît : « QUAND TA MÈRE TE DONNE UN CONSEIL » ;
  - la question au spectateur : « QUI A RAISON ? », « TU AURAIS FAIT QUOI ? ».
  Jamais d'étiquette vague (« MOMENT DRÔLE », « FOU RIRE EN LIVE », « CLIP INCROYABLE ») : elle ne donne aucune \
raison de rester.
- hook_alternatives : deux autres accroches, d'autres formules que hook, qui servent de légende sur TikTok. \
Écris d'abord trois accroches, garde la plus forte dans hook et les deux autres ici.
- title : titre YouTube de 90 caractères maximum, avec le nom du créateur, au plus un emoji, pas de hashtag.
- description : une ou deux phrases avec le nom du créateur et ce qui se passe, dans les mots que les gens \
tapent pour chercher (TikTok et YouTube s'en servent pour la recherche).
- hashtags : 3 à 6, en minuscules, sans « # » ni espace (nom du créateur, jeu s'il y a lieu, thème).
- trim_start / trim_end : bornes en secondes, relatives au début de l'extrait. Un short court se regarde jusqu'au \
bout et se revoit : vise 12 à 35 secondes, 45 au plus, et jusqu'à 60 secondes seulement pour une histoire \
complète qui tient en haleine. Commence au plus près de l'action : dès la première seconde, on voit la situation \
ou la réaction, pas une mise en place ; ne garde de la mise en place que le strict nécessaire pour comprendre. \
Termine juste après la chute ou la réaction à la chute, sans traîner : un short qui finit net se revoit en \
boucle. Coupe les temps morts. Si l'extrait est déjà bon, renvoie 0 et sa durée. Quand \
des spectateurs citent un instant de l'extrait en commentaire, c'est le moment qu'ils ont retenu : cherche-le dans \
la transcription et les images, et coupe autour de lui.
- long_start / long_end : la version longue du même moment, pour TikTok qui ne rémunère que les vidéos de plus \
d'une minute : de 61 à 90 secondes, avec toute la mise en place, la chute et les réactions qui suivent, sans temps \
mort. Elle démarre elle aussi sur une situation qui accroche. Si le moment ne tient pas 61 secondes sans temps \
mort, renvoie 0 et 0.

Les transcriptions viennent d'une reconnaissance vocale automatique : elles peuvent contenir des erreurs."""

COMPILATION_SYSTEM = """Tu es le rédacteur en chef d'une chaîne YouTube francophone de best-of de streamers. \
Tu écris le titre, l'introduction de la description, les hashtags et le texte de la miniature d'une \
compilation dont on te donne la liste des clips (du dernier au premier du classement). Titre de 90 caractères \
maximum avec les noms des créateurs les plus connus, sans mentir sur le contenu ; texte de miniature de 4 à 6 \
mots percutants en majuscules sans emoji ; 3 à 8 hashtags en minuscules sans « # »."""

SHORTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["publish", "reject"]},
                    "reason": {"type": "string"},
                    "score": {"type": "integer"},
                    "hook": {"type": "string"},
                    "hook_alternatives": {"type": "array", "items": {"type": "string"}},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "trim_start": {"type": "number"},
                    "trim_end": {"type": "number"},
                    "long_start": {"type": "number"},
                    "long_end": {"type": "number"},
                },
                "required": [
                    "id", "verdict", "reason", "score", "hook", "title", "description", "hashtags", "trim_start", "trim_end",
                    "long_start", "long_end", "hook_alternatives",
                ],  # fmt: skip
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

COMPILATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "intro": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "thumbnail_text": {"type": "string"},
    },
    "required": ["title", "intro", "hashtags", "thumbnail_text"],
    "additionalProperties": False,
}


@dataclass
class ReviewItem:
    candidate: Candidate
    duration: float  # length of the downloaded extract
    transcript: Transcript | None = None
    sheet: Path | None = None  # contact sheet of a few frames
    clip_span: tuple[float, float] | None = None  # where the original clip sits in the extract (None: the extract is the clip)


@dataclass
class Review:
    verdict: str  # "publish" | "reject"
    reason: str
    score: float  # 0..100
    meta: ShortMeta

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "score": self.score,
            "meta": {
                "hook": self.meta.hook,
                "title": self.meta.title,
                "description": self.meta.description,
                "hashtags": self.meta.hashtags,
                "trim_start": self.meta.trim_start,
                "trim_end": self.meta.trim_end,
                "ai": self.meta.ai,
                "long_start": self.meta.long_start,
                "long_end": self.meta.long_end,
                "hook_alternatives": self.meta.hook_alternatives,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Review:
        return cls(
            verdict=data["verdict"], reason=data.get("reason", ""), score=float(data.get("score", 0)), meta=ShortMeta(**data["meta"])
        )


def hashtag(text: str | None) -> str:
    """'Joueur du Grenier' -> 'joueurdugrenier' (letters and digits only, accents removed)."""
    if not text:
        return ""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_text.lower())


def _hashtags(values: list[str], limit: int = 8) -> list[str]:
    seen: list[str] = []
    for value in values:
        tag = hashtag(value)
        if tag and tag not in seen:
            seen.append(tag)
    return seen[:limit]


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip(" ,.;:-") + "…"


def default_trim(candidate: Candidate, duration: float) -> tuple[float | None, float | None]:
    """Long YouTube windows without an AI pick: keep MAX_SHORT seconds around the peak."""
    if duration <= MAX_SHORT + 1 or candidate.manual:
        return None, None
    start = max(0.0, (duration - MAX_SHORT) * 0.45)
    return round(start, 2), round(start + MAX_SHORT, 2)


def clip_title(candidate: Candidate) -> str:
    """The clip's own title, or "" when viewers gave it a throwaway name ("f", "lol", "...")."""
    title = clean(candidate.title)
    return title if sum(char.isalpha() for char in title) >= 4 else ""


def template_review(item: ReviewItem, config: Config) -> Review:
    """No AI: the original title becomes the hook and the title, popularity becomes the score."""
    candidate = item.candidate
    own = clip_title(candidate)
    base = own or f"Moment fort de {candidate.streamer}"
    title = base if candidate.streamer.lower() in base.lower() else f"{candidate.streamer} : {base}"
    trim_start, trim_end = item.clip_span or default_trim(candidate, item.duration)
    meta = ShortMeta(
        hook=_cut(base, 40),
        title=_cut(title, 95),
        description=f"{own} — {candidate.streamer} en live." if own else f"Un moment fort de {candidate.streamer} en live.",
        hashtags=_hashtags([candidate.streamer_login, candidate.game or "", *config.channel.hashtags]),
        trim_start=trim_start,
        trim_end=trim_end,
        ai=False,
    )
    return Review(verdict="publish", reason="gabarit (sans IA)", score=min(100.0, candidate.score * 10), meta=meta)


def _validated(raw: dict[str, Any], item: ReviewItem, config: Config) -> Review:
    candidate = item.candidate
    fallback = template_review(item, config)
    hook = _cut(clean(str(raw.get("hook") or "")), 48) or fallback.meta.hook
    alternatives: list[str] = []
    for text in raw.get("hook_alternatives") or []:
        other = _cut(clean(str(text or "")), 48)
        if other and other.upper() != hook.upper() and other.upper() not in (a.upper() for a in alternatives):
            alternatives.append(other)
    title = _cut(clean(str(raw.get("title") or "")), 95) or fallback.meta.title
    description = _cut(str(raw.get("description") or "").strip(), 900) or fallback.meta.description
    tags = _hashtags([*(raw.get("hashtags") or []), candidate.streamer_login, *config.channel.hashtags])
    trim_start, trim_end = fallback.meta.trim_start, fallback.meta.trim_end
    try:
        start, end = float(raw.get("trim_start", 0.0)), float(raw.get("trim_end", item.duration))
        start, end = max(0.0, start), min(item.duration, end)
        if end - start >= min(MIN_SHORT, item.duration) - 0.01 and (start > 0.05 or end < item.duration - 0.05):
            trim_start, trim_end = round(start, 2), round(min(end, start + MAX_TRIMMED), 2)
        elif end - start >= min(MIN_SHORT, item.duration) - 0.01:
            trim_start, trim_end = None, None
    except (TypeError, ValueError):
        pass
    if candidate.manual and (candidate.parts or (candidate.start is not None and candidate.end is not None)):
        trim_start, trim_end = None, None  # the owner picked this very section (or these clips): it is kept whole
    long_start, long_end = _long_cut(raw, item, config)
    verdict = raw.get("verdict") if raw.get("verdict") in {"publish", "reject"} else "publish"
    score = max(0.0, min(100.0, float(raw.get("score") or 0)))
    meta = ShortMeta(
        hook=hook, title=title, description=description, hashtags=tags, trim_start=trim_start, trim_end=trim_end, ai=True,
        long_start=long_start, long_end=long_end, hook_alternatives=alternatives[:2],
    )  # fmt: skip
    return Review(verdict=verdict, reason=_cut(str(raw.get("reason") or ""), 300), score=score, meta=meta)


def _long_cut(raw: dict[str, Any], item: ReviewItem, config: Config) -> tuple[float | None, float | None]:
    """Claude's long cut of the moment, when it fits in the extract and lasts long_min to long_max seconds."""
    if item.candidate.manual:
        return None, None
    rules = config.shorts
    try:
        start, end = max(0.0, float(raw.get("long_start") or 0.0)), min(item.duration, float(raw.get("long_end") or 0.0))
    except (TypeError, ValueError):
        return None, None
    if not rules.long_min <= end - start <= rules.long_max + 0.5:  # 0 and 0: no long cut; never under a minute
        return None, None
    return round(start, 2), round(end, 2)


def _age(created: datetime | None, now: datetime) -> str:
    if created is None:
        return "date inconnue"
    hours = (now - created).total_seconds() / 3600
    return f"il y a {hours:.0f} h" if hours < 48 else f"il y a {hours / 24:.0f} j"


class ClaudeCurator:
    """Reviews candidates and writes publication texts with Claude (structured JSON output)."""

    def __init__(self, api_key: str, config: Config, *, client: Any = None) -> None:
        import anthropic

        self.config = config
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=3)
        self.disabled_reason: str | None = None

    def _call(self, system: str, content: list[dict[str, Any]], schema: dict[str, Any]) -> dict[str, Any] | None:
        import anthropic

        if self.disabled_reason:
            return None
        model = self.config.ai.model
        extra: dict[str, Any] = {}
        if model in FALLBACK_MODELS:
            extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": 16000,
            "system": system,
            "output_config": {"effort": self.config.ai.effort, "format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": content}],
        }
        try:
            try:
                response = self.client.beta.messages.create(**request, **extra)
            except anthropic.BadRequestError as exc:
                if not extra:
                    raise
                # The refusal fallback is a beta: never let it cost the whole review.
                log.warning("Claude : repli serveur refusé (%s), nouvel essai sans.", exc.message)
                response = self.client.beta.messages.create(**request)
        except anthropic.AuthenticationError:
            self.disabled_reason = "ANTHROPIC_API_KEY refusée"
            log.error("Claude : clé API refusée, textes générés par gabarit pour ce passage.")
            return None
        except anthropic.PermissionDeniedError as exc:
            self.disabled_reason = "accès refusé"
            log.error("Claude : accès refusé (%s), textes générés par gabarit.", exc.message)
            return None
        except anthropic.BadRequestError as exc:
            if "credit balance" in str(exc.message).lower():
                # Nothing can be judged until the account is topped up: no more request in this run.
                self.disabled_reason = NO_CREDITS
            log.error("Claude : requête refusée (%s), textes générés par gabarit.", exc.message)
            return None
        except anthropic.RateLimitError:
            log.warning("Claude : limite de débit atteinte, textes générés par gabarit.")
            return None
        except anthropic.APIStatusError as exc:
            log.warning("Claude : erreur %s (%s), textes générés par gabarit.", exc.status_code, exc.message)
            return None
        except anthropic.APIConnectionError:
            log.warning("Claude : injoignable, textes générés par gabarit.")
            return None
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            log.warning("Claude a décliné la demande (%s), textes générés par gabarit.", getattr(details, "category", None))
            return None
        if response.stop_reason == "max_tokens":
            log.warning("Claude : réponse tronquée, textes générés par gabarit.")
            return None
        text = next((block.text for block in response.content if block.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("Claude : JSON illisible, textes générés par gabarit.")
            return None
        return data if isinstance(data, dict) else None

    def review(
        self, items: list[ReviewItem], *, now: datetime, recent: list[tuple[datetime | None, str]] | None = None
    ) -> dict[str, Review]:
        """One request for the whole batch: Claude compares the candidates with each other.

        `recent` lists the (date, title) of the shorts already published: the same moment is not published twice
        (even from another streamer's live), and the next part of a published story is titled as such.
        """
        if not items:
            return {}
        content: list[dict[str, Any]] = []
        if recent:
            listing = "\n".join(f"- {title} (publié {_age(at, now)})" for at, title in recent)
            content.append({"type": "text", "text": f"Shorts déjà publiés, du plus récent au plus ancien :\n{listing}"})
        ids: dict[str, ReviewItem] = {}
        for index, item in enumerate(items, start=1):
            clip_id = f"c{index}"
            ids[clip_id] = item
            candidate = item.candidate
            transcript = item.transcript.timestamped() if item.transcript and item.transcript.words else "(aucune parole détectée)"
            lines = [
                f'<candidat id="{clip_id}">',
                f"Créateur : {candidate.streamer} ({candidate.credit})",
                f"Source : {candidate.source}" + (" (ajouté à la main)" if candidate.manual else ""),
                f"Titre d'origine : {candidate.title or '(aucun)'}",
                f"Jeu / catégorie : {candidate.game or 'inconnu'}",
                f"Vues : {candidate.views} · publié {_age(candidate.created_at, now)} · durée de l'extrait : {item.duration:.1f} s",
            ]
            if item.clip_span:
                start, end = item.clip_span
                lines.append(f"Clip d'origine (choisi par les viewers) : de {start:.1f} s à {end:.1f} s de l'extrait ; avant et "
                             "après, c'est le replay du live : n'en garde que ce qui sert la mise en place ou la chute.")
            if candidate.cited and candidate.cited_at is not None and candidate.start is not None:
                offset = max(0.0, candidate.cited_at - candidate.start)
                lines.append(f"Moment cité par les spectateurs : vers {offset:.0f} s de l'extrait. Commentaire le plus aimé "
                             f"qui le cite (texte d'un spectateur) : « {candidate.cited} »")
            lines += ["Transcription (secondes depuis le début de l'extrait) :", transcript, "</candidat>"]
            content.append({"type": "text", "text": "\n".join(lines)})
            if self.config.ai.review_frames and item.sheet and item.sheet.exists():
                content.append({"type": "text", "text": f"Images de {clip_id} (4 instants, de gauche à droite puis de haut en bas) :"})
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.standard_b64encode(item.sheet.read_bytes()).decode("ascii"),
                        },
                    }
                )
        content.append({"type": "text", "text": f"Évalue les {len(items)} candidats ci-dessus : un objet par candidat, avec le même id."})
        data = self._call(SHORTS_SYSTEM, content, SHORTS_SCHEMA)
        reviews: dict[str, Review] = {}
        for raw in (data or {}).get("clips", []):
            item = ids.get(str(raw.get("id")))
            if item is not None and item.candidate.key not in reviews:
                reviews[item.candidate.key] = _validated(raw, item, self.config)
        missing = [item for item in items if item.candidate.key not in reviews]
        if data is not None and missing:
            log.warning("Claude n'a pas évalué %d candidat(s) : gabarit utilisé pour eux.", len(missing))
        for item in missing:
            reviews[item.candidate.key] = template_review(item, self.config)
        return reviews

    def compilation_meta(self, clips: list[tuple[Candidate, str]], *, period: str) -> CompilationMeta:
        fallback = template_compilation_meta(clips, self.config, period=period)
        listing = "\n".join(
            f"#{len(clips) - i} {candidate.streamer} — {label} ({candidate.views} vues)" for i, (candidate, label) in enumerate(clips)
        )
        content = [{"type": "text", "text": f"Chaîne : {self.config.channel.name}\nPériode : {period}\nClips :\n{listing}"}]
        data = self._call(COMPILATION_SYSTEM, content, COMPILATION_SCHEMA)
        if not data:
            return fallback
        return CompilationMeta(
            title=_cut(clean(str(data.get("title") or "")), 95) or fallback.title,
            description=_cut(str(data.get("intro") or "").strip(), 1500) or fallback.description,
            hashtags=_hashtags([*(data.get("hashtags") or []), *self.config.channel.hashtags]) or fallback.hashtags,
            thumbnail_text=_cut(clean(str(data.get("thumbnail_text") or "")), 40) or fallback.thumbnail_text,
            ai=True,
        )


def template_compilation_meta(clips: list[tuple[Candidate, str]], config: Config, *, period: str) -> CompilationMeta:
    names: list[str] = []
    for candidate, _ in reversed(clips):  # best first
        if candidate.streamer not in names:
            names.append(candidate.streamer)
    title = f"TOP {len(clips)} des meilleurs clips de la semaine ({', '.join(names[:3])})"
    return CompilationMeta(
        title=_cut(title, 95),
        description=f"Les {len(clips)} moments les plus forts de la semaine ({period}), du #{len(clips)} au #1.",
        hashtags=_hashtags([*names[:5], *config.channel.hashtags, "bestof"]),
        thumbnail_text=f"TOP {len(clips)} DE LA SEMAINE",
        ai=False,
    )
