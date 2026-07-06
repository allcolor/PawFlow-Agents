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
    def http_fetch_stream(self, *, url, method, headers, body, on_output):
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
    monkeypatch.setattr(relay_proxy, "_resolve_relay_service",
                        lambda user_id, relay_id, conv_id="": _Relay())

    pending = _Pending()
    relay_proxy._relay_proxy_handler(pending)

    assert pending.completed is None
    status, headers, chunks = pending.streamed
    assert status == 200
    assert headers == {"Content-Type": "text/event-stream", "X-Model": "local"}
    assert chunks == [b"data: ok\n\n"]
