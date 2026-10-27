"""Rapport de backtest en HTML autonome (un seul fichier, aucun script externe)."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from html import escape

from .backtest import BacktestResult


def fmt_time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _polyline(values: Sequence[float], lo: float, hi: float, width: int, height: int, pad: int) -> str:
    n = len(values)
    if n < 2:
        return ""
    step = max(1, n // 1000)  # 1000 points suffisent à l'écran
    pts = []
    for i in range(0, n, step):
        x = pad + (width - 2 * pad) * i / (n - 1)
        y = height - pad - (height - 2 * pad) * (values[i] - lo) / ((hi - lo) or 1)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def equity_svg(result: BacktestResult, width: int = 900, height: int = 300) -> str:
    series = result.equity + result.benchmark
    if len(result.equity) < 2:
        return "<p>Pas assez de données pour le graphique.</p>"
    lo, hi, pad = min(series), max(series), 30
    strat = _polyline(result.equity, lo, hi, width, height, pad)
    bench = _polyline(result.benchmark, lo, hi, width, height, pad)
    return f"""<svg viewBox="0 0 {width} {height}" role="img" aria-label="Évolution du capital">
  <line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" class="axis"/>
  <text x="{pad}" y="{height - 8}" class="label">{fmt_time(result.times[0])}</text>
  <text x="{width - pad}" y="{height - 8}" class="label" text-anchor="end">{fmt_time(result.times[-1])}</text>
  <text x="{pad}" y="{pad - 10}" class="label">{hi:,.0f}</text>
  <text x="{pad}" y="{height - pad - 6}" class="label">{lo:,.0f}</text>
  <polyline points="{bench}" class="bench"/>
  <polyline points="{strat}" class="strat"/>
</svg>"""


def render_report(result: BacktestResult, brand: str = "TradeBot") -> str:
    s = result.stats
    pf = "∞" if s.profit_factor is None else f"{s.profit_factor:.2f}"
    cards = [
        ("Rendement", f"{s.total_return_pct:+.2f} %", s.total_return_pct >= 0),
        ("Buy & hold", f"{s.buy_hold_pct:+.2f} %", s.buy_hold_pct >= 0),
        ("Drawdown max", f"-{s.max_drawdown_pct:.2f} %", None),
        ("Sharpe", f"{s.sharpe:.2f}", s.sharpe >= 1),
        ("Profit factor", pf, s.profit_factor is None or s.profit_factor >= 1),
        ("Trades", str(s.trades), None),
        ("Réussite", f"{s.win_rate:.1f} %", None),
        ("En position", f"{s.exposure_pct:.1f} %", None),
    ]
    cards_html = "".join(
        f'<div class="card"><span>{escape(k)}</span><strong class="{"" if ok is None else ("pos" if ok else "neg")}">'
        f"{escape(v)}</strong></div>" for k, v, ok in cards)
    rows = "".join(
        f"<tr><td>{fmt_time(t.opened_at)}</td><td>{fmt_time(t.closed_at)}</td><td>{t.entry_price:.6g}</td>"
        f"<td>{t.exit_price:.6g}</td><td class=\"{'pos' if t.pnl >= 0 else 'neg'}\">{t.pnl:+.2f} ({t.pnl_pct:+.2f} %)</td>"
        f"<td>{escape(t.reason)}</td></tr>" for t in reversed(result.trades[-300:]))
    title = f"{brand} — Backtest {result.symbol}"
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
:root {{ --bg:#0f1419; --panel:#182029; --text:#e6edf3; --muted:#8b98a5; --pos:#3fb950; --neg:#f85149; --line:#2d3a47; --accent:#58a6ff; }}
@media (prefers-color-scheme: light) {{ :root {{ --bg:#f6f8fa; --panel:#fff; --text:#1f2328; --muted:#59636e; --pos:#1a7f37; --neg:#cf222e; --line:#d0d7de; --accent:#0969da; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1000px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} .sub {{ color:var(--muted); margin:0 0 20px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; margin-bottom:20px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
.card span {{ display:block; color:var(--muted); font-size:13px; }} .card strong {{ font-size:20px; font-variant-numeric:tabular-nums; }}
.pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}
.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:20px; overflow-x:auto; }}
svg {{ width:100%; height:auto; display:block; }} .axis {{ stroke:var(--line); }}
.label {{ fill:var(--muted); font-size:12px; }} polyline {{ fill:none; stroke-width:2; }}
.strat {{ stroke:var(--accent); }} .bench {{ stroke:var(--muted); stroke-dasharray:4 4; stroke-width:1.5; }}
.legend {{ color:var(--muted); font-size:13px; margin-top:8px; }} .legend b {{ color:var(--accent); }}
table {{ width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }}
th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }} th {{ color:var(--muted); font-weight:500; }}
footer {{ color:var(--muted); font-size:12px; }}
</style></head><body><main>
<h1>{escape(title)}</h1>
<p class="sub">{escape(result.strategy)} · {escape(result.timeframe)} · capital initial {result.initial_capital:,.2f}
 · {fmt_time(result.times[0]) if result.times else ""} → {fmt_time(result.times[-1]) if result.times else ""}</p>
<div class="cards">{cards_html}</div>
<div class="panel">{equity_svg(result)}<div class="legend"><b>━ Stratégie</b> &nbsp; ┅ Buy &amp; hold</div></div>
<div class="panel"><table><thead><tr><th>Entrée</th><th>Sortie</th><th>Prix entrée</th><th>Prix sortie</th><th>Résultat</th><th>Raison</th></tr></thead>
<tbody>{rows or '<tr><td colspan="6">Aucun trade</td></tr>'}</tbody></table></div>
<footer>Simulation sur données historiques, frais et glissement inclus. Les performances passées ne préjugent pas
des performances futures. Le trading de cryptomonnaies comporte un risque de perte en capital.</footer>
</main></body></html>"""
