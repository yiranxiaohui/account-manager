"""用量额度：claude.ai /api/organizations/{org}/usage 响应 → 摘要；检测时顺带获取。"""
from __future__ import annotations

import httpx

from account_manager import plan, usage
from account_manager.session_check import MAX_USAGE_ORGS, inspect_session, query_org_usage

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
    assert s["org_uuid"] == "o-max" and s["org_name"] == "Personal" and s["plan"] == "max_20x"
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
    assert usage.summarize(FREE_PAYLOAD) == {"org_uuid": "", "org_name": "", "plan": "",
                                             "windows": [], "spend": None}


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


def test_inspect_session_usage_skips_console_api_org():
    make, calls = _factory(_handler(lambda req: httpx.Response(200, json=FREE_PAYLOAD)))
    probe = inspect_session("sk-x", want_usage=True, org_uuid="o-api", client_factory=make)
    # 授权到 Console API 组织时它没有用量额度，不查它，改显示能查用量的组织
    assert [p for p, _ in calls] == ["/api/organizations", "/api/organizations/o-max/usage"]
    assert probe.usage["org_uuid"] == "o-max" and probe.usage_error == ""

    api_only = [ORGS[0]]
    make, calls = _factory(lambda req: httpx.Response(200, json=api_only))
    probe = inspect_session("sk-x", want_usage=True, client_factory=make)
    assert [p for p, _ in calls] == ["/api/organizations"]
    assert probe.usage is None and "API" in probe.usage_error and probe.usage_orgs == []


TEAM_ORGS = [
    {"uuid": "o-uni", "name": "Northeastern University",
     "capabilities": ["chat", "raven", "raven_enterprise"], "raven_type": "enterprise"},
    {"uuid": "o-seat", "name": "Premium Seat - Northeastern University",
     "capabilities": ["chat", "raven", "raven_enterprise"], "raven_type": "enterprise"},
    {"uuid": "o-api", "name": "Console", "capabilities": ["api"]},
]
SEAT_PAYLOAD = {"limits": [
    {"kind": "session", "group": "session", "percent": 100, "severity": "critical",
     "resets_at": RESET_5H, "scope": None, "is_active": True},
    {"kind": "weekly_scoped", "group": "weekly", "percent": 0, "severity": "normal",
     "resets_at": RESET_7D, "scope": {"model": {"id": None, "display_name": "Fable"}},
     "is_active": False},
], "spend": {"enabled": False}}


def _team_handler(req):
    if req.url.path == "/api/organizations":
        return httpx.Response(200, json=TEAM_ORGS)
    if req.url.path == "/api/organizations/o-seat/usage":
        return httpx.Response(200, json=SEAT_PAYLOAD)
    return httpx.Response(200, json=ENTERPRISE_PAYLOAD)


def test_inspect_session_fetches_every_workspace():
    make, calls = _factory(_team_handler)
    probe = inspect_session("sk-x", want_usage=True, client_factory=make)
    # 同套餐的两个工作空间都查（显示的排第一个），Console API 组织不查
    assert [p for p, _ in calls] == ["/api/organizations", "/api/organizations/o-uni/usage",
                                     "/api/organizations/o-seat/usage"]
    assert [s["org_uuid"] for s in probe.usage_orgs] == ["o-uni", "o-seat"]
    assert probe.usage["org_uuid"] == "o-uni" and probe.usage["spend"]["limit"] == 1.0
    seat = probe.usage_orgs[1]
    assert seat["plan"] == "enterprise" and seat["org_name"].startswith("Premium Seat")
    assert [(w["kind"], w["percent"]) for w in seat["windows"]] == [
        ("session", 100), ("weekly_scoped", 0)]

    # 手动切换过的组织优先于已授权的组织，排第一个查
    make, calls = _factory(_team_handler)
    probe = inspect_session("sk-x", want_usage=True, org_uuid="o-uni", usage_org_uuid="o-seat",
                            client_factory=make)
    assert [p for p, _ in calls][1] == "/api/organizations/o-seat/usage"
    assert probe.usage["org_uuid"] == "o-seat"
    # 切换过的组织已不在列表里时退回已授权的组织
    make, _ = _factory(_team_handler)
    probe = inspect_session("sk-x", want_usage=True, org_uuid="o-seat", usage_org_uuid="gone",
                            client_factory=make)
    assert probe.usage["org_uuid"] == "o-seat"


def test_inspect_session_one_workspace_failing_keeps_others():
    def handler(req):
        if req.url.path == "/api/organizations/o-uni/usage":
            return httpx.Response(500, json={})
        return _team_handler(req)
    make, _ = _factory(handler)
    probe = inspect_session("sk-x", want_usage=True, client_factory=make)
    assert probe.status == "alive"
    assert probe.usage is None and "500" in probe.usage_error
    assert [s["org_uuid"] for s in probe.usage_orgs] == ["o-seat"]
    assert set(probe.usage_errors) == {"o-uni"}


def test_inspect_session_caps_workspaces():
    many = [{"uuid": f"o-{i}", "name": f"Team {i}", "capabilities": ["chat", "raven"],
             "raven_type": "team"} for i in range(10)]
    make, calls = _factory(lambda req: httpx.Response(200, json=many)
                           if req.url.path == "/api/organizations"
                           else httpx.Response(200, json=ENTERPRISE_PAYLOAD))
    probe = inspect_session("sk-x", want_usage=True, client_factory=make)
    assert len(calls) == 1 + MAX_USAGE_ORGS
    assert len(probe.usage_orgs) == MAX_USAGE_ORGS


def test_query_org_usage_single_workspace():
    make, calls = _factory(_team_handler)
    org = plan.org_items(TEAM_ORGS)[1]
    summary, error = query_org_usage("sk-x", None, org, client_factory=make)
    assert [p for p, _ in calls] == ["/api/organizations/o-seat/usage"]
    assert error == "" and summary["org_uuid"] == "o-seat" and summary["windows"]

    assert query_org_usage("", None, org, client_factory=make) == (None, "无 sessionKey")
    api_org = plan.org_items(TEAM_ORGS)[2]
    assert "API" in query_org_usage("sk-x", None, api_org, client_factory=make)[1]

    def boom(req):
        raise httpx.ConnectError("boom")
    make, _ = _factory(boom)
    summary, error = query_org_usage("sk-x", None, org, client_factory=make)
    assert summary is None and "ConnectError" in error


def test_usage_org_prefers_switched_then_authorized_then_rank():
    items = plan.org_items(TEAM_ORGS)
    assert [i["uuid"] for i in plan.usage_capable(items)] == ["o-uni", "o-seat"]
    assert plan.usage_org(items)["uuid"] == "o-uni"
    assert plan.usage_org(items, "o-seat", "o-uni")["uuid"] == "o-seat"
    assert plan.usage_org(items, None, "o-seat")["uuid"] == "o-seat"
    assert plan.usage_org(items, "o-api", None)["uuid"] == "o-uni"
    assert plan.usage_org(plan.org_items([TEAM_ORGS[2]])) is None


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
