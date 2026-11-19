"""AndroidManager：会话生命周期（全部外部副作用都用假实现替换）。"""
from __future__ import annotations

import threading
import time

import pytest

from server import android_sdk as sdk
from server.android import (
    CONSOLE_PORT,
    DISPLAY,
    SERIAL,
    WEB_PORT,
    WEB_SUBFOLDER,
    AndroidBusy,
    AndroidError,
    OWNER_ENV,
    AndroidManager,
    emulator_argv,
    owned_pids,
    reap_owned,
)

PROXY = "socks5://u:p@proxy.example:1080"


class FakeProc:
    def __init__(self, argv, env=None):
        self.argv = argv
        self.env = env
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def exit(self, code=0):
        self.returncode = code

    def terminate(self):
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout or 0)
        while self.returncode is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if self.returncode is None:
            raise TimeoutError
        return self.returncode


class FakeLauncher:
    def __init__(self):
        self.spawned = []

    def spawn(self, argv, env=None, stdout=None):
        p = FakeProc(argv, env)
        p.stdout = stdout
        self.spawned.append(p)
        return p

    def by_name(self, name):
        return [p for p in self.spawned if name in str(p.argv[0])]


class FakeInstaller:
    def __init__(self, ready=True):
        self._ready = ready
        self.starts = 0

    def ready(self):
        return self._ready

    def status(self):
        return {"state": "ready" if self._ready else "missing", "ready": self._ready}

    def start(self):
        self.starts += 1
        return self.status()


class FakeAdb:
    def __init__(self, boots_after=1, launcher=None):
        self.boots_after = boots_after
        self.checks = 0
        self.shutdowns = []
        self.identities = []
        self.keys = 0
        self.launcher = launcher

    def ensure_key(self):
        self.keys += 1

    def boot_completed(self, serial):
        self.checks += 1
        return self.boots_after is not None and self.checks >= self.boots_after

    def shutdown(self, serial):
        self.shutdowns.append(serial)
        # 真实模拟器收到 emu kill 后自行退出
        if self.launcher is not None:
            for p in self.launcher.by_name("emulator"):
                p.exit(0)

    def apply_identity(self, serial, properties):
        self.identities.append((serial, properties))


class FakeGateway:
    def __init__(self, proxy):
        self.proxy = proxy
        self.stopped = False
        self.http_proxy_url = "http://android:tok@127.0.0.1:40000"
        self.dns_host = "127.0.53.1"

    def stop(self):
        self.stopped = True


class FakeFitter:
    def __init__(self, display):
        self.display = display
        self.stopped = False

    def stop(self):
        self.stopped = True


def _mgr(tmp_path, **kw):
    launcher = kw.pop("launcher", None) or FakeLauncher()
    adb = kw.pop("adb", None) or FakeAdb(launcher=launcher)
    gateways = []
    fitters = []

    def gateway_fn(proxy):
        g = FakeGateway(proxy)
        gateways.append(g)
        return g

    def fitter_fn(display):
        f = FakeFitter(display)
        fitters.append(f)
        return f

    m = AndroidManager(
        tmp_path, now_fn=lambda: "2026-11-19T00:00:00Z", launcher=launcher,
        installer=kw.pop("installer", None) or FakeInstaller(), adb=adb,
        gateway_fn=kw.pop("gateway_fn", gateway_fn),
        timezone_fn=kw.pop("timezone_fn", lambda proxy: "Asia/Tokyo"),
        wait_display_fn=lambda display: None, wait_web_fn=lambda host, port: None,
        clear_display_fn=lambda display: None, window_fitter_fn=fitter_fn,
        kvm_fn=kw.pop("kvm_fn", lambda: True), reap_fn=kw.pop("reap_fn", lambda owner: 0),
        poll_interval=0.01, **kw,
    )
    m.test_launcher, m.test_adb, m.test_gateways, m.test_fitters = launcher, adb, gateways, fitters
    return m


