"""Kite Connect v3 manual login flow (official, no credential automation).

Workflow (see https://kite.trade/docs/connect/v3/):
 1. Read API key/secret from .env.
 2. Build the official login URL.
 3. Open it in the user's browser (user authenticates manually).
 4. Kite redirects to KITE_REDIRECT_URL with a `request_token`.
 5. A local HTTP server on that URL captures the token.
 6. Exchanged for an `access_token` via the official SDK.
 7. Stored under data/session/ (gitignored). Sessions expire daily; expiry is
    detected and re-authentication requested.

This module NEVER handles the user's Zerodha password, TOTP or PIN.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException, PermissionException

from ..config import get_settings
from ..errors import AuthError

log = logging.getLogger("scanner.auth")

LOGIN_URL_TEMPLATE = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
# Kite access tokens expire at ~06:00 IST the next calendar day.
SESSION_VALID_HOURS = 20


def build_login_url(api_key: str) -> str:
    return LOGIN_URL_TEMPLATE.format(api_key=api_key)


def _redirect_host_port(url: str) -> tuple[str, int]:
    p = urlparse(url)
    host = p.hostname or "127.0.0.1"
    port = p.port or (443 if p.scheme == "https" else 80)
    return host, port


class _CallbackHandler(BaseHTTPRequestHandler):
    request_token: str | None = None
    error: str | None = None

    def do_GET(self):  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "request_token" in params:
            _CallbackHandler.request_token = params["request_token"][0]
            body = b"<html><body><h2>Kite login captured.</h2>You may close this tab and return to the terminal.</body></html>"
            self.send_response(200)
        else:
            _CallbackHandler.error = params.get("message", ["no request_token"])[0]
            body = b"<html><body><h2>Login callback did not contain a request token.</h2></body></html>"
            self.send_response(400)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence default request logging
        pass


def _wait_for_callback(host: str, port: int, timeout_s: int = 600) -> str:
    _CallbackHandler.request_token = None
    _CallbackHandler.error = None
    server = HTTPServer((host, port), _CallbackHandler)
    server.timeout = timeout_s
    log.info("Waiting up to %ss for Kite redirect on http://%s:%s ...", timeout_s, host, port)
    try:
        while _CallbackHandler.request_token is None and _CallbackHandler.error is None:
            server.handle_request()
    finally:
        server.server_close()
    if _CallbackHandler.request_token:
        return _CallbackHandler.request_token
    raise AuthError(f"Kite redirect failed: {_CallbackHandler.error or 'timed out waiting for user login'}")


def login(timeout_s: int = 600, open_browser: bool = True) -> dict:
    """Run the interactive login flow. Returns session info dict."""
    s = get_settings()
    s.ensure_dirs()
    if not s.kite_api_key or not s.kite_api_secret:
        raise AuthError("KITE_API_KEY / KITE_API_SECRET missing in .env — copy .env.example and fill them in.")

    url = build_login_url(s.kite_api_key)
    host, port = _redirect_host_port(s.kite_redirect_url)
    print("\n" + "=" * 78)
    print("KITE CONNECT MANUAL LOGIN")
    print("1. A browser window will open (or copy the URL below into a browser).")
    print("2. Log in with YOUR Zerodha credentials and complete 2FA.")
    print("3. The app will capture the redirect automatically.")
    print(f"\nLogin URL:\n{url}\n")
    print("=" * 78 + "\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            log.warning("Could not open a browser automatically; open the URL manually.")

    request_token = _wait_for_callback(host, port, timeout_s)
    kite = KiteConnect(api_key=s.kite_api_key)
    session = kite.generate_session(request_token, api_secret=s.kite_api_secret)
    access_token = session["access_token"]

    payload = {
        "access_token": access_token,
        "api_key": s.kite_api_key,
        "user_id": session.get("user_id"),
        "obtained_at_utc": datetime.now(timezone.utc).isoformat(),
        "expires_hint_utc": (datetime.now(timezone.utc) + timedelta(hours=SESSION_VALID_HOURS)).isoformat(),
    }
    s.session_file.parent.mkdir(parents=True, exist_ok=True)
    s.session_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("Kite session stored for user %s (valid until ~06:00 IST tomorrow).", session.get("user_id"))
    return payload


def get_session_info() -> dict | None:
    s = get_settings()
    if not s.session_file.exists():
        return None
    try:
        return json.loads(s.session_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_kite() -> KiteConnect:
    """Return a KiteConnect client with a valid access token, or raise AuthError."""
    info = get_session_info()
    if not info or not info.get("access_token"):
        raise AuthError(
            "No Kite session found. Run `python -m scanner auth login` "
            "(manual browser login; the app never sees your password/TOTP)."
        )
    kite = KiteConnect(api_key=info["api_key"])
    kite.set_access_token(info["access_token"])
    return kite


def status() -> dict:
    """Check session validity with a live profile() call."""
    info = get_session_info()
    if not info:
        return {"authenticated": False, "detail": "No stored session. Run `python -m scanner auth login`."}
    try:
        kite = get_kite()
        profile = kite.profile()
        entitlement = _check_historical_entitlement(kite)
        return {
            "authenticated": True,
            "user_id": profile.get("user_id"),
            "user_name": profile.get("user_name"),
            "obtained_at_utc": info.get("obtained_at_utc"),
            "historical_data_api": entitlement,
        }
    except TokenException as e:
        return {
            "authenticated": False,
            "detail": f"Session expired or invalid ({e}). Run `python -m scanner auth login` again.",
        }
    except PermissionException as e:
        return {"authenticated": False, "detail": f"API permission error: {e}"}
    except Exception as e:  # network etc.
        return {"authenticated": None, "detail": f"Could not verify session: {type(e).__name__}: {e}"}


def _check_historical_entitlement(kite: KiteConnect) -> dict:
    """Historical market-data API is a paid add-on; verify with one real request."""
    try:
        data = kite.historical_data(264969, "2026-01-01", "2026-01-10", "day")  # NIFTY 500
        return {"enabled": True, "detail": f"probe returned {len(data)} daily candles for NIFTY 500"}
    except PermissionException as e:
        return {"enabled": False,
                "detail": (f"Account lacks historical-data API access ({e}). "
                           "Enable the 'Historical market data' add-on in the Kite developer console "
                           "(https://developers.kite.trade) to download candles.")}
    except Exception as e:
        return {"enabled": None, "detail": f"Entitlement probe failed: {type(e).__name__}: {e}"}


def logout() -> dict:
    s = get_settings()
    info = get_session_info()
    result = {"logged_out": False, "detail": ""}
    if info and info.get("access_token"):
        try:
            kite = KiteConnect(api_key=info["api_key"])
            kite.set_access_token(info["access_token"])
            kite.invalidate_access_token(info["access_token"])
            result = {"logged_out": True, "detail": "Access token invalidated at Kite."}
        except Exception as e:
            log.warning("Server-side invalidate failed (%s); removing local session anyway.", e)
            result = {"logged_out": True, "detail": f"Local session removed (server invalidate failed: {e})."}
    if s.session_file.exists():
        s.session_file.unlink()
    return result
