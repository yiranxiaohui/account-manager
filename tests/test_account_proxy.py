"""账号代理只能来自代理池：旧数据迁移、地址同步、删除保护、注册绑定。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from server import account_proxy, db, runner
from server.app import create_app
from server.config_store import Config, load_config, save_config

POOL = [
    {"id": "jp", "name": "日本", "url": "socks5h://u:p@1.1.1.1:1080"},
    {"id": "us", "name": "美国", "url": "http://2.2.2.2:8080"},
]


def _pool(path):
    return [dict(e) for e in load_config(path).saved_proxies]


def _seed_legacy(tmp_path, rows):
    """模拟旧版数据库：只有手填的 proxy 地址，没有 proxy_id。"""
    conn = db.init_db(tmp_path / "account-manager.db")
    for email, url in rows:
        db.upsert_account(conn, email, "x.com", "", "", None, "success",
                          session_key="sk", proxy=url)
    conn.close()


def _app(tmp_path):
    return create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml",
                      now_fn=lambda: "2026-11-16T00:00:00Z")


def _login(app):
    c = TestClient(app)
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200
    return c


def test_endpoint_key_treats_socks5_and_socks5h_as_same_proxy():
    assert account_proxy.endpoint_key("socks5://u:p@H.example:1080") == \
        account_proxy.endpoint_key("socks5h://u:p@h.example:1080")
    assert account_proxy.endpoint_key("socks5://u:p%40x@h:1") == ("socks5", "h", 1, "u", "p@x")
    assert account_proxy.endpoint_key("http://h:1") != account_proxy.endpoint_key("https://h:1")
    assert account_proxy.endpoint_key("socks5://u:p@h:1") != \
        account_proxy.endpoint_key("socks5://u:other@h:1")
    assert account_proxy.endpoint_key("not a url") is None


def test_startup_migrates_manual_proxies_into_pool(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", "saved_proxies": POOL})
    _seed_legacy(tmp_path, [
        ("exact@x.com", "http://2.2.2.2:8080"),          # 与代理池地址完全相同
        ("alias@x.com", "socks5://u:p@1.1.1.1:1080"),    # 同一代理，socks5 写法
        ("new1@x.com", "socks5://a:b@3.3.3.3:1080"),     # 代理池里没有
        ("new2@x.com", "socks5://a:b@3.3.3.3:1080"),     # 同上，只建一个条目
        ("bad@x.com", "socks4://user:pw@4.4.4.4:1080"),  # SOCKS4 不支持认证，无法入池
        ("none@x.com", ""),
    ])
    app = _app(tmp_path)
    conn = app.state.cr.conn
    pool = _pool(tmp_path / "config.yaml")
    assert len(pool) == 3
    created = pool[2]
    assert created["url"] == "socks5://a:b@3.3.3.3:1080"
    assert created["name"] == "账号迁移 · 3.3.3.3:1080"
    assert "source" not in created  # 迁移来的条目按手动条目处理

    def row(email):
        return db.get_account(conn, email)

    assert (row("exact@x.com")["proxy_id"], row("exact@x.com")["proxy"]) == ("us", POOL[1]["url"])
    # 等价地址统一成代理池里的写法
    assert (row("alias@x.com")["proxy_id"], row("alias@x.com")["proxy"]) == ("jp", POOL[0]["url"])
    assert row("new1@x.com")["proxy_id"] == row("new2@x.com")["proxy_id"] == created["id"]
    # 无效地址保持原样，不丢数据，等用户在面板改选
    assert row("bad@x.com")["proxy"] == "socks4://user:pw@4.4.4.4:1080"
    assert not row("bad@x.com")["proxy_id"]
    assert row("none@x.com")["proxy"] == "" and not row("none@x.com")["proxy_id"]

    # 幂等：再整理一次没有任何变化
    assert account_proxy.reconcile(conn, tmp_path / "config.yaml") == {"invalid": 1}
    assert len(_pool(tmp_path / "config.yaml")) == 3

    c = _login(app)
    res = c.get("/api/proxies/usage").json()
    assert res["usage"] == {"us": 1, "jp": 1, created["id"]: 2}
    # 悬浮提示用：每个条目绑定的账号列表（不含 sessionKey 等凭据）
    assert [a["email"] for a in res["accounts"][created["id"]]] == ["new1@x.com", "new2@x.com"]
    assert res["accounts"]["us"] == [
        {"email": "exact@x.com", "display_name": "", "check_status": ""},
    ]
    assert set(res["accounts"]) == set(res["usage"])


def test_migration_names_do_not_collide(tmp_path):
    save_config(tmp_path / "config.yaml", {"saved_proxies": [
        {"id": "x", "name": "账号迁移 · 5.5.5.5:1080", "url": "http://9.9.9.9:1"},
    ]})
    _seed_legacy(tmp_path, [("a@x.com", "socks5://5.5.5.5:1080")])
    _app(tmp_path)
    names = [e["name"] for e in _pool(tmp_path / "config.yaml")]
    assert names == ["账号迁移 · 5.5.5.5:1080", "账号迁移 · 5.5.5.5:1080 #2"]


def test_pool_edit_propagates_to_bound_accounts(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", "saved_proxies": POOL})
    _seed_legacy(tmp_path, [("a@x.com", POOL[0]["url"])])
    app = _app(tmp_path)
    c = _login(app)
    edited = [{**POOL[0], "url": "socks5h://u:p@1.1.1.1:2080"}, POOL[1]]
    assert c.put("/api/config", json={"saved_proxies": edited}).status_code == 200
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["proxy_id"] == "jp" and row["proxy"] == "socks5h://u:p@1.1.1.1:2080"


def test_used_pool_entry_cannot_be_deleted(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", "saved_proxies": POOL})
    _seed_legacy(tmp_path, [("a@x.com", POOL[0]["url"]), ("b@x.com", POOL[0]["url"])])
    app = _app(tmp_path)
    c = _login(app)
    r = c.put("/api/config", json={"saved_proxies": [POOL[1]]})
    assert r.status_code == 409
    assert "日本" in r.json()["detail"] and "2 个账号" in r.json()["detail"]
    assert len(_pool(tmp_path / "config.yaml")) == 2
    # 未被使用的条目可以删
    assert c.put("/api/config", json={"saved_proxies": [POOL[0]]}).status_code == 200
    assert [e["id"] for e in _pool(tmp_path / "config.yaml")] == ["jp"]
    # 账号换走后即可删除
    r = c.post("/api/accounts/batch-proxy", json={"emails": ["a@x.com", "b@x.com"], "proxy_id": ""})
    assert r.status_code == 200
    assert c.put("/api/config", json={"saved_proxies": []}).status_code == 200


def test_dangling_binding_is_rebound_by_address(tmp_path):
    save_config(tmp_path / "config.yaml", {"saved_proxies": POOL})
    conn = db.init_db(tmp_path / "account-manager.db")
    db.upsert_account(conn, "a@x.com", "x.com", "", "", None, "success",
                      proxy="http://2.2.2.2:8080", proxy_id="gone")
    db.upsert_account(conn, "b@x.com", "x.com", "", "", None, "success", proxy_id="gone2")
    stats = account_proxy.reconcile(conn, tmp_path / "config.yaml")
    assert stats == {"bound": 1, "cleared": 1}
    assert db.get_account(conn, "a@x.com")["proxy_id"] == "us"
    assert db.get_account(conn, "b@x.com")["proxy_id"] == ""


def test_bind_url_reuses_or_creates_entry(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(path, {"saved_proxies": POOL})
    assert account_proxy.bind_url(path, "", label="注册") == ("", "")
    assert account_proxy.bind_url(path, "socks5://u:p@1.1.1.1:1080", label="注册") == \
        ("jp", POOL[0]["url"])
    pid, url = account_proxy.bind_url(path, "socks5://7.7.7.7:1080", label="注册")
    assert url == "socks5://7.7.7.7:1080"
    assert _pool(path)[-1] == {"id": pid, "name": "注册 · 7.7.7.7:1080", "url": url}
    assert account_proxy.bind_url(path, url, label="注册") == (pid, url)
    with pytest.raises(ValueError):
        account_proxy.bind_url(path, "ftp://nope", label="注册")


def _wait(conn, rid):
    for _ in range(100):
        if db.get_run(conn, rid)["status"] != "running":
            return
        time.sleep(0.02)


def test_registration_binds_account_to_pool_entry(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(path, {"saved_proxies": POOL})
    conn = db.init_db(tmp_path / "t.db")
    r = runner.Runner(conn, tmp_path, lambda: "t",
                      proxy_binder=lambda url: account_proxy.bind_url(path, url, label="注册"))

    def flow_with(proxy):
        def fake_flow(*, email=None, domain=None, config=None, **kw):
            return {"email": email, "sessionKey": "sk-ant-x", "proxy": proxy}
        return fake_flow

    _wait(conn, r.start(Config(), email="pool@x.com", flow_fn=flow_with(POOL[1]["url"])))
    row = db.get_account(conn, "pool@x.com")
    assert (row["proxy_id"], row["proxy"]) == ("us", POOL[1]["url"])

    # 设置页的固定注册代理 / 3x-ui 专属代理不在代理池时自动加入
    _wait(conn, r.start(Config(), email="new@x.com", flow_fn=flow_with("socks5://8.8.8.8:1080")))
    row = db.get_account(conn, "new@x.com")
    entry = _pool(path)[-1]
    assert entry["url"] == "socks5://8.8.8.8:1080" and row["proxy_id"] == entry["id"]

    # 直连注册不绑定
    _wait(conn, r.start(Config(), email="direct@x.com", flow_fn=flow_with("")))
    row = db.get_account(conn, "direct@x.com")
    assert row["proxy"] == "" and row["proxy_id"] == ""


def test_registration_keeps_account_when_binding_fails(tmp_path):
    conn = db.init_db(tmp_path / "t.db")

    def broken(url):
        raise RuntimeError("config unwritable")

    r = runner.Runner(conn, tmp_path, lambda: "t", proxy_binder=broken)
    rid = r.start(Config(), email="a@x.com", flow_fn=lambda **kw: {
        "email": "a@x.com", "sessionKey": "sk", "proxy": "socks5://h:1"})
    _wait(conn, rid)
    assert db.get_run(conn, rid)["status"] == "success"
    row = db.get_account(conn, "a@x.com")
    assert row["proxy"] == "socks5://h:1" and row["proxy_id"] == ""


def test_sub2api_sync_updates_bound_accounts(tmp_path, monkeypatch):
    import httpx

    from server import sub2api

    items = [{"id": 1, "name": "s1", "protocol": "socks5", "host": "6.6.6.6", "port": 1080,
              "username": "u", "password": "p", "status": "active"}]

    def handle(req):
        return httpx.Response(200, json={"code": 0, "data": items})

    monkeypatch.setattr(sub2api, "TRANSPORT", httpx.MockTransport(handle))
    save_config(tmp_path / "config.yaml", {
        "panel_password": "pw", "sub2api_base_url": "http://s2a.test", "sub2api_admin_key": "k",
    })
    app = _app(tmp_path)
    c = _login(app)
    assert c.post("/api/sub2api/proxies/import", json={"ids": [1]}).status_code == 200
    entry = _pool(tmp_path / "config.yaml")[0]
    db.upsert_account(app.state.cr.conn, "a@x.com", "x.com", "", "", None, "success")
    assert c.patch("/api/accounts/a@x.com", json={"proxy_id": entry["id"]}).status_code == 200
    items[0]["port"] = 2080
    assert c.post("/api/sub2api/proxies/sync").json()["summary"]["updated"] == 1
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["proxy_id"] == entry["id"] and row["proxy"] == "socks5://u:p@6.6.6.6:2080"
