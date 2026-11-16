"""账号编辑/删除：PATCH /api/accounts/{email} 与 DELETE /api/accounts/{email}。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from server import db
from server.app import create_app
from server.config_store import save_config


POOL = [
    {"id": "p-old", "name": "旧代理", "url": "socks5://old:1"},
    {"id": "p-new", "name": "新代理", "url": "socks5://new:2"},
    {"id": "p-3", "name": "代理三", "url": "socks5://p:3"},
]


def _app(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", "saved_proxies": POOL})
    return create_app(
        data_dir=tmp_path,
        config_path=tmp_path / "config.yaml",
        now_fn=lambda: "2026-07-31T00:00:00Z",
    )


def _seed(app):
    conn = app.state.cr.conn
    db.upsert_account(
        conn, "a@x.com", "x.com", "", "mb1", 1, "success",
        password="pw-a", session_key="sk-ant-a", proxy="socks5://old:1", proxy_id="p-old",
    )
    conn.commit()


def _client(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    return app, c


def test_patch_updates_fields(tmp_path):
    app, c = _client(tmp_path)
    r = c.patch("/api/accounts/a@x.com", json={
        "proxy_id": "p-new", "display_name": "备注",
    })
    assert r.status_code == 200
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["proxy"] == "socks5://new:2" and row["proxy_id"] == "p-new"
    assert row["display_name"] == "备注"
    # 未提交的字段保持不变
    assert row["session_key"] == "sk-ant-a"
    assert row["password"] == "pw-a"
    # 响应带最新行 + 导出文本
    assert r.json()["proxy"] == "socks5://new:2"
    assert r.json()["proxy_id"] == "p-new"
    assert "text" in r.json()


def test_patch_rejects_manual_proxy_address(tmp_path):
    app, c = _client(tmp_path)
    r = c.patch("/api/accounts/a@x.com", json={"proxy": "socks5://manual:9", "display_name": "x"})
    assert r.status_code == 400 and "代理池" in r.json()["detail"]
    r = c.post("/api/accounts", json={"email": "m@x.com", "proxy": "socks5://manual:9"})
    assert r.status_code == 400
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["proxy"] == "socks5://old:1" and row["display_name"] == ""
    assert db.get_account(app.state.cr.conn, "m@x.com") is None


def test_patch_rejects_unknown_proxy_id(tmp_path):
    app, c = _client(tmp_path)
    assert c.patch("/api/accounts/a@x.com", json={"proxy_id": "nope"}).status_code == 400
    assert c.patch("/api/accounts/a@x.com", json={"proxy_id": 1}).status_code == 400
    assert db.get_account(app.state.cr.conn, "a@x.com")["proxy_id"] == "p-old"


def test_patch_can_clear_proxy(tmp_path):
    app, c = _client(tmp_path)
    r = c.patch("/api/accounts/a@x.com", json={"proxy_id": ""})
    assert r.status_code == 200
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["proxy"] == "" and row["proxy_id"] == ""


def test_patch_legacy_empty_proxy_clears(tmp_path):
    app, c = _client(tmp_path)
    assert c.patch("/api/accounts/a@x.com", json={"proxy": ""}).status_code == 200
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["proxy"] == "" and row["proxy_id"] == ""


def test_patch_unchanged_proxy_id_does_not_block_other_fields(tmp_path):
    app, c = _client(tmp_path)
    # 绑定的条目被手工从 config.yaml 删掉后，只改备注不应因代理校验失败
    save_config(tmp_path / "config.yaml", {"saved_proxies": POOL[1:]})
    r = c.patch("/api/accounts/a@x.com", json={"proxy_id": "p-old", "display_name": "改备注"})
    assert r.status_code == 200
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["display_name"] == "改备注" and row["proxy_id"] == "p-old"


def test_patch_ignores_non_editable_and_requires_known_field(tmp_path):
    app, c = _client(tmp_path)
    # status 等不可改；全是未知字段时 400
    r = c.patch("/api/accounts/a@x.com", json={"status": "failed", "foo": "bar"})
    assert r.status_code == 400
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row is not None and row["status"] == "success"


def test_patch_renames_email(tmp_path):
    app, c = _client(tmp_path)
    r = c.patch("/api/accounts/a@x.com", json={"email": " New@Y.com ", "proxy_id": "p-3"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "new@y.com" and body["domain"] == "y.com"
    conn = app.state.cr.conn
    assert db.get_account(conn, "a@x.com") is None
    row = db.get_account(conn, "new@y.com")
    assert row["session_key"] == "sk-ant-a" and row["proxy"] == "socks5://p:3"
    assert row["proxy_id"] == "p-3"


def test_patch_rename_conflict_and_invalid(tmp_path):
    app, c = _client(tmp_path)
    db.upsert_account(app.state.cr.conn, "b@x.com", "x.com", "", "", None, "success")
    assert c.patch("/api/accounts/a@x.com", json={"email": "b@x.com"}).status_code == 409
    assert c.patch("/api/accounts/a@x.com", json={"email": "not-an-email"}).status_code == 400
    assert c.patch("/api/accounts/a@x.com", json={"display_name": 1}).status_code == 400
    assert db.get_account(app.state.cr.conn, "a@x.com")["proxy"] == "socks5://old:1"


def test_create_account(tmp_path):
    app, c = _client(tmp_path)
    r = c.post("/api/accounts", json={
        "email": "Fresh@Z.io", "session_key": " sk-ant-new ", "password": "p w",
        "display_name": "手动", "mail_key": "mk", "mail_base_url": "https://m",
        "proxy_id": "p-new",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "fresh@z.io" and "text" in body
    row = db.get_account(app.state.cr.conn, "fresh@z.io")
    assert row["domain"] == "z.io" and row["status"] == "success"
    assert row["session_key"] == "sk-ant-new" and row["password"] == "p w"
    assert row["created_at"] == "2026-07-31T00:00:00Z"
    assert row["mail_key"] == "mk" and row["mail_base_url"] == "https://m"
    assert row["proxy_id"] == "p-new" and row["proxy"] == "socks5://new:2"
    # 列表里能查到
    assert any(a["email"] == "fresh@z.io" for a in c.get("/api/accounts").json())


def test_create_account_validation_and_conflict(tmp_path):
    app, c = _client(tmp_path)
    assert c.post("/api/accounts", json={"email": "a@x.com"}).status_code == 409
    # 已有账号不被覆盖
    assert db.get_account(app.state.cr.conn, "a@x.com")["session_key"] == "sk-ant-a"
    assert c.post("/api/accounts", json={}).status_code == 400
    assert c.post("/api/accounts", json={"email": "bad"}).status_code == 400
    assert c.post("/api/accounts", json={"email": "n@x.com", "proxy_id": "nope"}).status_code == 400
    assert db.get_account(app.state.cr.conn, "n@x.com") is None
    assert c.post("/api/accounts", content=b"not json",
                  headers={"content-type": "application/json"}).status_code == 400


def test_batch_delete(tmp_path):
    app, c = _client(tmp_path)
    conn = app.state.cr.conn
    db.upsert_account(conn, "b@x.com", "x.com", "", "", None, "success")
    db.upsert_account(conn, "c@x.com", "x.com", "", "", None, "success")
    r = c.post("/api/accounts/batch-delete",
               json={"emails": ["a@x.com", "b@x.com", "b@x.com", "nope@x.com"]})
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert [a["email"] for a in db.list_accounts(conn)] == ["c@x.com"]
    assert c.post("/api/accounts/batch-delete", json={"emails": []}).status_code == 400
    assert c.post("/api/accounts/batch-delete", json={"emails": "a"}).status_code == 400


def test_batch_proxy(tmp_path):
    app, c = _client(tmp_path)
    conn = app.state.cr.conn
    db.upsert_account(conn, "b@x.com", "x.com", "", "", None, "success")
    db.upsert_account(conn, "c@x.com", "x.com", "", "", None, "success")
    r = c.post("/api/accounts/batch-proxy",
               json={"emails": ["a@x.com", "b@x.com", "b@x.com", "nope@x.com"], "proxy_id": "p-3"})
    assert r.status_code == 200 and r.json()["updated"] == 2
    for email in ("a@x.com", "b@x.com"):
        row = db.get_account(conn, email)
        assert row["proxy_id"] == "p-3" and row["proxy"] == "socks5://p:3"
    assert db.get_account(conn, "c@x.com")["proxy_id"] in (None, "")
    r = c.post("/api/accounts/batch-proxy", json={"emails": ["b@x.com"], "proxy_id": ""})
    assert r.status_code == 200
    assert db.get_account(conn, "b@x.com")["proxy"] == ""
    assert c.post("/api/accounts/batch-proxy",
                  json={"emails": ["a@x.com"], "proxy_id": "nope"}).status_code == 400
    assert c.post("/api/accounts/batch-proxy", json={"emails": ["a@x.com"]}).status_code == 400
    assert c.post("/api/accounts/batch-proxy", json={"emails": [], "proxy_id": "p-3"}).status_code == 400
    assert db.get_account(conn, "a@x.com")["proxy_id"] == "p-3"


def test_patch_404_on_missing_account(tmp_path):
    _, c = _client(tmp_path)
    assert c.patch("/api/accounts/nope@x.com", json={"proxy_id": "p-new"}).status_code == 404


def test_delete_account(tmp_path):
    app, c = _client(tmp_path)
    r = c.request("DELETE", "/api/accounts/a@x.com")
    assert r.status_code == 200
    assert db.get_account(app.state.cr.conn, "a@x.com") is None
    assert c.request("DELETE", "/api/accounts/a@x.com").status_code == 404


def test_edit_requires_auth(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)  # 未登录
    assert c.patch("/api/accounts/a@x.com", json={"proxy_id": "p-new"}).status_code == 401
    assert c.request("DELETE", "/api/accounts/a@x.com").status_code == 401
    assert c.post("/api/accounts", json={"email": "n@x.com"}).status_code == 401
    assert c.post("/api/accounts/batch-delete", json={"emails": ["a@x.com"]}).status_code == 401
    assert c.post("/api/accounts/batch-proxy",
                  json={"emails": ["a@x.com"], "proxy_id": "p-new"}).status_code == 401
    assert c.get("/api/proxies/usage").status_code == 401
