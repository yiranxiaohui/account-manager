import os
import threading
import pytest
from server.takeover import TakeoverManager, TakeoverBusy, TakeoverError


def _now():
    return "2026-07-30T00:00:00Z"


class FakeProc:
    def __init__(self, argv, env=None):
        self.argv = argv
        self.env = env
        self.terminated = False
    def poll(self):
        return None
    def terminate(self):
        self.terminated = True
    def kill(self):
        self.terminated = True
    def wait(self, timeout=None):
        return 0


class FakeLauncher:
    def __init__(self):
        self.spawned = []
    def spawn(self, argv, env=None):
        p = FakeProc(argv, env)
        self.spawned.append(p)
        return p


class FakeBrowser:
    def __init__(self):
        self.closed = False
        self.owner_thread = threading.get_ident()
        self.relogin_thread = None
        self.relogin_kwargs = None
    def close(self):
        self.closed = True
    def relogin(self, **kwargs):
        self.relogin_thread = threading.get_ident()
        self.relogin_kwargs = kwargs
        return "sk-new"


class FakeFitter:
    def __init__(self, display, events=None):
        self.display = display
        self.stopped = False
        self._events = events

    def stop(self):
        self.stopped = True
        if self._events is not None:
            self._events.append("fitter.stop")


def _mgr(**kw):
    return TakeoverManager(
        now_fn=_now,
        window_fitter_fn=kw.pop("window_fitter_fn", lambda display: FakeFitter(display)),
        launcher=kw.pop("launcher", FakeLauncher()),
        browser_fn=kw.pop("browser_fn", lambda **k: FakeBrowser()),
        wait_display_fn=kw.pop("wait_display_fn", lambda display: None),
        wait_web_fn=kw.pop("wait_web_fn", lambda host, port: None),
        clear_display_fn=kw.pop("clear_display_fn", lambda display: None),
        **kw,
    )


def test_start_status_stop_sequence():
    launcher = FakeLauncher()
    browsers = []
    def bf(**k):
        b = FakeBrowser(); browsers.append(b); return b
    m = _mgr(launcher=launcher, browser_fn=bf)

    info = m.start(email="a@x.com", session_key="sk", proxy="", idle_timeout_s=999)
    assert info["email"] == "a@x.com"
    assert m.status() == {"running": True, "email": "a@x.com",
                          "started_at": "2026-07-30T00:00:00Z", "mode": "account"}
    xvfb, selkies = launcher.spawned
    # 先起虚拟屏，再让 Selkies 挂上去推流。
    assert xvfb.argv[:2] == ["Xvfb", ":100"]
    xvfb_args = " ".join(xvfb.argv)
    # X server 只在容器本地 UNIX socket 可达。
    assert "-nolisten tcp" in xvfb_args and "-ac" in xvfb_args
    # 屏幕尺寸是可调整上限：Selkies 连上后经 RandR 调到网页窗口尺寸。
    # 固定 1280x900 会把上限锁死，更大的窗口就只能缩放，鼠标坐标随之错位。
    assert "-screen 0 8192x4096x24" in xvfb_args
    from server.window_fit import XVFB_MAX
    assert f"{XVFB_MAX[0]}x{XVFB_MAX[1]}x24" in xvfb_args

    argv = selkies.argv
    # 只绑 localhost、以 /vnc 为前缀，由外层 Nginx + 面板 Cookie 鉴权。
    assert argv[argv.index("--addr") + 1] == "127.0.0.1"
    assert argv[argv.index("--port") + 1] == "14500"
    assert argv[argv.index("--subfolder") + 1] == "/vnc"
    assert argv[argv.index("--mode") + 1] == "websockets"
    assert "--enable-basic-auth=false" in argv
    # 接管用不到、又会扩大暴露面的功能全部关闭。
    for flag in ("--audio-enabled=false", "--microphone-enabled=false",
                 "--webcam-enabled=false", "--gamepad-enabled=false",
                 "--printing-enabled=false", "--file-transfers=none",
                 "--command-enabled=false", "--enable-sharing=false",
                 "--enable-collab=false", "--enable-shared=false",
                 "--second-screen=false"):
        assert flag in argv
    assert "--enable-clipboard=true" in argv
    assert "--enable-resize=true" in argv
    # Selkies 推的是 :100，运行时 socket 放在会话私有目录。
    assert selkies.env["DISPLAY"] == ":100"
    runtime_dir = selkies.env["XDG_RUNTIME_DIR"]
    assert os.path.isdir(runtime_dir)
    assert len(browsers) == 1

    m.stop()
    assert m.status()["running"] is False
    assert browsers[0].closed is True
    assert all(p.terminated for p in launcher.spawned)
    assert not os.path.exists(runtime_dir)


def test_selkies_bin_can_be_overridden(monkeypatch):
    from server.takeover import SELKIES_BIN_ENV, selkies_argv

    monkeypatch.delenv(SELKIES_BIN_ENV, raising=False)
    assert selkies_argv(14500)[0] == "selkies"
    monkeypatch.setenv(SELKIES_BIN_ENV, "/opt/selkies/bin/selkies")
    assert selkies_argv(14500)[0] == "/opt/selkies/bin/selkies"


