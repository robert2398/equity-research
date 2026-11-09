"""Constituent CSV host fallback and Kite series-suffix symbol mapping."""
import httpx
import pandas as pd

from scanner.data import constituents as C


def _instr(rows):
    return pd.DataFrame([{"instrument_token": t, "tradingsymbol": s, "name": s,
                          "segment": "NSE", "instrument_type": "EQ"} for s, t in rows])


def test_suffixed_listing_maps_to_bare_symbol():
    m = C.instruments_equities_map(_instr([("RELIANCE", 1), ("HFCL-BE", 2), ("EMBASSY-RR", 3)]))
    m = m.set_index("symbol")["token"]
    assert m["HFCL"] == 2 and m["EMBASSY"] == 3 and m["RELIANCE"] == 1
    assert m["HFCL-BE"] == 2  # original row kept


def test_plain_eq_listing_wins_over_suffixed():
    m = C.instruments_equities_map(_instr([("ABC", 1), ("ABC-BE", 2), ("XYZ-RR", 3), ("XYZ-BE", 4)]))
    m = m.set_index("symbol")["token"]
    assert m["ABC"] == 1
    assert m["XYZ"] == 4  # -BE preferred over -RR by SERIES_SUFFIXES order
    assert not m.index.duplicated().any()


def test_fetch_falls_back_to_niftyindices(monkeypatch):
    csv = "Company Name,Industry,Symbol,Series,ISIN Code\nFoo Ltd,Power,FOO,EQ,INE000000001\n"

    def handler(req: httpx.Request):
        if "archives.nseindia.com" in str(req.url):
            return httpx.Response(404)
        return httpx.Response(200, text=csv)

    real = httpx.Client
    monkeypatch.setattr(C.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    df = C._fetch_csv("ind_niftypower_list")
    assert df is not None and df["Symbol"].tolist() == ["FOO"]


def test_fetch_rejects_html_200(monkeypatch):
    real = httpx.Client
    monkeypatch.setattr(C.httpx, "Client", lambda **kw: real(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>nope</html>")), **kw))
    assert C._fetch_csv("ind_bogus_list") is None
