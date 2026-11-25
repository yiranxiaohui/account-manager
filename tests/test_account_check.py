"""session key 存活检测：POST /api/accounts/{email}/check（顺带识别套餐）。"""
from __future__ import annotations

import json
from unittest import mock

from fastapi.testclient import TestClient

from account_manager.session_check import SessionProbe
from server import db
from server.app import create_app
from server.config_store import save_config


def _app(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    return create_app(
        data_dir=tmp_path,
        config_path=tmp_path / "config.yaml",
        now_fn=lambda: "2026-07-31T00:00:00Z",
    )


def _seed(app):
    conn = app.state.cr.conn
    db.upsert_account(
        conn, "a@x.com", "x.com", "", "mb1", 1, "success",
        session_key="sk-ant-a", proxy="socks5://p:1",
    )
    conn.commit()


def _client(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    return app, c


def test_account_check_404_on_missing_account(tmp_path):
    _, c = _client(tmp_path)
    r = c.post("/api/accounts/nobody@x.com/check")
    assert r.status_code == 404


def test_account_check_alive(tmp_path):
    app, c = _client(tmp_path)
    with mock.patch("server.app.inspect_session", return_value=SessionProbe("alive", "有效")):
        r = c.post("/api/accounts/a@x.com/check")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "alive"
    assert body["detail"] == "有效"
    assert body["checked_at"]
    assert "plan" not in body  # 没有组织信息时不动套餐

    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["check_status"] == "alive"
    assert row["checked_at"] == body["checked_at"]


MAX_ORG = {"uuid": "o-max", "name": "Personal", "capabilities": ["chat", "claude_max"],
           "rate_limit_tier": "default_claude_max_20x", "billing_type": "google_play_subscription"}
API_ORG = {"uuid": "o-api", "name": "Console", "capabilities": ["api", "api_individual"],
           "rate_limit_tier": "auto_trust_tier_a", "billing_type": "prepaid"}


def test_account_check_records_plan(tmp_path):
    app, c = _client(tmp_path)
    probe = SessionProbe("alive", "有效", orgs=[API_ORG, MAX_ORG])
    with mock.patch("server.app.inspect_session", return_value=probe):
        body = c.post("/api/accounts/a@x.com/check").json()
    assert body["plan"] == "max_20x"
    assert body["plan_checked_at"] == body["checked_at"]
    orgs = json.loads(body["plan_orgs"])
    assert [(o["uuid"], o["plan"]) for o in orgs] == [("o-api", "api"), ("o-max", "max_20x")]
    assert all(set(o) == {"uuid", "name", "plan", "rate_limit_tier", "billing_type"} for o in orgs)

    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["plan"] == "max_20x"
    listed = c.get("/api/accounts").json()[0]
    assert listed["plan"] == "max_20x"

    # 之后检测失败（没有组织信息）时保留上次的套餐
    with mock.patch("server.app.inspect_session", return_value=SessionProbe("blocked", "拦截")):
        c.post("/api/accounts/a@x.com/check")
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["check_status"] == "blocked"
    assert row["plan"] == "max_20x"


def test_account_check_requires_auth(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)  # 未登录
    assert c.post("/api/accounts/a@x.com/check").status_code == 401


USAGE = {"org_uuid": "o-max", "org_name": "Personal", "spend": None,
         "windows": [{"kind": "session", "model": "", "percent": 46, "severity": "normal",
                      "resets_at": "2026-11-25T07:20:00+00:00", "active": False}]}


def test_account_check_records_usage(tmp_path):
    app, c = _client(tmp_path)
    db.update_account_oauth(app.state.cr.conn, "a@x.com", {"org_uuid": "o-max"})
    probe = SessionProbe("alive", "有效", orgs=[MAX_ORG], usage=USAGE, usage_orgs=[USAGE])
    with mock.patch("server.app.inspect_session", return_value=probe) as inspect:
        body = c.post("/api/accounts/a@x.com/check").json()
    # 检测时顺带查用量，显示的组织依次取手动切换的、已授权的组织
    assert inspect.call_args.kwargs == {"want_usage": True, "org_uuid": "o-max",
                                        "usage_org_uuid": None}
    stored = {**USAGE, "checked_at": body["checked_at"]}
    assert json.loads(body["usage"]) == stored
    assert json.loads(body["usage_orgs"]) == [stored]
    assert body["usage_checked_at"] == body["checked_at"]
    assert body["usage_org_uuid"] == ""
    assert "usage_error" not in body

    listed = c.get("/api/accounts").json()[0]
    assert json.loads(listed["usage"]) == stored
    assert listed["usage_checked_at"] == body["checked_at"]

    # 之后取不到用量（检测失败 / 接口异常）时保留上次的结果，并带回原因
    probe = SessionProbe("alive", "有效", orgs=[MAX_ORG], usage_error="用量接口响应异常（HTTP 500）",
                         usage_errors={"o-max": "用量接口响应异常（HTTP 500）"})
    with mock.patch("server.app.inspect_session", return_value=probe):
        body = c.post("/api/accounts/a@x.com/check").json()
    assert "usage" not in body
    assert body["usage_error"] == "用量接口响应异常（HTTP 500）"
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert json.loads(row["usage"]) == stored
    assert row["usage_checked_at"] == "2026-07-31T00:00:00Z"
