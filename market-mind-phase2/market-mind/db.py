"""Shared SQLite helpers, schema and settings."""
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

DB_PATH = Path(os.getenv("MARKET_MIND_DB") or Path(__file__).parent / "data" / "market_mind.db")
DATA_DIR = DB_PATH.parent
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_WATCHLIST = "^GSPC, ^IXIC, ^NSEI, GC=F, CL=F, BTC-USD, AAPL, MSFT, NVDA, RELIANCE.NS"

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, role TEXT, content TEXT);
CREATE TABLE IF NOT EXISTS docs(name TEXT PRIMARY KEY);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(doc UNINDEXED, text);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sync_log(source TEXT PRIMARY KEY, ts TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS prices(symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
    volume REAL, PRIMARY KEY(symbol, date));
CREATE TABLE IF NOT EXISTS filings(accession TEXT PRIMARY KEY, symbol TEXT, form TEXT, filed TEXT, description TEXT);
CREATE TABLE IF NOT EXISTS insider(accession TEXT, idx INTEGER, symbol TEXT, owner TEXT, role TEXT, date TEXT,
    code TEXT, shares REAL, price REAL, PRIMARY KEY(accession, idx));
CREATE TABLE IF NOT EXISTS form4_done(accession TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS cot(key TEXT, market TEXT, date TEXT, oi REAL, groups TEXT, report TEXT,
    PRIMARY KEY(key, date));
"""


def db():
    return closing(sqlite3.connect(DB_PATH, timeout=30))


def init():
    with db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)


def get_setting(key, default=""):
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key, value):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))
        con.commit()


def parse_watchlist(text):
    out = []
    for s in re.split(r"[,\s]+", (text or "").upper()):
        if re.fullmatch(r"[\^A-Z0-9.=\-]{1,15}", s) and s not in out:
            out.append(s)
    return out[:25]


def watchlist():
    return parse_watchlist(get_setting("watchlist", DEFAULT_WATCHLIST)) or parse_watchlist(DEFAULT_WATCHLIST)
