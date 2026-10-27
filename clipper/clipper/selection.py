from __future__ import annotations

import logging
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from .config import Config
from .models import Candidate
from .state import State, moment_of, same_moment

log = logging.getLogger(__name__)


def score(candidate: Candidate, median_views: float, now: datetime, *, freshness_weight: float) -> float:
    """Popularity, relative to the streamer's usual numbers, weighted by freshness and length fit.

    The relative term matters: a 2 000-view clip from a 3 000-viewer streamer is a better
    moment than a 5 000-view clip from a streamer whose every clip gets 20 000 views.
    """
    if candidate.manual:
        return 1e9
    views = max(candidate.views, 0)
    base = math.log10(views + 10)
    relative = 0.0
    if median_views > 0:
        relative = max(-1.0, min(2.0, math.log2((views + 1) / (median_views + 1))))
    age_hours = (now - candidate.created_at).total_seconds() / 3600 if candidate.created_at else 24.0
    fresh = math.exp(-max(age_hours, 0.0) / 36.0)
    duration = candidate.duration
    if 15 <= duration <= 60:
        fit = 1.0
    elif 8 <= duration < 15 or 60 < duration <= 90:
        fit = 0.85
    else:
        fit = 0.7  # long YouTube windows are trimmed later
    heat = candidate.heat if candidate.heat is not None else 1.0
    return (base + 0.5 * relative) * (1.0 - freshness_weight + freshness_weight * fresh) * fit * (0.5 + 0.5 * heat)


def rank(
    candidates: list[Candidate],
    state: State,
    config: Config,
    *,
    now: datetime,
    compilation: bool = False,
) -> list[Candidate]:
    """Filters out what must not be posted (again) and orders the rest, best first."""
    rules = config.shorts
    skipped: Counter[str] = Counter()
    unique: dict[str, Candidate] = {}
    for candidate in candidates:
        if candidate.key in unique:
            continue
        unique[candidate.key] = candidate
    kept: list[Candidate] = []
    for candidate in unique.values():
        if candidate.streamer_login and config.is_blocked(candidate.streamer_login):
            skipped["liste noire"] += 1
        elif compilation and candidate.key in state.compiled:
            skipped["déjà dans une compilation"] += 1
        elif compilation and candidate.key in state.rejected:
            skipped["écarté auparavant"] += 1
        elif not compilation and state.seen(candidate.key):
            skipped["déjà traité"] += 1
        elif not compilation and state.overlaps_posted(candidate):
            skipped["moment déjà publié"] += 1
        elif not candidate.manual and candidate.duration and candidate.duration < rules.min_duration:
            skipped["trop court"] += 1
        elif not candidate.manual and candidate.source == "twitch" and candidate.duration > rules.max_duration:
            skipped["trop long"] += 1
        elif not candidate.manual and candidate.source == "twitch" and candidate.views < config.twitch.min_views:
            skipped["pas assez de vues"] += 1
        else:
            kept.append(candidate)

    views_by_streamer: dict[tuple[str, str], list[int]] = defaultdict(list)
    for candidate in kept:
        views_by_streamer[(candidate.source, candidate.streamer_login)].append(candidate.views)
    for candidate in kept:
        peers = views_by_streamer[(candidate.source, candidate.streamer_login)]
        median = statistics.median(peers) if len(peers) >= 3 else 0.0
        candidate.score = score(candidate, median, now, freshness_weight=0.15 if compilation else 0.4)
    kept.sort(key=lambda c: c.score, reverse=True)

    # The same moment is often clipped by several viewers: keep the best version only.
    distinct: list[Candidate] = []
    for candidate in kept:
        moment = moment_of(candidate)
        if any(same_moment(moment, moment_of(other)) for other in distinct):
            skipped["doublon d'un même moment"] += 1
            continue
        distinct.append(candidate)

    if not compilation:
        since = now - timedelta(hours=24)
        allowed: dict[str, int] = {}
        capped: list[Candidate] = []
        for candidate in distinct:
            login = candidate.streamer_login
            if candidate.manual or not login:
                capped.append(candidate)
                continue
            if login not in allowed:
                allowed[login] = rules.max_per_streamer_per_day - state.posted_count(login, since)
            if allowed[login] <= 0:
                skipped["quota du streamer atteint"] += 1
                continue
            allowed[login] -= 1
            capped.append(candidate)
        distinct = capped

    if skipped:
        log.info("Écartés : %s", ", ".join(f"{reason} ({count})" for reason, count in skipped.most_common()))
    return distinct
