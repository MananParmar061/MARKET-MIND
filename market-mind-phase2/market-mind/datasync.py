"""Downloads free public data into the local database. This is the ONLY module that uses the internet.

Sources: Yahoo Finance chart endpoint (unofficial, daily prices), SEC EDGAR (filings, Form 4 insider trades),
CFTC Commitments of Traders (weekly futures positioning by trader category).
"""
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from db import DATA_DIR, db, get_setting, watchlist

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")
COT_DATASETS = {"tff": "gpe5-46if", "disagg": "72hh-3qpy"}  # CFTC futures-only datasets
COT_MARKETS = [
    {"key": "sp500", "label": "S&P 500 E-mini", "report": "tff", "pattern": "E-MINI S&P 500",
     "words": ["s&p", "sp500", "spx"], "symbols": ["^GSPC"]},
    {"key": "nasdaq", "label": "Nasdaq-100", "report": "tff", "pattern": "NASDAQ",
     "words": ["nasdaq", "ndx"], "symbols": ["^IXIC"]},
    {"key": "euro", "label": "Euro FX", "report": "tff", "pattern": "EURO FX",
     "words": ["euro", "eurusd"], "symbols": ["EURUSD=X"]},
    {"key": "yen", "label": "Japanese yen", "report": "tff", "pattern": "JAPANESE YEN",
     "words": ["yen", "jpy"], "symbols": ["JPY=X"]},
    {"key": "bitcoin", "label": "Bitcoin (CME)", "report": "tff", "pattern": "BITCOIN",
     "words": ["bitcoin", "btc"], "symbols": ["BTC-USD"]},
    {"key": "gold", "label": "Gold", "report": "disagg", "pattern": "GOLD",
     "words": ["gold"], "symbols": ["GC=F"]},
    {"key": "silver", "label": "Silver", "report": "disagg", "pattern": "SILVER",
     "words": ["silver"], "symbols": ["SI=F"]},
    {"key": "crude", "label": "Crude oil (WTI)", "report": "disagg", "pattern": "CRUDE OIL, LIGHT SWEET",
     "words": ["crude", "wti", "oil"], "symbols": ["CL=F"]},
    {"key": "copper", "label": "Copper", "report": "disagg", "pattern": "COPPER",
     "words": ["copper"], "symbols": ["HG=F"]},
]

STATE = {"running": False, "lines": [], "finished": None}
_lock = threading.Lock()


def _log(msg):
    STATE["lines"].append(msg)
    del STATE["lines"][:-80]


def _err(e):
    return f"{type(e).__name__}: {e}"[:160]


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _get(client, url, tries=2, **kw):
    for i in range(tries):
        r = client.get(url, **kw)
        if r.status_code in (429, 500, 502, 503) and i < tries - 1:
            time.sleep(4 * (i + 1))
            continue
        r.raise_for_status()
        return r


# ---------- parsers (pure functions, no network) ----------

def parse_yahoo(data):
    res = (data.get("chart") or {}).get("result")
    if not res:
        return []
    r = res[0]
    offset = (r.get("meta") or {}).get("gmtoffset") or 0
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    out = []
    for i, ts in enumerate(r.get("timestamp") or []):
        try:
            o, h, l, c = (q[k][i] for k in ("open", "high", "low", "close"))
            v = q["volume"][i] or 0
        except (KeyError, IndexError):
            continue
        if None in (o, h, l, c):
            continue
        out.append((datetime.fromtimestamp(ts + offset, timezone.utc).date().isoformat(), o, h, l, c, v))
    return out


def _interesting(form):
    return form in ("4", "4/A") or form.startswith(("10-K", "10-Q", "8-K", "SCHEDULE 13", "SC 13"))


def parse_submissions(data, since):
    rec = (data.get("filings") or {}).get("recent") or {}
    acc = rec.get("accessionNumber") or []
    n = len(acc)
    forms, filed = rec.get("form") or [""] * n, rec.get("filingDate") or [""] * n
    docs = rec.get("primaryDocument") or [""] * n
    descs = rec.get("primaryDocDescription") or [""] * n
    return [{"accession": acc[i], "form": forms[i], "filed": filed[i], "doc": docs[i], "desc": descs[i] or forms[i]}
            for i in range(n) if filed[i] >= since and _interesting(forms[i])]


