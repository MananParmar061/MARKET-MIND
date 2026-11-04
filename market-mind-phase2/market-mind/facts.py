"""Builds compact, factual text from the local database for the model. No internet needed."""
import json
import re
from datetime import date, timedelta

import analysis
from datasync import COT_MARKETS
from db import db, watchlist

ALIASES = {
    "^GSPC": ["s&p 500", "s&p", "sp500", "spx"], "^IXIC": ["nasdaq"], "^DJI": ["dow", "dow jones"],
    "^NSEI": ["nifty", "nifty 50", "nifty50"], "GC=F": ["gold"], "SI=F": ["silver"],
    "CL=F": ["crude", "oil", "wti"], "BTC-USD": ["bitcoin", "btc"], "ETH-USD": ["ethereum", "eth"],
    "EURUSD=X": ["euro", "eurusd"], "AAPL": ["apple"], "MSFT": ["microsoft"], "NVDA": ["nvidia"],
    "GOOGL": ["google", "alphabet"], "AMZN": ["amazon"], "TSLA": ["tesla"], "META": ["meta"],
    "RELIANCE.NS": ["reliance"],
}
CAT_LABEL = {"dealer": "Dealers", "asset_mgr": "Asset managers", "lev_money": "Leveraged funds",
             "other_rept": "Other reportables", "prod_merc": "Producers/merchants", "swap": "Swap dealers",
             "m_money": "Managed money"}
INSIDER_WORDS = ("insider", "form 4", "filing", "8-k", "10-k", "10-q", "13d", "13g")
COT_WORDS = ("cot", "commitment", "positioning", "hedge fund", "leveraged", "managed money", "asset manager",
             "speculat", "big player", "smart money")


def _has(q, phrase):
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", q) is not None


def _mentions(q, sym):
    base = re.split(r"[.=\-]", sym.lower().lstrip("^"))[0]
    names = {sym.lower().lstrip("^"), base, *ALIASES.get(sym, [])}
    return any(_has(q, n) for n in names if len(n) >= 2)


def _pct(a, b):
    return f"{(a / b - 1) * 100:+.1f}%" if b else "n/a"


def _money(x):
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"${x / div:.1f}{suffix}"
    return f"${x:,.0f}"


def freshness():
    with db() as con:
        rows = con.execute("SELECT source, ts FROM sync_log").fetchall()
    today = date.today().isoformat()
    if not rows:
        return (f"PC date: {today}. NO market data has been synced yet, so there are no prices, filings or "
                "positioning numbers available. Tell the user to press 'Sync now' in the sidebar (internet needed).")
    synced = "; ".join(f"{s} synced {t[:16].replace('T', ' ')} UTC" for s, t in rows)
    return (f"PC date: {today}. Local data: {synced}. Daily bars are not live, and the newest bar may be partial "
            "if the market was open at sync time.")


def _price_rows(sym, n=252):
    with db() as con:
        rows = con.execute("SELECT date, open, high, low, close, volume FROM prices WHERE symbol = ? "
                           "ORDER BY date DESC LIMIT ?", (sym, n)).fetchall()
    return rows[::-1]


def price_facts(sym):
    rows = _price_rows(sym)
    if not rows:
        return f"{sym}: no price data synced yet."
    c, last = [r[4] for r in rows], rows[-1]
    ch = lambda k: _pct(c[-1], c[-1 - k]) if len(c) > k else "n/a"
    lines = [f"{sym}: last bar {last[0]}, close {c[-1]:,.2f}. Change 1d {ch(1)}, 5d {ch(5)}, 20d {ch(20)}, 60d {ch(60)}.",
             f"Range of last {len(rows)} bars: low {min(r[3] for r in rows):,.2f}, high {max(r[2] for r in rows):,.2f}."]
    for n in (20, 50):
        if len(c) >= n:
            avg = sum(c[-n:]) / n
            lines.append(f"Close is {'above' if c[-1] > avg else 'below'} the {n}-bar average ({avg:,.2f}).")
    if len(rows) >= 5:
        lines.append(analysis.summarize_rows(rows[-60:], f"{sym} recent daily bars"))
    return "\n".join(lines)


def snapshot(symbols):
    lines = []
    for s in symbols:
        rows = _price_rows(s, 25)
        if rows:
            c = [r[4] for r in rows]
            ch = lambda k: _pct(c[-1], c[-1 - k]) if len(c) > k else "n/a"
            lines.append(f"- {s}: {c[-1]:,.2f} on {rows[-1][0]} (1d {ch(1)}, 5d {ch(5)}, 20d {ch(20)})")
    return "Watchlist snapshot:\n" + "\n".join(lines) if lines else ""


