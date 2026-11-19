"""Android 模拟器的网络出口：假 DNS + 本地 HTTP 代理 → 账号绑定的上游代理。

为什么不直接把上游代理交给模拟器：

- 模拟器只支持 ``-http-proxy``（HTTP 代理），号池里多是带账号密码的 SOCKS5；
- 模拟器在网络栈层面拦截 TCP 连接，只知道目标 IP，于是向代理发 ``CONNECT ip:port``。
  而域名由客户机经宿主 DNS 解析：本地 DNS 会被透明网关改写成 fake-ip（198.18.x.x），
  拿虚拟地址去 CONNECT 上游只会被拒；即便 DNS 干净，宿主直接解析也会暴露真实位置。

做法与 Clash 的 fake-ip 模式相同：模拟器用 ``-dns-server`` 指向这里的假 DNS，
每个域名分到 FAKE_NET 里一个固定的假地址；客户机随后 ``CONNECT 假地址:端口``
时，本地 HTTP 代理把假地址换回域名，交给上游代理远端解析（socks5h 语义）。

效果：

- 所有 TCP 都走账号代理，DNS 也不会从宿主泄露；
- 假地址在公网上不可路由，客户机发往域名的 UDP（如 QUIC）自然失败，应用回退到
  走代理的 TCP，不会绕开代理直连；
- 只监听 127.0.0.1，HTTP 代理还要求每次随机生成的口令，同容器的其他进程用不了它。
"""
from __future__ import annotations

import base64
import ipaddress
import secrets
import select
import socket
import ssl
import struct
import threading
from collections import OrderedDict
from urllib.parse import unquote, urlsplit

from account_manager import socks_relay

# 假地址段：198.18.0.0/15 是 RFC 2544 基准测试保留段。透明网关的 fake-ip 通常用
# 198.18.0.0/16，这里取另一半，避免与之混淆。
FAKE_NET = ipaddress.ip_network("198.19.0.0/16")
FAKE_TTL = 60
# 读请求头的上限与超时。
MAX_HEADER = 16 * 1024
HEADER_TIMEOUT = 15.0
UPSTREAM_TIMEOUT = 12.0
PIPE_BUFFER = 65536

DNS_TYPE_A = 1
DNS_TYPE_ANY = 255
DNS_CLASS_IN = 1
RCODE_OK = 0
RCODE_FORMERR = 1
RCODE_SERVFAIL = 2
RCODE_NXDOMAIN = 3
RCODE_NOTIMP = 4

SUPPORTED_SCHEMES = ("socks5", "socks5h", "http", "https")


class GatewayError(Exception):
    """上游代理不可用或配置不受支持。消息不含凭据，可直接展示。"""


# ---------------------------------------------------------------- 假地址分配


class FakeIpPool:
    """域名 ↔ 假地址的双向映射。地址用尽时回收最久未用的那个。"""

    def __init__(self, network: ipaddress.IPv4Network = FAKE_NET):
        self._first = int(network.network_address) + 1
        self._size = network.num_addresses - 2  # 去掉网络地址与广播地址
        self._next = 0
        self._by_name: OrderedDict[str, int] = OrderedDict()
        self._by_ip: dict[int, str] = {}
        self._lock = threading.Lock()
        self.network = network

    def ip_for(self, name: str) -> str:
        name = name.lower().rstrip(".")
        with self._lock:
            ip = self._by_name.get(name)
            if ip is not None:
                self._by_name.move_to_end(name)
                return str(ipaddress.IPv4Address(ip))
            if self._next < self._size:
                ip = self._first + self._next
                self._next += 1
            else:
                old_name, ip = self._by_name.popitem(last=False)
                self._by_ip.pop(ip, None)
            self._by_name[name] = ip
            self._by_ip[ip] = name
            return str(ipaddress.IPv4Address(ip))

    def name_for(self, ip: str) -> str | None:
        try:
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            return None
        with self._lock:
            name = self._by_ip.get(int(addr))
            if name is not None:
                self._by_name.move_to_end(name)
            return name

    def owns(self, ip: str) -> bool:
        try:
            return ipaddress.IPv4Address(ip) in self.network
        except ValueError:
            return False


# ---------------------------------------------------------------- 假 DNS


