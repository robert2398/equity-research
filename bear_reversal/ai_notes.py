"""On-demand LLM research notes for Next-Bull stocks (OpenRouter, web-grounded).

ON DEMAND ONLY: nothing here runs during the daily scan. A note is produced only
when the user clicks "Research" on a Next Bull Watch row (POST /api/ai-note/<SYM>
via serve_ui.py) or runs this file by hand. The daily run merely re-attaches notes
already in the cache (no tokens).

Scope: only symbols on today's Next Bull lists (signals + warming) are accepted —
other scan output never reaches the model.

The rule-based BAGGER SIGNAL (bagger_lab.py) is the only thing with a tested edge.
A note NEVER adds, removes or re-ranks names; it gives a human the context a chart
can't show:
  catalysts  — order wins, capacity, turnaround, sector tailwind behind the move
  red flags  — ASM/GSM surveillance, promoter pledge, SEBI/ED action, auditor exit,
               dilution to unknown allottees, pump/operator chatter, insolvency
  risk       — low / medium / high, the model's read of those red flags
Two depths: quick (default, bounded Exa search, ~$0.03) and deep (the model runs its own
searches with an explicit surveillance/pledge/SEBI/dilution checklist, ~$0.70).
Model output is untrusted text: schema-validated, clipped, rendered as plain text,
tagged with model + date, cached for cache_days (a repeat click is free).

    python bear_reversal/ai_notes.py INDORAMA            # research one name
    python bear_reversal/ai_notes.py INDORAMA --dry-run  # show the prompt, no API call
    python bear_reversal/ai_notes.py INDORAMA --deep     # thorough: model searches itself
    python bear_reversal/ai_notes.py --brief             # market/regime brief
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

API = "https://openrouter.ai/api/v1/chat/completions"
CACHE = HERE / "state" / "ai_notes.json"
WATCH_JSON = HERE / "ui_data" / "next_bull_watch.json"
RISKS = ("low", "medium", "high")
BRIEF_KEY = "__brief__"
_lock = threading.Lock()

SYSTEM = (
    "You are an equity research assistant for Indian NSE stocks. A rule-based screen "
    "has ALREADY selected the stock; you do not decide whether to buy it and you never "
    "give buy/sell/target-price advice. Using current web results (news, NSE/BSE "
    "filings, exchange surveillance lists), report what is driving the stock and any "
    "red flags. Prefer primary sources (exchange filings, company releases, SEBI) over "
    "tip sites. If you cannot verify something, leave it out. Be terse.")

NOTE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["company", "sector", "summary", "catalysts", "red_flags", "risk",
                 "sources"],
    "properties": {
        "company": {"type": "string"},
        "sector": {"type": "string"},
        "summary": {"type": "string", "description": "<= 40 words: why it is moving now"},
        "catalysts": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "red_flags": {"type": "array", "items": {"type": "string"}, "maxItems": 4,
                      "description": "ASM/GSM, pledge, SEBI/ED, auditor exit, dilution, "
                                     "pump chatter, insolvency, governance"},
        "risk": {"type": "string", "enum": list(RISKS)},
        "sources": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
}

BRIEF_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "drivers", "watch_for", "sources"],
    "properties": {
        "summary": {"type": "string", "description": "<= 60 words"},
        "drivers": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "watch_for": {"type": "array", "items": {"type": "string"}, "maxItems": 3,
                      "description": "events that would confirm or break a recovery"},
        "sources": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
}


class AiNoteError(Exception):
    """Refusal or failure the UI shows verbatim (never contains the API key)."""


def _cfg() -> dict:
    from data_access import load_config
    return load_config().get("ai_notes", {}) or {}


def _api_key() -> str | None:
    from dotenv import load_dotenv
    load_dotenv(HERE.parent / ".env", override=False)
    return os.environ.get("OPENROUTER_API_KEY") or None


def _load_watch() -> dict:
    try:
        return json.loads(WATCH_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AiNoteError("no next_bull_watch.json — run run_daily.py first")


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")


def _stock_prompt(row: dict, regime: dict, as_of: str) -> str:
    facts = {k: (round(row[k], 2) if isinstance(row.get(k), float) else row.get(k))
             for k in ("close", "rs126", "rs252", "turnover_cr", "turnover_accel",
                       "thrust_count_63d", "dd3y", "ret6m", "near52")}
    return (f"NSE symbol: {row['symbol']} (data as of {as_of}).\n"
            f"Why the screen picked it (do not re-judge this): {json.dumps(facts)} — "
            "rs = 6/12-month return percentile vs all NSE stocks, turnover_accel = recent "
            "20-day traded value vs prior 120 days, dd3y = distance from 3-year high.\n"
            f"Market regime: {regime.get('phase')} ({regime.get('breadth200', 0):.0%} of NSE "
            "stocks above 200-DMA).\n"
            "Search recent news and filings (last ~6 months) and return the JSON note.")


def _brief_prompt(watch: dict) -> str:
    r = watch["regime"]
    return (f"As of {watch['as_of']}: {r['breadth200']:.0%} of NSE stocks are above their "
            f"200-DMA (6-month low {r['breadth_min6m']:.0%}); phase {r['phase']}. "
            "Search current news and summarise what is driving Indian small/mid-cap breadth "
            "now (flows, policy, earnings, global) and what would confirm or break a "
            "recovery. No stock picks, no forecasts presented as fact.")


def _call(cfg: dict, key: str, prompt: str, schema: dict, name: str) -> dict:
    body = {
        "model": cfg.get("model", "openai/gpt-6.1-sol-pro"),
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": name, "strict": True, "schema": schema}},
        "reasoning": {"effort": cfg.get("reasoning_effort", "medium")},
        "max_tokens": int(cfg.get("max_tokens", 4000)),
        "usage": {"include": True},
    }
    if cfg.get("web_search", True):
        engine = cfg.get("web_engine", "exa")
        body["plugins"] = [{"id": "web", "engine": engine,
                            "max_results": int(cfg.get("web_max_results", 5))}]
        if engine == "native":                        # model searches itself: thorough, costly
            body["web_search_options"] = {
                "search_context_size": cfg.get("search_context_size", "low")}
    with httpx.Client(timeout=float(cfg.get("timeout_s", 180))) as client:
        r = client.post(API, json=body, headers={"Authorization": f"Bearer {key}",
                                                 "X-Title": "equity-research next-bull watch"})
    if r.status_code != 200:
        raise AiNoteError(f"OpenRouter HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    msg = data["choices"][0]["message"]
    try:
        note = json.loads(msg.get("content") or "{}")
    except ValueError:
        raise AiNoteError("model returned non-JSON output")
    # web plugin citations arrive as annotations; prefer them over model-typed URLs
    cites = [a["url_citation"]["url"] for a in (msg.get("annotations") or [])
             if a.get("type") == "url_citation" and a.get("url_citation", {}).get("url")]
    if cites:
        note["sources"] = list(dict.fromkeys(cites))[:5]
    usage = data.get("usage") or {}
    note["usage"] = {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "cost")
                     if usage.get(k) is not None}
    return note


_INLINE_CITE = re.compile(r"\s*\(\[[^\]]*\]\([^)]*\)\)")      # ([site](url)) — sources are listed apart
_CUT_CITE = re.compile(r"\s*\(\[[^)]*$")                       # citation cut off by an earlier clip


def _text(v, limit: int) -> str:
    t = _CUT_CITE.sub("", _INLINE_CITE.sub("", str(v or ""))).strip()
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return t


def _url(u: str) -> str:
    """Drop tracking params (utm_*) from a source URL."""
    parts = urllib.parse.urlsplit(u)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if not k.startswith("utm_")]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def _clip(note: dict, schema: dict) -> dict:
    """Schema guard: keep only expected fields, strip inline citations, clip at word
    boundaries, coerce enums."""
    out = {}
    for k, spec in schema["properties"].items():
        v = note.get(k)
        if spec["type"] == "array":
            if k == "sources":
                items = [_url(str(x))[:300] for x in (v or []) if str(x).startswith("http")]
            else:
                items = [t for t in (_text(x, 240) for x in (v or [])) if t]
            out[k] = items[:spec.get("maxItems", 5)]
        elif "enum" in spec:
            out[k] = v if v in spec["enum"] else None
        else:
            out[k] = _text(v, 400)
    if note.get("usage"):
        out["usage"] = note["usage"]
    return out


DEEP_CHECKLIST = (
    "\nDEEP CHECK — search for each explicitly and report what you find (or that it is "
    "unverified): NSE/BSE ASM or GSM surveillance stage; promoter pledge % in the latest "
    "shareholding pattern; SEBI/ED/SFIO orders; auditor resignation or qualified opinion; "
    "preferential allotments, QIPs or warrants (to whom); credit-rating actions.")


def _fresh(entry: dict | None, cfg: dict, today: date, deep: bool = False) -> bool:
    """A cached note answers a click if it is recent, from the same model, and at least as
    deep as asked (a deep note also answers a quick click)."""
    return bool(entry and entry.get("note") and entry.get("model") == cfg.get("model")
                and (not deep or entry.get("depth") == "deep")
                and (today - date.fromisoformat(entry["as_of"])).days < int(cfg.get("cache_days", 7)))


def _run_cfg(cfg: dict, deep: bool) -> dict:
    """Quick = bounded Exa search (~$0.03); deep = the model searches itself (~$0.70)."""
    return {**cfg, **(cfg.get("deep") or {})} if deep else cfg


def research(symbol: str, refresh: bool = False, dry_run: bool = False,
             deep: bool = False) -> dict:
    """One click = at most one model call, for one Next-Bull symbol."""
    cfg = _cfg()
    if not cfg.get("enabled", False):
        raise AiNoteError("ai_notes disabled in bear_reversal.yaml")
    watch = _load_watch()
    rows = {r["symbol"]: r for lst in ("signals", "warming") for r in watch.get(lst, [])}
    symbol = symbol.upper()
    if symbol not in rows:
        raise AiNoteError(f"{symbol} is not on today's Next Bull lists — notes are limited "
                          "to those names")
    today = date.fromisoformat(watch["as_of"])
    cache = _load_cache()
    if not refresh and _fresh(cache.get(symbol), cfg, today, deep):
        c = cache[symbol]
        return {**c["note"], "as_of": c["as_of"], "depth": c.get("depth", "quick"),
                "cached": True}
    run = _run_cfg(cfg, deep)
    prompt = _stock_prompt(rows[symbol], watch["regime"], watch["as_of"]) \
        + (DEEP_CHECKLIST if deep else "")
    depth = "deep" if deep else "quick"
    if dry_run:
        return {"dry_run": True, "model": cfg.get("model"), "depth": depth,
                "web_engine": run.get("web_engine"), "prompt": prompt}
    key = _api_key()
    if not key:
        raise AiNoteError("OPENROUTER_API_KEY not set in .env")
    note = _clip(_call(run, key, prompt, NOTE_SCHEMA, "stock_note"), NOTE_SCHEMA)
    with _lock:
        cache = _load_cache()
        cache[symbol] = {"as_of": watch["as_of"], "model": cfg.get("model"), "depth": depth,
                         "note": note}
        _save_cache(cache)
    return {**note, "as_of": watch["as_of"], "depth": depth, "cached": False}


def brief(refresh: bool = False, dry_run: bool = False) -> dict:
    """Market/regime brief — on demand, cached per data date."""
    cfg = _cfg()
    if not cfg.get("enabled", False):
        raise AiNoteError("ai_notes disabled in bear_reversal.yaml")
    watch = _load_watch()
    cache = _load_cache()
    b = cache.get(BRIEF_KEY)
    if not refresh and b and b.get("as_of") == watch["as_of"] and b.get("model") == cfg.get("model"):
        return {**b["note"], "as_of": b["as_of"], "cached": True}
    prompt = _brief_prompt(watch)
    if dry_run:
        return {"dry_run": True, "model": cfg.get("model"), "prompt": prompt}
    key = _api_key()
    if not key:
        raise AiNoteError("OPENROUTER_API_KEY not set in .env")
    note = _clip(_call(cfg, key, prompt, BRIEF_SCHEMA, "market_brief"), BRIEF_SCHEMA)
    with _lock:
        cache = _load_cache()
        cache[BRIEF_KEY] = {"as_of": watch["as_of"], "model": cfg.get("model"), "note": note}
        _save_cache(cache)
    return {**note, "as_of": watch["as_of"], "cached": False}


def attach_cached(watch: dict) -> dict:
    """Merge already-paid-for notes into a watch dict. Makes NO model calls."""
    cfg = _cfg()
    cache = _load_cache()
    for lst in ("signals", "warming", "watch"):
        for r in watch.get(lst, []):
            c = cache.get(r["symbol"])
            if c and c.get("note"):
                r["ai"] = {**c["note"], "as_of": c["as_of"], "depth": c.get("depth", "quick")}
    b = cache.get(BRIEF_KEY)
    if b and b.get("note") and b.get("as_of") == watch.get("as_of"):
        watch["ai_brief"] = {**b["note"], "as_of": b["as_of"]}
    watch["ai_meta"] = ({"model": cfg.get("model"), "on_demand": True,
                         "cache_days": int(cfg.get("cache_days", 7)),
                         "cost_hint": cfg.get("cost_hint"),
                         "deep_cost_hint": (cfg.get("deep") or {}).get("cost_hint"),
                         "policy": "advisory notes only — rule-based lists are unchanged"}
                        if cfg.get("enabled") else None)
    return watch


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    try:
        if "--brief" in sys.argv:
            out = brief(refresh="--refresh" in sys.argv, dry_run=dry)
        elif args:
            out = research(args[0], refresh="--refresh" in sys.argv, dry_run=dry,
                           deep="--deep" in sys.argv)
        else:
            raise SystemExit(__doc__)
    except AiNoteError as e:
        raise SystemExit(f"ai note: {e}")
    print(json.dumps(out, indent=1, ensure_ascii=True))
