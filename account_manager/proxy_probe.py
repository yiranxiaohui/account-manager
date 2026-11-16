"""经代理探测出口：是否连得通、出口 IP、所在地区与往返延迟。

面板「代理池」的测试按钮用它。依次尝试几个公开的 IP 查询站点，取第一个成功的
结果；全部失败时返回最后一个错误。错误信息会抹掉代理凭据后再返回，可以直接展示。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import unquote, urlsplit

PROBE_URLS = (
    "https://ipinfo.io/json",
    "https://ipwho.is/",
    "https://ipapi.co/json/",
)
DEFAULT_TIMEOUT = 10.0
_ERROR_MAX = 200


def _default_get(url: str, *, proxy: str, timeout: float):
    from curl_cffi import requests as cffi_requests

    return cffi_requests.get(url, proxy=proxy, timeout=timeout, impersonate="chrome")


def _curl_proxy(url: str) -> str:
    """SOCKS 一律交给上游解析域名：本地 DNS 可能是 fake-ip（198.18.x.x）。"""
    text = url.strip()
    if text.lower().startswith("socks5://"):
        return "socks5h://" + text[len("socks5://"):]
    return text


def _scrub(message: str, url: str) -> str:
    """去掉错误信息里可能带出的代理用户名 / 密码。"""
    try:
        parts = urlsplit(url)
        secrets = {parts.password or "", unquote(parts.password or ""),
                   parts.username or "", unquote(parts.username or "")}
    except ValueError:
        secrets = set()
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        message = message.replace(secret, "***")
    message = " ".join(message.split())
    return message[:_ERROR_MAX]


def _str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_location(data: dict) -> dict:
    """兼容 ipinfo.io / ipwho.is / ipapi.co 三种返回格式。"""
    tz = data.get("timezone")
    if isinstance(tz, dict):  # ipwho.is: {"timezone": {"id": "Asia/Tokyo"}}
        tz = tz.get("id")
    country_code = _str(data.get("country_code"))
    country = _str(data.get("country_name")) or _str(data.get("country"))
    if not country_code and len(country) == 2 and country.isalpha():
        country_code = country  # ipinfo 的 country 本身就是两位代码
    connection = data.get("connection") if isinstance(data.get("connection"), dict) else {}
    return {
        "ip": _str(data.get("ip")),
        "country": country,
        "country_code": country_code.upper(),
        "region": _str(data.get("region")),
        "city": _str(data.get("city")),
        "timezone": _str(tz),
        "org": _str(data.get("org")) or _str(connection.get("org")) or _str(connection.get("isp")),
    }


def probe_proxy(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    get: Callable | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """经代理请求 IP 查询站点。返回 {ok, ip, country, ..., latency_ms, error}。"""
    fetch = get or _default_get
    proxy = _curl_proxy(url)
    last_error = "无法连接任何 IP 查询站点"
    for target in PROBE_URLS:
        started = clock()
        try:
            resp = fetch(target, proxy=proxy, timeout=timeout)
            elapsed = clock() - started
            if resp.status_code != 200:
                last_error = f"{urlsplit(target).hostname} 返回 HTTP {resp.status_code}"
                continue
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 — 任何失败都换下一个站点再试
            detail = str(exc) or type(exc).__name__
            last_error = _scrub(f"{type(exc).__name__}: {detail}", url)
            continue
        if not isinstance(data, dict) or data.get("success") is False:
            last_error = f"{urlsplit(target).hostname} 返回内容无法识别"
            continue
        info = parse_location(data)
        if not info["ip"]:
            last_error = f"{urlsplit(target).hostname} 未返回出口 IP"
            continue
        return {"ok": True, **info, "latency_ms": max(0, round(elapsed * 1000)), "error": ""}
    return {"ok": False, "ip": "", "country": "", "country_code": "", "region": "",
            "city": "", "timezone": "", "org": "", "latency_ms": None, "error": last_error}
