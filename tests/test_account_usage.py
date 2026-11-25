"""账号多组织（工作空间）用量额度：检测时按组织缓存，POST /api/accounts/{email}/usage 切换显示。"""
from __future__ import annotations

import json
from unittest import mock

from fastapi.testclient import TestClient

from account_manager.session_check import SessionProbe
from server import account_usage, db
from server.app import create_app
from server.config_store import save_config

UNI = {"uuid": "o-uni", "name": "Northeastern University", "raven_type": "enterprise",
       "capabilities": ["chat", "raven", "raven_enterprise"]}
SEAT = {"uuid": "o-seat", "name": "Premium Seat - Northeastern University",
        "raven_type": "enterprise", "capabilities": ["chat", "raven", "raven_enterprise"]}
API = {"uuid": "o-api", "name": "Console", "capabilities": ["api"]}

UNI_USAGE = {"org_uuid": "o-uni", "org_name": UNI["name"], "plan": "enterprise", "windows": [],
             "spend": {"used": 0.0, "limit": 1.0, "currency": "USD", "percent": 0,
                       "severity": "normal"}}
SEAT_USAGE = {"org_uuid": "o-seat", "org_name": SEAT["name"], "plan": "enterprise", "spend": None,
              "windows": [{"kind": "session", "model": "", "percent": 100,
                           "severity": "critical", "resets_at": "2026-11-25T11:20:00+00:00",
                           "active": True}]}


class Clock:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return f"2026-11-25T00:00:{self.n:02d}Z"


