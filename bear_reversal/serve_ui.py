"""Zero-dependency API + static server for the React frontend.

    python bear_reversal/serve_ui.py            # http://localhost:8123

Serves:
  /                      -> frontend/dist (built React app)
  /api/bundle            -> ui_data/bundle.json
  /api/stock/<SYMBOL>[?tf=W&asof=] -> candles (daily or weekly, since 2014, any NSE stock) +
                             MAs + RS vs benchmarks (full-market panel; Kite store fallback; from the
                             shared parquet store, read-only)
  /api/sector/<NAME>     -> sector index close series
  POST /api/ai-note/<SYM>[?deep=1] -> on-demand AI research note for a Next Bull name (ai_notes.py;
                             the only routes that spend LLM tokens, one call per click)
  POST /api/ai-brief      -> on-demand AI market/regime brief
  /api/dossier/<SYM>      -> research dossier for a Next Bull name: signal facts + research
                             checklist + Markdown (research_dossier.py; no LLM)
  /api/ledger             -> signal ledger: episodes + your decisions (signal_ledger.py)
  POST /api/ledger/<ID>   -> save your decision on one episode (JSON body: status, note,
                             buy_date, buy_price, exit_date, exit_price)
  POST /api/ledger-settings -> {"slots": N}

Dev mode: `npm run dev` in frontend/ proxies /api to this server (vite.config.js).
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

DIST = HERE.parent / "frontend" / "dist"
UI_DATA = HERE / "ui_data"

MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json",
        ".ico": "image/x-icon", ".woff2": "font/woff2"}

_cache: dict = {}


def _dao():
    if "dao" not in _cache:
        from data_access import DataAccess
        _cache["dao"] = DataAccess()
    return _cache["dao"]


def _bench_series() -> dict[str, pd.Series]:
    if "bench" not in _cache:
        dao = _dao()
        out = {}
        for role, label in (("broad", "n500"), ("headline", "n50"),
                            ("midcap", "midcap"), ("smallcap", "smallcap")):
            try:
                s = dao.bench_close(role)
            except Exception:
                s = None
            if s is None:
                from scanner.data.instruments import resolve_index
                name = dao.cfg["run"]["benchmarks"].get(role)
                meta = resolve_index(dao.instruments, name) if name else None
                if meta:
                    df = dao.store.read_candles("index", meta["token"])
                    if df is not None and len(df):
                        s = df.set_index("date")["close"]
            if s is not None and len(s):
                out[label] = s
        _cache["bench"] = out
    return _cache["bench"]


def _panel_candles(symbol: str) -> pd.DataFrame | None:
    """Daily split/bonus-adjusted OHLC for any NSE stock since 2014 (incl. delisted), read
    column-wise from the bagger panel cache (bagger_data.py). volume = traded value / close."""
    from bagger_data import CACHE
    kp = CACHE / "keys.parquet"
    if not kp.exists():
        return None
    stamp = kp.stat().st_mtime
    if _cache.get("keys_stamp") != stamp:
        k = pd.read_parquet(kp)
        _cache["keys"] = dict(zip(k["symbol"].astype(str).str.upper(), k["key"]))
        _cache["keys_stamp"] = stamp
    key = _cache["keys"].get(symbol.upper())
    if key is None:
        return None
    cols = {f: pd.read_parquet(CACHE / f"{f}.parquet", columns=[key])[key]
            for f in ("open", "high", "low", "close", "turnover")}
    df = pd.DataFrame(cols).dropna(subset=["close"])
    for f in ("open", "high", "low"):
        df[f] = df[f].fillna(df["close"])
    df["volume"] = (df["turnover"] / df["close"]).fillna(0)
    return df.rename_axis("date").reset_index()


def _kite_candles(symbol: str) -> pd.DataFrame | None:
    dao = _dao()
    row = dao.universe[dao.universe["symbol"] == symbol.upper()] if dao.universe is not None else None
    if row is None or not len(row):
        return None
    return dao.store.read_candles("stock", int(row["token"].iloc[0]))


WEEKLY = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
MA_WINDOWS = {"D": (20, 50, 200), "W": (10, 30, 40)}     # 40 weeks ~ 200 sessions


def stock_payload(symbol: str, asof: str | None = None, tf: str = "D") -> dict | None:
    """Candles + MAs (daily, or weekly bars ending Friday) for the whole history since 2014,
    plus RS vs benchmarks. The full-market panel first; the Kite store as a fallback."""
    tf = "W" if str(tf).upper().startswith("W") else "D"
    df, source = _panel_candles(symbol), "full-market panel (split-adjusted)"
    if df is None or not len(df):
        df, source = _kite_candles(symbol), "kite store"
    if df is None or not len(df):
        return None
    df = df.assign(date=pd.to_datetime(df["date"]))
    if asof:
        df = df[df["date"] <= pd.Timestamp(asof)]
        if not len(df):
            return None
    df = df.reset_index(drop=True)
    c = df["close"]
    c.index = pd.DatetimeIndex(df["date"])          # date-indexed for benchmark joins

    bars = df.set_index("date")[list(WEEKLY)]
    if tf == "W":
        bars = bars.resample("W-FRI").agg(WEEKLY).dropna(subset=["close"])
        bars.index = [d.date() if d <= c.index[-1] else c.index[-1].date() for d in bars.index]
        bars.index = pd.DatetimeIndex(bars.index)   # last bar dated at the last session
    mas = {f"ma{i}": bars["close"].rolling(n).mean()
           for i, n in zip((20, 50, 200), MA_WINDOWS[tf])}

    rs = {}
    for label, bench in _bench_series().items():
        a = pd.concat([c.rename("s"), bench.rename("b")], axis=1, join="inner").dropna().tail(500)
        if len(a) > 30:
            rs[label] = {"dates": [d.strftime("%Y-%m-%d") for d in a.index],
                         "ratio": [round(float(v), 5) for v in (a["s"] / a["b"]).tolist()],
                         "name": str(bench.name or label)}
    rets = {}
    for lbl, n in (("1w", 5), ("1m", 21), ("3m", 63), ("1y", 252)):
        rets[lbl] = round(float(c.iloc[-1] / c.iloc[-1 - n] - 1.0), 4) if len(c) > n else None
    rnd = lambda col: [None if v != v else round(float(v), 2) for v in col]
    return {
        "symbol": symbol.upper(),
        "sector": _dao().sector_of().get(symbol.upper(), "UNMAPPED"),
        "tf": tf, "source": source,
        "first_date": df["date"].iloc[0].strftime("%Y-%m-%d"),
        "dates": [d.strftime("%Y-%m-%d") for d in bars.index],
        "ohlc": {k: rnd(bars[k]) for k in ("open", "high", "low", "close")},
        "volume": [float(v) for v in bars["volume"].fillna(0)],
        **{k: rnd(v) for k, v in mas.items()},
        "ma_labels": [f"MA{n}" if tf == "D" else f"{n}W" for n in MA_WINDOWS[tf]],
        "rs": rs,
        "returns": rets,
        "last_close": round(float(c.iloc[-1]), 2),
        "avg_turnover_20d": round(float((c * df["volume"].to_numpy()).tail(20).mean()), 0),
    }


def _replay_matrix() -> dict:
    """Sectors x weeks matrix (built once, cached) for the rotation timeline."""
    if "matrix" in _cache:
        return _cache["matrix"]
    import json as _json
    rdir = UI_DATA / "replay"
    weeks = []
    if (rdir / "index.json").exists():
        idx = _json.loads((rdir / "index.json").read_text(encoding="utf-8"))
        weeks = [w["date"] for w in idx.get("weeks", [])]
    sectors: list[str] = []
    grid: dict[str, dict[str, dict]] = {}
    for d in weeks:
        f = rdir / f"{d}.json"
        if not f.exists():
            continue
        w = _json.loads(f.read_text(encoding="utf-8"))
        for s in w.get("sectors", []):
            name = s["sector"]
            if name not in grid:
                sectors.append(name)
                grid[name] = {}
            grid[name][d] = {"excess_1m": s.get("excess_1m"), "ret_1m": s.get("ret_1m"),
                             "rs_rank": s.get("rs_rank"),
                             "rotation": s.get("rotation_status")}
    _cache["matrix"] = {"dates": weeks, "sectors": sectors, "grid": grid}
    return _cache["matrix"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        from ui_export import _clean
        try:
            body = json.dumps(_clean(obj), allow_nan=False, default=str).encode()
        except ValueError:
            body = json.dumps({"error": "serialization"}).encode()
            code = 500
        self._send(code, body, "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 <= n <= 64_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_POST(self):  # noqa: N802
        """Ledger decisions, and on-demand AI research (the only routes that spend LLM tokens,
        one call per click)."""
        import urllib.parse as _up
        parsed = _up.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        ml = re.match(r"^/api/ledger/([A-Za-z0-9&\-_.]{1,20}:\d{4}-\d{2}-\d{2})$", path)
        if ml or path == "/api/ledger-settings":
            import signal_ledger
            try:
                body = self._body()
                if not isinstance(body, dict):
                    raise ValueError("JSON object expected")
                self._json(signal_ledger.set_entry(ml.group(1), body) if ml
                           else signal_ledger.set_settings(body))
            except (KeyError, FileNotFoundError) as e:
                self._json({"error": str(e).strip("'\"")}, 404)
            except (ValueError, TypeError) as e:
                self._json({"error": str(e)}, 400)
            return
        q = _up.parse_qs(parsed.query)
        refresh = q.get("refresh", ["0"])[0] == "1"
        deep = q.get("deep", ["0"])[0] == "1"
        m = re.match(r"^/api/ai-note/([A-Za-z0-9&\-_.]{1,20})$", path)
        if m or path == "/api/ai-brief":
            from ai_notes import AiNoteError, brief, research
            try:
                out = research(m.group(1), refresh=refresh, deep=deep) if m \
                    else brief(refresh=refresh)
                self._json(out)
            except AiNoteError as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {str(e)[:200]}"}, 502)
            return
        self._json({"error": "not found"}, 404)

    def do_GET(self):  # noqa: N802
        import urllib.parse as _up
        parsed = _up.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        qs = _up.parse_qs(parsed.query)
        if path == "/api/bundle":
            p = UI_DATA / "bundle.json"
            if p.exists():
                self._send(200, p.read_bytes(), "application/json")
            else:
                self._json({"error": "bundle missing — run python bear_reversal/ui_export.py"}, 404)
            return
        m = re.match(r"^/api/replay/?$", path)
        if m:
            p = UI_DATA / "replay" / "index.json"
            self._send(200, p.read_bytes(), "application/json") if p.exists() \
                else self._json({"error": "no replay — run python bear_reversal/replay.py"}, 404)
            return
        m = re.match(r"^/api/next-bull-watch$", path)
        if m:
            p = UI_DATA / "next_bull_watch.json"
            if p.exists():
                watch = json.loads(p.read_text(encoding="utf-8"))
                try:                                  # cached AI notes only — never a model call
                    from ai_notes import attach_cached
                    attach_cached(watch)
                except Exception:
                    pass
                self._json(watch)
            else:
                self._json({"error": "no watch — produced by run_daily.py"}, 404)
            return
        if path == "/api/ledger":                     # signal ledger — no LLM, no cost
            import signal_ledger
            try:
                self._json(signal_ledger.view())
            except FileNotFoundError as e:
                self._json({"error": str(e)}, 404)
            return
        m = re.match(r"^/api/dossier/([A-Za-z0-9&\-_.]{1,20})$", path)
        if m:                                         # research dossier — no LLM, no cost
            p = UI_DATA / "next_bull_watch.json"
            if not p.exists():
                self._json({"error": "no watch — produced by run_daily.py"}, 404)
                return
            from research_dossier import build
            try:
                self._json(build(json.loads(p.read_text(encoding="utf-8")), m.group(1)))
            except KeyError as e:
                self._json({"error": str(e).strip("'\"")}, 404)
            return
        m = re.match(r"^/api/mover-structure$", path)
        if m:
            p = UI_DATA / "mover_structure.json"
            if p.exists():
                self._send(200, p.read_bytes(), "application/json")
            else:
                self._json({"error": "no structure study — run mover_structure.py"}, 404)
            return
        m = re.match(r"^/api/movers-study$", path)
        if m:
            lane = qs.get("lane", ["A"])[0]
            fname = "movers_study_laneB.json" if lane.upper() == "B" else "movers_study.json"
            p = UI_DATA / fname
            if p.exists():
                self._send(200, p.read_bytes(), "application/json")
            else:
                self._json({"error": f"no lane-{lane} study — run big_movers.py" + (" --lane-b" if lane.upper() == "B" else "")}, 404)
            return
        m = re.match(r"^/api/replay/matrix$", path)
        if m:
            self._json(_replay_matrix())
            return
        m = re.match(r"^/api/replay/(\d{4}-\d{2}-\d{2})$", path)
        if m:
            p = UI_DATA / "replay" / f"{m.group(1)}.json"
            if p.exists():
                self._send(200, p.read_bytes(), "application/json")
            else:
                self._json({"error": "unknown replay week"}, 404)
            return
        m = re.match(r"^/api/stock/([A-Za-z0-9&\-.+]+)$", path)
        if m:
            asof = qs.get("asof", [None])[0]
            payload = stock_payload(m.group(1), asof=asof, tf=qs.get("tf", ["D"])[0])
            self._json(payload if payload else {"error": "unknown symbol"}, 200 if payload else 404)
            return
        m = re.match(r"^/api/sector/(.+)$", path)
        if m:
            closes = _dao().sector_index_closes()
            name = m.group(1)
            if name in closes:
                s = closes[name]
                self._json({"name": name,
                            "dates": [d.strftime("%Y-%m-%d") for d in s.index],
                            "close": [round(float(v), 2) for v in s.tolist()]})
            else:
                self._json({"error": "unknown sector"}, 404)
            return
        if path == "/api/health":
            self._json({"ok": True})
            return
        # static frontend
        if path == "/":
            path = "/index.html"
        f = (DIST / path.lstrip("/")).resolve()
        if str(f).startswith(str(DIST.resolve())) and f.exists() and f.is_file():
            self._send(200, f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))
        else:
            index = DIST / "index.html"
            if index.exists() and not path.startswith("/api"):
                self._send(200, index.read_bytes(), "text/html")
            else:
                self._send(404, b"not found", "text/plain")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    print(f"Bear-Reversal UI: http://localhost:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
