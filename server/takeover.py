"""接管会话：注入 sessionKey 的已登录 claude.ai 浏览器 + Selkies，供网页接管。

与注册流程（server/runner.py）平级、各用各的屏：注册自管 :110 起的 Xvfb
（account_manager/display.py），接管自管 :100 的 Xvfb，再由 Selkies 把这块屏以
H.264 视频流经 WebSocket 推给网页客户端。Selkies 按网页窗口实时调整屏幕分辨率，
支持双向剪贴板，客户端断线自动重连。单例：同一时刻只允许一个接管会话。

两种模式：``account`` 注入已有账号的 sessionKey 打开已登录页面；``manual``
不注入 Cookie、直接打开登录页，由用户在接管画面里手动登录任意邮箱，之后
面板轮询 ``read_session_key`` 把新 Cookie 取出入库。Selkies 只绑 localhost，不对外暴露端口，
由同容器 Nginx 反代，并通过 FastAPI auth_request 复用面板鉴权。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

from account_manager import console

# Selkies 装在镜像的独立 venv 里（见 Dockerfile），可用该变量指定可执行文件。
SELKIES_BIN_ENV = "ACCOUNT_MANAGER_SELKIES_BIN"
# 与 server/window_fit.py 的 XVFB_MAX 保持一致：这是屏幕可调整的上限，
# 客户端连上后 Selkies 会经 RandR 把屏幕调到网页窗口的实际尺寸。
XVFB_SCREEN = "8192x4096x24"
# Selkies 启动要加载 Python 依赖和编码器，慢机器上需要多等一会儿。
SELKIES_READY_TIMEOUT_S = 30.0


def xvfb_argv(display: str) -> list[str]:
    """接管虚拟屏：只开本地 UNIX socket；浏览器由本进程创建，因此关闭 Xauthority 校验。"""
    return [
        "Xvfb", display,
        "-screen", "0", XVFB_SCREEN,
        "-nolisten", "tcp", "-noreset", "-ac", "-dpi", "96",
        "+extension", "GLX", "+extension", "Composite",
    ]


def selkies_bin() -> str:
    return os.environ.get(SELKIES_BIN_ENV) or "selkies"


def selkies_argv(port: int, *, subfolder: str = "/vnc",
                 title: str = "Account Manager 接管") -> list[str]:
    """Selkies 只做「把这块屏推给网页」：

    - 只绑 127.0.0.1，路径前缀 /vnc，外层 Nginx + 面板 Cookie 负责鉴权，自身不再要密码；
    - 只走 WebSocket（与面板同一个端口），不开 WebRTC/UDP；
    - 音频、麦克风、摄像头、手柄、打印、文件传输、分享链接、副屏、命令执行等接管用不到的
      功能全部关闭，侧边栏只留画质、分辨率、剪贴板、全屏和手机输入相关的按钮；
    - 帧率默认 30（可在侧边栏调到 60），登录类页面足够流畅，又能省一半 CPU 编码开销。

    其余参数仍可用容器环境变量 ``SELKIES_*`` 调整（命令行参数优先）。
    """
    off = "false"
    return [
        selkies_bin(),
        "--addr", "127.0.0.1",
        "--port", str(port),
        "--subfolder", subfolder,
        "--mode", "websockets",
        f"--enable-basic-auth={off}",
        f"--audio-enabled={off}",
        f"--microphone-enabled={off}",
        f"--webcam-enabled={off}",
        f"--gamepad-enabled={off}",
        f"--printing-enabled={off}",
        "--file-transfers=none",
        f"--command-enabled={off}",
        f"--enable-sharing={off}",
        f"--enable-collab={off}",
        f"--enable-shared={off}",
        f"--enable-player2={off}",
        f"--enable-player3={off}",
        f"--enable-player4={off}",
        f"--second-screen={off}",
        "--enable-clipboard=true",
        "--clipboard-seamless=true",
        "--enable-resize=true",
        "--framerate=30,8-60",
        f"--ui-title={title}",
        f"--ui-show-logo={off}",
        f"--ui-sidebar-show-apps={off}",
        f"--ui-sidebar-show-files={off}",
        f"--ui-sidebar-show-gamepads={off}",
        f"--ui-sidebar-show-webcam={off}",
        f"--ui-sidebar-show-sharing={off}",
        f"--ui-sidebar-show-audio-settings={off}",
        f"--ui-sidebar-show-gaming-mode={off}",
    ]


class TakeoverBusy(Exception):
    pass


class TakeoverError(Exception):
    pass


class ProcessLauncher:
    """subprocess.Popen 的薄封装，便于测试注入假实现。

    子进程留在面板进程的进程组里：面板被 supervisord 整组停止时一并退出，不会留下
    占着 :100 和 Web 端口的孤儿进程。
    """

    def spawn(self, argv: list[str], env: dict | None = None, stdout=None):
        """stdout 给一个已打开的文件时，子进程的标准输出和错误都写进去。"""
        if stdout is None:
            return subprocess.Popen(argv, env=env)
        return subprocess.Popen(argv, env=env, stdout=stdout, stderr=subprocess.STDOUT)


def _terminate(proc) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class TakeoverManager:
    def __init__(self, *, now_fn, launcher=None, browser_fn=None,
                 wait_display_fn=None, wait_web_fn=None, window_fitter_fn=None,
                 clear_display_fn=None, display=":100", web_port=14500):
        self.now_fn = now_fn
        self.launcher = launcher or ProcessLauncher()
        self._browser_fn = browser_fn
        if window_fitter_fn is not None:
            self._window_fitter_fn = window_fitter_fn
        else:
            from server.window_fit import WindowFitter
            self._window_fitter_fn = lambda display: WindowFitter(display).start()
        if wait_display_fn is not None:
            self._wait_display = wait_display_fn
        else:
            from server.takeover_browser import wait_x_socket
            self._wait_display = lambda display: wait_x_socket(display)
        if wait_web_fn is not None:
            self._wait_web = wait_web_fn
        else:
            from server.takeover_browser import wait_tcp_port
            self._wait_web = lambda host, port: wait_tcp_port(
                host, port, timeout=SELKIES_READY_TIMEOUT_S, what="Selkies",
            )
        if clear_display_fn is not None:
            self._clear_display = clear_display_fn
        else:
            from server.takeover_browser import clear_stale_x_display
            self._clear_display = clear_stale_x_display
        self.display = display
        self.web_port = web_port
        self._lock = threading.RLock()
        self._active = False
        self._email = None
        self._started_at = None
        self._mode = None
        self._proxy = ""
        self._login_email = ""
        self._saved_key = ""
        self._rejected_keys: set[str] = set()
        self._xvfb = None
        self._selkies = None
        self._runtime_dir = None
        self._browser = None
        self._browser_executor = None
        self._window_fitter = None
        self._timer = None
        self._timer_generation = 0
        self._idle_timeout_s = 0.0

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._active,
                "email": self._email,
                "started_at": self._started_at,
                "mode": self._mode,
            }

    def capture_context(self) -> dict:
        """供面板取 sk 时使用的内部上下文（含代理，切勿原样返回给前端）。"""
        with self._lock:
            return {
                "running": self._active,
                "mode": self._mode,
                "email": self._email,
                "login_email": self._login_email,
                "proxy": self._proxy,
                "saved_key": self._saved_key,
                "rejected_keys": set(self._rejected_keys),
            }

    def start(self, *, email, session_key, proxy="", idle_timeout_s=900,
              mode="account", login_email="") -> dict:
        with self._lock:
            if self._active:
                raise TakeoverBusy("已有接管会话，请先结束")
            if self._browser_fn is None:
                from server.takeover_browser import open_takeover_browser
                self._browser_fn = open_takeover_browser
            try:
                # 先起虚拟屏，再让 Selkies 挂上去推流。上次异常退出（如容器重启）
                # 可能留下 :100 的 socket 文件，先清掉，免得「等屏就绪」被旧文件骗过。
                self._clear_display(self.display)
                self._xvfb = self.launcher.spawn(xvfb_argv(self.display))
                self._wait_display(self.display)
                # Selkies 的运行时 socket（输入、剪贴板等）放在会话私有的临时目录，
                # 结束时整个删掉。
                self._runtime_dir = tempfile.mkdtemp(prefix="account-manager-selkies-")
                env = dict(os.environ)
                env.update(DISPLAY=self.display, XDG_RUNTIME_DIR=self._runtime_dir)
                env.pop("WAYLAND_DISPLAY", None)
                self._selkies = self.launcher.spawn(selkies_argv(self.web_port), env=env)
                self._wait_web("127.0.0.1", self.web_port)
                # Playwright 的 Sync API 通过线程绑定的 greenlet 驱动事件循环。
                # FastAPI 的启动、停止请求可能落到不同工作线程，因此浏览器的
                # 创建和关闭都必须显式派发到同一条专用线程。
                self._browser_executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="takeover-browser",
                )
                self._browser = self._browser_executor.submit(
                    self._browser_fn,
                    session_key=session_key,
                    proxy=proxy,
                    display=self.display,
                    login_email=login_email,
                ).result()
                # 桌面没有窗口管理器：由它把浏览器窗口持续铺满虚拟屏，
                # 否则固定 1280x900 的窗口在宽屏右侧留黑、在矮视窗底部被裁掉。
                self._window_fitter = self._window_fitter_fn(self.display)
            except Exception as exc:  # noqa: BLE001
                console.log(f"启动接管会话失败：{exc}")
                self._teardown()
                raise TakeoverError(f"启动接管会话失败：{exc}") from exc
            self._active = True
            self._email = email or None
            self._started_at = self.now_fn()
            self._mode = mode
            self._proxy = proxy or ""
            self._login_email = login_email or ""
            self._saved_key = session_key or ""
            self._rejected_keys = set()
            console.log(
                f"接管会话已启动：{email}" if mode == "account"
                else f"手动登录会话已启动：{login_email or '（未指定邮箱）'}"
            )
            self._idle_timeout_s = float(idle_timeout_s)
            self._arm_idle_timer_locked()
            return {"email": email, "started_at": self._started_at}

    def _arm_idle_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer_generation += 1
        generation = self._timer_generation
        self._timer = threading.Timer(
            self._idle_timeout_s, self._idle_stop, args=(generation,),
        )
        self._timer.daemon = True
        self._timer.start()

    def touch(self) -> None:
        """续期活动接管会话的空闲回收计时器。"""
        with self._lock:
            if not self._active:
                raise TakeoverError("当前没有活动的接管会话")
            self._arm_idle_timer_locked()

    def relogin(self, **kwargs) -> str:
        """在活动接管浏览器所属线程执行一次自动重新登录。"""
        with self._lock:
            if not self._active or self._browser is None or self._browser_executor is None:
                raise TakeoverError("当前没有活动的接管会话")
            relogin = getattr(self._browser, "relogin", None)
            if not callable(relogin):
                raise TakeoverError("当前接管浏览器不支持重新登录")
            try:
                return self._browser_executor.submit(relogin, **kwargs).result()
            except TakeoverError:
                raise
            except Exception as exc:  # noqa: BLE001
                console.log(f"接管浏览器重新登录失败：{exc}")
                raise TakeoverError(str(exc)) from exc

    def read_session_key(self) -> str:
        """在接管浏览器所属线程读取当前 sessionKey Cookie；没有则返回空串。"""
        with self._lock:
            if not self._active or self._browser is None or self._browser_executor is None:
                raise TakeoverError("当前没有活动的接管会话")
            reader = getattr(self._browser, "session_key", None)
            if not callable(reader):
                raise TakeoverError("当前接管浏览器不支持读取 sessionKey")
            try:
                return self._browser_executor.submit(reader).result() or ""
            except Exception as exc:  # noqa: BLE001
                raise TakeoverError(f"读取 sessionKey 失败：{exc}") from exc

    def mark_saved(self, *, email: str, session_key: str) -> None:
        """记录已入库的 sk，并把会话归属切到对应账号（之后可「重新自动登录」）。"""
        with self._lock:
            if not self._active:
                return
            self._email = email
            self._saved_key = session_key

    def mark_rejected(self, session_key: str) -> None:
        """记下检测为失效的 sk，轮询时不再重复检测同一个值。"""
        with self._lock:
            if self._active:
                self._rejected_keys.add(session_key)

    def _idle_stop(self, generation: int):
        with self._lock:
            # cancel() 与回调启动可能竞争；旧一代计时器不得关闭
            # 已经被心跳续期的会话。
            if not self._active or generation != self._timer_generation:
                return
            console.log("接管会话空闲超时，自动结束。")
            self.stop()

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._timer_generation += 1
            self._teardown()
            self._active = False
            self._email = None
            self._started_at = None
            self._mode = None
            self._proxy = ""
            self._login_email = ""
            self._saved_key = ""
            self._rejected_keys = set()
            self._idle_timeout_s = 0.0

    def _teardown(self):
        # 先停窗口自适应，免得它在浏览器/X 桌面关闭过程中还在操作窗口。
        fitter = self._window_fitter
        self._window_fitter = None
        if fitter is not None:
            try:
                fitter.stop()
            except Exception as exc:  # noqa: BLE001
                console.log(f"停止接管窗口自适应失败：{exc}")
        browser = self._browser
        browser_executor = self._browser_executor
        self._browser = None
        self._browser_executor = None
        if browser is not None:
            try:
                if browser_executor is not None:
                    browser_executor.submit(browser.close).result()
                else:
                    browser.close()
            except Exception as exc:  # noqa: BLE001
                console.log(f"关闭接管浏览器失败：{exc}")
        if browser_executor is not None:
            browser_executor.shutdown(wait=True, cancel_futures=True)
        # 先停推流再关屏，免得 Selkies 在屏幕消失后报一串采集错误。
        if self._selkies is not None:
            _terminate(self._selkies)
            self._selkies = None
        if self._xvfb is not None:
            _terminate(self._xvfb)
            self._xvfb = None
        if self._runtime_dir is not None:
            shutil.rmtree(self._runtime_dir, ignore_errors=True)
            self._runtime_dir = None
