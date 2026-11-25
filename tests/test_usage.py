"""用量额度：claude.ai /api/organizations/{org}/usage 响应 → 摘要；检测时顺带获取。"""
from __future__ import annotations

import httpx

from account_manager import plan, usage
from account_manager.session_check import inspect_session

RESET_5H = "2026-11-25T08:19:59.571181+00:00"
RESET_7D = "2026-12-01T03:59:59.571202+00:00"

# 实测（2026-11）响应的裁剪版，只保留解析用到的字段与几个占位的 null 字段
MAX_PAYLOAD = {
    "five_hour": {"utilization": 86.0, "resets_at": RESET_5H},
    "seven_day": {"utilization": 75.0, "resets_at": RESET_7D},
    "seven_day_opus": None,
    "tangelo": None,
    "limits": [
        {"kind": "weekly_scoped", "group": "weekly", "percent": 100, "severity": "critical",
         "resets_at": RESET_7D, "scope": {"model": {"id": None, "display_name": "Fable"},
                                          "surface": None}, "is_active": True},
        {"kind": "session", "group": "session", "percent": 86, "severity": "warning",
         "resets_at": RESET_5H, "scope": None, "is_active": False},
        {"kind": "weekly_all", "group": "weekly", "percent": 75, "severity": "warning",
         "resets_at": RESET_7D, "scope": None, "is_active": False},
    ],
    "spend": {"used": {"amount_minor": 0, "currency": "USD", "exponent": 2}, "limit": None,
              "percent": 0, "severity": "normal", "enabled": False},
    "extra_usage": {"is_enabled": False, "monthly_limit": None, "used_credits": None},
}

ENTERPRISE_PAYLOAD = {
    "five_hour": None,
    "seven_day": None,
    "limits": [],
    "spend": {"used": {"amount_minor": 37, "currency": "USD", "exponent": 2},
              "limit": {"amount_minor": 100, "currency": "USD", "exponent": 2},
              "percent": 37, "severity": "normal", "enabled": True},
    "extra_usage": {"is_enabled": True, "monthly_limit": 100, "used_credits": 37.0,
                    "currency": "USD", "decimal_places": 2},
}

FREE_PAYLOAD = {"five_hour": None, "seven_day": None, "limits": [], "spend": None,
                "extra_usage": None}


def test_summarize_max_limits_sorted():
    s = usage.summarize(MAX_PAYLOAD, {"uuid": "o-max", "name": "Personal", "plan": "max_20x"})
    assert s["org_uuid"] == "o-max" and s["org_name"] == "Personal"
    assert [(w["kind"], w["model"], w["percent"], w["severity"]) for w in s["windows"]] == [
        ("session", "", 86, "warning"),
        ("weekly_all", "", 75, "warning"),
        ("weekly_scoped", "Fable", 100, "critical"),
    ]
    assert s["windows"][0]["resets_at"] == RESET_5H
    assert [w["active"] for w in s["windows"]] == [False, False, True]
    assert s["spend"] is None  # Max 没启用用量额度


def test_summarize_enterprise_spend():
    s = usage.summarize(ENTERPRISE_PAYLOAD)
    assert s["windows"] == []
    assert s["spend"] == {"used": 0.37, "limit": 1.0, "currency": "USD", "percent": 37,
                          "severity": "normal"}
    assert s["org_uuid"] == "" and s["org_name"] == ""


def test_summarize_extra_usage_fallback():
    payload = {"limits": [], "extra_usage": ENTERPRISE_PAYLOAD["extra_usage"]}
    s = usage.summarize(payload)
    assert s["spend"] == {"used": 0.37, "limit": 1.0, "currency": "USD", "percent": 37,
                          "severity": "normal"}


def test_summarize_free_has_no_data():
    assert usage.summarize(FREE_PAYLOAD) == {"org_uuid": "", "org_name": "", "windows": [],
                                             "spend": None}


def test_summarize_legacy_fields_without_limits():
    payload = {"five_hour": {"utilization": 25.4, "resets_at": RESET_5H},
               "seven_day": {"utilization": 100.0, "resets_at": RESET_7D},
               "seven_day_sonnet": {"utilization": 80, "resets_at": RESET_7D},
               "seven_day_opus": None}
    s = usage.summarize(payload)
    assert [(w["kind"], w["model"], w["percent"], w["severity"]) for w in s["windows"]] == [
        ("session", "", 25, "normal"),
        ("weekly_all", "", 100, "critical"),
        ("weekly_scoped", "Sonnet", 80, "warning"),
    ]