def _client(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", now_fn=Clock())
    db.upsert_account(app.state.cr.conn, "a@x.com", "x.com", "", "mb1", 1, "success",
                      session_key="sk-ant-a", proxy="socks5://p:1")
    app.state.cr.conn.commit()
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    return app, c


def _check(c, probe):
    with mock.patch("server.app.inspect_session", return_value=probe) as inspect:
        body = c.post("/api/accounts/a@x.com/check").json()
    return body, inspect.call_args.kwargs


def _team_probe(*usage_orgs, orgs=(UNI, SEAT, API)):
    return SessionProbe("alive", "有效", orgs=list(orgs), usage=usage_orgs[0] if usage_orgs else None,
                        usage_orgs=list(usage_orgs))


def test_check_caches_every_workspace(tmp_path):
    app, c = _client(tmp_path)
    body, _ = _check(c, _team_probe(UNI_USAGE, SEAT_USAGE))
    orgs = json.loads(body["usage_orgs"])
    assert [o["org_uuid"] for o in orgs] == ["o-uni", "o-seat"]
    assert all(o["checked_at"] == body["checked_at"] for o in orgs)
    # 没切换过、没授权过：显示套餐最高的组织（同名次取靠前的）
    assert json.loads(body["usage"])["org_uuid"] == "o-uni"


def test_switch_workspace_queries_and_persists(tmp_path):
    app, c = _client(tmp_path)
    _check(c, _team_probe(UNI_USAGE, SEAT_USAGE))
    fresh = {**SEAT_USAGE, "windows": [{**SEAT_USAGE["windows"][0], "percent": 40}]}
    with mock.patch("server.app.query_org_usage", return_value=(fresh, "")) as query:
        r = c.post("/api/accounts/a@x.com/usage", json={"org_uuid": "o-seat"})
    assert r.status_code == 200
    body = r.json()
    # 只查切换到的组织，走账号的 sessionKey 与代理
    args = query.call_args.args
    assert args[:2] == ("sk-ant-a", "socks5://p:1") and args[2]["uuid"] == "o-seat"
    assert body["usage_org_uuid"] == "o-seat"
    shown = json.loads(body["usage"])
    assert shown["org_uuid"] == "o-seat" and shown["windows"][0]["percent"] == 40
    assert body["usage_checked_at"] == shown["checked_at"]
    assert "usage_error" not in body
    # 另一个组织的缓存还在
    cached = {o["org_uuid"]: o for o in json.loads(body["usage_orgs"])}
    assert cached["o-uni"]["spend"]["limit"] == 1.0

    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["usage_org_uuid"] == "o-seat"
    # 之后检测时优先查、显示切换到的组织
    body, kwargs = _check(c, _team_probe(SEAT_USAGE, UNI_USAGE))
    assert kwargs["usage_org_uuid"] == "o-seat"
    assert json.loads(body["usage"])["org_uuid"] == "o-seat"
    assert [o["org_uuid"] for o in json.loads(body["usage_orgs"])] == ["o-uni", "o-seat"]


def test_switch_failure_keeps_cached_result(tmp_path):
    app, c = _client(tmp_path)
    body, _ = _check(c, _team_probe(UNI_USAGE, SEAT_USAGE))
    checked = body["checked_at"]
    with mock.patch("server.app.query_org_usage", return_value=(None, "请求失败：ConnectError")):
        body = c.post("/api/accounts/a@x.com/usage", json={"org_uuid": "o-seat"}).json()
    assert body["usage_error"] == "请求失败：ConnectError"
    assert body["usage_org_uuid"] == "o-seat"
    assert json.loads(body["usage"])["org_uuid"] == "o-seat"
    assert body["usage_checked_at"] == checked  # 沿用检测时的结果


def test_switch_to_workspace_without_data(tmp_path):
    app, c = _client(tmp_path)
    _check(c, _team_probe(UNI_USAGE))  # 只取到了一个组织
    with mock.patch("server.app.query_org_usage", return_value=(None, "用量接口响应异常（HTTP 403）")):
        body = c.post("/api/accounts/a@x.com/usage", json={"org_uuid": "o-seat"}).json()
    # 切换到的组织没有数据时不显示别的组织的额度
    assert body["usage"] == "" and body["usage_checked_at"] == ""
    assert body["usage_org_uuid"] == "o-seat"
    assert [o["org_uuid"] for o in json.loads(body["usage_orgs"])] == ["o-uni"]


def test_switch_validation(tmp_path):
    app, c = _client(tmp_path)
    with mock.patch("server.app.query_org_usage") as query:
        # 还没检测过（没有组织列表）
        assert c.post("/api/accounts/a@x.com/usage", json={"org_uuid": "o-seat"}).status_code == 400
        _check(c, _team_probe(UNI_USAGE))
        assert c.post("/api/accounts/a@x.com/usage", json={"org_uuid": "gone"}).status_code == 400
        assert c.post("/api/accounts/a@x.com/usage", json={"org_uuid": "o-api"}).status_code == 400
        assert c.post("/api/accounts/a@x.com/usage", json={}).status_code == 400
        assert c.post("/api/accounts/a@x.com/usage", content=b"x").status_code == 400
        assert c.post("/api/accounts/no@x.com/usage", json={"org_uuid": "o-uni"}).status_code == 404
    query.assert_not_called()
    anon = TestClient(app)
    assert anon.post("/api/accounts/a@x.com/usage", json={"org_uuid": "o-uni"}).status_code == 401


def test_legacy_single_usage_is_kept_as_cache(tmp_path):
    app, c = _client(tmp_path)
    conn = app.state.cr.conn
    # 旧版本只存了一个组织的 usage，没有 usage_orgs
    _check(c, SessionProbe("alive", "有效", orgs=[UNI, SEAT]))
    db.update_account_usage(conn, "a@x.com", json.dumps(UNI_USAGE), "2026-11-24T00:00:00Z")
    with mock.patch("server.app.query_org_usage", return_value=(SEAT_USAGE, "")):
        c.post("/api/accounts/a@x.com/usage", json={"org_uuid": "o-seat"})
    with mock.patch("server.app.query_org_usage", return_value=(None, "请求失败：ReadTimeout")):
        body = c.post("/api/accounts/a@x.com/usage", json={"org_uuid": "o-uni"}).json()
    shown = json.loads(body["usage"])
    assert shown["org_uuid"] == "o-uni" and shown["checked_at"] == "2026-11-24T00:00:00Z"
    assert body["usage_checked_at"] == "2026-11-24T00:00:00Z"


def test_check_drops_workspaces_the_account_left(tmp_path):
    app, c = _client(tmp_path)
    _check(c, _team_probe(UNI_USAGE, SEAT_USAGE))
    with mock.patch("server.app.query_org_usage", return_value=(SEAT_USAGE, "")):
        c.post("/api/accounts/a@x.com/usage", json={"org_uuid": "o-seat"})
    # 账号退出了 Premium Seat：缓存去掉它，显示退回剩下的组织
    body, _ = _check(c, _team_probe(UNI_USAGE, orgs=(UNI,)))
    assert [o["org_uuid"] for o in json.loads(body["usage_orgs"])] == ["o-uni"]
    assert json.loads(body["usage"])["org_uuid"] == "o-uni"


def test_shown_workspace_follows_oauth_until_switched(tmp_path):
    app, c = _client(tmp_path)
    conn = app.state.cr.conn
    _check(c, _team_probe(UNI_USAGE, SEAT_USAGE))
    db.update_account_oauth(conn, "a@x.com", {"org_uuid": "o-seat"})
    account_usage.refresh_shown(conn, "a@x.com")
    assert json.loads(db.get_account(conn, "a@x.com")["usage"])["org_uuid"] == "o-seat"

    with mock.patch("server.app.query_org_usage", return_value=(UNI_USAGE, "")):
        c.post("/api/accounts/a@x.com/usage", json={"org_uuid": "o-uni"})
    account_usage.refresh_shown(conn, "a@x.com")
    # 手动切换过之后不再跟着授权的组织走
    assert json.loads(db.get_account(conn, "a@x.com")["usage"])["org_uuid"] == "o-uni"
