"""Android 模拟器网络出口：假 DNS 与本地 HTTP 代理。"""
from __future__ import annotations

import base64
import ipaddress
import socket
import struct
import threading

import pytest

from account_manager import android_gateway as gw
from account_manager import socks_relay


# ---------------------------------------------------------------- 假地址池


def test_pool_assigns_stable_case_insensitive_addresses():
    pool = gw.FakeIpPool()
    a = pool.ip_for("Claude.AI.")
    assert a == pool.ip_for("claude.ai")
    assert ipaddress.IPv4Address(a) in gw.FAKE_NET
    b = pool.ip_for("api.anthropic.com")
    assert b != a
    assert pool.name_for(a) == "claude.ai"
    assert pool.name_for(b) == "api.anthropic.com"
    assert pool.owns(a) and not pool.owns("1.1.1.1")
    assert pool.name_for("198.19.9.9") is None


def test_pool_recycles_least_recently_used_when_full():
    pool = gw.FakeIpPool(ipaddress.ip_network("198.19.0.0/30"))  # 只有 2 个可用地址
    a = pool.ip_for("a.example")
    b = pool.ip_for("b.example")
    pool.name_for(a)  # a 刚用过，回收最久未用的 b
    c = pool.ip_for("c.example")
    assert c == b
    assert pool.name_for(b) == "c.example"
    assert pool.name_for(a) == "a.example"


# ---------------------------------------------------------------- 假 DNS 报文


def _query(name: str, qtype: int = 1, qid: int = 0x1234, flags: int = 0x0100) -> bytes:
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".") if p)
    return struct.pack("!HHHHHH", qid, flags, 1, 0, 0, 0) + labels + b"\x00" + struct.pack(
        "!HH", qtype, 1)


def _parse(reply: bytes):
    qid, flags, qd, an, _, _ = struct.unpack("!HHHHHH", reply[:12])
    return qid, flags, qd, an


def test_dns_a_query_returns_fake_address():
    pool = gw.FakeIpPool()
    reply = gw.build_dns_response(_query("claude.ai"), pool)
    qid, flags, qd, an = _parse(reply)
    assert qid == 0x1234
    assert flags & 0x8000  # QR
    assert flags & 0x0100  # 保留 RD
    assert flags & 0x000F == gw.RCODE_OK
    assert (qd, an) == (1, 1)
    assert socket.inet_ntoa(reply[-4:]) == pool.ip_for("claude.ai")


def test_dns_other_types_get_empty_answer():
    pool = gw.FakeIpPool()
    for qtype in (28, 65, 16):  # AAAA / HTTPS / TXT
        _, flags, qd, an = _parse(gw.build_dns_response(_query("claude.ai", qtype), pool))
        assert flags & 0x000F == gw.RCODE_OK
        assert (qd, an) == (1, 0)


def test_dns_reverse_and_single_label_names_are_nxdomain():
    pool = gw.FakeIpPool()
    for name in ("4.3.2.1.in-addr.arpa", "localhost", "printer.local"):
        _, flags, _, an = _parse(gw.build_dns_response(_query(name), pool))
        assert flags & 0x000F == gw.RCODE_NXDOMAIN
        assert an == 0


def test_dns_malformed_and_unsupported_opcode():
    pool = gw.FakeIpPool()
    assert gw.build_dns_response(b"\x00", pool) is None
    _, flags, _, _ = _parse(gw.build_dns_response(b"\x12\x34\x01\x00\x00\x00", pool))
    assert flags & 0x000F == gw.RCODE_FORMERR
    _, flags, _, _ = _parse(gw.build_dns_response(_query("claude.ai", flags=0x2100), pool))
    assert flags & 0x000F == gw.RCODE_NOTIMP


def test_fake_dns_server_answers_over_udp():
    pool = gw.FakeIpPool()
    server = gw.FakeDnsServer(pool, host="127.0.0.1", port=0).start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        s.sendto(_query("example.com"), ("127.0.0.1", server.port))
        reply = s.recv(512)
        s.close()
    finally:
        server.stop()
    assert socket.inet_ntoa(reply[-4:]) == pool.ip_for("example.com")


def test_fake_dns_bind_failure_is_reported():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    try:
        with pytest.raises(gw.GatewayError, match="假 DNS 无法监听"):
            gw.FakeDnsServer(gw.FakeIpPool(), host="127.0.0.1",
                             port=blocker.getsockname()[1]).start()
    finally:
        blocker.close()