def _parse_question(packet: bytes) -> tuple[int, int, str, int, int, int]:
    """返回 (id, flags, qname, qtype, qclass, 问题段结束偏移)。只认单个问题。"""
    if len(packet) < 12:
        raise ValueError("报文过短")
    qid, flags, qdcount = struct.unpack("!HHH", packet[:6])
    if qdcount != 1:
        raise ValueError("只支持单个问题")
    labels = []
    offset = 12
    while True:
        if offset >= len(packet):
            raise ValueError("问题段截断")
        length = packet[offset]
        offset += 1
        if length == 0:
            break
        if length & 0xC0:
            raise ValueError("问题段不应使用压缩指针")
        label = packet[offset:offset + length]
        if len(label) != length:
            raise ValueError("问题段截断")
        labels.append(label.decode("ascii"))  # 非 ASCII 抛 UnicodeDecodeError（ValueError）
        offset += length
    if offset + 4 > len(packet):
        raise ValueError("问题段截断")
    qtype, qclass = struct.unpack("!HH", packet[offset:offset + 4])
    return qid, flags, ".".join(labels), qtype, qclass, offset + 4


def _header(qid: int, flags: int, rcode: int, qdcount: int, ancount: int) -> bytes:
    # QR=1，保留 opcode 与 RD，置 RA。
    out_flags = 0x8000 | (flags & 0x7900) | 0x0080 | (rcode & 0xF)
    return struct.pack("!HHHHHH", qid, out_flags, qdcount, ancount, 0, 0)


def build_dns_response(packet: bytes, pool: FakeIpPool) -> bytes | None:
    """按查询构造应答；A 记录给假地址，其他类型一律「有此名字但无该类型记录」。

    返回 None 表示报文连 ID 都读不出，直接丢弃。
    """
    if len(packet) < 4:
        return None
    try:
        qid, flags, name, qtype, qclass, end = _parse_question(packet)
    except (ValueError, UnicodeDecodeError):
        qid, flags = struct.unpack("!HH", packet[:4])
        return _header(qid, flags, RCODE_FORMERR, 0, 0)
    question = packet[12:end]
    if (flags >> 11) & 0xF:  # 只支持标准查询
        return _header(qid, flags, RCODE_NOTIMP, 1, 0) + question
    lowered = name.lower().rstrip(".")
    if not lowered or lowered.endswith((".arpa", ".local")) or "." not in lowered:
        # 反向解析、mDNS 式名字和单标签名都不该出网。
        return _header(qid, flags, RCODE_NXDOMAIN, 1, 0) + question
    if qclass != DNS_CLASS_IN or qtype not in (DNS_TYPE_A, DNS_TYPE_ANY):
        # AAAA/HTTPS/TXT 等：空应答，让客户机只用 IPv4 假地址。
        return _header(qid, flags, RCODE_OK, 1, 0) + question
    ip = pool.ip_for(lowered)
    answer = (
        b"\xc0\x0c"  # 指回问题段里的名字
        + struct.pack("!HHIH", DNS_TYPE_A, DNS_CLASS_IN, FAKE_TTL, 4)
        + socket.inet_aton(ip)
    )
    return _header(qid, flags, RCODE_OK, 1, 1) + question + answer


class FakeDnsServer:
    """单线程 UDP 假 DNS。应答只依赖内存映射，无需并发。"""

    def __init__(self, pool: FakeIpPool, host: str = "127.0.0.1", port: int = 53):
        self.pool = pool
        self.host = host
        self.port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> FakeDnsServer:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.host, self.port))
        except OSError as exc:
            sock.close()
            raise GatewayError(
                f"假 DNS 无法监听 {self.host}:{self.port}（{exc.strerror or exc}）"
            ) from exc
        self.port = sock.getsockname()[1]
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="android-fake-dns", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        sock = self._sock
        while not self._stop.is_set() and sock is not None:
            try:
                readable, _, _ = select.select([sock], [], [], 0.5)
                if not readable:
                    continue
                packet, addr = sock.recvfrom(4096)
            except OSError:
                if self._stop.is_set():
                    return
                continue
            try:
                reply = build_dns_response(packet, self.pool)
            except Exception:  # noqa: BLE001 — 单个坏包不能打死服务
                reply = None
            if reply is not None:
                try:
                    sock.sendto(reply, addr)
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None


# ---------------------------------------------------------------- 上游连接