def test_summarize_rejects_non_object_and_skips_bad_limits():
    assert usage.summarize(None) is None
    assert usage.summarize([1, 2]) is None
    s = usage.summarize({"limits": [None, {"kind": "session"}, {"percent": 5},
                                    {"kind": "new_kind", "percent": 3.6, "severity": "odd"}]})
    assert s["windows"] == [{"kind": "new_kind", "model": "", "percent": 4,
                             "severity": "normal", "resets_at": "", "active": False}]


def test_primary_org_prefers_authorized_then_rank():
    items = plan.org_items([
        {"uuid": "o-api", "capabilities": ["api"]},
        {"uuid": "o-pro", "capabilities": ["chat", "claude_pro"]},
        {"uuid": "o-max", "capabilities": ["chat", "claude_max"],
         "rate_limit_tier": "default_claude_max_20x"},
    ])
    assert plan.primary_org(items)["uuid"] == "o-max"
    assert plan.primary_org(items, "o-pro")["uuid"] == "o-pro"
    assert plan.primary_org(items, "gone")["uuid"] == "o-max"
    assert plan.primary_org([]) is None


ORGS = [{"uuid": "o-api", "name": "Console", "capabilities": ["api"]},
        {"uuid": "o-max", "name": "Personal", "capabilities": ["chat", "claude_max"],
         "rate_limit_tier": "default_claude_max_20x"}]


def _factory(handler):
    calls = []

    def make(proxy=None):
        def wrapped(req):
            calls.append((req.url.path, req.headers.get("cookie", "")))
            return handler(req)
        return httpx.Client(transport=httpx.MockTransport(wrapped))

    return make, calls


def _handler(usage_response):
    def handler(req):
        if req.url.path == "/api/organizations":
            return httpx.Response(200, json=ORGS)
        return usage_response(req)
    return handler


def test_inspect_session_fetches_primary_org_usage():
    make, calls = _factory(_handler(lambda req: httpx.Response(200, json=MAX_PAYLOAD)))
    probe = inspect_session("sk-x", want_usage=True, client_factory=make)
    assert probe.status == "alive"
    assert [p for p, _ in calls] == ["/api/organizations", "/api/organizations/o-max/usage"]
    assert all("sessionKey=sk-x" in cookie for _, cookie in calls)
    assert probe.usage["org_uuid"] == "o-max"
    assert probe.usage["windows"][0]["kind"] == "session"
    assert probe.usage_error == ""


def test_inspect_session_usage_uses_authorized_org():
    make, calls = _factory(_handler(lambda req: httpx.Response(200, json=FREE_PAYLOAD)))
    probe = inspect_session("sk-x", want_usage=True, org_uuid="o-api", client_factory=make)
    # 授权到 Console API 组织时没有用量额度，不发请求
    assert [p for p, _ in calls] == ["/api/organizations"]
    assert probe.usage is None and "API" in probe.usage_error


def test_inspect_session_usage_failure_keeps_alive():
    make, _ = _factory(_handler(lambda req: httpx.Response(
        403, headers={"cf-mitigated": "challenge"}, text="<html>challenge</html>")))
    probe = inspect_session("sk-x", want_usage=True, client_factory=make)
    assert probe.status == "alive"
    assert probe.usage is None and "403" in probe.usage_error

    def boom(req):
        raise httpx.ConnectError("boom")
    make, _ = _factory(_handler(boom))
    probe = inspect_session("sk-x", want_usage=True, client_factory=make)
    assert probe.status == "alive"
    assert probe.usage is None and "ConnectError" in probe.usage_error


def test_inspect_session_without_want_usage_or_dead_skips_usage():
    make, calls = _factory(_handler(lambda req: httpx.Response(200, json=MAX_PAYLOAD)))
    assert inspect_session("sk-x", client_factory=make).usage is None
    assert [p for p, _ in calls] == ["/api/organizations"]

    make, calls = _factory(lambda req: httpx.Response(401, json={"error": {}}))
    probe = inspect_session("sk-x", want_usage=True, client_factory=make)
    assert probe.status == "dead" and probe.usage is None
    assert len(calls) == 1
