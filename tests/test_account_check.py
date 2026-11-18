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
