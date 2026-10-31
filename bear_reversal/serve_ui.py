"""Zero-dependency API + static server for the React frontend.

    python bear_reversal/serve_ui.py            # http://localhost:8123

Serves:
  /                      -> frontend/dist (built React app)
  /api/bundle            -> ui_data/bundle.json
  /api/stock/<SYMBOL>    -> on-demand candles + MAs + RS vs benchmarks (from the
                             shared parquet store, read-only)
  /api/sector/<NAME>     -> sector index close series
  POST /api/ai-note/<SYM>[?deep=1] -> on-demand AI research note for a Next Bull name (ai_notes.py;
                             the only routes that spend LLM tokens, one call per click)
  POST /api/ai-brief      -> on-demand AI market/regime brief

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


def stock_payload(symbol: str, sessions: int = 500, asof: str | None = None) -> dict | None:
    dao = _dao()
    token_row = dao.universe[dao.universe["symbol"] == symbol.upper()] if dao.universe is not None else None
    if token_row is None or not len(token_row):
        return None
    df = dao.store.read_candles("stock", int(token_row["token"].iloc[0]))
    if df is None or not len(df):
        return None
    if asof:
        df = df[df["date"] <= pd.Timestamp(asof)]
        if df is None or not len(df):
            return None
    df = df.tail(sessions).reset_index(drop=True)
    c = df["close"]
    c.index = pd.DatetimeIndex(df["date"])          # date-indexed for benchmark joins
    ma20 = c.rolling(20).mean()
    ma50 = c.rolling(50).mean()
    ma200 = c.rolling(200).mean()

    rs = {}
    for label, bench in _bench_series().items():
        a = pd.concat([c.rename("s"), bench.rename("b")], axis=1, join="inner").dropna()
        if len(a) > 30:
            rs[label] = {"dates": [d.strftime("%Y-%m-%d") for d in a.index][-sessions:],
                         "ratio": [round(float(v), 5) for v in (a["s"] / a["b"]).tolist()][-sessions:],
                         "name": str(bench.name or label)}
    rets = {}
    for lbl, n in (("1w", 5), ("1m", 21), ("3m", 63)):
        rets[lbl] = round(float(c.iloc[-1] / c.iloc[-1 - n] - 1.0), 4) if len(c) > n else None
    return {
        "symbol": symbol.upper(),
        "sector": dao.sector_of().get(symbol.upper(), "UNMAPPED"),
        "dates": [d.strftime("%Y-%m-%d") for d in df["date"]],
        "ohlc": {
            "open": [round(float(v), 2) for v in df["open"]],
            "high": [round(float(v), 2) for v in df["high"]],
            "low": [round(float(v), 2) for v in df["low"]],
            "close": [round(float(v), 2) for v in df["close"]],
        },
        "volume": [float(v) for v in df["volume"]],
        "ma20": [None if v != v else round(float(v), 2) for v in ma20],
        "ma50": [None if v != v else round(float(v), 2) for v in ma50],
        "ma200": [None if v != v else round(float(v), 2) for v in ma200],
        "rs": rs,
        "returns": rets,
        "avg_turnover_20d": round(float((c * df["volume"]).tail(20).mean()), 0),
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

    def do_POST(self):  # noqa: N802
        """On-demand AI research: the only routes that spend LLM tokens (one call per click)."""
        import urllib.parse as _up
        parsed = _up.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
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
            payload = stock_payload(m.group(1), asof=asof)
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