def test_start_clears_stale_display_before_xvfb():
    events = []

    class RecordingLauncher(FakeLauncher):
        def spawn(self, argv, env=None):
            events.append(f"spawn:{argv[0]}")
            return super().spawn(argv, env)

    m = _mgr(launcher=RecordingLauncher(),
             clear_display_fn=lambda display: events.append(f"clear:{display}"),
             wait_display_fn=lambda display: events.append(f"wait:{display}"))
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    m.stop()
    assert events[:4] == ["clear::100", "spawn:Xvfb", "wait::100", "spawn:selkies"]


def test_teardown_stops_selkies_before_xvfb():
    order = []

    class OrderedProc(FakeProc):
        def terminate(self):
            order.append(self.argv[0])
            super().terminate()

    class OrderedLauncher(FakeLauncher):
        def spawn(self, argv, env=None):
            p = OrderedProc(argv, env)
            self.spawned.append(p)
            return p

    m = _mgr(launcher=OrderedLauncher())
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    m.stop()
    assert order == ["selkies", "Xvfb"]


def test_second_start_while_running_raises_busy():
    m = _mgr()
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    with pytest.raises(TakeoverBusy):
        m.start(email="b@x.com", session_key="sk2", idle_timeout_s=999)
    m.stop()


def test_start_rolls_back_when_browser_fails():
    launcher = FakeLauncher()
    def bad_browser(**k):
        raise RuntimeError("boom")
    m = _mgr(launcher=launcher, browser_fn=bad_browser)
    with pytest.raises(TakeoverError):
        m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    assert all(p.terminated for p in launcher.spawned)
    assert m.status()["running"] is False


def test_stop_is_idempotent():
    m = _mgr()
    m.stop()
    assert m.status()["running"] is False


def test_relogin_runs_on_browser_owner_thread():
    browsers = []

    def bf(**kwargs):
        browser = FakeBrowser()
        browsers.append(browser)
        return browser

    m = _mgr(browser_fn=bf)
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    try:
        session_key = m.relogin(
            email="a@x.com",
            mail_base_url="https://mail.test",
            mail_api_key="ak_child",
            login_timeout=120,
        )
    finally:
        m.stop()

    assert session_key == "sk-new"
    assert browsers[0].relogin_thread == browsers[0].owner_thread
    assert browsers[0].relogin_kwargs["email"] == "a@x.com"


def test_relogin_requires_active_takeover():
    m = _mgr()
    with pytest.raises(TakeoverError, match="没有活动"):
        m.relogin(
            email="a@x.com",
            mail_base_url="https://mail.test",
            mail_api_key="ak_child",
            login_timeout=120,
        )


def test_browser_lifecycle_stays_on_one_thread_across_request_workers():
    """启动和停止可来自不同 API 工作线程，但 Playwright 必须留在同一线程。"""
    browsers = []
    start_errors = []

    class ThreadBoundBrowser:
        def __init__(self):
            self.owner_thread = threading.get_ident()
            self.close_thread = None
            self.closed = False

        def close(self):
            self.close_thread = threading.get_ident()
            if self.close_thread != self.owner_thread:
                raise RuntimeError("browser closed from a different thread")
            self.closed = True

    def browser_fn(**kwargs):
        browser = ThreadBoundBrowser()
        browsers.append(browser)
        return browser

    m = _mgr(browser_fn=browser_fn)

    # 连续两轮模拟异步 start 路由的 asyncio.to_thread 工作线程；stop 则由
    # 当前线程调用。旧实现首轮跨线程关闭失败，第二轮会撞上残留的事件循环。
    for cycle in (1, 2):
        def start_from_worker():
            try:
                m.start(
                    email=f"a{cycle}@x.com", session_key="sk", idle_timeout_s=999,
                )
            except Exception as exc:  # pragma: no cover - asserted below
                start_errors.append(exc)

        start_thread = threading.Thread(target=start_from_worker)
        start_thread.start()
        start_thread.join(timeout=5)

        assert not start_thread.is_alive()
        assert start_errors == []
        m.stop()

    assert len(browsers) == 2
    assert all(browser.closed for browser in browsers)
    assert all(
        browser.close_thread == browser.owner_thread for browser in browsers
    )


def test_idle_timeout_auto_stops_without_heartbeat():
    m = _mgr()
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=0.05)
    for _ in range(50):
        if not m.status()["running"]:
            break
        threading.Event().wait(0.02)
    assert m.status()["running"] is False


def test_heartbeat_extends_idle_timeout():
    m = _mgr()
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=0.08)
    threading.Event().wait(0.05)
    m.touch()
    threading.Event().wait(0.05)
    assert m.status()["running"] is True

    for _ in range(20):
        if not m.status()["running"]:
            break
        threading.Event().wait(0.02)
    assert m.status()["running"] is False


