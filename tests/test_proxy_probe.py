"""经代理探测出口 IP / 地区 / 延迟。"""
from __future__ import annotations

from account_manager import proxy_probe


class Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


def _clock():
    ticks = iter([0.0, 0.25, 1.0, 1.5, 2.0, 2.4, 3.0, 3.1])
    return lambda: next(ticks)


def test_ipinfo_result_and_socks_remote_dns():
    calls = []

    def get(url, *, proxy, timeout):
        calls.append((url, proxy, timeout))
        return Resp(200, {"ip": "1.2.3.4", "city": "Tokyo", "region": "Tokyo", "country": "JP",
                          "timezone": "Asia/Tokyo", "org": "AS1 Example"})

    out = proxy_probe.probe_proxy("socks5://u:p@h:1080", get=get, clock=_clock(), timeout=5)
    assert out == {"ok": True, "ip": "1.2.3.4", "country": "JP", "country_code": "JP",
                   "region": "Tokyo", "city": "Tokyo", "timezone": "Asia/Tokyo",
                   "org": "AS1 Example", "latency_ms": 250, "error": ""}
    assert calls == [("https://ipinfo.io/json", "socks5h://u:p@h:1080", 5)]


def test_falls_back_to_next_site_and_parses_ipwho():
    def get(url, *, proxy, timeout):
        if "ipinfo" in url:
            return Resp(429, {})
        return Resp(200, {"success": True, "ip": "5.6.7.8", "country": "Japan", "country_code": "jp",
                          "city": "Osaka", "timezone": {"id": "Asia/Tokyo"},
                          "connection": {"isp": "ISP"}})

    out = proxy_probe.probe_proxy("http://h:1", get=get, clock=_clock())
    assert out["ok"] and out["ip"] == "5.6.7.8" and out["country"] == "Japan"
    assert out["country_code"] == "JP" and out["timezone"] == "Asia/Tokyo" and out["org"] == "ISP"
    assert out["latency_ms"] == 500


def test_failure_scrubs_credentials():
    def get(url, *, proxy, timeout):
        raise RuntimeError("SOCKS5 auth failed for user s3cr%3Ft / s3cr?t at h:1080")

    out = proxy_probe.probe_proxy("socks5://user:s3cr%3Ft@h:1080", get=get, clock=_clock())
    assert out["ok"] is False and out["latency_ms"] is None and out["ip"] == ""
    assert "s3cr" not in out["error"] and "RuntimeError" in out["error"]


def test_unrecognized_payloads():
    def get(url, *, proxy, timeout):
        if "ipinfo" in url:
            return Resp(200, ValueError("not json"))
        if "ipwho" in url:
            return Resp(200, {"success": False})
        return Resp(200, {"city": "x"})

    out = proxy_probe.probe_proxy("http://h:1", get=get, clock=_clock())
    assert out["ok"] is False and "未返回出口 IP" in out["error"]
