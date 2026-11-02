import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from service.publishing import url_safety


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/a",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://[::1]/",
    "http://169.254.169.254/latest/meta-data",
    "http://100.100.100.200/latest/meta-data/",
    "http://[::ffff:100.100.100.200]/latest/meta-data/",
    "file:///etc/passwd",
    "ftp://example.com/x",
])
def test_private_and_unsupported_targets_rejected(url):
    with pytest.raises(url_safety.URLSafetyError):
        url_safety.validate_url(url)


def test_public_host_accepted():
    assert url_safety.validate_url("https://example.com/") == "https://example.com/"


def test_fetch_pinned_connects_to_validated_ip(monkeypatch):
    """DNS 返回公网地址后，实际连接必须使用同一个已校验 IP。"""
    connected = []

    def fake_socket_create_connection(address, timeout=None, source_address=None):
        connected.append(address)
        raise ConnectionRefusedError("stop after address capture")

    monkeypatch.setattr(socket, "create_connection", fake_socket_create_connection)
    monkeypatch.setattr(url_safety, "resolve_public_ips",
                        lambda host: (url_safety.ipaddress.ip_address("93.184.216.34"),))

    with pytest.raises(ConnectionRefusedError):
        url_safety.fetch_pinned("http://example.com/", timeout=5)
    assert connected == [("93.184.216.34", 80)]


def test_slow_drip_response_respects_total_deadline(monkeypatch):
    """慢速响应必须按总超时中断，而不是被逐块读无限延长。"""
    import time

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            for _ in range(100):
                try:
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.2)
                except OSError:
                    return

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.handle_request, daemon=True).start()
    monkeypatch.setattr(url_safety, "resolve_public_ips",
                        lambda host: (url_safety.ipaddress.ip_address("127.0.0.1"),))

    started = time.monotonic()
    with pytest.raises(url_safety.URLSafetyError, match="超时"):
        url_safety.fetch_pinned(f"http://example.com:{port}/", timeout=1)
    assert time.monotonic() - started < 3
    server.server_close()


def test_redirect_to_private_address_rejected(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1/secret")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.handle_request, daemon=True).start()

    def fake_resolve(host):
        if host == "example.com":
            return (url_safety.ipaddress.ip_address("127.0.0.1"),)
        raise url_safety.URLSafetyError(f"private target rejected: {host}")

    monkeypatch.setattr(url_safety, "resolve_public_ips", fake_resolve)

    with pytest.raises(url_safety.URLSafetyError):
        url_safety.fetch_pinned(f"http://example.com:{port}/", timeout=5)
    server.server_close()
