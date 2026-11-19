"""接管桌面没有窗口管理器：让浏览器主窗口始终铺满接管虚拟屏。

为什么需要它：接管用 Xvfb + Selkies，Selkies 会经 RandR 把 Xvfb 根窗口调到网页
客户端的画布尺寸（随浏览器窗口变化）。但桌面里没有窗口管理器，
Chromium 按启动参数 `--window-size=1280,900` 开出窗口后就再没人去调它：画布比它宽时
右侧一片黑，比它矮时底部（输入框）被裁掉。

实测把 X 窗口调到桌面尺寸后，innerWidth/innerHeight、visualViewport 与 CSS
媒体查询都随真实尺寸变化，页面按新视口正常排版。

实现取最简单可靠的轮询：每隔 POLL_INTERVAL 看一眼根窗口尺寸和顶层窗口，
不一致就 configure。新开窗口、客户端调尺寸、浏览器自己改尺寸都能收敛，
且没有事件订阅/事件循环要维护。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from account_manager import console

POLL_INTERVAL = 0.3
# Xvfb 启动时的 -screen 尺寸（见 takeover.py）。Selkies 客户端连上之前根窗口就是
# 这个上限值；此时铺满会开出一个 8192x4096 的巨型窗口，白吃内存，跳过等客户端。
XVFB_MAX = (8192, 4096)
# 单窗口尺寸上限：超大屏也够用，防止异常尺寸把渲染内存撑爆。
MAX_FIT = (3840, 2160)
# Chromium 浏览器窗口的 WM_CLASS class（不区分大小写）。instance 会带上
# user-data-dir（如 "chromium-browser (/tmp/playwright_chromiumdev_profile-xxx)"），
# 每次启动都不同，所以按 class 认。另有一个 class 为 "Chrome" 的 10x10 组长窗口，
# 它从不映射，靠 viewable 过滤即可。
BROWSER_CLASSES = frozenset({"chromium-browser", "chromium", "chrome", "google-chrome"})


@dataclass(frozen=True)
class TopLevel:
    """顶层窗口快照（与 Xlib 解耦，便于纯函数测试）。"""

    id: int
    wm_class: str
    viewable: bool
    override_redirect: bool
    x: int
    y: int
    width: int
    height: int
    name: str = ""


def is_browser_window(win: TopLevel) -> bool:
    return win.wm_class.lower() in BROWSER_CLASSES


def fit_size(root: tuple[int, int]) -> tuple[int, int] | None:
    """根窗口尺寸 → 浏览器窗口目标尺寸；None 表示暂不调整。"""
    width, height = root
    if (width, height) == XVFB_MAX or width <= 0 or height <= 0:
        return None
    return min(width, MAX_FIT[0]), min(height, MAX_FIT[1])


def plan_fit(root: tuple[int, int], windows) -> list[tuple[int, tuple[int, int]]]:
    """返回需要调整的 (窗口 id, (宽, 高))，目标位置固定 (0, 0)。

    只动可见的浏览器主窗口；override-redirect 窗口（下拉框、右键菜单、tooltip）
    由浏览器自己定位，绝不能碰。已经铺满的窗口不重复 configure，避免无谓重绘。
    """
    target = fit_size(root)
    if target is None:
        return []
    plan = []
    for win in windows:
        if not win.viewable or win.override_redirect or not is_browser_window(win):
            continue
        if (win.x, win.y, win.width, win.height) == (0, 0, *target):
            continue
        plan.append((win.id, target))
    return plan


# Android 模拟器（Qt 界面）：主窗口标题形如「Android Emulator - <avd>:<端口>」，右侧工具栏
# 是另一个标题为「Emulator」的窄顶层窗口，会自己跟着主窗口移动。
EMULATOR_CLASS = "emulator"
EMULATOR_MAIN_PREFIX = "Android Emulator"
EMULATOR_TOOLBAR_NAME = "Emulator"
# 工具栏还没映射时按这个宽度预留（实测 54px）。
EMULATOR_TOOLBAR_WIDTH = 54
# 尺寸差在这个范围内不再 configure：模拟器会把窗口微调到自己的宽高比，
# 不留余量会和它来回拉扯。
EMULATOR_TOLERANCE = 2


def _is_emulator_main(win: TopLevel) -> bool:
    return win.wm_class.lower() == EMULATOR_CLASS and win.name.startswith(EMULATOR_MAIN_PREFIX)


def _is_emulator_toolbar(win: TopLevel) -> bool:
    return win.wm_class.lower() == EMULATOR_CLASS and win.name == EMULATOR_TOOLBAR_NAME


def plan_emulator_fit(root: tuple[int, int], windows, device: tuple[int, int]):
    """模拟器主窗口按设备宽高比缩放到根窗口内、连同工具栏整体居中。

    device 是竖屏时的 (宽, 高)；主窗口变成横向时（用户在工具栏里旋转了屏幕）
    按横屏比例算。返回 [(窗口 id, (x, y, 宽, 高))]。
    """
    if fit_size(root) is None:
        return []
    root_w, root_h = root
    main = next((w for w in windows if w.viewable and not w.override_redirect
                 and _is_emulator_main(w)), None)
    if main is None:
        return []
    toolbar = next((w for w in windows if w.viewable and _is_emulator_toolbar(w)), None)
    toolbar_w = toolbar.width if toolbar is not None else EMULATOR_TOOLBAR_WIDTH
    dev_w, dev_h = device
    if main.width > main.height:
        dev_w, dev_h = dev_h, dev_w
    avail_w = max(1, root_w - toolbar_w)
    scale = min(avail_w / dev_w, root_h / dev_h)
    width = max(1, int(dev_w * scale))
    height = max(1, int(dev_h * scale))
    x = max(0, (root_w - width - toolbar_w) // 2)
    y = max(0, (root_h - height) // 2)
    if (abs(main.x - x) <= EMULATOR_TOLERANCE and abs(main.y - y) <= EMULATOR_TOLERANCE
            and abs(main.width - width) <= EMULATOR_TOLERANCE
            and abs(main.height - height) <= EMULATOR_TOLERANCE):
        return []
    return [(main.id, (x, y, width, height))]


class WindowFitter:
    """后台线程：持续把浏览器主窗口铺满指定 X display 的根窗口。

    planner 可替换：签名为 ``planner(root, windows) -> [(id, (x, y, 宽, 高))]``，
    Android 模拟器用它把手机窗口按比例居中。
    """

    def __init__(self, display: str, *, interval: float = POLL_INTERVAL, planner=None):
        self.display = display
        self.interval = interval
        self._planner = planner
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> WindowFitter:
        self._thread = threading.Thread(
            target=self._run, name="takeover-window-fit", daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        try:
            from Xlib import display as xdisplay
        except Exception as exc:  # noqa: BLE001
            console.log(f"接管窗口自适应不可用（缺少 python-xlib）：{exc}")
            return
        conn = None
        warned = False
        while not self._stop.is_set():
            try:
                if conn is None:
                    conn = xdisplay.Display(self.display)
                self._tick(conn)
            except Exception as exc:  # noqa: BLE001 — 窗口瞬间消失等都在这里兜住
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                    conn = None
                if not warned and not self._stop.is_set():
                    console.log(f"接管窗口自适应暂时失败，将自动重试：{type(exc).__name__}")
                    warned = True
            self._stop.wait(self.interval)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _snapshot(conn) -> tuple[tuple[int, int], list[TopLevel]]:
        from Xlib import X
        from Xlib import error as xerror

        root = conn.screen().root
        geo = root.get_geometry()
        windows = []
        for child in root.query_tree().children:
            try:
                attrs = child.get_attributes()
                if attrs.map_state != X.IsViewable:
                    continue
                cls = child.get_wm_class() or ("", "")
                g = child.get_geometry()
            except (xerror.BadWindow, xerror.BadDrawable):
                continue  # 窗口在枚举与查询之间被销毁
            try:
                name = child.get_wm_name() or ""
            except (xerror.BadWindow, xerror.BadDrawable):
                continue
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
            windows.append(TopLevel(
                id=child.id, wm_class=cls[1], viewable=True,
                override_redirect=bool(attrs.override_redirect),
                x=g.x, y=g.y, width=g.width, height=g.height, name=str(name),
            ))
        return (geo.width, geo.height), windows

    def _tick(self, conn) -> None:
        from Xlib import error as xerror

        root, windows = self._snapshot(conn)
        if self._planner is not None:
            plan = self._planner(root, windows)
        else:
            plan = [(win_id, (0, 0, w, h)) for win_id, (w, h) in plan_fit(root, windows)]
        if not plan:
            return
        for win_id, (x, y, width, height) in plan:
            conn.create_resource_object("window", win_id).configure(
                x=x, y=y, width=width, height=height, border_width=0,
                # 窗口可能在快照后就被销毁；这类异步错误吃掉，下一轮自然收敛。
                onerror=xerror.CatchError(xerror.BadWindow, xerror.BadMatch),
            )
        conn.sync()
