"""An unattended API login must release its callback server when time expires."""
import pytest

from scanner.auth import kite_auth
from scanner.errors import AuthError


def test_callback_timeout_is_total_deadline_and_closes_server(monkeypatch):
    values = iter([100.0, 100.0, 101.0])
    monkeypatch.setattr(kite_auth.time, "monotonic", lambda: next(values))

    class Server:
        def __init__(self):
            self.timeout = None
            self.calls = 0
            self.closed = False

        def handle_request(self):
            self.calls += 1

        def server_close(self):
            self.closed = True

    server = Server()
    monkeypatch.setattr(kite_auth, "HTTPServer", lambda *args: server)
    with pytest.raises(AuthError, match="timed out"):
        kite_auth._wait_for_callback("127.0.0.1", 8765, timeout_s=1)
    assert server.calls == 1
    assert server.closed