# ---------------------------------------------------------------- 上游配置


def test_parse_upstream_accepts_socks5_and_http():
    cfg = gw.parse_upstream("socks5h://u%40x:p%3Aw@proxy.example:1080")
    assert cfg == {"scheme": "socks5", "host": "proxy.example", "port": 1080,
                   "username": "u@x", "password": "p:w"}
    assert gw.parse_upstream("http://h:8080")["scheme"] == "http"
    assert gw.parse_upstream("https://h:443")["scheme"] == "https"


@pytest.mark.parametrize("url, message", [
    ("", "没有绑定代理"),
    ("socks4://h:1080", "暂不支持"),
    ("socks5://h", "缺少主机或端口"),
    ("http://h:notaport", "格式不正确"),
])
def test_parse_upstream_rejects_unusable(url, message):
    with pytest.raises(gw.GatewayError, match=message):
        gw.parse_upstream(url)


def test_parse_upstream_error_never_contains_password():
    with pytest.raises(gw.GatewayError) as exc:
        gw.parse_upstream("socks4://user:secret@h:1080")
    assert "secret" not in str(exc.value)


# ---------------------------------------------------------------- 本地 HTTP 代理


class _Upstreams:
    """假上游：connect_fn 返回 socketpair 的一端，测试从另一端读写。"""

    def __init__(self, error=None):
        self.calls = []
        self.peers = []
        self.error = error

    def __call__(self, cfg, host, port):
        self.calls.append((host, port))
        if self.error:
            raise socks_relay.UpstreamError(self.error)
        ours, theirs = socket.socketpair()
        self.peers.append(theirs)
        return ours


@pytest.fixture
def gateway():
    created = []

    def make(**kwargs):
        upstreams = kwargs.pop("upstreams", None) or _Upstreams()
        g = gw.AndroidGateway("socks5://u:p@up.example:1080", dns_host="127.0.0.1", dns_port=0,
                              connect_fn=upstreams, **kwargs).start()
        created.append(g)
        return g, upstreams

    yield make
    for g in created:
        g.stop()


def _auth_header(g) -> str:
    userinfo = g.http_proxy_url.split("//", 1)[1].split("@", 1)[0]
    return "Proxy-Authorization: Basic " + base64.b64encode(userinfo.encode()).decode()


def _send(g, text: str) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", g.port), timeout=5)
    s.sendall(text.encode())
    return s


