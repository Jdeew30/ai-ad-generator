"""The robot's dashboard: dashboard.md on the state branch, rewritten after each run.

Counts of the last 7 and 30 days (moments found, prepared, sent, failed), the sources and creators that give the
most, Claude's average mark, the YouTube watch (channels read, videos followed) and the latest shorts sent.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from .models import parse_time


def render(state: Any, creators: Any, now: datetime) -> str:
    """The dashboard, in Markdown (GitHub shows it as a page)."""
    lines = [f"# Tableau de bord du clipper", "", f"Mis à jour le {now:%d/%m/%Y à %H:%M} UTC.", ""]
    lines += ["## Activité", "", "| | 7 jours | 30 jours |", "|---|---:|---:|"]
    for label, field in (("Moments trouvés (Twitch + YouTube)", "detected"), ("Vidéos téléchargées et préparées", "prepared"),
                         ("Shorts envoyés", "published"), ("Écartés par la revue", "rejected"),
                         ("Échecs (téléchargement, montage, envoi)", "failed")):  # fmt: skip
        lines.append(f"| {label} | {_total(state.runs, field, now, 7)} | {_total(state.runs, field, now, 30)} |")
    lines.append(f"| Passages | {_count_runs(state.runs, now, 7)} | {_count_runs(state.runs, now, 30)} |")

    posts = [(key, entry) for key, entry in state.posted.items() if _within(entry.get("at"), now, 30)]
    marks = [entry["score"] for _, entry in posts if entry.get("score")]
    lines += ["", "## Shorts des 30 derniers jours", ""]
    sources = Counter(entry.get("source") or "?" for _, entry in posts)
    lines.append("Par source : " + (", ".join(f"{_source(name)} {count}" for name, count in sources.most_common()) or "aucun") + ".")
    if marks:
        lines.append(f"Note moyenne de Claude : {sum(marks) / len(marks):.0f}/100 sur {len(marks)} short(s) notés "
                     "(une estimation de l'intérêt du passage, pas une mesure).")
    creators_seen = Counter(entry.get("streamer") or entry.get("streamer_login") or "?" for _, entry in posts)
    if creators_seen:
        lines.append("Créateurs les plus repris : " + ", ".join(f"{name} ({count})" for name, count in creators_seen.most_common(10)) + ".")

    lines += ["", "## Veille YouTube", ""]
    if creators is not None:
        summary = creators.summary()
        lines.append(f"{summary['creators']} créateurs suivis, {summary['channels']} chaînes lues à chaque passage par leur "
                     f"flux RSS (dont {summary['secondary']} chaînes secondaires vérifiées), {summary['with_id']} identifiées ; "
                     f"{summary['set_aside']} mises de côté (introuvables).")
    videos = getattr(state, "youtube_videos", {}) or {}
    examined = [entry for entry in videos.values() if entry.get("examined")]
    lines.append(f"{len(videos)} vidéos récentes suivies, {len(examined)} regardées de près "
                 f"({sum(1 for entry in examined if entry.get('outcome') == 'moments')} avec des moments à extraire).")

    lines += ["", "## Derniers shorts envoyés", "", "| Date | Source | Créateur | Short | Note |", "|---|---|---|---|---:|"]
    latest = sorted(state.posted.values(), key=lambda entry: entry.get("at") or "", reverse=True)[:20]
    for entry in latest:
        when = parse_time(entry.get("at"))
        title = (entry.get("title") or "").replace("|", "/")
        link = f"[{title}]({entry['url']})" if entry.get("url") else title
        mark = f"{entry['score']:.0f}" if entry.get("score") else ""
        lines.append(f"| {when:%d/%m %H:%M} | {_source(entry.get('source'))} | {entry.get('streamer') or ''} | {link} | {mark} |"
                     if when else f"| | | | {link} | {mark} |")

    failures = sorted(state.failures.items(), key=lambda pair: pair[1].get("at") or "", reverse=True)[:10]
    if failures:
        lines += ["", "## Derniers échecs", ""]
        lines += [f"- {key} ({entry.get('count', 1)}×) : {(entry.get('error') or '').strip()[:160]}" for key, entry in failures]
    return "\n".join(lines) + "\n"


def _within(at: str | None, now: datetime, days: int) -> bool:
    when = parse_time(at)
    return when is not None and now - when <= timedelta(days=days)


def _total(runs: list[dict[str, Any]], field: str, now: datetime, days: int) -> int:
    return sum(int(run.get(field) or 0) for run in runs if _within(run.get("at"), now, days))


def _count_runs(runs: list[dict[str, Any]], now: datetime, days: int) -> int:
    return sum(1 for run in runs if _within(run.get("at"), now, days))


def _source(name: str | None) -> str:
    return {"twitch": "Twitch", "youtube": "YouTube", "manual": "à la demande"}.get(name or "", name or "?")
