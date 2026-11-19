"""接管窗口自适应：纯规划逻辑 + 真实 Xvfb 集成（缺 Xvfb 时跳过）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

from server.window_fit import (
    MAX_FIT,
    XVFB_MAX,
    TopLevel,
    WindowFitter,
    fit_size,
    plan_emulator_fit,
    plan_fit,
)


def _win(id=1, wm_class="Chromium-browser", viewable=True, override=False, geo=(0, 0, 1280, 900)):
    return TopLevel(id, wm_class, viewable, override, *geo)


def test_fit_size_matches_client_desktop():
    assert fit_size((1672, 855)) == (1672, 855)


def test_fit_size_skips_until_selkies_client_resizes_desktop():
    # 客户端连上前根窗口还是 Xvfb 上限，铺满只会开出巨型窗口
    assert fit_size(XVFB_MAX) is None
    assert fit_size((0, 0)) is None


def test_fit_size_capped():
    assert fit_size((5000, 3000)) == MAX_FIT


def test_plan_resizes_browser_main_window():
    # 截图里的场景：桌面 1672x855，浏览器窗口还是启动时的 1280x900
    assert plan_fit((1672, 855), [_win(id=7)]) == [(7, (1672, 855))]


def test_plan_moves_window_back_to_origin():
    assert plan_fit((1672, 855), [_win(geo=(10, 20, 1672, 855))]) == [(1, (1672, 855))]


def test_plan_skips_already_fitted_window():
    assert plan_fit((1672, 855), [_win(geo=(0, 0, 1672, 855))]) == []


def test_plan_ignores_menus_hidden_and_non_browser_windows():
    windows = [
        _win(id=2, override=True),            # 下拉框/右键菜单：浏览器自己定位
        _win(id=3, viewable=False),           # 未映射
        _win(id=4, wm_class="Selkies"),       # 非浏览器窗口
        _win(id=5),                           # 真正的浏览器主窗口
    ]
    assert plan_fit((1920, 1040), windows) == [(5, (1920, 1040))]


@pytest.mark.parametrize("wm_class", ["Chromium-browser", "Chromium", "Google-chrome"])
def test_chromium_window_classes_are_recognised(wm_class):
    assert plan_fit((1600, 900), [_win(wm_class=wm_class)]) == [(1, (1600, 900))]


def test_plan_noop_before_client_connects():
    assert plan_fit(XVFB_MAX, [_win()]) == []


# ---- Android 模拟器窗口 ----

DEVICE = (720, 1600)


def _emu(id=10, name="Android Emulator - acct_x:5580", geo=(100, 100, 720, 1600),
         viewable=True):
    return TopLevel(id, "Emulator", viewable, False, *geo, name=name)


def _toolbar(width=54):
    return TopLevel(11, "Emulator", True, False, 820, 100, width, 506, name="Emulator")


def test_emulator_scaled_to_height_and_centered_with_toolbar():
    plan = plan_emulator_fit((1280, 800), [_emu(), _toolbar()], DEVICE)
    # 高度铺满 800，宽按 720:1600 等比 = 360；连同 54px 工具栏整体水平居中
    assert plan == [(10, ((1280 - 360 - 54) // 2, 0, 360, 800))]


def test_emulator_limited_by_narrow_viewport():
    plan = plan_emulator_fit((414, 900), [_emu(), _toolbar()], DEVICE)
    (_, (x, y, w, h)), = plan
    assert w == 414 - 54 and x == 0
    assert h == int(1600 * (360 / 720)) and y == (900 - h) // 2


def test_emulator_landscape_window_uses_rotated_ratio():
    plan = plan_emulator_fit((1600, 900), [_emu(geo=(0, 0, 1600, 720)), _toolbar()], DEVICE)
    (_, (x, y, w, h)), = plan
    assert w > h
    assert abs(w / h - 1600 / 720) < 0.01


def test_emulator_fit_is_stable_within_tolerance():
    root = (1280, 800)
    (_, (x, y, w, h)), = plan_emulator_fit(root, [_emu(), _toolbar()], DEVICE)
    assert plan_emulator_fit(root, [_emu(geo=(x + 1, y, w - 2, h + 1)), _toolbar()], DEVICE) == []


def test_emulator_fit_skips_until_client_connects_and_ignores_other_windows():
    assert plan_emulator_fit(XVFB_MAX, [_emu()], DEVICE) == []
    others = [_toolbar(), _win(id=3), _emu(id=4, name="Extended Controls")]
    assert plan_emulator_fit((1280, 800), others, DEVICE) == []
    assert plan_emulator_fit((1280, 800), [_emu(viewable=False)], DEVICE) == []


def test_fitter_stop_is_safe_without_start():
    WindowFitter(":999").stop()


# ---- 真实 X server 集成 ----


@pytest.fixture
def xvfb():
    if not shutil.which("Xvfb"):
        pytest.skip("需要 Xvfb")
    pytest.importorskip("Xlib")
    disp = ":%d" % (90 + os.getpid() % 800)
    proc = subprocess.Popen(
        ["Xvfb", disp, "-screen", "0", "1672x855x24", "-nolisten", "tcp", "-ac"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    sock = f"/tmp/.X11-unix/X{disp[1:]}"
    deadline = time.time() + 10
    while not os.path.exists(sock) and time.time() < deadline:
        time.sleep(0.05)
    try:
        yield disp
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _wait(fn, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.05)
    return False


def test_fitter_resizes_real_browser_window_but_not_menus(xvfb):
    from Xlib import X, display

    d = display.Display(xvfb)
    root = d.screen().root

    def make(wm_class, w, h, override=False):
        win = root.create_window(
            0, 0, w, h, 0, d.screen().root_depth, X.InputOutput, X.CopyFromParent,
            override_redirect=override,
        )
        win.set_wm_class("chromium-browser (/tmp/profile)", wm_class)
        win.map()
        return win

    browser = make("Chromium-browser", 1280, 900)
    menu = make("Chromium-browser", 200, 300, override=True)
    d.sync()

    fitter = WindowFitter(xvfb, interval=0.05).start()
    try:
        def geo(w):
            g = w.get_geometry()
            return (g.x, g.y, g.width, g.height)

        assert _wait(lambda: geo(browser) == (0, 0, 1672, 855)), geo(browser)
        assert geo(menu) == (0, 0, 200, 300)  # override-redirect 菜单不被拉伸

        # 新开的浏览器窗口（如弹窗）同样会被铺满
        second = make("Chromium-browser", 640, 480)
        d.sync()
        assert _wait(lambda: geo(second) == (0, 0, 1672, 855)), geo(second)
    finally:
        fitter.stop()
        d.close()