def test_heartbeat_requires_active_session():
    m = _mgr()
    with pytest.raises(TakeoverError, match="没有活动"):
        m.touch()


def test_wait_x_socket_times_out_fast(tmp_path):
    from server.takeover_browser import wait_x_socket
    import pytest
    with pytest.raises(TimeoutError):
        wait_x_socket(":100", timeout=0.2, poll=0.02, sock_dir=str(tmp_path))


def test_wait_x_socket_succeeds_when_present(tmp_path):
    from server.takeover_browser import wait_x_socket
    (tmp_path / "X100").write_text("")  # 伪造 X socket 文件
    wait_x_socket(":100", timeout=0.5, poll=0.02, sock_dir=str(tmp_path))  # 不抛即通过


def test_clear_stale_x_display_removes_files_of_dead_server(tmp_path):
    from server.takeover_browser import clear_stale_x_display

    sock_dir = tmp_path / ".X11-unix"
    sock_dir.mkdir()
    (sock_dir / "X100").write_text("")
    # PID 远超 pid_max，不可能存活
    (tmp_path / ".X100-lock").write_text("     99999999\n")
    clear_stale_x_display(":100", sock_dir=str(sock_dir), lock_dir=str(tmp_path))
    assert not (sock_dir / "X100").exists()
    assert not (tmp_path / ".X100-lock").exists()


def test_clear_stale_x_display_keeps_files_of_live_server(tmp_path):
    import os
    from server.takeover_browser import clear_stale_x_display

    sock_dir = tmp_path / ".X11-unix"
    sock_dir.mkdir()
    (sock_dir / "X100").write_text("")
    (tmp_path / ".X100-lock").write_text(f"{os.getpid():>10}\n")
    clear_stale_x_display(":100", sock_dir=str(sock_dir), lock_dir=str(tmp_path))
    assert (sock_dir / "X100").exists()
    assert (tmp_path / ".X100-lock").exists()


def test_wait_tcp_port_retries_until_selkies_is_ready():
    from server.takeover_browser import wait_tcp_port

    attempts = []

    class FakeConnection:
        def close(self):
            pass

    def connect(address, timeout):
        attempts.append((address, timeout))
        if len(attempts) < 3:
            raise ConnectionRefusedError
        return FakeConnection()

    wait_tcp_port("127.0.0.1", 14500, timeout=0.2, poll=0.01, connect_fn=connect)
    assert len(attempts) == 3


def test_wait_tcp_port_names_the_service_on_timeout():
    from server.takeover_browser import wait_tcp_port

    def refuse(address, timeout):
        raise ConnectionRefusedError

    with pytest.raises(TimeoutError, match="Selkies"):
        wait_tcp_port("127.0.0.1", 14500, timeout=0.05, poll=0.01,
                      connect_fn=refuse, what="Selkies")


def test_start_rolls_back_when_selkies_web_port_never_opens():
    launcher = FakeLauncher()

    def fail_wait(host, port):
        raise TimeoutError(f"{host}:{port}")

    m = _mgr(launcher=launcher, wait_web_fn=fail_wait)
    with pytest.raises(TakeoverError, match="14500"):
        m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    assert all(p.terminated for p in launcher.spawned)


# ---- 窗口自适应（桌面无窗口管理器，浏览器窗口需铺满接管虚拟屏）----


def test_window_fitter_started_for_display_and_stopped_before_browser_close():
    events = []
    fitters = []

    class OrderedBrowser(FakeBrowser):
        def close(self):
            events.append("browser.close")
            super().close()

    def fitter_fn(display):
        events.append("fitter.start")
        f = FakeFitter(display, events)
        fitters.append(f)
        return f

    m = _mgr(browser_fn=lambda **k: OrderedBrowser(), window_fitter_fn=fitter_fn,
             display=":123")
    m.start(email="a@x.com", session_key="sk")
    assert [f.display for f in fitters] == [":123"]
    assert not fitters[0].stopped
    m.stop()
    assert fitters[0].stopped
    # 先停自适应再关浏览器，避免它操作正在销毁的窗口
    assert events == ["fitter.start", "fitter.stop", "browser.close"]


def test_window_fitter_not_started_when_browser_fails():
    started = []

    def bad_browser(**k):
        raise RuntimeError("boom")

    m = _mgr(browser_fn=bad_browser, window_fitter_fn=lambda d: started.append(d))
    with pytest.raises(TakeoverError):
        m.start(email="a@x.com", session_key="sk")
    assert started == []


def test_start_rolls_back_when_window_fitter_fails():
    launcher = FakeLauncher()
    browsers = []

    def bf(**k):
        b = FakeBrowser()
        browsers.append(b)
        return b

    def bad_fitter(display):
        raise RuntimeError("no X")

    m = _mgr(launcher=launcher, browser_fn=bf, window_fitter_fn=bad_fitter)
    with pytest.raises(TakeoverError):
        m.start(email="a@x.com", session_key="sk")
    assert browsers and browsers[0].closed
    assert all(p.terminated for p in launcher.spawned)
    assert m.status()["running"] is False
