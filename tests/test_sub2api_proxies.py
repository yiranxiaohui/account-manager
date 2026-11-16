"""sub2api 代理池对接：列出 sub2api 代理、导入本地代理池、同步已导入条目。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from server import sub2api, sub2api_proxies
from server.app import create_app
from server.config_store import load_config, save_config

ADMIN_KEY = "admin-secret-key"
BASE = "http://s2a.test:8080"
NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def _proxy(id_, name, *, protocol="socks5", host=None, port=1080, username="u", password="p@ss:w/rd",
           status="active", expires_at=None, scoped=False, **extra):
    item = {"id": id_, "name": name, "protocol": protocol, "host": host or f"10.0.0.{id_}",
            "port": port, "status": status, "expires_at": expires_at,
            "account_count": 2, "latency_ms": 120, "latency_status": "success",
            "country": "日本", "country_code": "jp", "city": "东京", **extra}
    if not scoped:
        item["username"] = username
        if password:
            item["password"] = password
    return item


class FakeSub2API:
    def __init__(self, proxies):
        self.proxies = proxies
        self.calls: list[httpx.Request] = []

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if req.headers.get("x-api-key") != ADMIN_KEY:
            return httpx.Response(401, json={"code": "UNAUTHORIZED", "message": "x"})
        if req.url.path == "/api/v1/admin/proxies/all" and req.method == "GET":
            assert req.url.params["with_count"] == "true"
            return httpx.Response(200, json={"code": 0, "data": self.proxies})
        return httpx.Response(404, text="404 page not found")


@pytest.fixture
def env(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {
        "panel_password": "pw", "sub2api_base_url": BASE + "/api/v1/",
        "sub2api_admin_key": ADMIN_KEY,
        "saved_proxies": [{"id": "m1", "name": "手动", "url": "http://h:8080"}],
    })
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml",
                     now_fn=lambda: "2026-10-07T00:00:00Z")
    fake = FakeSub2API([
        _proxy(1, "日本-1"),
        _proxy(2, "无认证", protocol="http", username="", password=""),
        _proxy(3, "过期", expires_at="2026-10-01T00:00:00.123456789Z"),
        _proxy(4, "重复", protocol="http", host="h", port=8080, username="", password=""),
        _proxy(5, "v6", protocol="socks5h", host="2001:db8::1"),
    ])
    monkeypatch.setattr(sub2api, "TRANSPORT", httpx.MockTransport(fake.handle))
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    return app, c, fake


def _saved(app):
    return list(load_config(app.state.cr.config_path).saved_proxies)


def test_build_url_quotes_credentials_and_ipv6():
    assert sub2api_proxies.build_url(_proxy(1, "x")) == "socks5://u:p%40ss%3Aw%2Frd@10.0.0.1:1080"
    assert sub2api_proxies.build_url(_proxy(2, "x", protocol="HTTP", username="", password="")) \
        == "http://10.0.0.2:1080"
    assert sub2api_proxies.build_url(_proxy(3, "x", protocol="socks5h", host="2001:db8::1")) \
        == "socks5h://u:p%40ss%3Aw%2Frd@[2001:db8::1]:1080"
    for bad in (_proxy(4, "x", protocol="socks4"), _proxy(5, "x", host="a/b"),
                _proxy(6, "x", port=0), _proxy(7, "x", host="h@evil")):
        with pytest.raises(ValueError):
            sub2api_proxies.build_url(bad)


def test_list_hides_credentials_and_marks_state(env):
    _, c, fake = env
    r = c.get("/api/sub2api/proxies")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["base_url"] == BASE and body["credentials_visible"] is True
    by_id = {p["id"]: p for p in body["proxies"]}
    assert by_id[1]["importable"] and by_id[1]["has_auth"] is True
    assert by_id[1]["location"] == "日本 · 东京" and by_id[1]["country_code"] == "JP"
    assert by_id[1]["latency_ms"] == 120 and by_id[1]["account_count"] == 2
    assert by_id[2]["has_auth"] is False
    assert by_id[3]["expired"] and not by_id[3]["importable"] and "过期" in by_id[3]["reason"]
    assert by_id[3]["expires_at"] == "2026-10-01T00:00:00Z"
    assert by_id[4]["duplicate_of"] == "手动"
    assert by_id[5]["importable"]
    assert "p@ss" not in r.text and "p%40ss" not in r.text and '"username"' not in r.text
    assert ADMIN_KEY not in r.text
    assert str(fake.calls[0].url).startswith(BASE + "/api/v1/admin/proxies/all?")


def test_import_creates_entries_and_reports(env):
    app, c, _ = env
    r = c.post("/api/sub2api/proxies/import", json={"ids": [1, 2, 3, 4, 99, 1]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["summary"] == {"created": 2, "updated": 0, "unchanged": 0, "skipped": 1,
                              "failed": 1, "missing": 1}
    assert [x["result"] for x in out["results"]] == ["created", "created", "failed", "skipped", "missing"]
    assert "p@ss" not in r.text and "p%40ss" not in r.text
    saved = _saved(app)
    assert saved[0] == {"id": "m1", "name": "手动", "url": "http://h:8080"}
    first = saved[1]
    assert first["name"] == "日本-1" and first["url"] == "socks5://u:p%40ss%3Aw%2Frd@10.0.0.1:1080"
    assert first["source"] == "sub2api" and first["source_id"] == 1
    assert first["source_base_url"] == BASE and first["location"] == "日本 · 东京"
    assert first["id"].startswith("s2a-") and saved[2]["url"] == "http://10.0.0.2:1080"
    # 已导入的再次出现在列表里时标为已导入
    listed = {p["id"]: p for p in c.get("/api/sub2api/proxies").json()["proxies"]}
    assert listed[1]["local"] == {"id": first["id"], "name": "日本-1", "outdated": False}


def test_reimport_updates_url_but_keeps_local_name_and_id(env):
    app, c, fake = env
    c.post("/api/sub2api/proxies/import", json={"ids": [1]})
    entry = _saved(app)[1]
    renamed = [*_saved(app)[:1], {**entry, "name": "我的日本"}]
    assert c.put("/api/config", json={"saved_proxies": renamed}).status_code == 200
    fake.proxies[0] = _proxy(1, "日本-1-改", password="new", host="10.9.9.9")
    listed = {p["id"]: p for p in c.get("/api/sub2api/proxies").json()["proxies"]}
    assert listed[1]["local"]["outdated"] is True
    r = c.post("/api/sub2api/proxies/import", json={"ids": [1]})
    assert r.json()["results"][0]["result"] == "updated"
    after = _saved(app)[1]
    assert after["id"] == entry["id"] and after["name"] == "我的日本"
    assert after["url"] == "socks5://u:new@10.9.9.9:1080"
    r = c.post("/api/sub2api/proxies/import", json={"ids": [1]})
    assert r.json()["results"][0]["result"] == "unchanged"


def test_duplicate_names_get_suffix(env):
    app, c, fake = env
    fake.proxies.append(_proxy(6, "日本-1", host="10.0.0.66"))
    c.post("/api/sub2api/proxies/import", json={"ids": [1, 6]})
    assert [p["name"] for p in _saved(app)[1:]] == ["日本-1", "日本-1 #6"]


def test_sync_updates_linked_and_reports_missing(env):
    app, c, fake = env
    assert c.post("/api/sub2api/proxies/sync").json()["summary"]["updated"] == 0
    assert fake.calls == []  # 没有已导入条目时不请求 sub2api
    c.post("/api/sub2api/proxies/import", json={"ids": [1, 2]})
    fake.proxies = [_proxy(1, "日本-1", port=2080)]
    r = c.post("/api/sub2api/proxies/sync")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["summary"]["updated"] == 1 and out["summary"]["missing"] == 1
    missing = next(x for x in out["results"] if x["result"] == "missing")
    saved = _saved(app)
    assert missing["local_id"] == saved[2]["id"]  # 只报告，不删除
    assert saved[1]["url"].endswith(":2080") and len(saved) == 3


def test_scoped_key_cannot_import(env):
    app, c, fake = env
    fake.proxies = [_proxy(1, "受限", scoped=True)]
    body = c.get("/api/sub2api/proxies").json()
    assert body["credentials_visible"] is False
    assert body["proxies"][0]["importable"] is False and body["proxies"][0]["has_auth"] is None
    r = c.post("/api/sub2api/proxies/import", json={"ids": [1]})
    assert r.json()["summary"]["failed"] == 1 and len(_saved(app)) == 1


def test_import_validation_and_errors(env):
    app, c, _ = env
    for body in ({}, {"ids": []}, {"ids": ["1"]}, {"ids": [0]}, {"ids": [True]}):
        assert c.post("/api/sub2api/proxies/import", json=body).status_code == 400
    assert c.post("/api/sub2api/proxies/import", content=b"[").status_code == 400
    save_config(app.state.cr.config_path, {"sub2api_admin_key": "wrong"})
    r = c.get("/api/sub2api/proxies")
    assert r.status_code == 502 and "API Key 无效" in r.json()["detail"]
    save_config(app.state.cr.config_path, {"sub2api_base_url": ""})
    assert c.get("/api/sub2api/proxies").status_code == 400


def test_routes_require_auth(env):
    app, _, _ = env
    anon = TestClient(app)
    assert anon.get("/api/sub2api/proxies").status_code == 401
    assert anon.post("/api/sub2api/proxies/import", json={"ids": [1]}).status_code == 401
    assert anon.post("/api/sub2api/proxies/sync").status_code == 401
    assert anon.post("/api/proxies/test", json={"url": "http://h:1"}).status_code == 401


def test_saved_proxy_source_validation(tmp_path):
    path = tmp_path / "config.yaml"
    base = {"id": "a", "name": "A", "url": "http://h:1"}
    cfg = save_config(path, {"saved_proxies": [
        {**base, "source": "sub2api", "source_id": 3, "source_base_url": BASE, "location": "日本",
         "junk": "dropped"},
        {**base, "id": "b", "source": "manual"},
    ]})
    assert cfg.saved_proxies[0] == {**base, "source": "sub2api", "source_id": 3,
                                    "source_base_url": BASE, "location": "日本"}
    assert cfg.saved_proxies[1] == {**base, "id": "b"}
    for bad in ({"source": "other"}, {"source": "sub2api", "source_id": "3", "source_base_url": BASE},
                {"source": "sub2api", "source_id": 3}):
        with pytest.raises(ValueError):
            save_config(path, {"saved_proxies": [{**base, **bad}]})


def test_proxy_test_endpoint(env, monkeypatch):
    app, c, _ = env
    seen = []

    def fake_probe(url):
        seen.append(url)
        return {"ok": True, "ip": "1.2.3.4", "country": "JP", "country_code": "JP", "region": "",
                "city": "Tokyo", "timezone": "Asia/Tokyo", "org": "", "latency_ms": 80, "error": ""}

    from account_manager import proxy_probe
    monkeypatch.setattr(proxy_probe, "probe_proxy", fake_probe)
    r = c.post("/api/proxies/test", json={"id": "m1"})
    assert r.status_code == 200 and r.json()["ip"] == "1.2.3.4"
    assert r.json()["tested_at"] == "2026-10-07T00:00:00Z"
    assert c.post("/api/proxies/test", json={"url": "socks5://u:p@h:1"}).status_code == 200
    assert seen == ["http://h:8080", "socks5://u:p@h:1"]
    assert c.post("/api/proxies/test", json={"id": "nope"}).status_code == 400
    assert c.post("/api/proxies/test", json={"url": "not-a-proxy"}).status_code == 400
    assert c.post("/api/proxies/test", json={}).status_code == 400
    assert json.dumps(r.json())  # 可序列化