def parse_upstream(url: str) -> dict:
    """上游代理地址 → 连接参数。只接受 socks5(h)/http(s)，消息里不带凭据。"""
    text = (url or "").strip()
    if not text:
        raise GatewayError("账号没有绑定代理")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise GatewayError("代理地址格式不正确") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in SUPPORTED_SCHEMES:
        raise GatewayError(
            f"Android 模拟器暂不支持 {scheme or '未知'} 代理，请改用 socks5 或 http 代理"
        )
    if not parts.hostname or port is None:
        raise GatewayError("代理地址不完整：缺少主机或端口")
    cfg = {
        "scheme": "socks5" if scheme == "socks5h" else scheme,
        "host": parts.hostname,
        "port": port,
        "username": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
    }
    if cfg["scheme"] == "socks5":
        for field in ("username", "password"):
            if len(cfg[field].encode()) > socks_relay.MAX_CREDENTIAL_LEN:
                raise GatewayError("代理用户名或密码过长（SOCKS5 最多 255 字节）")
    return cfg


def _read_http_head(sock: socket.socket, limit: int = MAX_HEADER) -> tuple[bytes, bytes]:
    """读到空行为止，返回 (头部含结尾空行, 已多读的数据)。"""
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > limit:
            raise ValueError("请求头过长")
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("连接在请求头结束前关闭")
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    return head + b"\r\n\r\n", rest