def insider_facts(sym, days=90):
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with db() as con:
        rows = con.execute("SELECT owner, role, date, code, shares, price FROM insider "
                           "WHERE symbol = ? AND date >= ? AND code IN ('P', 'S')", (sym, cutoff)).fetchall()
        has_filings = con.execute("SELECT 1 FROM filings WHERE symbol = ? LIMIT 1", (sym,)).fetchone()
    if not rows:
        return (f"{sym} insiders: no open-market purchases or sales in the last {days} days among synced Form 4 filings."
                if has_filings else "")
    val = lambda r: (r[4] or 0) * (r[5] or 0)
    buys, sells = [r for r in rows if r[3] == "P"], [r for r in rows if r[3] == "S"]
    lines = [f"{sym} insiders, last {days} days (Form 4, open-market only): {len(buys)} purchases worth "
             f"{_money(sum(map(val, buys)))}; {len(sells)} sales worth {_money(sum(map(val, sells)))}. "
             "Sales are often pre-scheduled and are weaker signals than purchases."]
    for o, role, d, code, sh, pr in sorted(rows, key=val, reverse=True)[:3]:
        lines.append(f"- {o} ({role or 'insider'}) {'bought' if code == 'P' else 'sold'} {sh or 0:,.0f} shares "
                     f"@ {pr or 0:,.2f} on {d} ({_money((sh or 0) * (pr or 0))})")
    return "\n".join(lines)


def filing_facts(sym):
    with db() as con:
        rows = con.execute("SELECT form, filed, description FROM filings WHERE symbol = ? AND form NOT IN ('4', '4/A') "
                           "ORDER BY filed DESC LIMIT 4", (sym,)).fetchall()
    return f"{sym} recent SEC filings: " + "; ".join(f"{f} on {d} ({desc})" for f, d, desc in rows) if rows else ""


def global_insiders(days=30):
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with db() as con:
        rows = con.execute("SELECT symbol, owner, role, date, shares, price FROM insider WHERE code = 'P' AND date >= ?",
                           (cutoff,)).fetchall()
    rows = sorted(rows, key=lambda r: (r[4] or 0) * (r[5] or 0), reverse=True)[:5]
    if not rows:
        return ""
    return f"Largest insider open-market purchases in the watchlist, last {days} days:\n" + "\n".join(
        f"- {s}: {o} ({role or 'insider'}) bought {sh or 0:,.0f} shares @ {pr or 0:,.2f} on {d} "
        f"({_money((sh or 0) * (pr or 0))})" for s, o, role, d, sh, pr in rows)


def cot_facts(key, brief=False):
    spec = next(m for m in COT_MARKETS if m["key"] == key)
    with db() as con:
        rows = con.execute("SELECT date, oi, groups, market FROM cot WHERE key = ? ORDER BY date DESC LIMIT 5",
                           (key,)).fetchall()
    if not rows:
        return ""
    d0, oi0, g0, name = rows[0]
    g0 = json.loads(g0)
    prev = json.loads(rows[1][2]) if len(rows) > 1 else {}
    old = json.loads(rows[4][2]) if len(rows) > 4 else {}
    net = lambda g, cat: g[cat][0] - g[cat][1]
    if brief:
        cat = "lev_money" if "lev_money" in g0 else "m_money" if "m_money" in g0 else next(iter(g0))
        return f"- {spec['label']} ({d0}): {CAT_LABEL[cat]} net {net(g0, cat):+,.0f}"
    lines = [f"COT {spec['label']} ({name}), positions as of {d0}, open interest {oi0:,.0f}:"]
    for cat in g0:
        n = net(g0, cat)
        line = f"- {CAT_LABEL.get(cat, cat)}: net {n:+,.0f} ({n / oi0 * 100:+.1f}% of open interest)" if oi0 else f"- {cat}: net {n:+,.0f}"
        if cat in prev:
            line += f", {n - net(prev, cat):+,.0f} vs prior week"
        if cat in old:
            line += f", {n - net(old, cat):+,.0f} vs 4 weeks ago"
        lines.append(line)
    return "\n".join(lines)


def build_facts(question):
    q = question.lower()
    wl = watchlist()
    mentioned = [s for s in wl if _mentions(q, s)]
    parts = [freshness()]
    if mentioned:
        for s in mentioned[:2]:
            parts += [price_facts(s), insider_facts(s), filing_facts(s)]
    else:
        parts.append(snapshot(wl))
        if any(w in q for w in INSIDER_WORDS):
            parts.append(global_insiders())

    cot_keys = [m["key"] for m in COT_MARKETS
                if any(_has(q, w) for w in m["words"]) or any(s in mentioned for s in m["symbols"])]
    if cot_keys:
        parts += [cot_facts(k) for k in cot_keys[:2]]
    elif any(w in q for w in COT_WORDS):
        parts.append("COT overview (latest weekly report):\n" + "\n".join(
            filter(None, (cot_facts(m["key"], brief=True) for m in COT_MARKETS))))
    if cot_keys or any(w in q for w in COT_WORDS):
        parts.append("(COT is weekly Tuesday data published on Friday, grouped by trader category, "
                     "never individual traders.)")
    return "\n\n".join(p for p in parts if p)[:3500]
