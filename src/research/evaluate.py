"""Statistical evaluation of labeled snapshots — M14 (DECISIONS #24).

Pre-registered questions (DECISIONS #24) are answered from data, not from
what experts use. Rules, fixed before any result was seen:

- Panel rows only (every scored stock every 5 min); the metric is the
  return in excess of NIFTY (`xs_nifty_<h>`), per horizon.
- Minutes of one stock are correlated, so uncertainty comes from a
  bootstrap that resamples whole days (95% interval).
- A bucket with fewer than MIN_ROWS rows or MIN_DAYS days is
  INSUFFICIENT; no verdict is given.
- A comparison is a finding only if its interval excludes 0 at two or more
  horizons with the same sign AND the sign holds in both chronological
  halves of the days. Otherwise: NO EVIDENCE.
"""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

HORIZONS = (5, 15, 30, 60)
MIN_ROWS, MIN_DAYS = 30, 20
BOOT = 400
_DROP = ("p_", "mfe_", "mae_", "fwd_", "net_", "xs_sector_", "truncated_")   # unused by the report; saves memory
TECH_WEIGHTS = {"setup": 0.20, "volume": 0.20, "movement": 0.15, "momentum": 0.15,
                "liquidity": 0.05}            # the engine weights of the technical groups only


def load_rows(labeled_dir: Path, start: str | None = None, end: str | None = None,
              version: str | None = None) -> list[dict]:
    rows = []
    for p in sorted(Path(labeled_dir).glob("*.jsonl")):
        if (start and p.stem < start) or (end and p.stem > end):
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("trigger") == "panel" and (version is None or r.get("strategy_version") == version):
                rows.append({k: v for k, v in r.items() if not k.startswith(_DROP)})
    return rows


# -- statistics ----------------------------------------------------------------

def _by_day(rows: list[dict], metric: str) -> dict[str, tuple[float, int]]:
    """day -> (sum, count): the bootstrap then costs O(days), not O(rows)."""
    d: dict = defaultdict(lambda: [0.0, 0])
    for r in rows:
        if r.get(metric) is not None:
            d[r["day"]][0] += r[metric]
            d[r["day"]][1] += 1
    return {k: (v[0], v[1]) for k, v in d.items()}


def _pooled_mean(days: dict[str, tuple[float, int]], keys: Iterable[str]) -> float | None:
    total = n = 0
    for k in keys:
        s, c = days.get(k, (0.0, 0))
        total, n = total + s, n + c
    return total / n if n else None


def counts(rows: list[dict], metric: str) -> dict:
    d = _by_day(rows, metric)
    return {"n": sum(c for _, c in d.values()), "days": len(d)}


def bootstrap_diff(a: list[dict], b: list[dict] | None, metric: str, seed: int = 7,
                   n: int = BOOT) -> tuple[float | None, float | None, float | None]:
    """Mean of `a` (minus mean of `b` if given) and its 95% day-bootstrap interval."""
    da, db = _by_day(a, metric), _by_day(b, metric) if b is not None else {}
    days = sorted(set(da) | set(db))
    point_a = _pooled_mean(da, days)
    point_b = _pooled_mean(db, days) if b is not None else 0.0
    if point_a is None or point_b is None:
        return None, None, None
    rng, stats = random.Random(seed), []
    for _ in range(n):
        pick = [rng.choice(days) for _ in days]
        ma, mb = _pooled_mean(da, pick), (_pooled_mean(db, pick) if b is not None else 0.0)
        if ma is not None and mb is not None:
            stats.append(ma - mb)
    if len(stats) < n // 2:
        return point_a - point_b, None, None
    stats.sort()
    return point_a - point_b, stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats)) - 1]


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    try:
        return statistics.correlation(rx, ry)
    except statistics.StatisticsError:       # constant input
        return None


def daily_ic(rows: list[dict], feature: Callable[[dict], float | None], metric: str):
    """Mean over days of the within-day rank correlation, its t-stat and day count."""
    per_day = defaultdict(lambda: ([], []))
    for r in rows:
        x, y = feature(r), r.get(metric)
        if x is not None and y is not None:
            per_day[r["day"]][0].append(x)
            per_day[r["day"]][1].append(y)
    ics = [ic for xs, ys in per_day.values() if (ic := spearman(xs, ys)) is not None]
    if len(ics) < 2:
        return (ics[0] if ics else None), None, len(ics)
    m, sd = statistics.fmean(ics), statistics.stdev(ics)
    return m, (m / sd * len(ics) ** 0.5 if sd > 0 else None), len(ics)