def _wait_phase(m, phase, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = m.status()
        if st["phase"] == phase or (phase is None and not st["running"]):
            return st
        time.sleep(0.01)
    raise AssertionError(f"phase 没到 {phase}：{m.status()}")


def test_start_boots_emulator_through_account_proxy(tmp_path):
    m = _mgr(tmp_path)
    st = m.start(email="a@x.com", proxy=PROXY, idle_timeout_s=0)
    assert st["running"] is True and st["email"] == "a@x.com"
    st = _wait_phase(m, "running")
    assert st["timezone"] == "Asia/Tokyo"
    assert st["envs"] == ["a@x.com"]
    launcher = m.test_launcher
    assert [p.argv[0] for p in launcher.spawned][:2] == ["Xvfb", launcher.spawned[1].argv[0]]
    xvfb = launcher.by_name("Xvfb")[0]
    assert xvfb.argv[1] == DISPLAY
    selkies = launcher.spawned[1]
    assert selkies.argv[selkies.argv.index("--port") + 1] == str(WEB_PORT)
    assert selkies.argv[selkies.argv.index("--subfolder") + 1] == WEB_SUBFOLDER
    assert selkies.env["DISPLAY"] == DISPLAY
    emu = launcher.spawned[2]
    argv = emu.argv
    assert argv[0].endswith("sdk/emulator/emulator")
    assert argv[argv.index("-avd") + 1] == sdk.avd_name("a@x.com")
    assert argv[argv.index("-http-proxy") + 1] == "http://android:tok@127.0.0.1:40000"
    assert argv[argv.index("-dns-server") + 1] == "127.0.53.1"
    assert argv[argv.index("-timezone") + 1] == "Asia/Tokyo"
    assert argv[argv.index("-ports") + 1] == f"{CONSOLE_PORT},{CONSOLE_PORT + 1}"
    assert "-no-snapshot" in argv and "-no-window" not in argv
    assert emu.env["DISPLAY"] == DISPLAY
    assert emu.env["ANDROID_AVD_HOME"].endswith("android/avd")
    assert "http_proxy" not in emu.env
    assert emu.stdout is not None and emu.stdout.name.endswith("android/emulator.log")
    assert selkies.stdout is None  # 只有模拟器的输出改写到文件
    # 会话拉起的进程都带归属标记，面板崩溃重启后据此回收孤儿进程。
    owner = str((tmp_path / "android").resolve())
    assert all(p.env[OWNER_ENV] == owner for p in launcher.spawned)
    assert m.test_gateways[0].proxy == PROXY
    assert m.test_fitters[0].display == DISPLAY
    assert m.test_adb.keys == 1
    assert [serial for serial, _ in m.test_adb.identities] == [SERIAL]
    props = dict(m.test_adb.identities[0][1])
    identity = sdk.device_identity("a@x.com")
    assert props["ro.product.model"] == identity.model
    assert props["ro.serialno"] == identity.serial
    assert props["ro.boot.verifiedbootstate"] == "green"
    m.stop()


def test_stop_shuts_guest_down_then_releases_everything(tmp_path):
    m = _mgr(tmp_path)
    m.start(email="a@x.com", proxy=PROXY)
    _wait_phase(m, "running")
    assert m.stop() is True
    st = m.status()
    assert st["running"] is False and st["last_error"] == ""
    assert m.test_adb.shutdowns == [SERIAL]
    assert all(p.poll() is not None for p in m.test_launcher.spawned)
    assert m.test_gateways[0].stopped and m.test_fitters[0].stopped
    assert m.stop() is False  # 幂等


def test_second_start_is_busy(tmp_path):
    m = _mgr(tmp_path)
    m.start(email="a@x.com", proxy=PROXY)
    with pytest.raises(AndroidBusy):
        m.start(email="b@x.com", proxy=PROXY)
    m.stop()


@pytest.mark.parametrize("kw, start_kw, message", [
    ({"kvm_fn": lambda: False}, {}, "/dev/kvm"),
    ({"installer": FakeInstaller(ready=False)}, {}, "尚未安装"),
    ({}, {"proxy": ""}, "没有绑定代理"),
    ({}, {"proxy": "socks4://h:1080"}, "暂不支持"),
])
def test_start_preconditions(tmp_path, kw, start_kw, message):
    m = _mgr(tmp_path, **kw)
    with pytest.raises(AndroidError, match=message):
        m.start(**{"email": "a@x.com", "proxy": PROXY, **start_kw})
    assert m.status()["running"] is False
    assert m.test_launcher.spawned == []


def test_emulator_crash_during_boot_is_reported_and_cleaned(tmp_path):
    launcher = FakeLauncher()
    adb = FakeAdb(boots_after=None)
    m = _mgr(tmp_path, launcher=launcher, adb=adb)
    m.start(email="a@x.com", proxy=PROXY)
    _wait_phase(m, "booting")
    launcher.by_name("emulator")[0].exit(1)
    st = _wait_phase(m, None)
    assert "模拟器进程已退出" in st["last_error"]
    assert all(p.poll() is not None for p in launcher.spawned)
    assert m.test_gateways[0].stopped


def test_boot_timeout(tmp_path):
    m = _mgr(tmp_path, adb=FakeAdb(boots_after=None), boot_timeout_s=0.05)
    m.start(email="a@x.com", proxy=PROXY)
    st = _wait_phase(m, None)
    assert "等待系统启动超时" in st["last_error"]


def test_emulator_exit_while_running_ends_session(tmp_path):
    m = _mgr(tmp_path)
    m.start(email="a@x.com", proxy=PROXY)
    _wait_phase(m, "running")
    m.test_launcher.by_name("emulator")[0].exit(0)
    st = _wait_phase(m, None)
    assert "模拟器已退出" in st["last_error"]
    assert m.test_fitters[0].stopped


def test_stop_during_boot_cancels_quietly(tmp_path):
    launcher = FakeLauncher()
    m = _mgr(tmp_path, launcher=launcher, adb=FakeAdb(boots_after=None, launcher=launcher))
    m.start(email="a@x.com", proxy=PROXY)
    _wait_phase(m, "booting")
    m.stop()
    st = m.status()
    assert st["running"] is False and st["last_error"] == ""
    assert all(p.poll() is not None for p in m.test_launcher.spawned)


def test_stop_before_gateway_is_ready_releases_late_resources(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    gateways = []

    def slow_gateway(proxy):
        entered.set()
        release.wait(5)
        g = FakeGateway(proxy)
        gateways.append(g)
        return g

    m = _mgr(tmp_path, gateway_fn=slow_gateway)
    m.start(email="a@x.com", proxy=PROXY)
    assert entered.wait(5)
    stopper = threading.Thread(target=m.stop)
    stopper.start()
    time.sleep(0.05)
    release.set()
    stopper.join(10)
    deadline = time.monotonic() + 5
    while not gateways or not gateways[0].stopped:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert m.test_launcher.spawned == []
    assert m.status()["running"] is False


def test_idle_timeout_stops_and_heartbeat_extends(tmp_path):
    m = _mgr(tmp_path)
    m.start(email="a@x.com", proxy=PROXY, idle_timeout_s=0.3)
    _wait_phase(m, "running")
    for _ in range(4):
        time.sleep(0.15)
        m.touch()
    assert m.status()["running"] is True
    _wait_phase(m, None, timeout=3)
    with pytest.raises(AndroidError):
        m.touch()


def test_env_changes_are_blocked_while_that_account_runs(tmp_path):
    m = _mgr(tmp_path)
    sdk.ensure_avd(m.paths, "b@x.com")
    m.start(email="a@x.com", proxy=PROXY)
    _wait_phase(m, "running")
    with pytest.raises(AndroidBusy):
        m.delete_env("a@x.com")
    with pytest.raises(AndroidBusy):
        m.rename_env("a@x.com", "c@x.com")
    assert m.delete_env("b@x.com") is True
    assert m.running_email() == "a@x.com"
    m.stop()
    assert m.rename_env("a@x.com", "c@x.com") is True
    assert m.status()["envs"] == ["c@x.com"]


def test_install_delegates_to_installer(tmp_path):
    installer = FakeInstaller(ready=False)
    m = _mgr(tmp_path, installer=installer)
    m.install()
    assert installer.starts == 1
    assert m.status()["install"]["state"] == "missing"


def test_emulator_argv_without_timezone(tmp_path):
    paths = sdk.AndroidPaths(tmp_path)
    argv = emulator_argv(paths, "acct_x", http_proxy="http://a:b@127.0.0.1:1",
                         dns_host="127.0.53.1", timezone=None)
    assert "-timezone" not in argv
    assert "-no-nested-warnings" in argv
    assert argv[argv.index("-gpu") + 1] == "swiftshader_indirect"


def test_stale_processes_are_reaped_on_init_and_before_boot(tmp_path):
    calls = []
    m = _mgr(tmp_path, reap_fn=lambda owner: calls.append(owner) or 0)
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls == [m.owner]
    m.start(email="a@x.com", proxy=PROXY)
    _wait_phase(m, "running")
    assert calls == [m.owner, m.owner]
    m.stop()


def test_reap_owned_only_kills_marked_processes(tmp_path):
    import os
    import subprocess
    import sys

    owner = str(tmp_path)
    code = "import time; time.sleep(60)"
    marked = subprocess.Popen([sys.executable, "-c", code],
                              env={**os.environ, OWNER_ENV: owner})
    other = subprocess.Popen([sys.executable, "-c", code],
                             env={**os.environ, OWNER_ENV: owner + "-other"})
    try:
        deadline = time.monotonic() + 5
        while marked.pid not in owned_pids(owner) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert owned_pids(owner) == [marked.pid]
        assert reap_owned(owner, timeout=5) == 1
        assert marked.wait(timeout=5) is not None
        assert other.poll() is None
    finally:
        for proc in (marked, other):
            if proc.poll() is None:
                proc.kill()
                proc.wait()