def _format_target(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _http_upstream(cfg: dict, host: str, port: int) -> socket.socket:
    sock = socket.create_connection((cfg["host"], cfg["port"]), timeout=UPSTREAM_TIMEOUT)
    try:
        if cfg["scheme"] == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=cfg["host"])
        target = _format_target(host, port)
        lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
        if cfg["username"] or cfg["password"]:
            token = base64.b64encode(f"{cfg['username']}:{cfg['password']}".encode()).decode()
            lines.append(f"Proxy-Authorization: Basic {token}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        head, rest = _read_http_head(sock)
        status = head.split(b"\r\n", 1)[0].split(b" ")
        if len(status) < 2 or status[1] != b"200":
            code = status[1].decode(errors="replace") if len(status) > 1 else "?"
            raise socks_relay.UpstreamError(f"上游 HTTP 代理拒绝 CONNECT（{code}）")
        if rest:
            raise socks_relay.UpstreamError("上游 HTTP 代理在隧道建立前多发了数据")
        sock.settimeout(None)
        return sock
    except BaseException:
        sock.close()
        raise


def open_upstream(cfg: dict, host: str, port: int) -> socket.socket:
    """经上游代理连到 host:port；域名原样交给上游解析。"""
    try:
        if cfg["scheme"] == "socks5":
            return socks_relay._connect_upstream(cfg, host, port)  # noqa: SLF001
        return _http_upstream(cfg, host, port)
    except socks_relay.UpstreamError:
        raise
    except (OSError, ValueError, ConnectionError) as exc:
        raise socks_relay.UpstreamError(f"上游连接失败：{exc}") from exc


# ---------------------------------------------------------------- 本地 HTTP 代理


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(PIPE_BUFFER)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _blocked_ip(host: str) -> bool:
    """不允许经代理访问的字面 IP：内网、回环、链路本地、站点本地、组播与保留段。

    客户机的「私人 DNS」会探测 DNS 服务器（模拟器里是 10.0.2.3 / fec0::3）的 853
    端口，这类地址交给上游只会白等超时。
    """
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    if getattr(addr, "is_site_local", False):
        return True
    return not addr.is_global or addr.is_multicast or addr.is_reserved


def _split_host_port(target: str, default_port: int) -> tuple[str, int]:
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        port = int(rest[1:]) if rest.startswith(":") else default_port
        return host, port
    if target.count(":") == 1:
        host, _, port = target.partition(":")
        return host, int(port)
    return target, default_port


class AndroidGateway:
    """模拟器用的出口：假 DNS + 带口令的本地 HTTP 代理，转发到账号代理。

        gw = AndroidGateway("socks5://user:pass@host:1080").start()
        emulator ... -dns-server gw.dns_host -http-proxy gw.http_proxy_url
    """

    host = "127.0.0.1"

    def __init__(self, upstream_url: str, *, dns_host: str = "127.0.0.1", dns_port: int = 53,
                 on_error=None, connect_fn=None):
        self._cfg = parse_upstream(upstream_url)
        self.pool = FakeIpPool()
        self._dns = FakeDnsServer(self.pool, host=dns_host, port=dns_port)
        self._on_error = on_error or (lambda msg: None)
        self._connect = connect_fn or open_upstream
        self._user = "android"
        self._token = secrets.token_urlsafe(18)
        self._expected_auth = "Basic " + base64.b64encode(
            f"{self._user}:{self._token}".encode()
        ).decode()
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._live: set[socket.socket] = set()
        self._live_lock = threading.Lock()
        self.port: int | None = None

    # -- 生命周期

    @property
    def dns_host(self) -> str:
        return self._dns.host

    @property
    def dns_port(self) -> int:
        return self._dns.port

    @property
    def http_proxy_url(self) -> str:
        """给模拟器 ``-http-proxy`` 的地址（含一次性口令，切勿写进日志）。"""
        if self.port is None:
            raise RuntimeError("网关尚未启动")
        return f"http://{self._user}:{self._token}@{self.host}:{self.port}"

    def start(self) -> AndroidGateway:
        self._dns.start()
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.host, 0))
            listener.listen(128)
        except OSError:
            self._dns.stop()
            raise
        self._listener = listener
        self.port = listener.getsockname()[1]
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="android-gateway",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        with self._live_lock:
            live = list(self._live)
            self._live.clear()
        for sock in live:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._dns.stop()
        self.port = None

    def __enter__(self) -> AndroidGateway:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- 连接处理

    def _track(self, sock: socket.socket) -> bool:
        with self._live_lock:
            if self._stop.is_set():
                return False
            self._live.add(sock)
            return True

    def _untrack(self, sock: socket.socket) -> None:
        with self._live_lock:
            self._live.discard(sock)
        try:
            sock.close()
        except OSError:
            pass

    def _accept_loop(self) -> None:
        listener = self._listener
        while not self._stop.is_set() and listener is not None:
            try:
                readable, _, _ = select.select([listener], [], [], 0.5)
                if not readable:
                    continue
                client, _ = listener.accept()
            except OSError:
                if self._stop.is_set():
                    return
                continue
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def resolve_target(self, host: str) -> str | None:
        """客户机给的目标 → 交给上游的主机名；None 表示拒绝。"""
        host = host.strip("[]")
        if self.pool.owns(host):
            return self.pool.name_for(host)  # 未知假地址（如网关重启后）返回 None
        if _blocked_ip(host):
            return None
        return host

    @staticmethod
    def _reply(client: socket.socket, status: str, extra: str = "") -> None:
        try:
            client.sendall(
                f"HTTP/1.1 {status}\r\n{extra}Content-Length: 0\r\nConnection: close\r\n\r\n"
                .encode()
            )
        except OSError:
            pass

    def _handle(self, client: socket.socket) -> None:
        if not self._track(client):
            client.close()
            return
        upstream = None
        try:
            client.settimeout(HEADER_TIMEOUT)
            head, rest = _read_http_head(client)
            lines = head.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) != 3:
                self._reply(client, "400 Bad Request")
                return
            method, target, version = parts
            headers = [line for line in lines[1:] if line]
            auth = next(
                (h.split(":", 1)[1].strip() for h in headers
                 if h.lower().startswith("proxy-authorization:")),
                "",
            )
            if not secrets.compare_digest(auth, self._expected_auth):
                self._reply(client, "407 Proxy Authentication Required",
                            'Proxy-Authenticate: Basic realm="android"\r\n')
                return

            if method.upper() == "CONNECT":
                host, port = _split_host_port(target, 443)
                forward = rest
            else:
                url = urlsplit(target)
                if url.scheme.lower() != "http" or not url.hostname:
                    self._reply(client, "400 Bad Request")
                    return
                host, port = url.hostname, url.port or 80
                path = url.path or "/"
                if url.query:
                    path += "?" + url.query
                kept = [h for h in headers if not h.lower().startswith(
                    ("proxy-authorization:", "proxy-connection:"))]
                forward = (
                    f"{method} {path} {version}\r\n" + "".join(f"{h}\r\n" for h in kept)
                    + "\r\n"
                ).encode("latin-1") + rest

            name = self.resolve_target(host)
            if name is None:
                self._reply(client, "403 Forbidden")
                return
            try:
                upstream = self._connect(self._cfg, name, port)
            except socks_relay.UpstreamError as exc:
                self._on_error(f"{name}:{port} → {exc}")
                self._reply(client, "502 Bad Gateway")
                return
            if not self._track(upstream):
                return
            if method.upper() == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if forward:
                upstream.sendall(forward)
            client.settimeout(None)
            back = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
            back.start()
            _pipe(client, upstream)
            back.join()
        except (OSError, ValueError, ConnectionError):
            pass
        except Exception as exc:  # noqa: BLE001
            self._on_error(f"Android 网关内部错误：{exc!r}")
        finally:
            if upstream is not None:
                self._untrack(upstream)
            self._untrack(client)
