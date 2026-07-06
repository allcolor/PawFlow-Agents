from tasks.io import relay_proxy


class _Pending:
    method = "POST"
    path = "/relay-proxy/relay1/tok/localhost:11434/v1/chat/completions"
    remote_addr = "127.0.0.1"
    query_string = ""
    body = b"{}"
    headers = {"Content-Type": "application/json"}
    path_params = {
        "relay_id": "relay1",
        "token": "tok",
        "rest": "localhost:11434/v1/chat/completions",
    }

    def __init__(self):
        self.completed = None
        self.streamed = None

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)

    def complete_stream(self, status, headers, stream):
        self.streamed = (status, headers, list(stream))


class _Relay:
    calls = []

    def http_fetch_stream(self, *, url, method, headers, body, local, on_output):
        self.calls.append({"url": url, "local": local})
        assert url == "http://localhost:11434/v1/chat/completions"
        assert method == "POST"
        on_output("start", {
            "status": 200,
            "headers": {
                "Content-Type": "text/event-stream",
                "Content-Length": "999",
                "Transfer-Encoding": "chunked",
                "Connection": "keep-alive",
                "X-Model": "local",
            },
        })
        on_output("chunk", b"data: ok\n\n")
        on_output("end", {})


def test_relay_proxy_stream_drops_backend_hop_by_hop_headers(monkeypatch):
    monkeypatch.setattr("core.relay_proxy_auth.lookup_token",
                        lambda token: ("alice", "relay1", "conv1"))
    monkeypatch.setattr("core.relay_proxy_auth.is_private_ip", lambda ip: True)
    relay = _Relay()
    relay.calls = []
    monkeypatch.setattr(relay_proxy, "_resolve_relay_service",
                        lambda user_id, relay_id, conv_id="": relay)

    pending = _Pending()
    relay_proxy._relay_proxy_handler(pending)

    assert pending.completed is None
    status, headers, chunks = pending.streamed
    assert status == 200
    assert headers == {"Content-Type": "text/event-stream", "X-Model": "local"}
    assert chunks == [b"data: ok\n\n"]
    assert relay.calls == [{
        "url": "http://localhost:11434/v1/chat/completions",
        "local": True,
    }]


def test_relay_proxy_container_prefix_disables_local_fetch(monkeypatch):
    monkeypatch.setattr("core.relay_proxy_auth.lookup_token",
                        lambda token: ("alice", "relay1", "conv1"))
    monkeypatch.setattr("core.relay_proxy_auth.is_private_ip", lambda ip: True)
    relay = _Relay()
    relay.calls = []
    monkeypatch.setattr(relay_proxy, "_resolve_relay_service",
                        lambda user_id, relay_id, conv_id="": relay)

    pending = _Pending()
    pending.path_params = dict(pending.path_params)
    pending.path_params["rest"] = "c/localhost:11434/v1/chat/completions"
    relay_proxy._relay_proxy_handler(pending)

    assert relay.calls == [{
        "url": "http://localhost:11434/v1/chat/completions",
        "local": False,
    }]


def test_relay_proxy_rejects_public_source_ip_even_with_valid_token(monkeypatch):
    monkeypatch.setattr("core.relay_proxy_auth.lookup_token",
                        lambda token: ("alice", "relay1", "conv1"))
    relay = _Relay()
    relay.calls = []
    monkeypatch.setattr(relay_proxy, "_resolve_relay_service",
                        lambda user_id, relay_id, conv_id="": relay)

    pending = _Pending()
    pending.remote_addr = "203.0.113.10"
    relay_proxy._relay_proxy_handler(pending)

    assert pending.completed[0] == 403
    assert pending.streamed is None
    assert relay.calls == []


def test_relay_proxy_route_is_gateway_exempt_and_private_only():
    class _Http:
        def __init__(self):
            self.routes = []

        def get_routes(self):
            return []

        def register_route(self, method, pattern, owner, **kwargs):
            self.routes.append((method, pattern, owner, kwargs))

    http = _Http()
    relay_proxy._register_routes(http)

    assert http.routes
    for _method, pattern, owner, kwargs in http.routes:
        assert pattern == "/relay-proxy/{relay_id}/{token}/{rest+}"
        assert owner == "_relay_proxy"
        assert kwargs["public"] is True
        assert kwargs["private_only"] is True
        assert kwargs["gateway_exempt"] is True


def test_relay_proxy_route_registration_updates_existing_owner_routes():
    class _Http:
        def __init__(self):
            self.routes = []

        def get_routes(self):
            return [{
                "method": "POST",
                "pattern": "/relay-proxy/{relay_id}/{token}/{rest+}",
                "owner": "_relay_proxy",
                "private_only": False,
            }]

        def register_route(self, method, pattern, owner, **kwargs):
            self.routes.append((method, pattern, owner, kwargs))

    http = _Http()
    relay_proxy._register_routes(http)

    assert http.routes
    assert all(route[3]["private_only"] is True for route in http.routes)
