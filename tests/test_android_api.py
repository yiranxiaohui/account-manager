"""Android 模拟器接口：鉴权、参数校验，以及账号删除/改名时对虚拟设备的处理。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from server import db
from server.app import create_app
from server.config_store import save_config

PROXY = "socks5://u:p@proxy.example:1080"


class FakeAndroid:
    def __init__(self, *a, **k):
        self.running = None
        self.starts = []
        self.stops = 0
        self.touches = 0
        self.deleted = []
        self.renamed = []
        self.installs = 0
        self.error = None

    def status(self):
        return {"running": self.running is not None, "email": self.running,
                "phase": "running" if self.running else None, "kvm": True,
                "install": {"state": "ready", "ready": True}, "envs": [], "last_error": ""}

    def running_email(self):
        return self.running

    def install(self):
        self.installs += 1
        return {"state": "installing"}

    def start(self, *, email, proxy, idle_timeout_s=0):
        from server.android import AndroidBusy
        if self.error is not None:
            raise self.error
        if self.running:
            raise AndroidBusy("已有运行中的 Android 模拟器，请先停止")
        self.starts.append((email, proxy, idle_timeout_s))
        self.running = email
        return self.status()

    def stop(self):
        self.stops += 1
        was = self.running is not None
        self.running = None
        return was

    def touch(self):
        from server.android import AndroidError
        if not self.running:
            raise AndroidError("当前没有运行中的 Android 模拟器")
        self.touches += 1

    def delete_env(self, email):
        self.deleted.append(email)
        return True

    def rename_env(self, old, new):
        self.renamed.append((old, new))
        return True


def _app(tmp_path, monkeypatch, **cfg):
    import server.deps as deps

    monkeypatch.setattr(deps, "AndroidManager", FakeAndroid)
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", **cfg})
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml",
                     now_fn=lambda: "2026-11-19T00:00:00Z")
    c = TestClient(app)
    return app, c


def _login(c):
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200


def _seed(app, email, proxy=PROXY):
    db.create_account(app.state.cr.conn, email, {"proxy": proxy, "proxy_id": "p1" if proxy else ""},
                      created_at="t")


def test_android_routes_require_auth(tmp_path, monkeypatch):
    _, c = _app(tmp_path, monkeypatch)
    assert c.get("/api/android").status_code == 401
    assert c.post("/api/android/start", json={"email": "a@x.com"}).status_code == 401
    assert c.post("/api/android/stop").status_code == 401
    assert c.post("/api/android/install").status_code == 401
    assert c.post("/api/android/heartbeat").status_code == 401


def test_start_uses_account_proxy_and_idle_timeout(tmp_path, monkeypatch):
    app, c = _app(tmp_path, monkeypatch, android_idle_timeout_min=7)
    _login(c)
    _seed(app, "a@x.com")
    r = c.post("/api/android/start", json={"email": "a@x.com"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "a@x.com"
    fake = app.state.cr.android
    assert fake.starts == [("a@x.com", PROXY, 420)]
    assert c.get("/api/android").json()["running"] is True
    assert c.post("/api/android/heartbeat").status_code == 200
    r = c.post("/api/android/start", json={"email": "a@x.com"})
    assert r.status_code == 409
    assert c.post("/api/android/stop").json() == {"ok": True, "stopped": True}
    assert c.post("/api/android/heartbeat").status_code == 409


def test_start_validation(tmp_path, monkeypatch):
    app, c = _app(tmp_path, monkeypatch)
    _login(c)
    _seed(app, "noproxy@x.com", proxy="")
    assert c.post("/api/android/start", json={}).status_code == 400
    assert c.post("/api/android/start", json={"email": "nobody@x.com"}).status_code == 404
    r = c.post("/api/android/start", json={"email": "noproxy@x.com"})
    assert r.status_code == 400 and "没有绑定代理" in r.json()["detail"]
    assert app.state.cr.android.starts == []


def test_start_surfaces_manager_errors(tmp_path, monkeypatch):
    from server.android import AndroidError

    app, c = _app(tmp_path, monkeypatch)
    _login(c)
    _seed(app, "a@x.com")
    app.state.cr.android.error = AndroidError("Android 运行环境尚未安装，请先安装")
    r = c.post("/api/android/start", json={"email": "a@x.com"})
    assert r.status_code == 400 and "尚未安装" in r.json()["detail"]


def test_install_route(tmp_path, monkeypatch):
    app, c = _app(tmp_path, monkeypatch)
    _login(c)
    assert c.post("/api/android/install").json() == {"state": "installing"}
    assert app.state.cr.android.installs == 1


def test_deleting_account_removes_its_env_but_not_while_running(tmp_path, monkeypatch):
    app, c = _app(tmp_path, monkeypatch)
    _login(c)
    for e in ("a@x.com", "b@x.com", "c@x.com"):
        _seed(app, e)
    fake = app.state.cr.android
    fake.running = "a@x.com"
    r = c.delete("/api/accounts/a@x.com")
    assert r.status_code == 409 and "正在运行" in r.json()["detail"]
    r = c.post("/api/accounts/batch-delete", json={"emails": ["a@x.com", "b@x.com"]})
    assert r.status_code == 409
    assert db.get_account(app.state.cr.conn, "b@x.com") is not None
    assert c.delete("/api/accounts/b@x.com").status_code == 200
    assert c.post("/api/accounts/batch-delete", json={"emails": ["c@x.com"]}).json()["deleted"] == 1
    assert fake.deleted == ["b@x.com", "c@x.com"]


def test_renaming_account_moves_env_but_not_while_running(tmp_path, monkeypatch):
    app, c = _app(tmp_path, monkeypatch)
    _login(c)
    _seed(app, "old@x.com")
    fake = app.state.cr.android
    fake.running = "old@x.com"
    assert c.patch("/api/accounts/old@x.com", json={"email": "new@x.com"}).status_code == 409
    fake.running = None
    r = c.patch("/api/accounts/old@x.com", json={"email": "new@x.com"})
    assert r.status_code == 200, r.text
    assert fake.renamed == [("old@x.com", "new@x.com")]


def test_shutdown_stops_emulator(tmp_path, monkeypatch):
    app, c = _app(tmp_path, monkeypatch)
    with TestClient(app):
        pass
    assert app.state.cr.android.stops >= 1
