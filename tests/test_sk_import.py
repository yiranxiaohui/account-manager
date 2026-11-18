"""sk 批量导入：解析、探测取邮箱、选代理入库、HTTP 接口。"""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

import server.app as appmod
from account_manager.session_check import SessionProbe, probe_session
from server import db, sk_import
from server.app import create_app
from server.config_store import save_config

SK1 = "sk-ant-sid01-" + "a" * 40
SK2 = "sk-ant-sid01-" + "b" * 40
SK3 = "sk-ant-sid01-" + "c" * 40
PROXY_URL = "socks5://u:secret@h:1080"
PRO_ORG = {"uuid": "o-pro", "name": "P", "capabilities": ["chat", "claude_pro"],
           "rate_limit_tier": "default_claude_ai", "billing_type": "apple_subscription"}


def test_parse_formats_and_errors():
    text = f"""
# comment
{SK1}
User@Example.com----{SK2}
{SK3},x@y.io
not a key
{SK1}
"""
    items, errors = sk_import.parse(text)
    assert [(i.session_key, i.email) for i in items] == [
        (SK1, ""), (SK2, "user@example.com"), (SK3, "x@y.io"),
    ]
    assert [e["result"] for e in errors] == ["invalid", "duplicate"]


def test_probe_session_fetches_email():
    def handler(req):
        if req.url.path == "/api/organizations":
            return httpx.Response(200, json=[{"uuid": "o"}])
        return httpx.Response(200, json={"email_address": "Me@Mail.com"})

    make = lambda proxy=None: httpx.Client(transport=httpx.MockTransport(handler))  # noqa: E731
    assert probe_session("sk-x", want_email=True, client_factory=make) == (
        "alive", "有效", "me@mail.com")
    assert probe_session("sk-x", client_factory=make) == ("alive", "有效", "")


def _client(tmp_path):
    save_config(tmp_path / "config.yaml", {
        "panel_password": "pw",
        "saved_proxies": [{"id": "p1", "name": "美国", "url": PROXY_URL}],
    })
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml",
                     now_fn=lambda: "2026-07-29T00:00:00Z")
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    return c, app.state.cr.conn


def test_import_with_proxy_check_and_skip_dead(tmp_path, monkeypatch):
    calls = []

    def fake_probe(sk, proxy=None, *, want_email=False):
        calls.append((sk, proxy, want_email))
        if sk == SK1:
            return SessionProbe("alive", "有效", "found@claude.ai", [PRO_ORG])
        if sk == SK2:
            return SessionProbe("alive", "有效", "")
        return SessionProbe("dead", "已失效（HTTP 401）", "")

    monkeypatch.setattr(appmod, "inspect_session", fake_probe)
    c, conn = _client(tmp_path)
    r = c.post("/api/accounts/import", json={
        "text": f"{SK1}\nkeep@x.com----{SK2}\n{SK3}", "proxy_id": "p1"})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["created"] == 2 and body["summary"]["skipped"] == 1
    assert sorted(calls) == sorted([(SK1, PROXY_URL, True), (SK2, PROXY_URL, False),
                                    (SK3, PROXY_URL, True)])
    a = db.get_account(conn, "found@claude.ai")
    assert a["session_key"] == SK1 and a["proxy"] == PROXY_URL
    assert a["proxy_id"] == "p1"  # 绑定代理池条目
    assert a["status"] == "success" and a["check_status"] == "alive"
    assert a["plan"] == "pro" and a["plan_checked_at"] == "2026-07-29T00:00:00Z"
    keep = db.get_account(conn, "keep@x.com")
    assert keep["session_key"] == SK2 and not keep["plan"]  # 没有组织信息就不记套餐
    assert db.get_account(conn, sk_import.placeholder_email(SK3)) is None
    # 结果里不回显完整 sk
    assert SK1 not in r.text


def test_import_without_check_updates_existing_and_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "inspect_session", lambda *a, **k: 1 / 0)
    c, conn = _client(tmp_path)
    db.upsert_account(conn, "old@x.com", "x.com", "", "", None, "success",
                      session_key="sk-old", proxy="http://keep:1")
    r = c.post("/api/accounts/import", json={
        "text": f"old@x.com {SK1}\n{SK2}", "check": False})
    assert r.status_code == 200
    assert r.json()["summary"] == {"created": 1, "updated": 1, "skipped": 0,
                                   "invalid": 0, "duplicate": 0}
    old = db.get_account(conn, "old@x.com")
    assert old["session_key"] == SK1 and old["proxy"] == "http://keep:1"
    ph = db.get_account(conn, sk_import.placeholder_email(SK2))
    assert ph["session_key"] == SK2 and ph["proxy"] == ""


def test_import_unknown_proxy_and_auth(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post("/api/accounts/import", json={"text": SK1, "proxy_id": "nope"})
    assert r.status_code == 400
    c.post("/api/logout")
    c.cookies.clear()
    assert c.post("/api/accounts/import", json={"text": SK1}).status_code == 401