def summary(rows: list[dict], metric: str) -> dict:
    vals = [r[metric] for r in rows if r.get(metric) is not None]
    days = {r["day"] for r in rows if r.get(metric) is not None}
    mean, lo, hi = bootstrap_diff(rows, None, metric)
    return {"n": len(vals), "days": len(days), "mean": mean, "lo": lo, "hi": hi,
            "median": statistics.median(vals) if vals else None,
            "hit": sum(v > 0 for v in vals) / len(vals) if vals else None}


def sufficient(s: dict) -> bool:   # accepts counts() or summary()
    return s["n"] >= MIN_ROWS and s["days"] >= MIN_DAYS


def verdict(a: list[dict], b: list[dict], horizons=HORIZONS, metric="xs_nifty") -> str:
    """FINDING (+/-) only if significant at >= 2 horizons, same sign, and the
    sign holds in both chronological halves of the days."""
    for rows in (a, b):
        if not any(sufficient(counts(rows, f"{metric}_{h}")) for h in horizons):
            return "INSUFFICIENT"
    signs = []
    for h in horizons:
        m = f"{metric}_{h}"
        if not (sufficient(counts(a, m)) and sufficient(counts(b, m))):
            continue
        d, lo, hi = bootstrap_diff(a, b, m)
        if lo is not None and (lo > 0 or hi < 0):
            signs.append((h, 1 if lo > 0 else -1))
    if len(signs) < 2 or len({s for _, s in signs}) != 1:
        return "NO EVIDENCE"
    sign = signs[0][1]
    days = sorted({r["day"] for r in a + b})
    half = days[len(days) // 2]
    for part in (lambda r: r["day"] < half, lambda r: r["day"] >= half):
        h = signs[0][0]
        d, _, _ = bootstrap_diff([r for r in a if part(r)], [r for r in b if part(r)], f"{metric}_{h}", n=50)
        if d is None or (d > 0) != (sign > 0):
            return "NO EVIDENCE (not stable across halves)"
    return "FINDING: better" if sign > 0 else "FINDING: worse"


# -- helpers for the questions -------------------------------------------------------

def tech_score(r: dict) -> float | None:
    used = [(w, r.get(f"g_{g}")) for g, w in TECH_WEIGHTS.items() if r.get(f"g_{g}") is not None]
    total = sum(w for w, _ in used)
    return sum(w * v for w, v in used) / total if total else None


def residualize(rows: list[dict], metric_prefix: str = "xs_nifty", bins: int = 10) -> list[dict]:
    """Subtract the mean outcome of rows with a similar technical score (deciles),
    so a comparison asks: does X add information BEYOND technicals?"""
    scored = sorted((r for r in rows if tech_score(r) is not None), key=tech_score)
    out = []
    for i in range(bins):
        chunk = [dict(r) for r in scored[i * len(scored) // bins:(i + 1) * len(scored) // bins]]
        for h in HORIZONS:
            m = f"{metric_prefix}_{h}"
            vals = [r[m] for r in chunk if r.get(m) is not None]
            mean = sum(vals) / len(vals) if vals else 0.0
            for r in chunk:
                r[f"res_{h}"] = r[m] - mean if r.get(m) is not None else None
        out += chunk
    return out


def quantile_buckets(rows: list[dict], key: Callable[[dict], float | None], q: int = 5):
    have = sorted((r for r in rows if key(r) is not None), key=key)
    out = {}
    for i in range(q):
        chunk = have[i * len(have) // q:(i + 1) * len(have) // q]
        if chunk:
            out[f"Q{i + 1} [{key(chunk[0]):.2f}..{key(chunk[-1]):.2f}]"] = chunk
    return out


def band(value: float | None, edges: list[tuple[float, str]]) -> str | None:
    if value is None:
        return None
    label = None
    for lo, name in edges:
        if value >= lo:
            label = name
    return label


# -- report ----------------------------------------------------------------------------

def _fmt(x, pct=True):
    return "—" if x is None else (f"{x:+.3f}%" if pct else f"{x:.2f}")


def table(buckets: dict[str, list[dict]], metric="xs_nifty", horizons=HORIZONS) -> list[str]:
    head = "| bucket | " + " | ".join(f"{h}m mean [95% CI] · hit · n/days" for h in horizons) + " |"
    lines = [head, "|" + "---|" * (len(horizons) + 1)]
    for name, rows in buckets.items():
        cells = []
        for h in horizons:
            s = summary(rows, f"{metric}_{h}")
            flag = "" if sufficient(s) else " ⚠"
            cells.append(f"{_fmt(s['mean'])} [{_fmt(s['lo'])}, {_fmt(s['hi'])}] · "
                         f"{_fmt(s['hit'] * 100 if s['hit'] is not None else None, False)}% · "
                         f"{s['n']}/{s['days']}{flag}")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return lines


def ic_line(rows, feature, name, metric="xs_nifty", horizons=HORIZONS) -> str:
    parts = []
    for h in horizons:
        ic, t, n = daily_ic(rows, feature, f"{metric}_{h}")
        parts.append(f"{h}m IC {_fmt(ic, False)} (t {_fmt(t, False)}, {n} days)")
    return f"- Rank correlation of {name} with forward excess return: " + "; ".join(parts)


def by(rows, key) -> dict[str, list[dict]]:
    d = defaultdict(list)
    for r in rows:
        k = key(r)
        if k is not None:
            d[str(k)].append(r)
    return dict(sorted(d.items()))


def questions(rows: list[dict]) -> list[tuple[str, list[str]]]:
    day = [r for r in rows if r["mode"] == "DAY"]
    scalp = [r for r in rows if r["mode"] == "SCALP"]
    out = []

    rv = lambda r: r.get("rvol")
    mv_terc = quantile_buckets(day, lambda r: r.get("g_movement"), 3)
    within = [ic_line(chunk, rv, f"RVOL within movement {k}") for k, chunk in mv_terc.items()]
    qb = quantile_buckets(day, rv)
    keys = list(qb)
    out.append(("1. Does RVOL improve forward returns? (DAY rows)",
                table(qb) + [ic_line(day, rv, "RVOL"), *within,
                             f"- Verdict (top vs bottom quintile): "
                             f"{verdict(qb[keys[-1]], qb[keys[0]]) if len(keys) >= 2 else 'INSUFFICIENT'}"]))

    res = residualize([r for r in day if r.get("sector_verdict") != "UNAVAILABLE"])
    sb = by(res, lambda r: r.get("sector_verdict"))
    lines = ["Raw:"] + table(sb) + ["", "Beyond technicals (outcome minus same-technical-decile mean):"]
    lines += table(sb, metric="res")
    lines.append(f"- Verdict CONFIRMED vs WEAK (beyond technicals): "
                 f"{verdict(sb.get('CONFIRMED', []), sb.get('WEAK', []), metric='res')}")
    out.append(("2. Does sector confirmation improve outcomes?", lines))

    res = residualize([r for r in rows if r.get("news_verdict") not in (None, "NOT_CHECKED")])
    nb = by(res, lambda r: r.get("news_verdict"))
    lines = table(nb, metric="res") + [
        f"- Verdict POSITIVE vs NO_RELEVANT_INFORMATION (beyond technicals): "
        f"{verdict(nb.get('POSITIVE', []), nb.get('NO_RELEVANT_INFORMATION', []), metric='res')}",
        "- Live sessions only: no historical news exists (never backfilled)."]
    out.append(("3. Does news add information beyond technicals?", lines))

    cb = by(day, lambda r: None if r.get("confluence_count") is None
            else ("3+" if r["confluence_count"] >= 3 else str(r["confluence_count"])))
    out.append(("4. Does confluence improve outcomes? (setup families active, DAY)",
                table(cb) + [f"- Verdict 3+ vs 1: {verdict(cb.get('3+', []), cb.get('1', []))}",
                             f"- Verdict 2 vs 1: {verdict(cb.get('2', []), cb.get('1', []))}"]))

    edges = [(0, "<50"), (50, "50-64"), (65, "65-79"), (80, "80+")]
    for mode, mrows in (("DAY", day), ("SCALP", scalp)):
        bb = by(mrows, lambda r: band(r["score"], edges))
        out.append((f"5. Does the 80+ group beat 65-79? ({mode})",
                    table(bb) + [ic_line(mrows, lambda r: r["score"], "final score"),
                                 f"- Verdict 80+ vs 65-79: {verdict(bb.get('80+', []), bb.get('65-79', []))}",
                                 f"- Verdict 65-79 vs 50-64: {verdict(bb.get('65-79', []), bb.get('50-64', []))}"]))

    dc = lambda r: r.get("raw_movement_day_change_pct")
    db = by(day, lambda r: band(dc(r), [(-99, "<0%"), (0, "0-1%"), (1, "1-2%"), (2, "2-3%"), (3, ">3%")]))
    out.append(("6. Does the 2% prior move help? (day change vs previous close, DAY)",
                table(db) + [f"- Verdict 2-3% vs 1-2%: {verdict(db.get('2-3%', []), db.get('1-2%', []))}",
                             f"- Verdict >3% vs 1-2%: {verdict(db.get('>3%', []), db.get('1-2%', []))}",
                             "- No explicit 2% rule exists in the engine; closest: momentum ROC full "
                             "marks at 2% (DAY) and the movement ramp 0-3% day change."]))

    mq = quantile_buckets([r for r in scalp if r.get("micro_score") is not None],
                          lambda r: r["micro_score"], 3)
    mk = list(mq)
    out.append(("7. Does microstructure improve SCALP candidates? (5/15m)",
                table(mq, horizons=(5, 15)) +
                [ic_line(scalp, lambda r: r.get("micro_score"), "micro score", horizons=(5, 15)),
                 f"- Verdict top vs bottom tercile: "
                 f"{verdict(mq[mk[-1]], mq[mk[0]], horizons=(5, 15)) if len(mk) >= 2 else 'INSUFFICIENT'}",
                 "- Live sessions only: no historical depth/tick data."]))

    lunch = [r for r in day if r.get("lunch_penalty")]
    other = [r for r in day if not r.get("lunch_penalty") and "11:00" <= r["as_of"][11:16] < "14:30"]
    lb = {"lunch 11:30-13:30": lunch, "11:00-11:30 + 13:30-14:30": other}
    same = lambda rs: [r for r in rs if (r.get("base_score") or 0) >= 50]
    out.append(("8. Does the lunch penalty improve results? (DAY, same pre-penalty score >= 50)",
                table({k: same(v) for k, v in lb.items()}) +
                [f"- Verdict lunch vs neighbouring hours (pre-penalty score >= 50): "
                 f"{verdict(same(lunch), same(other))} — the penalty is justified only if lunch is worse."]))

    names = sorted({k[6:] for r in day for k in r if k.startswith("setup_")} - {"best", "best_state"})
    sb = {n: [r for r in day if r.get(f"setup_{n}") == "TRIGGERED"] for n in names}
    none = [r for r in day if not r.get("triggered")]
    out.append(("9. Are some setups consistently worse? (TRIGGERED, DAY)",
                table(sb | {"(no setup triggered)": none}) +
                [f"- {n} vs no setup: {verdict(v, none)}" for n, v in sb.items()]))

    groups = ["movement", "volume", "momentum", "setup", "market", "sector", "liquidity"]
    lines = ["Correlation between group scores (DAY rows, pairwise complete):", "",
             "| | " + " | ".join(groups) + " |", "|" + "---|" * (len(groups) + 1)]
    for g in groups:
        cells = []
        for h in groups:
            pairs = [(r[f"g_{g}"], r[f"g_{h}"]) for r in day
                     if r.get(f"g_{g}") is not None and r.get(f"g_{h}") is not None]
            c = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) > 20 else None
            cells.append(_fmt(c, False))
        lines.append(f"| {g} | " + " | ".join(cells) + " |")
    lines += ["", "Predictive value of each group on its own:"]
    lines += [ic_line(day, lambda r, g=g: r.get(f"g_{g}"), g, horizons=(15, 30)) for g in groups]
    lines.append("- Two groups with correlation > 0.7 and no separate predictive value are redundant.")
    out.append(("10. Are some components redundant?", lines))
    return out


def report(rows: list[dict], title: str) -> str:
    days = sorted({r["day"] for r in rows})
    head = [f"# {title}", "",
            f"Panel rows: {len(rows)} · days: {len(days)} "
            f"({days[0] if days else '—'} .. {days[-1] if days else '—'}) · "
            f"versions: {', '.join(sorted({str(r.get('strategy_version')) for r in rows})) or '—'}",
            "", "Metric: forward return in excess of NIFTY, entry at the open of the snapshot "
            "minute. 95% intervals resample whole days. ⚠ = fewer than "
            f"{MIN_ROWS} rows or {MIN_DAYS} days (no verdict).", ""]
    if len(days) < MIN_DAYS:
        head += [f"**{len(days)} day(s): description, not evidence.**", ""]
    body = []
    for name, lines in questions(rows):
        body += [f"## {name}", "", *lines, ""]
    return "\n".join(head + body)