def _read_head(s: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def test_gateway_listens_on_loopback_with_random_credentials(gateway):
    g, _ = gateway()
    assert g.http_proxy_url.startswith("http://android:")
    assert g.http_proxy_url.endswith(f"@127.0.0.1:{g.port}")
    other, _ = gateway()
    assert other.http_proxy_url != g.http_proxy_url


def test_connect_to_fake_address_tunnels_domain_to_upstream(gateway):
    g, ups = gateway()
    fake = g.pool.ip_for("claude.ai")
    s = _send(g, f"CONNECT {fake}:443 HTTP/1.1\r\nHost: {fake}:443\r\n{_auth_header(g)}\r\n\r\nEARLY")
    head = _read_head(s)
    assert head.startswith(b"HTTP/1.1 200")
    assert ups.calls == [("claude.ai", 443)]
    peer = ups.peers[0]
    peer.settimeout(5)
    assert peer.recv(100) == b"EARLY"  # 握手后紧跟着的数据不能丢
    s.sendall(b"client-hello")
    assert peer.recv(100) == b"client-hello"
    peer.sendall(b"server-hello")
    assert s.recv(100) == b"server-hello"
    s.close()
    peer.close()


def test_connect_requires_gateway_credentials(gateway):
    g, ups = gateway()
    s = _send(g, "CONNECT 1.1.1.1:443 HTTP/1.1\r\n\r\n")
    assert _read_head(s).startswith(b"HTTP/1.1 407")
    s.close()
    s = _send(g, "CONNECT 1.1.1.1:443 HTTP/1.1\r\n"
                 "Proxy-Authorization: Basic YW5kcm9pZDp3cm9uZw==\r\n\r\n")
    assert _read_head(s).startswith(b"HTTP/1.1 407")
    s.close()
    assert ups.calls == []


@pytest.mark.parametrize("target", [
    "10.0.2.3:853", "[fec0::3]:853", "127.0.0.1:8790", "192.168.1.1:80", "198.19.200.1:443",
])
def test_private_and_unknown_fake_targets_are_refused(gateway, target):
    g, ups = gateway()
    s = _send(g, f"CONNECT {target} HTTP/1.1\r\n{_auth_header(g)}\r\n\r\n")
    assert _read_head(s).startswith(b"HTTP/1.1 403")
    s.close()
    assert ups.calls == []


def test_public_ip_targets_pass_through(gateway):
    g, ups = gateway()
    s = _send(g, f"CONNECT 1.1.1.1:443 HTTP/1.1\r\n{_auth_header(g)}\r\n\r\n")
    assert _read_head(s).startswith(b"HTTP/1.1 200")
    assert ups.calls == [("1.1.1.1", 443)]
    s.close()


def test_plain_http_request_is_rewritten_to_origin_form(gateway):
    g, ups = gateway()
    fake = g.pool.ip_for("example.com")
    s = _send(
        g,
        f"GET http://{fake}/path?q=1 HTTP/1.1\r\nHost: example.com\r\n{_auth_header(g)}\r\n"
        "Proxy-Connection: keep-alive\r\nUser-Agent: t\r\n\r\n",
    )
    peer_ready = threading.Event()

    def wait_peer():
        while not ups.peers:
            threading.Event().wait(0.01)
        peer_ready.set()

    threading.Thread(target=wait_peer, daemon=True).start()
    assert peer_ready.wait(5)
    peer = ups.peers[0]
    peer.settimeout(5)
    request = _read_head(peer).decode()
    assert ups.calls == [("example.com", 80)]
    assert request.startswith("GET /path?q=1 HTTP/1.1\r\n")
    assert "Host: example.com" in request
    assert "User-Agent: t" in request
    assert "Proxy-Authorization" not in request
    assert "Proxy-Connection" not in request
    peer.sendall(b"HTTP/1.1 204 No Content\r\n\r\n")
    assert _read_head(s).startswith(b"HTTP/1.1 204")
    s.close()
    peer.close()


def test_upstream_failure_returns_502_and_reports_without_secrets(gateway):
    errors = []
    g, ups = gateway(upstreams=_Upstreams(error="上游拒绝 CONNECT"), on_error=errors.append)
    fake = g.pool.ip_for("claude.ai")
    s = _send(g, f"CONNECT {fake}:443 HTTP/1.1\r\n{_auth_header(g)}\r\n\r\n")
    assert _read_head(s).startswith(b"HTTP/1.1 502")
    s.close()
    assert errors and "claude.ai:443" in errors[0]
    assert all(":p@" not in e for e in errors)


def test_stop_closes_live_tunnels(gateway):
    g, ups = gateway()
    s = _send(g, f"CONNECT 1.1.1.1:443 HTTP/1.1\r\n{_auth_header(g)}\r\n\r\n")
    assert _read_head(s).startswith(b"HTTP/1.1 200")
    g.stop()
    s.settimeout(5)
    assert s.recv(10) == b""
    s.close()


# ---------------------------------------------------------------- HTTP 上游


def _fake_http_proxy(status: bytes):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    seen = {}

    def serve():
        conn, _ = listener.accept()
        seen["head"] = _read_head(conn).decode()
        conn.sendall(b"HTTP/1.1 " + status + b"\r\n\r\n")
        if status.startswith(b"200"):
            seen["data"] = conn.recv(100)
        conn.close()
        listener.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return listener.getsockname()[1], seen, t


def test_http_upstream_sends_connect_with_basic_auth():
    port, seen, t = _fake_http_proxy(b"200 Connection established")
    cfg = gw.parse_upstream(f"http://user:pa%3Ass@127.0.0.1:{port}")
    sock = gw.open_upstream(cfg, "claude.ai", 443)
    sock.sendall(b"hello")
    t.join(5)
    sock.close()
    head = seen["head"]
    assert head.startswith("CONNECT claude.ai:443 HTTP/1.1\r\n")
    token = base64.b64encode(b"user:pa:ss").decode()
    assert f"Proxy-Authorization: Basic {token}" in head
    assert seen["data"] == b"hello"


def test_http_upstream_rejection_raises_upstream_error():
    port, _, t = _fake_http_proxy(b"407 Proxy Authentication Required")
    cfg = gw.parse_upstream(f"http://127.0.0.1:{port}")
    with pytest.raises(socks_relay.UpstreamError, match="407"):
        gw.open_upstream(cfg, "claude.ai", 443)
    t.join(5)
