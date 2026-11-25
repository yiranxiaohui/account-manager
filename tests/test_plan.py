"""账号套餐识别：组织字段 → 套餐、主套餐选择、落库与授权联动。"""
from __future__ import annotations

import json

import httpx
import pytest

from account_manager import oauth, plan
from account_manager.session_check import inspect_session
from server import account_plan, db, oauth_acquire

# 以下组织字段取自 2026-11 线上账号的实测响应（仅保留套餐相关字段）
FREE = {"uuid": "o-free", "name": "free", "raven_type": None, "capabilities": ["chat"],
        "rate_limit_tier": "default_claude_ai", "billing_type": "none",
        "analytics_subscription_plan": "claude_free"}
PRO = {"uuid": "o-pro", "name": "pro", "raven_type": None, "capabilities": ["chat", "claude_pro"],
       "rate_limit_tier": "default_claude_ai", "billing_type": "apple_subscription",
       "analytics_subscription_plan": "claude_pro"}
MAX20 = {"uuid": "o-max", "name": "max", "raven_type": None,
         "capabilities": ["claude_max", "chat"], "rate_limit_tier": "default_claude_max_20x",
         "billing_type": "google_play_subscription", "analytics_subscription_plan": "claude_max 20x"}
ENT = {"uuid": "o-ent", "name": "Acme", "raven_type": "enterprise",
       "capabilities": ["raven", "analytics_api", "raven_enterprise", "chat",
                        "customer_terms:negotiated"],
       "rate_limit_tier": "manual_tier_3", "billing_type": "stripe_subscription_contracted",
       "analytics_subscription_plan": "claude_enterprise"}
API = {"uuid": "o-api", "name": "console", "raven_type": None,
       "capabilities": ["api", "api_individual", "customer_terms:standard"],
       "rate_limit_tier": "auto_trust_tier_a", "billing_type": "prepaid",
       "analytics_subscription_plan": "api_individual"}


@pytest.mark.parametrize(("org", "expected"), [
    (FREE, "free"),
    (PRO, "pro"),
    (MAX20, "max_20x"),
    ({**MAX20, "rate_limit_tier": "default_claude_max_5x",
      "analytics_subscription_plan": "claude_max 5x"}, "max_5x"),
    ({**MAX20, "rate_limit_tier": "", "analytics_subscription_plan": ""}, "max"),
    # rate_limit_tier 缺失时退回 analytics_subscription_plan 判倍数
    ({**MAX20, "rate_limit_tier": ""}, "max_20x"),
    # Ultra（原 Max 20x）：字段里改叫 ultra 时也认作 max_20x
    ({**MAX20, "rate_limit_tier": "default_claude_ultra", "analytics_subscription_plan": ""}, "max_20x"),
    ({**MAX20, "capabilities": ["chat", "claude_ultra"], "rate_limit_tier": "",
      "analytics_subscription_plan": ""}, "max_20x"),
    ({**MAX20, "capabilities": ["chat"], "rate_limit_tier": "",
      "analytics_subscription_plan": "claude_ultra"}, "max_20x"),
    (ENT, "enterprise"),
    ({"uuid": "o-team", "raven_type": "team", "capabilities": ["chat", "raven"]}, "team"),
    (API, "api"),
    ({"uuid": "o-x", "capabilities": []}, "unknown"),
    ("not a dict", "unknown"),
])
def test_org_plan(org, expected):
    assert plan.org_plan(org) == expected


def test_org_items_accept_raw_payload_and_summaries():
    raw = {"organizations": [MAX20, {"name": "no uuid"}, API]}
    items = plan.org_items(raw)
    assert items == [
        {"uuid": "o-max", "name": "max", "plan": "max_20x",
         "rate_limit_tier": "default_claude_max_20x", "billing_type": "google_play_subscription"},
        {"uuid": "o-api", "name": "console", "plan": "api",
         "rate_limit_tier": "auto_trust_tier_a", "billing_type": "prepaid"},
    ]
    # 已是摘要（无 capabilities）时沿用其中的 plan；元组同样接受
    assert plan.org_items(tuple(items)) == items
    assert plan.org_items(None) == [] and plan.org_items("x") == []


def test_primary_plan_prefers_authorized_org_then_rank():
    items = plan.org_items([API, PRO, ENT])
    assert plan.primary_plan(items) == "enterprise"
    assert plan.primary_plan(items, "o-pro") == "pro"
    assert plan.primary_plan(items, "gone") == "enterprise"
    # Console API 组织只在没有其它组织时才算
    assert plan.primary_plan(plan.org_items([API, FREE])) == "free"
    assert plan.primary_plan(plan.org_items([API])) == "api"
    assert plan.primary_plan([]) == ""
    assert set(plan.PLAN_LABELS) >= {i["plan"] for i in items}