def parse_form4(xml_data):
    root = ET.fromstring(xml_data)
    owner = (root.findtext("reportingOwner/reportingOwnerId/rptOwnerName") or "unknown").strip()
    role = ""
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    if rel is not None:
        flag = lambda tag: (rel.findtext(tag) or "").strip().lower() in ("1", "true")
        role = ((rel.findtext("officerTitle") or "").strip()
                or ("Director" if flag("isDirector") else "")
                or ("10% owner" if flag("isTenPercentOwner") else "")
                or ("Officer" if flag("isOfficer") else ""))
    txns = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        txns.append(((t.findtext("transactionDate/value") or "")[:10],
                     (t.findtext("transactionCoding/transactionCode") or "").strip(),
                     _f(t.findtext("transactionAmounts/transactionShares/value")),
                     _f(t.findtext("transactionAmounts/transactionPricePerShare/value"))))
    return owner, role, txns


def cot_extract(row, report):
    """Return (market, date, open_interest, {category: [long, short]}) or None if the row is unusable."""
    market = row.get("market_and_exchange_names")
    d = (row.get("report_date_as_yyyy_mm_dd") or "")[:10]
    oi = _f(row.get("open_interest_all"))
    if not market or not d or oi is None:
        return None
    cats = ("dealer", "asset_mgr", "lev_money", "other_rept") if report == "tff" \
        else ("prod_merc", "swap", "m_money", "other_rept")
    groups = {}
    for cat in cats:
        pair = []
        for side in ("long", "short"):
            rx = re.compile(rf"^{cat}_+positions_{side}(_all)?$")
            pair.append(next((_f(v) for k, v in row.items() if rx.match(k)), None))
        if None not in pair:
            groups[cat] = pair
    return (market, d, oi, groups) if groups else None


# ---------- downloaders ----------

def sync_prices(client, symbols):
    ok = 0
    for sym in symbols:
        try:
            r = _get(client, f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym, safe='')}",
                     params={"range": "1y", "interval": "1d"}, headers={"User-Agent": BROWSER_UA})
            rows = parse_yahoo(r.json())
            if not rows:
                raise ValueError("no price rows returned")
            with db() as con:
                con.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", [(sym, *x) for x in rows])
                con.commit()
            _log(f"prices {sym}: {len(rows)} bars, last {rows[-1][0]}")
            ok += 1
        except Exception as e:
            _log(f"prices {sym}: FAILED ({_err(e)})")
        time.sleep(0.6)
    return ok


def _ticker_map(client, hdr):
    f = DATA_DIR / "company_tickers.json"
    if f.exists() and time.time() - f.stat().st_mtime < 7 * 86400:
        raw = json.loads(f.read_text())
    else:
        raw = _get(client, "https://www.sec.gov/files/company_tickers.json", headers=hdr).json()
        f.write_text(json.dumps(raw))
        time.sleep(0.2)
    return {v["ticker"].upper(): int(v["cik_str"]) for v in raw.values()}


def sync_sec(client, symbols, email):
    hdr = {"User-Agent": f"MarketMind personal-research {email}"}
    tmap = _ticker_map(client, hdr)
    since = (date.today() - timedelta(days=180)).isoformat()
    f4_since = (date.today() - timedelta(days=90)).isoformat()
    done_syms = 0
    for sym in symbols:
        cik = tmap.get(sym)
        if not cik:
            continue
        try:
            subs = _get(client, f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=hdr).json()
            time.sleep(0.2)
            items = parse_submissions(subs, since)
            with db() as con:
                con.executemany("INSERT OR REPLACE INTO filings VALUES (?,?,?,?,?)",
                                [(i["accession"], sym, i["form"], i["filed"], i["desc"]) for i in items])
                con.commit()
                seen = {r[0] for r in con.execute("SELECT accession FROM form4_done")}
            todo = [i for i in items if i["form"] in ("4", "4/A") and i["filed"] >= f4_since
                    and i["accession"] not in seen][:25]
            parsed = 0
            for it in todo:
                url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{it['accession'].replace('-', '')}/"
                       f"{it['doc'].split('/')[-1]}")
                try:
                    owner, role, txns = parse_form4(_get(client, url, headers=hdr).content)
                    with db() as con:
                        con.executemany("INSERT OR REPLACE INTO insider VALUES (?,?,?,?,?,?,?,?,?)",
                                        [(it["accession"], k, sym, owner, role, d, code, sh, pr)
                                         for k, (d, code, sh, pr) in enumerate(txns)])
                        con.execute("INSERT OR REPLACE INTO form4_done VALUES (?)", (it["accession"],))
                        con.commit()
                    parsed += 1
                except Exception as e:
                    _log(f"  form 4 {it['accession']}: skipped ({_err(e)})")
                time.sleep(0.2)
            _log(f"sec {sym}: {len(items)} filings, {parsed} new insider forms")
            done_syms += 1
        except Exception as e:
            _log(f"sec {sym}: FAILED ({_err(e)})")
    return done_syms


