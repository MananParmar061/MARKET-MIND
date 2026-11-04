"""Market Mind: a private chat assistant that runs entirely on this PC (FastAPI + Ollama + SQLite).
Only datasync.py ever uses the internet, and only when you press "Sync now"."""
import io
import json
import os
import re
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import analysis
import datasync
import facts
from db import DEFAULT_WATCHLIST, db, get_setting, init, parse_watchlist, set_setting

OLLAMA = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("MODEL", "qwen2.5:3b")

SYSTEM = (
    "You are Market Mind, a private financial-markets research assistant running offline on the user's PC. "
    "Rules: (1) Use only numbers that appear in the conversation, the LOCAL MARKET DATA or the file excerpts "
    "below; never invent prices, volumes, filings, positions or news. (2) If the data needed is missing or old, "
    "say what is missing and mention the data date. (3) Explain the evidence behind each observation instead of "
    "just saying buy or sell. (4) Order-flow and positioning data never reveal which named institution is "
    "trading; COT groups are categories, not individual traders. (5) You are not a financial advisor. "
    "Keep answers concise and quote dates next to numbers."
)

init()
app = FastAPI(title="Market Mind")


def chunk(text, size=1000):
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > size:
            out.append(para[:size])
            para = para[size:]
        if not para:
            continue
        if cur and len(cur) + len(para) > size:
            out.append(cur)
            cur = ""
        cur = (cur + "\n\n" + para).strip()
    if cur:
        out.append(cur)
    return out


def search(question, k=3):
    words = {w.lower() for w in re.findall(r"\w{3,}", question)}
    if not words:
        return ""
    match = " OR ".join(f'"{w}"' for w in words)
    with db() as con:
        rows = con.execute(
            "SELECT doc, text FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?", (match, k)
        ).fetchall()
    return "\n\n".join(f"[{d}]\n{t}" for d, t in rows)


class Ask(BaseModel):
    message: str


class Settings(BaseModel):
    sec_email: str = ""
    watchlist: str = ""


@app.post("/api/chat")
async def chat(ask: Ask):
    system = SYSTEM + "\n\nLOCAL MARKET DATA (read from this PC's database):\n" + facts.build_facts(ask.message)
    excerpts = search(ask.message)
    if excerpts:
        system += f"\n\nEXCERPTS FROM THE USER'S LOCAL FILES:\n{excerpts}"
    with db() as con:
        hist = con.execute(
            "SELECT role, content FROM (SELECT * FROM messages ORDER BY id DESC LIMIT 6) ORDER BY id"
        ).fetchall()
        con.execute("INSERT INTO messages(role, content) VALUES ('user', ?)", (ask.message,))
        con.commit()
    msgs = [{"role": "system", "content": system}]
    msgs += [{"role": r, "content": c} for r, c in hist]
    msgs.append({"role": "user", "content": ask.message})

    async def stream():
        parts = []
        body = {"model": MODEL, "messages": msgs, "stream": True,
                "options": {"num_ctx": 6144, "temperature": 0.3}}
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", f"{OLLAMA}/api/chat", json=body) as r:
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        if "error" in data:
                            yield f"Ollama error: {data['error']}"
                            return
                        piece = data.get("message", {}).get("content", "")
                        parts.append(piece)
                        yield piece
        except httpx.ConnectError:
            yield "Cannot reach Ollama. Start Ollama from the Windows Start menu, then try again."
            return
        if parts:
            with db() as con:
                con.execute("INSERT INTO messages(role, content) VALUES ('assistant', ?)", ("".join(parts),))
                con.commit()

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    raw = await file.read()
    name, ext = file.filename, Path(file.filename).suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(raw)).pages)
    elif ext == ".csv":
        text = analysis.summarize_csv(raw)
    else:
        text = raw.decode("utf-8", "ignore")
    pieces = chunk(text)
    if not pieces:
        raise HTTPException(400, "No readable text found in this file (scanned PDFs are not supported yet).")
    with db() as con:
        con.execute("DELETE FROM chunks WHERE doc = ?", (name,))
        con.executemany("INSERT INTO chunks(doc, text) VALUES (?, ?)", [(name, p) for p in pieces])
        con.execute("INSERT OR REPLACE INTO docs(name) VALUES (?)", (name,))
        con.commit()
    return {"name": name, "chunks": len(pieces), "summary": text if ext == ".csv" else ""}


@app.get("/api/docs")
def docs():
    with db() as con:
        return [r[0] for r in con.execute("SELECT name FROM docs ORDER BY name")]


@app.get("/api/history")
def history():
    with db() as con:
        rows = con.execute("SELECT role, content FROM messages ORDER BY id DESC LIMIT 50").fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


@app.delete("/api/history")
def clear_history():
    with db() as con:
        con.execute("DELETE FROM messages")
        con.commit()
    return {"ok": True}


@app.get("/api/settings")
def read_settings():
    return {"sec_email": get_setting("sec_email"), "watchlist": get_setting("watchlist", DEFAULT_WATCHLIST)}


@app.post("/api/settings")
def save_settings(s: Settings):
    email = s.sec_email.strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "That does not look like an email address.")
    symbols = parse_watchlist(s.watchlist)
    if not symbols:
        raise HTTPException(400, "Enter at least one symbol.")
    set_setting("sec_email", email)
    set_setting("watchlist", ", ".join(symbols))
    return {"sec_email": email, "watchlist": ", ".join(symbols)}


@app.post("/api/sync")
def start_sync():
    if not datasync.start():
        raise HTTPException(409, "A sync is already running.")
    return {"started": True}


@app.get("/api/sync/status")
def sync_status():
    with db() as con:
        sources = {s: {"ts": t, "detail": d} for s, t, d in con.execute("SELECT source, ts, detail FROM sync_log")}
    return {"running": datasync.STATE["running"], "lines": datasync.STATE["lines"][-14:], "sources": sources}


@app.get("/api/status")
async def status():
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            names = [m["name"] for m in (await c.get(f"{OLLAMA}/api/tags")).json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        return {"ollama": False, "model": MODEL, "ready": False}
    return {"ollama": True, "model": MODEL, "ready": MODEL in names or f"{MODEL}:latest" in names}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
