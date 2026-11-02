import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from service.publishing import browser_ops, url_safety


def test_dns_timeout_prevents_late_connection(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    connected = []

    def slow_resolve(host):
        entered.set()
        release.wait(2)
        return (url_safety.ipaddress.ip_address('8.8.8.8'),)

    monkeypatch.setattr(url_safety, 'resolve_public_ips', slow_resolve)
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **kw: connected.append(a))
    start = time.monotonic()
    try:
        with pytest.raises(url_safety.URLSafetyError, match='超时'):
            url_safety.fetch_pinned('http://example.test/', timeout=0.1)
        assert entered.is_set()
        assert time.monotonic() - start < 0.8
    finally:
        release.set()
    time.sleep(0.1)
    assert connected == []


def test_slow_headers_obey_total_deadline(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            for part in [b'HTTP/1.1 200 OK\r\n', b'X-Test: ', b'one', b'two', b'\r\n\r\n']:
                try:
                    self.wfile.write(part)
                    self.wfile.flush()
                    time.sleep(0.12)
                except OSError:
                    break

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    monkeypatch.setattr(url_safety, 'resolve_public_ips', lambda host: (url_safety.ipaddress.ip_address('127.0.0.1'),))
    start = time.monotonic()
    try:
        with pytest.raises((url_safety.URLSafetyError, TimeoutError)):
            url_safety.fetch_pinned(f'http://example.test:{server.server_port}/', timeout=0.25)
        assert time.monotonic() - start < 0.5
    finally:
        thread.join(2)
        server.server_close()


@pytest.mark.parametrize('operation', [browser_ops.browser_fetch, browser_ops.browser_screenshot])
def test_browser_operations_reject_non_public_before_launch(operation, monkeypatch):
    """浏览器操作必须在启动浏览器前拒绝非公网目标，而不是交给浏览器再判断。"""
    launched = []
    monkeypatch.setattr(browser_ops, 'resolve_public_ips', lambda host: launched.append(host))
    monkeypatch.setattr(
        browser_ops, '_validate_url',
        lambda url: (_ for _ in ()).throw(url_safety.URLSafetyError('url 主机 解析到非公网地址')))

    def _no_launch(*args, **kwargs):   # pragma: no cover - 失败路径不应触达
        raise AssertionError('浏览器不应在校验失败后被启动')

    monkeypatch.setattr('service.publishing.browser.BuiltinBrowser', _no_launch)

    with pytest.raises((ValueError, RuntimeError), match='非公网|安全'):
        operation(None, 'synthetic', 'http://rebinding.test/')
    assert launched == []


def test_pinned_resolver_rules_embed_validated_ip(monkeypatch):
    """钉扎参数必须包含所有已校验公网地址，且只产生一个 Chromium 参数。"""
    monkeypatch.setattr(
        browser_ops, 'resolve_public_ips',
        lambda host: (url_safety.ipaddress.ip_address('93.184.216.34'),
                      url_safety.ipaddress.ip_address('2606:2800:220:1:248:1893:25c8:1945')))

    rules = browser_ops._pinned_resolver_rules('https://example.test/page')

    assert len(rules) == 1
    assert rules[0].startswith('--host-resolver-rules=MAP example.test ')
    assert '93.184.216.34' in rules[0]
    assert '2606:2800:220:1:248:1893:25c8:1945' in rules[0]


def test_request_guard_blocks_private_hosts(monkeypatch):
    """子资源守卫必须拦下解析到内网的跨域请求。"""
    def resolve(host):
        if host in {'example.test', 'cdn.example.test'}:
            return (url_safety.ipaddress.ip_address('93.184.216.34'),)
        raise url_safety.URLSafetyError(f'url 主机 {host} 解析到非公网地址')

    monkeypatch.setattr(browser_ops, 'resolve_public_ips', resolve)
    continued, aborted = [], []

    class Route:
        def __init__(self, url):
            self.request = type('Req', (), {'url': url})()

        def continue_(self):
            continued.append(self.request.url)

        def abort(self, reason):
            aborted.append((self.request.url, reason))

    class Page:
        def __init__(self):
            self.handler = None

        def route(self, pattern, handler):
            self.handler = handler

    page = Page()
    browser_ops._guard_requests(page, 'example.test')
    page.handler(Route('https://example.test/app.js'))
    page.handler(Route('https://cdn.example.test/lib.js'))
    page.handler(Route('http://169.254.169.254/latest/meta-data/'))
    page.handler(Route('data:text/plain,hi'))

    assert continued == ['https://example.test/app.js', 'https://cdn.example.test/lib.js',
                         'data:text/plain,hi']
    assert aborted == [('http://169.254.169.254/latest/meta-data/', 'blockedbyclient')]
