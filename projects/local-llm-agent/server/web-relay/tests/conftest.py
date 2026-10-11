"""pytest の fixture。部品は `wr_helpers` にある。"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

import pytest
from wr_helpers import Env, LocalServer, build_env, make_handler


@pytest.fixture(scope="session")
def http_server() -> Iterator[LocalServer]:
    state = LocalServer(port=0)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    state.port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield state
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def env(http_server: LocalServer) -> Env:
    with http_server.lock:
        http_server.requests.clear()
    return build_env(http_server)
