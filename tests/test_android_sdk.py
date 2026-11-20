"""Android 运行环境：解压、校验、安装状态与账号虚拟设备。"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import struct
import zipfile

import pytest

from server import android_sdk as sdk


def _zip_bytes(entries) -> bytes:
    """entries: [(name, data|None, mode)]；data 为 None 表示目录，mode 带文件类型位。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.create_system = 3  # Unix，external_attr 高 16 位才有意义
            info.external_attr = mode << 16
            zf.writestr(info, b"" if data is None else data)
    return buf.getvalue()


def test_extract_strips_prefix_and_keeps_modes_and_links(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(_zip_bytes([
        ("emulator/", None, stat.S_IFDIR | 0o755),
        ("emulator/emulator", b"#!/bin/sh\n", stat.S_IFREG | 0o755),
        ("emulator/lib/libx.so.1", b"lib", stat.S_IFREG | 0o644),
        ("emulator/lib/libx.so", b"libx.so.1", stat.S_IFLNK | 0o777),
        ("emulator/../evil", b"x", stat.S_IFREG | 0o644),
        ("other/file", b"x", stat.S_IFREG | 0o644),
    ]))
    dest = tmp_path / "sdk" / "emulator"
    assert sdk.extract_zip(archive, "emulator/", dest) == 3
    exe = dest / "emulator"
    assert exe.read_bytes() == b"#!/bin/sh\n"
    assert os.stat(exe).st_mode & 0o111
    assert (dest / "lib" / "libx.so").is_symlink()
    assert os.readlink(dest / "lib" / "libx.so") == "libx.so.1"
    assert not (tmp_path / "sdk" / "evil").exists()
    assert not (tmp_path / "evil").exists()
    assert not (dest / "file").exists()


def test_extract_rejects_link_escaping_destination(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(_zip_bytes([
        ("x86_64/link", b"../../etc/passwd", stat.S_IFLNK | 0o777),
    ]))
    with pytest.raises(sdk.InstallError, match="越界链接"):
        sdk.extract_zip(archive, "x86_64/", tmp_path / "out")


def _fake_packages(tmp_path, monkeypatch, image: bytes = b"img" * 10):
    payloads = {}
    pkgs = []
    for key, prefix, dest, files in (
        ("emu", "emulator/", "emulator", [("emulator/emulator", b"emu", 0o755)]),
        ("adb", "platform-tools/", "platform-tools", [("platform-tools/adb", b"adb", 0o755)]),
        (sdk.SYSTEM_IMAGE_PACKAGE, "x86_64/", sdk.SYSTEM_IMAGE_DIR,
         [("x86_64/system.img", image, 0o644)]),
    ):
        data = _zip_bytes([(n, d, stat.S_IFREG | m) for n, d, m in files])
        url = f"https://dl.test/{key}.zip"
        payloads[url] = data
        pkgs.append(sdk.Package(key=key, label=key, url=url, size=len(data),
                                sha256=hashlib.sha256(data).hexdigest(),
                                strip_prefix=prefix, dest=dest))
    monkeypatch.setattr(sdk, "PACKAGES", tuple(pkgs))
    monkeypatch.setattr(sdk, "TOTAL_DOWNLOAD", sum(p.size for p in pkgs))
    monkeypatch.delenv("ANDROID_IMAGE_MASK", raising=False)
    downloads = []

    def download(url, dest, on_progress):
        downloads.append(url)
        data = payloads[url]
        dest.write_bytes(data)
        on_progress(len(data))
        return hashlib.sha256(data).hexdigest()

    return payloads, downloads, download


def test_installer_installs_all_packages_once(tmp_path, monkeypatch):
    _, downloads, download = _fake_packages(tmp_path, monkeypatch)
    paths = sdk.AndroidPaths(tmp_path / "android")
    inst = sdk.SdkInstaller(paths, download_fn=download, disk_free_fn=lambda p: 10 ** 12)
    assert inst.status()["state"] == "missing"
    inst.install()
    assert sdk.sdk_ready(paths)
    assert (paths.sdk / "emulator" / "emulator").read_bytes() == b"emu"
    assert (paths.sdk / sdk.SYSTEM_IMAGE_DIR / "system.img").exists()
    assert os.access(paths.sdk / "platform-tools" / "adb", os.X_OK)
    assert list(paths.downloads.iterdir()) == []  # 压缩包装完即删
    assert inst.status()["state"] == "ready"
    inst.install()
    assert len(downloads) == 3  # 已装好的不再下载


def _play_disk_bytes() -> bytes:
    """和 Play 镜像同布局的最小盘：vbmeta、super、数据区里的 prop 文本。"""
    from server import android_image as ai

    entries_lba, entry_size, count = 2, 128, 32
    total = 0x801000
    head = bytearray(total)
    header = bytearray(92)
    header[0:8] = ai.GPT_SIGNATURE
    struct.pack_into("<Q", header, 72, entries_lba)
    struct.pack_into("<I", header, 80, count)
    struct.pack_into("<I", header, 84, entry_size)
    head[512:512 + 92] = header
    for i, (name, start, end) in enumerate((("vbmeta", 2048, 4095),
                                           ("super", 6144, 12287))):
        row = bytearray(entry_size)
        row[0:16] = bytes([i + 1]) * 16
        struct.pack_into("<QQ", row, 32, start, end)
        row[56:128] = name.encode("utf-16-le").ljust(72, b"\0")
        off = entries_lba * 512 + i * entry_size
        head[off:off + entry_size] = row
    body = b"\0ro.hardware=ranchu\nro.kernel.qemu=1\0dev-keys\0"
    at = 6144 * 512 + ai.LP_DATA_OFFSET
    head[at:at + len(body)] = body
    return bytes(head)


def test_system_image_is_left_untouched_by_default(tmp_path, monkeypatch):
    image = _play_disk_bytes()
    _, _, download = _fake_packages(tmp_path, monkeypatch, image=image)
    paths = sdk.AndroidPaths(tmp_path / "android")
    sdk.SdkInstaller(paths, download_fn=download, disk_free_fn=lambda p: 10 ** 12).install()
    installed = paths.sdk / sdk.SYSTEM_IMAGE_DIR / "system.img"
    assert installed.read_bytes() == image
    assert not sdk.image_mask_paths(paths).exists()
    assert sdk.image_mask_enabled() is False


def test_image_mask_requires_explicit_opt_in(tmp_path, monkeypatch):
    image = _play_disk_bytes()
    _, _, download = _fake_packages(tmp_path, monkeypatch, image=image)
    monkeypatch.setenv("ANDROID_IMAGE_MASK", "1")
    assert sdk.image_mask_enabled() is True
    paths = sdk.AndroidPaths(tmp_path / "android")
    sdk.SdkInstaller(paths, download_fn=download, disk_free_fn=lambda p: 10 ** 12).install()
    installed = paths.sdk / sdk.SYSTEM_IMAGE_DIR / "system.img"
    assert installed.read_bytes() != image
    marker = json.loads((sdk.image_mask_paths(paths) / "system.json").read_text())
    assert marker["substituted"]["ro.hardware"] == 1
    backup = (sdk.image_mask_paths(paths) / "system.img.orig")
    assert backup.read_bytes() == image


def test_image_mask_skips_broken_and_foreign_images(tmp_path, monkeypatch):
    monkeypatch.setenv("ANDROID_IMAGE_MASK", "1")
    _, _, download = _fake_packages(tmp_path, monkeypatch)  # system.img 不是 GPT 盘
    paths = sdk.AndroidPaths(tmp_path / "android")
    sdk.SdkInstaller(paths, download_fn=download, disk_free_fn=lambda p: 10 ** 12).install()
    installed = paths.sdk / sdk.SYSTEM_IMAGE_DIR / "system.img"
    assert installed.read_bytes() == b"img" * 10
    assert sdk.sdk_ready(paths)
    assert not (sdk.image_mask_paths(paths) / "system.json").exists()


def test_installer_rejects_checksum_mismatch(tmp_path, monkeypatch):
    _, _, download = _fake_packages(tmp_path, monkeypatch)
    paths = sdk.AndroidPaths(tmp_path / "android")

    def bad_download(url, dest, on_progress):
        download(url, dest, on_progress)
        return "0" * 64

    inst = sdk.SdkInstaller(paths, download_fn=bad_download, disk_free_fn=lambda p: 10 ** 12)
    with pytest.raises(sdk.InstallError, match="SHA-256 不匹配"):
        inst.install()
    assert not sdk.sdk_ready(paths)
    assert not (paths.sdk / "emulator").exists()
    assert list(paths.downloads.iterdir()) == []


def test_installer_checks_free_disk_space(tmp_path, monkeypatch):
    _, downloads, download = _fake_packages(tmp_path, monkeypatch)
    inst = sdk.SdkInstaller(sdk.AndroidPaths(tmp_path / "android"), download_fn=download,
                            disk_free_fn=lambda p: 1024)
    with pytest.raises(sdk.InstallError, match="磁盘空间不足"):
        inst.install()
    assert downloads == []


def test_installer_background_job_reports_error_and_recovers(tmp_path, monkeypatch):
    _, _, download = _fake_packages(tmp_path, monkeypatch)
    calls = {"n": 0}

    def flaky(url, dest, on_progress):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sdk.InstallError("下载失败：HTTP 503")
        return download(url, dest, on_progress)

    inst = sdk.SdkInstaller(sdk.AndroidPaths(tmp_path / "android"), download_fn=flaky,
                            disk_free_fn=lambda p: 10 ** 12)
    inst.start()
    inst.wait(10)
    st = inst.status()
    assert st["state"] == "error" and "503" in st["error"]
    inst.start()
    inst.wait(10)
    st = inst.status()
    assert st["state"] == "ready" and st["ready"] is True and st["error"] == ""


def test_real_package_pins_are_well_formed():
    for pkg in sdk.PACKAGES:
        assert pkg.url.startswith("https://dl.google.com/android/repository/")
        assert len(pkg.sha256) == 64 and int(pkg.sha256, 16) >= 0
        assert pkg.strip_prefix.endswith("/")
        assert pkg.size > 0
    assert sdk.TOTAL_DOWNLOAD == sum(p.size for p in sdk.PACKAGES)


# ---------------------------------------------------------------- 账号虚拟设备


def test_avd_name_is_stable_and_safe():
    a = sdk.avd_name("User+tag@Example.com")
    assert a == sdk.avd_name(" user+tag@example.com ")
    assert a.startswith("acct_") and a.replace("_", "").isalnum()
    assert a != sdk.avd_name("other@example.com")


def test_device_identity_is_stable_and_looks_like_a_phone():
    a = sdk.device_identity("User+tag@Example.com")
    assert a == sdk.device_identity(" user+tag@example.com ")
    assert (a.brand, a.manufacturer, a.model, a.device) == ("google", "Google", "Pixel 8 Pro", "husky")
    assert len(a.serial) == 12 and a.serial.isalnum()
    assert len(a.imei) == 15 and a.imei.isdigit()
    # Luhn 校验位：从右往左隔位翻倍，总和能被 10 整除。
    total = 0
    for pos, ch in enumerate(reversed(a.imei)):
        digit = int(ch) * (2 if pos % 2 else 1)
        total += digit - 9 if digit > 9 else digit
    assert total % 10 == 0
    assert len(a.meid) == 15
    assert len(a.android_id) == 16
    assert len(a.wifi_mac.split(":")) == 6 and len(a.bluetooth.split(":")) == 6
    assert int(a.wifi_mac[:2], 16) & 0x02  # 本地管理位：像批次分配的随机 MAC
    assert sdk.device_identity("other@example.com") != a
    assert "emulator" not in (a.serial + a.model).lower()


def test_avd_uses_official_profile_and_device_hardware():
    identity = sdk.device_identity("a@x.com")
    lines = dict(line.split("=", 1) for line in sdk.avd_config("acct_x", identity).splitlines())
    # 代号必须是官方 profile：Play 认证不认识自造设备代号。
    assert lines["hw.device.name"] == "husky"
    assert lines["hw.model"] == "Pixel 8 Pro"
    # 真机有摄像头和音频；"none" 是模拟器特征。
    assert lines["hw.camera.back"] != "none"
    assert lines["hw.audioOutput"] == "yes"
    # 传感器、GPS、通话能力与 ARC：真机都有，缺了就是模拟器特征。
    for key in ("hw.sensors.accelerometer", "hw.sensors.gyroscope",
                "hw.sensors.magnetometer", "hw.gps", "hw.gsmSim"):
        assert lines[key] == "yes"
    assert lines["hw.arc"] == "no"


def test_guest_properties_only_lists_writable_keys():
    identity = sdk.device_identity("a@x.com")
    names = [k for k, _ in sdk.guest_properties(identity)]
    # Android 16+ 禁止 shell 改 ro.*，塞进去的 setprop 只会报错。
    assert not any(k.startswith("ro.") for k in names)
    assert "gsm.sim.imei" in names
    injected = dict(sdk.emulator_props(identity))
    assert injected["ro.serialno"] == identity.serial
    assert injected["ro.product.model"] == "Pixel 8 Pro"


def test_existing_screen_is_preserved_across_refresh(tmp_path, monkeypatch):
    paths = sdk.AndroidPaths(tmp_path / "android")
    monkeypatch.setenv("ANDROID_EMULATOR_WIDTH", "1080")
    monkeypatch.setenv("ANDROID_EMULATOR_HEIGHT", "2400")
    monkeypatch.setenv("ANDROID_EMULATOR_DENSITY", "420")
    sdk.ensure_avd(paths, "a@x.com")
    assert sdk.screen_size(paths, "a@x.com") == (1080, 2400, 420)
    monkeypatch.setenv("ANDROID_EMULATOR_WIDTH", "720")
    monkeypatch.setenv("ANDROID_EMULATOR_HEIGHT", "1600")
    monkeypatch.setenv("ANDROID_EMULATOR_DENSITY", "320")
    sdk.ensure_avd(paths, "a@x.com")
    assert sdk.screen_size(paths, "a@x.com") == (1080, 2400, 420)
    identity = json.loads((sdk.avd_dir(paths, "a@x.com") / sdk.IDENTITY_FILE).read_text())
    assert identity["imei"] == sdk.device_identity("a@x.com").imei


def test_ensure_avd_writes_config_and_pointer(tmp_path, monkeypatch):
    monkeypatch.setenv("ANDROID_EMULATOR_CORES", "2")
    monkeypatch.setenv("ANDROID_EMULATOR_RAM_MB", "3072")
    paths = sdk.AndroidPaths(tmp_path / "android")
    name = sdk.ensure_avd(paths, "a@x.com")
    directory = sdk.avd_dir(paths, "a@x.com")
    device = directory / sdk.SYSTEM_IMAGE_TARGET
    config = (device / "config.ini").read_text()
    assert f"AvdId={name}\n" in config
    assert f"image.sysdir.1={sdk.SYSTEM_IMAGE_SYSDIR}\n" in config
    assert "PlayStore.enabled=true\n" in config
    assert "hw.cpu.ncore=2\n" in config
    assert "hw.ramSize=3072\n" in config
    assert "hw.keyboard=yes\n" in config
    pointer = (paths.avd / f"{name}.ini").read_text()
    assert f"path={device}\n" in pointer
    assert f"target={sdk.SYSTEM_IMAGE_TARGET}\n" in pointer
    assert sdk.list_envs(paths) == ["a@x.com"]


def test_rename_and_delete_env(tmp_path):
    paths = sdk.AndroidPaths(tmp_path / "android")
    old_dir = sdk.avd_dir(paths, "old@x.com")
    old_dir.mkdir(parents=True)
    (old_dir / "config.ini").write_text("target=android-34\n")
    (old_dir / "userdata-qemu.img.qcow2").write_bytes(b"data")
    sdk.ensure_avd(paths, "old@x.com")
    (old_dir / sdk.SYSTEM_IMAGE_TARGET / "userdata-qemu.img.qcow2").write_bytes(b"new data")
    assert sdk.rename_env(paths, "old@x.com", "new@x.com") is True
    new_dir = sdk.avd_dir(paths, "new@x.com")
    assert (new_dir / "userdata-qemu.img.qcow2").read_bytes() == b"data"
    assert not sdk.avd_dir(paths, "old@x.com").exists()
    assert not (paths.avd / f"{sdk.avd_name('old@x.com')}.ini").exists()
    assert (new_dir / "config.ini").read_text() == "target=android-34\n"
    assert (new_dir / sdk.SYSTEM_IMAGE_TARGET / "userdata-qemu.img.qcow2").read_bytes() == b"new data"
    assert f"path={new_dir / sdk.SYSTEM_IMAGE_TARGET}\n" in (paths.avd / f"{sdk.avd_name('new@x.com')}.ini").read_text()
    assert sdk.list_envs(paths) == ["new@x.com"]
    assert sdk.rename_env(paths, "missing@x.com", "z@x.com") is False

    sdk.ensure_avd(paths, "other@x.com")
    with pytest.raises(sdk.InstallError, match="已有 Android 环境"):
        sdk.rename_env(paths, "new@x.com", "other@x.com")

    assert sdk.delete_env(paths, "new@x.com") is True
    assert not new_dir.exists()
    assert sdk.delete_env(paths, "new@x.com") is False
    assert sdk.list_envs(paths) == ["other@x.com"]


def test_list_envs_skips_broken_dirs(tmp_path):
    paths = sdk.AndroidPaths(tmp_path / "android")
    (paths.avd / "acct_broken.avd").mkdir(parents=True)
    (paths.avd / "acct_bad.avd").mkdir()
    (paths.avd / "acct_bad.avd" / sdk.ACCOUNT_FILE).write_text("{")
    assert sdk.list_envs(paths) == []
    assert json.loads(json.dumps(sdk.list_envs(sdk.AndroidPaths(tmp_path / "none")))) == []


def test_checkin_request_carries_device_fields():
    identity = sdk.device_identity("a@x.com")
    body = sdk.checkin_request(identity, "a@x.com")
    assert b"Pixel 8 Pro" in body and b"husky" in body and identity.android_id.encode() in body


def test_profile_is_cached_per_account(tmp_path):
    path = tmp_path / "avd" / "acct_x.avd" / sdk.DEVICE_PROFILE_FILE
    path.parent.mkdir(parents=True)
    sdk.save_device_profile(path, {"checkin_1": "ab"})
    assert sdk.load_device_profile(path) == {"checkin_1": "ab"}
    assert sdk.load_device_profile(tmp_path / "missing.json") == {}
