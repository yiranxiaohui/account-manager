"""Android 运行环境：解压、校验、安装状态与账号虚拟设备。"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
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


def _fake_packages(tmp_path, monkeypatch):
    payloads = {}
    pkgs = []
    for key, prefix, dest, files in (
        ("emu", "emulator/", "emulator", [("emulator/emulator", b"emu", 0o755)]),
        ("adb", "platform-tools/", "platform-tools", [("platform-tools/adb", b"adb", 0o755)]),
        ("img", "x86_64/", sdk.SYSTEM_IMAGE_DIR, [("x86_64/system.img", b"img" * 10, 0o644)]),
    ):
        data = _zip_bytes([(n, d, stat.S_IFREG | m) for n, d, m in files])
        url = f"https://dl.test/{key}.zip"
        payloads[url] = data
        pkgs.append(sdk.Package(key=key, label=key, url=url, size=len(data),
                                sha256=hashlib.sha256(data).hexdigest(),
                                strip_prefix=prefix, dest=dest))
    monkeypatch.setattr(sdk, "PACKAGES", tuple(pkgs))
    monkeypatch.setattr(sdk, "TOTAL_DOWNLOAD", sum(p.size for p in pkgs))
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