def sync_cot(client):
    cutoff = (date.today() - timedelta(days=200)).isoformat()
    ok = 0
    for m in COT_MARKETS:
        try:
            r = _get(client, f"https://publicreporting.cftc.gov/resource/{COT_DATASETS[m['report']]}.json",
                     params={"$where": (f"market_and_exchange_names like '%{m['pattern']}%' AND "
                                        f"report_date_as_yyyy_mm_dd > '{cutoff}T00:00:00.000'"),
                             "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": "400"},
                     headers={"User-Agent": "MarketMind personal-research"})
            body = r.json()
            rows = [x for x in (cot_extract(row, m["report"]) for row in body) if x]
            if not rows:
                keys = ", ".join(list(body[0])[:6]) if body else "no rows"
                raise ValueError(f"no usable rows (fields seen: {keys})")
            latest = max(x[1] for x in rows)
            best = max((x for x in rows if x[1] == latest), key=lambda x: x[2])[0]  # largest market that matches
            keep = [x for x in rows if x[0] == best]
            with db() as con:
                con.execute("DELETE FROM cot WHERE key = ?", (m["key"],))
                con.executemany("INSERT OR REPLACE INTO cot VALUES (?,?,?,?,?,?)",
                                [(m["key"], mk, d, oi, json.dumps(g), m["report"]) for mk, d, oi, g in keep])
                con.commit()
            _log(f"cot {m['label']}: {best}, {len(keep)} weeks, latest {latest}")
            ok += 1
        except Exception as e:
            _log(f"cot {m['label']}: FAILED ({_err(e)})")
        time.sleep(0.3)
    return ok


# ---------- orchestration ----------

def _stamp(source, detail):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO sync_log VALUES (?,?,?)",
                    (source, datetime.now(timezone.utc).isoformat(timespec="seconds"), detail))
        con.commit()


def run_sync(client=None):
    if client is None:
        import httpx
        client = httpx.Client(timeout=30, follow_redirects=True)
    symbols = watchlist()
    email = get_setting("sec_email").strip()

    _log(f"--- prices ({len(symbols)} symbols) ---")
    n = sync_prices(client, symbols)
    if n:
        _stamp("prices", f"{n} of {len(symbols)} symbols")

    _log("--- SEC filings and insider trades ---")
    if not email:
        _log("skipped: add your email under Data settings (the SEC requires a contact in every request)")
    else:
        try:
            n = sync_sec(client, symbols, email)
            if n:
                _stamp("sec", f"{n} companies")
            else:
                _log("no US-listed companies from your watchlist were found at the SEC")
        except Exception as e:
            _log(f"SEC FAILED ({_err(e)})")

    _log("--- CFTC positioning ---")
    n = sync_cot(client)
    if n:
        _stamp("cot", f"{n} of {len(COT_MARKETS)} markets")
    _log("--- finished ---")


def start():
    with _lock:
        if STATE["running"]:
            return False
        STATE.update(running=True, lines=[], finished=None)

    def work():
        try:
            run_sync()
        except Exception as e:
            _log(f"sync stopped: {_err(e)}")
        finally:
            STATE.update(running=False, finished=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    threading.Thread(target=work, daemon=True).start()
    return True