def test_inspect_session_returns_orgs_only_when_alive():
    def handler(req):
        return httpx.Response(200, json=[MAX20])

    make = lambda proxy=None: httpx.Client(transport=httpx.MockTransport(handler))  # noqa: E731
    res = inspect_session("sk-x", client_factory=make)
    assert (res.status, res.email) == ("alive", "")
    assert res.orgs == [MAX20]

    dead = lambda proxy=None: httpx.Client(  # noqa: E731
        transport=httpx.MockTransport(lambda req: httpx.Response(401, json={"error": "x"})))
    res = inspect_session("sk-x", client_factory=dead)
    assert res.status == "dead" and res.orgs == []
    assert inspect_session("").orgs == []


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "t.db")
    db.upsert_account(c, "a@x.com", "x.com", "", "", None, "success", session_key="sk-a")
    return c


def test_record_and_refresh_primary(conn):
    assert account_plan.record(conn, "a@x.com", [], "t0") == {}
    assert account_plan.record(conn, "nobody@x.com", [PRO], "t0") == {}

    fields = account_plan.record(conn, "a@x.com", [PRO, ENT], "t1")
    assert fields["plan"] == "enterprise" and fields["plan_checked_at"] == "t1"
    row = db.get_account(conn, "a@x.com")
    assert row["plan"] == "enterprise"
    assert [o["plan"] for o in json.loads(row["plan_orgs"])] == ["pro", "enterprise"]

    # 授权到 Pro 组织后，不发请求按已存的组织列表重算
    db.update_account_oauth(conn, "a@x.com", {"access_token": "at", "org_uuid": "o-pro"})
    account_plan.refresh_primary(conn, "a@x.com")
    row = db.get_account(conn, "a@x.com")
    assert row["plan"] == "pro" and row["plan_checked_at"] == "t1"
    # 之后再检测，也以授权的组织为准
    assert account_plan.record(conn, "a@x.com", [PRO, ENT], "t2")["plan"] == "pro"


def test_init_db_migrates_plan_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE accounts (email TEXT UNIQUE, domain TEXT, status TEXT)")
    old.execute("INSERT INTO accounts VALUES ('a@x.com','x.com','success')")
    old.commit()
    old.close()
    c = db.init_db(path)
    assert db.update_account_plan(c, "a@x.com", "pro", "[]", "t")
    row = db.get_account(c, "a@x.com")
    assert (row["plan"], row["plan_orgs"], row["plan_checked_at"]) == ("pro", "[]", "t")


def _tokens(org_uuid, organizations=()):
    return oauth.OAuthTokens(
        access_token="at", refresh_token="rt", token_type="Bearer", expires_in=3600,
        expires_at=1, scope="s", org_uuid=org_uuid, account_uuid="acc",
        email_address="a@x.com", org_name="n", organizations=organizations,
    )


def test_acquire_records_plan_from_authorize(conn):
    orgs = tuple(plan.org_items([PRO, MAX20]))
    row = oauth_acquire.acquire(conn, "a@x.com", now="t3",
                                authorize=lambda *a, **k: _tokens("o-pro", orgs))
    assert row["plan"] == "pro" and row["plan_checked_at"] == "t3"


def test_acquire_without_orgs_recomputes_from_saved(conn):
    account_plan.record(conn, "a@x.com", [PRO, MAX20], "t1")
    assert db.get_account(conn, "a@x.com")["plan"] == "max_20x"
    row = oauth_acquire.acquire(conn, "a@x.com", now="t3",
                                authorize=lambda *a, **k: _tokens("o-pro"))
    assert row["plan"] == "pro" and row["plan_checked_at"] == "t1"


def test_list_orgs_records_plan(conn):
    def lister(sk, proxy, *, preferred=None):
        return {"organizations": oauth.summarize_orgs([MAX20, API]), "default": "o-max"}

    out = oauth_acquire.list_orgs(conn, "a@x.com", now="t4", lister=lister)
    assert [o["plan"] for o in out["organizations"]] == ["max_20x", "api"]
    row = db.get_account(conn, "a@x.com")
    assert row["plan"] == "max_20x" and row["plan_checked_at"] == "t4"


def test_authorize_with_session_key_returns_org_summaries():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/api/organizations":
            return httpx.Response(200, json=[PRO])
        if req.url.path.endswith("/authorize"):
            return httpx.Response(200, json={
                "redirect_uri": "https://platform.claude.com/oauth/code/callback?code=c&state=s"})
        return httpx.Response(200, json={"access_token": "AT-SECRET", "refresh_token": "RT-SECRET",
                                          "expires_in": 60})

    make = lambda proxy=None: httpx.Client(transport=httpx.MockTransport(handler))  # noqa: E731
    tokens = oauth.authorize_with_session_key("sk-x", client_factory=make, now_fn=lambda: 0)
    assert tokens.org_uuid == "o-pro"
    assert [o["plan"] for o in tokens.organizations] == ["pro"]
    assert "SECRET" not in json.dumps(tokens.organizations)
    assert calls[0] == "/api/organizations"
