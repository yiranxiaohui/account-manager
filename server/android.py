"""Android 模拟器会话：每个账号一台独立的虚拟设备，同一时刻只运行一台。

与浏览器接管（server/takeover.py）互不干扰、各用各的屏：接管用 :100 + Selkies 14500，
这里用 :101 + Selkies 14501（网页路径 /android-vnc/）。一次会话由这些部分组成：

- 账号代理出口（account_manager.android_gateway）：假 DNS + 本地 HTTP 代理，
  模拟器的全部 TCP 与 DNS 都经账号绑定的代理出去；
- Xvfb 虚拟屏 + Selkies：把模拟器窗口推给网页，窗口按网页大小等比缩放、居中；
- 模拟器本体：带 KVM 加速、冷启动（不存快照），数据盘按账号长期保留。

启动是异步的：start() 只做校验并拉起后台线程，冷启动要 30～90 秒，进度经
status() 的 phase 反映（starting → booting → running）。stop() 先让客户机把缓存
落盘再关模拟器，避免刚安装/登录的数据丢失。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from account_manager import console
from account_manager.android_gateway import AndroidGateway, GatewayError, parse_upstream
from server import android_sdk
from server.takeover import ProcessLauncher, selkies_argv, xvfb_argv

DISPLAY = ":101"
WEB_PORT = 14501
WEB_SUBFOLDER = "/android-vnc"
CONSOLE_PORT = 5580  # adb 端口是它 +1
SERIAL = f"emulator-{CONSOLE_PORT}"
# 假 DNS 的监听地址：模拟器的 -dns-server 只能写 IP，端口固定 53。用一个少见的
# 回环地址，避开宿主上可能已有的本地 DNS（如 127.0.0.53 的 systemd-resolved）。
DNS_HOST_ENV = "ANDROID_DNS_HOST"
DEFAULT_DNS_HOST = "127.0.53.1"
KVM_DEVICE = "/dev/kvm"
BOOT_TIMEOUT_S = 300.0
STOP_TIMEOUT_S = 20.0
SELKIES_READY_TIMEOUT_S = 30.0
# 模拟器本体的输出（相对 data/android），每次启动覆盖。
EMULATOR_LOG = "emulator.log"
# 本会话拉起的每个进程（Xvfb、Selkies、模拟器及其子进程）都带上这个环境变量，值是运行
# 环境目录。面板进程崩溃重启后，靠它认出上一个面板留下的孤儿进程并回收（否则旧模拟器
# 一直占着内存和 AVD 锁，虚拟屏与推流端口也被占用）。
OWNER_ENV = "ACCOUNT_MANAGER_ANDROID_OWNER"


def owned_pids(owner: str, proc_dir: str = "/proc") -> list[int]:
    """环境变量里带着 OWNER_ENV=owner 的进程（不含自己）。"""
    marker = f"{OWNER_ENV}={owner}".encode()
    me = os.getpid()
    pids = []
    try:
        entries = os.listdir(proc_dir)
    except OSError:
        return pids
    for entry in entries:
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            with open(os.path.join(proc_dir, entry, "environ"), "rb") as fh:
                env = fh.read().split(b"\0")
        except OSError:
            continue
        if marker in env:
            pids.append(int(entry))
    return pids


def reap_owned(owner: str, *, timeout: float = 5.0) -> int:
    """结束上一个面板进程遗留的会话进程：先 SIGTERM，超时再 SIGKILL。返回处理的进程数。"""
    import signal

    pids = owned_pids(owner)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + timeout
    alive = list(pids)
    while alive and time.monotonic() < deadline:
        time.sleep(0.1)
        alive = [pid for pid in alive if pid in owned_pids(owner)]
    for pid in alive:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return len(pids)


class AndroidBusy(Exception):
    pass


class AndroidError(Exception):
    pass


def kvm_available(path: str = KVM_DEVICE) -> bool:
    return os.path.exists(path) and os.access(path, os.R_OK | os.W_OK)


def emulator_argv(paths: android_sdk.AndroidPaths, avd: str, *, http_proxy: str,
                  dns_host: str, timezone: str | None) -> list[str]:
    argv = [
        str(paths.emulator), "-avd", avd,
        "-no-audio", "-no-boot-anim",
        # 冷启动、不存快照：快照约等于内存大小，每个账号存一份太占盘。
        "-no-snapshot",
        "-no-metrics", "-no-nested-warnings", "-crash-report-mode", "disabled",
        "-gpu", "swiftshader_indirect",
        "-http-proxy", http_proxy,
        "-dns-server", dns_host,
        "-ports", f"{CONSOLE_PORT},{CONSOLE_PORT + 1}",
    ]
    if timezone:
        argv += ["-timezone", timezone]
    return argv


def android_env(paths: android_sdk.AndroidPaths, display: str | None = None,
                owner: str | None = None) -> dict:
    env = dict(os.environ)
    env.update(
        ANDROID_SDK_ROOT=str(paths.sdk),
        ANDROID_HOME=str(paths.sdk),
        ANDROID_AVD_HOME=str(paths.avd),
        ANDROID_EMULATOR_HOME=str(paths.home),
        ANDROID_USER_HOME=str(paths.home),
        ADB_VENDOR_KEYS=str(paths.adb_key),
        # 模拟器的 Qt 设置（Emulator.conf）也收进运行环境目录。
        XDG_CONFIG_HOME=str(paths.home / "config"),
    )
    env.pop("http_proxy", None)
    env.pop("HTTP_PROXY", None)
    if display:
        env["DISPLAY"] = display
    if owner:
        env[OWNER_ENV] = owner
    return env


class AdbRunner:
    """调用 adb 的薄封装，便于测试替换。"""

    def __init__(self, paths: android_sdk.AndroidPaths):
        self.paths = paths

    def run(self, *args: str, timeout: float = 10.0) -> str:
        proc = subprocess.run(
            [str(self.paths.adb), *args], env=android_env(self.paths),
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            raise AndroidError((proc.stderr or proc.stdout).strip() or "adb 执行失败")
        return proc.stdout

    def ensure_key(self) -> None:
        """adb 与模拟器共用 home/adbkey：模拟器把公钥预置进系统，adb 免确认直连。"""
        if self.paths.adb_key.exists():
            return
        self.paths.home.mkdir(parents=True, exist_ok=True)
        self.run("keygen", str(self.paths.adb_key), timeout=30)

    def boot_completed(self, serial: str) -> bool:
        try:
            out = self.run("-s", serial, "shell", "getprop", "sys.boot_completed", timeout=10)
        except (AndroidError, subprocess.TimeoutExpired, OSError):
            return False
        return out.strip() == "1"

    def shutdown(self, serial: str) -> None:
        """先让客户机把文件系统缓存写回数据盘，再经控制台关模拟器。"""
        for args, timeout in ((("shell", "sync"), 15), (("emu", "kill"), 10)):
            try:
                self.run("-s", serial, *args, timeout=timeout)
            except (AndroidError, subprocess.TimeoutExpired, OSError):
                pass


def _terminate(proc, timeout: float = 5.0) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
        proc.terminate()
        proc.wait(timeout=timeout)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


class _Session:
    def __init__(self, email: str, proxy: str, started_at: str):
        self.email = email
        self.proxy = proxy
        self.started_at = started_at
        self.phase = "starting"
        self.timezone = ""
        self.cancelled = threading.Event()
        self.gateway = None
        self.xvfb = None
        self.selkies = None
        self.emulator = None
        self.fitter = None
        self.runtime_dir = None
        self.thread: threading.Thread | None = None


class AndroidManager:
    def __init__(self, data_dir: Path, *, now_fn, launcher=None, installer=None, adb=None,
                 gateway_fn=None, timezone_fn=None, wait_display_fn=None, wait_web_fn=None,
                 clear_display_fn=None, window_fitter_fn=None, kvm_fn=None, reap_fn=None,
                 display=DISPLAY, web_port=WEB_PORT, boot_timeout_s=BOOT_TIMEOUT_S,
                 poll_interval=2.0):
        self.paths = android_sdk.AndroidPaths(Path(data_dir) / "android")
        self.owner = str(self.paths.root.resolve())
        self._reap = reap_fn or reap_owned
        self.now_fn = now_fn
        self.launcher = launcher or ProcessLauncher()
        self.installer = installer or android_sdk.SdkInstaller(self.paths)
        self.adb = adb or AdbRunner(self.paths)
        self._gateway_fn = gateway_fn or (
            lambda proxy: AndroidGateway(
                proxy, dns_host=os.environ.get(DNS_HOST_ENV) or DEFAULT_DNS_HOST,
                on_error=lambda msg: console.log(f"Android 代理：{msg}"),
            ).start()
        )
        if timezone_fn is None:
            def timezone_fn(proxy):
                from account_manager import browser
                return browser.lookup_timezone(proxy)
        self._timezone_fn = timezone_fn
        if wait_display_fn is None:
            from server.takeover_browser import wait_x_socket
            wait_display_fn = wait_x_socket
        self._wait_display = wait_display_fn
        if wait_web_fn is None:
            from server.takeover_browser import wait_tcp_port

            def wait_web_fn(host, port):
                wait_tcp_port(host, port, timeout=SELKIES_READY_TIMEOUT_S, what="Selkies")
        self._wait_web = wait_web_fn
        if clear_display_fn is None:
            from server.takeover_browser import clear_stale_x_display
            clear_display_fn = clear_stale_x_display
        self._clear_display = clear_display_fn
        if window_fitter_fn is None:
            from server.window_fit import WindowFitter, plan_emulator_fit

            device = (android_sdk.DEVICE_WIDTH, android_sdk.DEVICE_HEIGHT)

            def window_fitter_fn(display):
                return WindowFitter(
                    display,
                    planner=lambda root, wins: plan_emulator_fit(root, wins, device),
                ).start()
        self._window_fitter_fn = window_fitter_fn
        self._kvm = kvm_fn or kvm_available
        self.display = display
        self.web_port = web_port
        self.boot_timeout_s = boot_timeout_s
        self.poll_interval = poll_interval
        self._lock = threading.RLock()
        self._session: _Session | None = None
        self._last_error = ""
        self._timer = None
        self._timer_generation = 0
        self._idle_timeout_s = 0.0
        # 面板崩溃重启后，上一个面板拉起的模拟器还在跑：后台回收，不拖慢启动。
        threading.Thread(target=self._reap_stale, name="android-reap", daemon=True).start()

    def _reap_stale(self) -> None:
        try:
            n = self._reap(self.owner)
        except Exception as exc:  # noqa: BLE001
            console.log(f"回收残留的 Android 模拟器进程失败：{exc}")
            return
        if n:
            console.log(f"已回收上次遗留的 {n} 个 Android 模拟器相关进程。")

    # ------------------------------------------------------------ 状态

    def status(self) -> dict:
        with self._lock:
            s = self._session
            running = {
                "running": s is not None,
                "email": s.email if s else None,
                "phase": s.phase if s else None,
                "started_at": s.started_at if s else None,
                "timezone": (s.timezone or None) if s else None,
                "last_error": self._last_error,
            }
        return {
            **running,
            "kvm": self._kvm(),
            "install": self.installer.status(),
            "envs": android_sdk.list_envs(self.paths),
        }

    def running_email(self) -> str | None:
        with self._lock:
            return self._session.email if self._session else None

    def install(self) -> dict:
        return self.installer.start()

    # ------------------------------------------------------------ 启动

    def start(self, *, email: str, proxy: str, idle_timeout_s: float = 0) -> dict:
        with self._lock:
            if self._session is not None:
                raise AndroidBusy("已有运行中的 Android 模拟器，请先停止")
            if not self._kvm():
                raise AndroidError(
                    "容器里没有可用的 /dev/kvm：请在宿主开放 KVM，并在 docker-compose.yml 里"
                    "加上 devices: [\"/dev/kvm:/dev/kvm\"]"
                )
            if not self.installer.ready():
                raise AndroidError("Android 运行环境尚未安装，请先安装")
            try:
                parse_upstream(proxy)
            except GatewayError as exc:
                raise AndroidError(str(exc)) from None
            session = _Session(email, proxy, self.now_fn())
            self._session = session
            self._last_error = ""
            self._idle_timeout_s = float(idle_timeout_s or 0)
            self._arm_idle_timer_locked()
            session.thread = threading.Thread(
                target=self._boot, args=(session,), name="android-boot", daemon=True,
            )
            session.thread.start()
        console.log(f"正在启动 Android 模拟器：{email}")
        return self.status()

    def _check(self, session: _Session) -> None:
        if session.cancelled.is_set():
            raise _Cancelled()

    def _register(self, session: _Session, attr: str, value) -> None:
        """登记一项资源；会话已被停止时立刻回收它并中止启动。"""
        with self._lock:
            if not session.cancelled.is_set():
                setattr(session, attr, value)
                return
        self._release(attr, value)
        raise _Cancelled()

    def _boot(self, session: _Session) -> None:
        try:
            self._reap_stale()
            self.adb.ensure_key()
            avd = android_sdk.ensure_avd(self.paths, session.email)
            self._check(session)
            self._register(session, "gateway", self._gateway_fn(session.proxy))
            tz = None
            try:
                tz = self._timezone_fn(session.proxy)
            except Exception:  # noqa: BLE001 — 查不到时区不影响使用
                tz = None
            session.timezone = tz or ""
            self._check(session)

            marked = dict(os.environ)
            marked[OWNER_ENV] = self.owner
            self._clear_display(self.display)
            self._register(session, "xvfb", self.launcher.spawn(
                xvfb_argv(self.display), env=marked,
            ))
            self._wait_display(self.display)
            self._check(session)
            runtime_dir = tempfile.mkdtemp(prefix="account-manager-android-selkies-")
            self._register(session, "runtime_dir", runtime_dir)
            env = dict(marked)
            env.update(DISPLAY=self.display, XDG_RUNTIME_DIR=runtime_dir)
            env.pop("WAYLAND_DISPLAY", None)
            self._register(session, "selkies", self.launcher.spawn(
                selkies_argv(self.web_port, subfolder=WEB_SUBFOLDER,
                             title="Account Manager Android"),
                env=env,
            ))
            self._wait_web("127.0.0.1", self.web_port)
            self._check(session)

            gateway = session.gateway
            argv = emulator_argv(
                self.paths, avd, http_proxy=gateway.http_proxy_url,
                dns_host=gateway.dns_host, timezone=tz,
            )
            # 模拟器日志很吵（开机期间反复重试 adb 等），写进单独的文件，不混进容器日志。
            self.paths.root.mkdir(parents=True, exist_ok=True)
            with open(self.paths.root / EMULATOR_LOG, "wb") as log_file:
                self._register(session, "emulator", self.launcher.spawn(
                    argv, env=android_env(self.paths, self.display, self.owner),
                    stdout=log_file,
                ))
            self._register(session, "fitter", self._window_fitter_fn(self.display))
            with self._lock:
                if session.cancelled.is_set():
                    raise _Cancelled()
                session.phase = "booting"
            self._wait_boot(session)
            with self._lock:
                if session.cancelled.is_set():
                    raise _Cancelled()
                session.phase = "running"
            console.log(f"Android 模拟器已启动：{session.email}"
                        + (f"（时区 {tz}）" if tz else ""))
            self._watch(session)
        except _Cancelled:
            return
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or type(exc).__name__
            console.log(f"Android 模拟器启动失败：{message}")
            self._fail(session, f"启动失败：{message}")

    def _wait_boot(self, session: _Session) -> None:
        deadline = time.monotonic() + self.boot_timeout_s
        while True:
            self._check(session)
            proc = session.emulator
            if proc is not None and proc.poll() is not None:
                raise AndroidError(
                    f"模拟器进程已退出（退出码 {proc.returncode}），详见 data/android/{EMULATOR_LOG}"
                )
            if self.adb.boot_completed(SERIAL):
                return
            if time.monotonic() >= deadline:
                raise AndroidError(
                    f"等待系统启动超时（{self.boot_timeout_s:.0f} 秒），详见 data/android/{EMULATOR_LOG}"
                )
            session.cancelled.wait(self.poll_interval)

    def _watch(self, session: _Session) -> None:
        """运行期间盯着模拟器进程：用户在模拟器里关机或进程崩溃时收拾会话。"""
        proc = session.emulator
        while not session.cancelled.is_set():
            if proc is not None and proc.poll() is not None:
                console.log(f"Android 模拟器已退出（退出码 {proc.returncode}）。")
                self._fail(session, f"模拟器已退出（退出码 {proc.returncode}）")
                return
            session.cancelled.wait(self.poll_interval)

    def _fail(self, session: _Session, message: str) -> None:
        with self._lock:
            if self._session is not session or session.cancelled.is_set():
                return
            session.cancelled.set()
            self._session = None
            self._last_error = message
            self._cancel_timer_locked()
        self._teardown(session, graceful=False)

    # ------------------------------------------------------------ 停止

    def stop(self) -> bool:
        with self._lock:
            session = self._session
            if session is None:
                return False
            session.cancelled.set()
            self._session = None
            self._cancel_timer_locked()
        console.log(f"正在停止 Android 模拟器：{session.email}")
        self._teardown(session, graceful=True)
        if session.thread is not None and session.thread is not threading.current_thread():
            session.thread.join(timeout=10)
        console.log("Android 模拟器已停止。")
        return True

    def _teardown(self, session: _Session, *, graceful: bool) -> None:
        with self._lock:
            items = {k: getattr(session, k) for k in
                     ("fitter", "emulator", "selkies", "xvfb", "runtime_dir", "gateway")}
            for k in items:
                setattr(session, k, None)
        # 顺序：先停窗口自适应；模拟器关机（客户机落盘）；再停推流、关屏、停代理出口。
        self._release("fitter", items["fitter"])
        emulator = items["emulator"]
        if emulator is not None and emulator.poll() is None:
            if graceful:
                self.adb.shutdown(SERIAL)
                try:
                    emulator.wait(timeout=STOP_TIMEOUT_S)
                except Exception:  # noqa: BLE001
                    pass
            _terminate(emulator, timeout=10)
        for k in ("selkies", "xvfb", "runtime_dir", "gateway"):
            self._release(k, items[k])

    @staticmethod
    def _release(attr: str, value) -> None:
        if value is None:
            return
        try:
            if attr == "fitter":
                value.stop()
            elif attr == "gateway":
                value.stop()
            elif attr == "runtime_dir":
                shutil.rmtree(value, ignore_errors=True)
            else:
                _terminate(value)
        except Exception as exc:  # noqa: BLE001
            console.log(f"清理 Android 会话资源（{attr}）失败：{exc}")

    # ------------------------------------------------------------ 空闲回收

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._timer_generation += 1

    def _arm_idle_timer_locked(self) -> None:
        self._cancel_timer_locked()
        if self._idle_timeout_s <= 0:
            return
        generation = self._timer_generation
        self._timer = threading.Timer(self._idle_timeout_s, self._idle_stop, args=(generation,))
        self._timer.daemon = True
        self._timer.start()

    def touch(self) -> None:
        """画面页心跳：续期空闲回收计时器。"""
        with self._lock:
            if self._session is None:
                raise AndroidError("当前没有运行中的 Android 模拟器")
            self._arm_idle_timer_locked()

    def _idle_stop(self, generation: int) -> None:
        with self._lock:
            if self._session is None or generation != self._timer_generation:
                return
        console.log("Android 模拟器画面长时间无人查看，自动停止。")
        self.stop()

    # ------------------------------------------------------------ 账号环境

    def delete_env(self, email: str) -> bool:
        with self._lock:
            if self._session is not None and self._session.email == email:
                raise AndroidBusy("该账号的 Android 模拟器正在运行，请先停止")
            return android_sdk.delete_env(self.paths, email)

    def rename_env(self, old_email: str, new_email: str) -> bool:
        with self._lock:
            if self._session is not None and self._session.email == old_email:
                raise AndroidBusy("该账号的 Android 模拟器正在运行，请先停止")
            return android_sdk.rename_env(self.paths, old_email, new_email)


class _Cancelled(Exception):
    pass
