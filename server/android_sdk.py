"""Android 模拟器运行环境：SDK 目录布局、按需下载安装、每个账号的 AVD。

运行环境不打进镜像：模拟器加系统镜像解压后约 4 GB，每次发版都让部署机重新拉一遍
不划算。面板第一次需要时从 Google 官方仓库下载到数据目录（``data/android``），
之后长期复用。下载地址、大小和 SHA-256 都固定在 PACKAGES 里，换版本时一并更新。

目录布局（都在 ``data/android`` 下）::

    sdk/emulator/                    模拟器
    sdk/platform-tools/              adb
    sdk/system-images/android-34/google_apis_playstore/x86_64/
    avd/<名字>.ini, avd/<名字>.avd/  每个账号一台虚拟设备（数据盘随用随长）
    home/                            模拟器与 adb 的配置、adb 密钥
    downloads/                       下载中的临时文件，安装完删除
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

from account_manager import console

REPO = "https://dl.google.com/android/repository/"


@dataclass(frozen=True)
class Package:
    key: str          # 安装标记里用的标识
    label: str        # 进度里显示的名字
    url: str
    size: int
    sha256: str
    strip_prefix: str  # 压缩包里的顶层目录，解压时去掉
    dest: str          # 相对 sdk/ 的安装目录


SYSTEM_IMAGE_DIR = "system-images/android-34/google_apis_playstore/x86_64"
SYSTEM_IMAGE_SYSDIR = SYSTEM_IMAGE_DIR + "/"

PACKAGES = (
    Package(
        key="emulator-37.2.13",
        label="Android 模拟器",
        url=REPO + "emulator-linux_x64-16740729.zip",
        size=349_658_551,
        sha256="cbc7c3cfb83220b8a0f9c513fdab557712e74d339ec19ddffa43f23350ae9bdd",
        strip_prefix="emulator/",
        dest="emulator",
    ),
    Package(
        key="platform-tools-37.0.1",
        label="adb（platform-tools）",
        url=REPO + "platform-tools_r37.0.1-linux.zip",
        size=9_054_187,
        sha256="d230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1",
        strip_prefix="platform-tools/",
        dest="platform-tools",
    ),
    Package(
        key="system-image-34-google_apis_playstore-x86_64-r14",
        label="Android 14 系统镜像（Google Play）",
        url=REPO + "sys-img/google_apis_playstore/x86_64-34_r14.zip",
        size=1_510_752_654,
        sha256="22e7797a7e14d346bc6ed867d84ed423beaf4364db567edaa97f85a99558cf80",
        strip_prefix="x86_64/",
        dest=SYSTEM_IMAGE_DIR,
    ),
)

TOTAL_DOWNLOAD = sum(p.size for p in PACKAGES)
# 解压后大约是压缩包的 2.5 倍；再给第一台虚拟设备的数据盘留 2 GB。
REQUIRED_FREE = int(TOTAL_DOWNLOAD * 3.5) + 2 * 1024 ** 3
MARKER = ".account-manager-package.json"
CHUNK = 1024 * 1024


class InstallError(Exception):
    pass


class AndroidPaths:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.sdk = self.root / "sdk"
        self.avd = self.root / "avd"
        self.home = self.root / "home"
        self.downloads = self.root / "downloads"

    @property
    def emulator(self) -> Path:
        return self.sdk / "emulator" / "emulator"

    @property
    def adb(self) -> Path:
        return self.sdk / "platform-tools" / "adb"

    @property
    def adb_key(self) -> Path:
        return self.home / "adbkey"


def package_installed(paths: AndroidPaths, pkg: Package) -> bool:
    marker = paths.sdk / pkg.dest / MARKER
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("key") == pkg.key and data.get("sha256") == pkg.sha256


def sdk_ready(paths: AndroidPaths) -> bool:
    return all(package_installed(paths, p) for p in PACKAGES)


def _safe_member(name: str, prefix: str) -> str | None:
    """压缩包成员 → 相对安装目录的路径；不在顶层目录下或试图越界的返回 None。"""
    if not name.startswith(prefix):
        return None
    rel = name[len(prefix):]
    if not rel:
        return None
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or rel.startswith("/"):
        return None
    return "/".join(parts) + ("/" if rel.endswith("/") else "")


def extract_zip(archive: Path, prefix: str, dest: Path) -> int:
    """把 archive 里 prefix 目录下的内容解压到 dest，保留可执行权限与符号链接。

    zipfile 默认会丢掉 Unix 权限（模拟器、adb 都要可执行位），符号链接也会变成
    内容是目标路径的普通文件，这里按 external_attr 自己还原。返回解压的文件数。
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    count = 0
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            rel = _safe_member(info.filename, prefix)
            if rel is None:
                continue
            target = (dest / rel).resolve()
            if target != root and root not in target.parents:
                raise InstallError(f"压缩包里有越界路径：{info.filename}")
            mode = (info.external_attr >> 16) & 0xFFFF
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                link = zf.read(info).decode("utf-8")
                resolved = (target.parent / link).resolve()
                if os.path.isabs(link) or (resolved != root and root not in resolved.parents):
                    raise InstallError(f"压缩包里有越界链接：{info.filename}")
                if target.is_symlink() or target.exists():
                    target.unlink()
                os.symlink(link, target)
                count += 1
                continue
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
            perm = mode & 0o777
            os.chmod(target, perm if perm else 0o644)
            count += 1
    return count


def _http_download(url: str, dest: Path, on_progress) -> str:
    """下载到 dest，边下边算 SHA-256，返回十六进制摘要。"""
    import httpx

    digest = hashlib.sha256()
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60.0)) as resp:
        if resp.status_code != 200:
            raise InstallError(f"下载失败：HTTP {resp.status_code}")
        with open(dest, "wb") as out:
            for chunk in resp.iter_bytes(CHUNK):
                out.write(chunk)
                digest.update(chunk)
                on_progress(len(chunk))
    return digest.hexdigest()


class SdkInstaller:
    """后台下载安装运行环境；同一时刻只跑一个安装任务。"""

    def __init__(self, paths: AndroidPaths, *, download_fn=None, disk_free_fn=None):
        self.paths = paths
        self._download = download_fn or _http_download
        self._disk_free = disk_free_fn or (lambda p: shutil.disk_usage(p).free)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state = "idle"
        self._step = ""
        self._downloaded = 0
        self._error = ""

    def ready(self) -> bool:
        return sdk_ready(self.paths)

    def status(self) -> dict:
        with self._lock:
            installing = self._state == "installing"
            state = self._state
            step, downloaded, error = self._step, self._downloaded, self._error
        ready = self.ready()
        if not installing:
            state = "ready" if ready else ("error" if state == "error" else "missing")
        return {
            "state": state,
            "ready": ready,
            "step": step if installing else "",
            "downloaded": downloaded if installing else 0,
            "total": TOTAL_DOWNLOAD,
            "error": error if state == "error" else "",
        }

    def start(self) -> dict:
        with self._lock:
            if self._state == "installing":
                return self._status_unlocked()
            self._state = "installing"
            self._step = "准备中"
            self._downloaded = 0
            self._error = ""
            self._thread = threading.Thread(target=self._run, name="android-sdk-install",
                                            daemon=True)
            self._thread.start()
        return self.status()

    def _status_unlocked(self) -> dict:
        return {"state": self._state, "ready": False, "step": self._step,
                "downloaded": self._downloaded, "total": TOTAL_DOWNLOAD, "error": ""}

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _progress(self, n: int) -> None:
        with self._lock:
            self._downloaded += n

    def _run(self) -> None:
        try:
            self.install()
        except Exception as exc:  # noqa: BLE001
            console.log(f"安装 Android 运行环境失败：{exc}")
            with self._lock:
                self._state = "error"
                self._error = str(exc) or type(exc).__name__
            return
        with self._lock:
            self._state = "ready"
            self._step = ""
        console.log("Android 运行环境已安装。")

    def install(self) -> None:
        paths = self.paths
        for d in (paths.sdk, paths.downloads, paths.avd, paths.home):
            d.mkdir(parents=True, exist_ok=True)
        pending = [p for p in PACKAGES if not package_installed(paths, p)]
        with self._lock:
            # 已装好的包直接计入进度，进度条从已完成的部分接着走。
            self._downloaded = sum(p.size for p in PACKAGES if p not in pending)
        if not pending:
            return
        need = int(sum(p.size for p in pending) * 3.5) + 2 * 1024 ** 3
        free = self._disk_free(paths.root)
        if free < need:
            raise InstallError(
                f"磁盘空间不足：需要约 {need / 1024 ** 3:.1f} GB，当前只剩 {free / 1024 ** 3:.1f} GB"
            )
        for pkg in pending:
            self._install_one(pkg)

    def _install_one(self, pkg: Package) -> None:
        paths = self.paths
        archive = paths.downloads / (pkg.key + ".zip")
        partial = archive.with_suffix(".zip.part")
        with self._lock:
            self._step = f"下载 {pkg.label}"
        console.log(f"开始下载 {pkg.label}（{pkg.size / 1024 ** 2:.0f} MB）……")
        try:
            digest = self._download(pkg.url, partial, self._progress)
            if digest != pkg.sha256:
                raise InstallError(f"{pkg.label} 校验失败：SHA-256 不匹配，请重试")
            partial.replace(archive)
            with self._lock:
                self._step = f"解压 {pkg.label}"
            final = paths.sdk / pkg.dest
            staging = final.parent / (final.name + ".installing")
            shutil.rmtree(staging, ignore_errors=True)
            extract_zip(archive, pkg.strip_prefix, staging)
            (staging / MARKER).write_text(
                json.dumps({"key": pkg.key, "sha256": pkg.sha256}), encoding="utf-8",
            )
            shutil.rmtree(final, ignore_errors=True)
            staging.replace(final)
        finally:
            for f in (partial, archive):
                try:
                    f.unlink()
                except FileNotFoundError:
                    pass
        console.log(f"{pkg.label} 已安装。")


# ---------------------------------------------------------------- 账号的虚拟设备

DEVICE_WIDTH = 720
DEVICE_HEIGHT = 1600
DEVICE_DENSITY = 320
ACCOUNT_FILE = "account.json"


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return min(max(value, low), high)


def avd_name(email: str) -> str:
    """账号 → 虚拟设备名。只用邮箱摘要：AVD 名只允许字母数字和 ._-。"""
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]
    return f"acct_{digest}"


def avd_dir(paths: AndroidPaths, email: str) -> Path:
    return paths.avd / f"{avd_name(email)}.avd"


def avd_config(name: str) -> str:
    cores = _env_int("ANDROID_EMULATOR_CORES", 4, 1, 16)
    ram = _env_int("ANDROID_EMULATOR_RAM_MB", 2048, 1024, 16384)
    lines = {
        "AvdId": name,
        "avd.ini.displayname": name,
        "avd.ini.encoding": "UTF-8",
        "abi.type": "x86_64",
        "hw.cpu.arch": "x86_64",
        "hw.cpu.ncore": str(cores),
        "hw.ramSize": str(ram),
        "disk.dataPartition.size": "8G",
        "image.sysdir.1": SYSTEM_IMAGE_SYSDIR,
        "tag.id": "google_apis_playstore",
        "tag.display": "Google Play",
        "PlayStore.enabled": "true",
        "target": "android-34",
        "hw.lcd.width": str(DEVICE_WIDTH),
        "hw.lcd.height": str(DEVICE_HEIGHT),
        "hw.lcd.density": str(DEVICE_DENSITY),
        "hw.keyboard": "yes",
        "hw.mainKeys": "no",
        "hw.gpu.enabled": "yes",
        "hw.gpu.mode": "swiftshader_indirect",
        "hw.audioInput": "no",
        "hw.audioOutput": "no",
        "hw.camera.back": "none",
        "hw.camera.front": "none",
        "hw.initialOrientation": "portrait",
        "showDeviceFrame": "no",
        "fastboot.forceColdBoot": "yes",
    }
    return "".join(f"{k}={v}\n" for k, v in lines.items())


def ensure_avd(paths: AndroidPaths, email: str) -> str:
    """建好（或刷新配置）账号的虚拟设备，返回 AVD 名。数据盘由模拟器首次启动时创建。"""
    name = avd_name(email)
    directory = avd_dir(paths, email)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.ini").write_text(avd_config(name), encoding="utf-8")
    (directory / ACCOUNT_FILE).write_text(json.dumps({"email": email}), encoding="utf-8")
    (paths.avd / f"{name}.ini").write_text(
        f"avd.ini.encoding=UTF-8\npath={directory}\ntarget=android-34\n", encoding="utf-8",
    )
    return name


def list_envs(paths: AndroidPaths) -> list[str]:
    """已经建过虚拟设备的账号邮箱。"""
    out = []
    if not paths.avd.is_dir():
        return out
    for d in sorted(paths.avd.glob("acct_*.avd")):
        try:
            email = json.loads((d / ACCOUNT_FILE).read_text(encoding="utf-8")).get("email")
        except (OSError, ValueError):
            continue
        if isinstance(email, str) and email:
            out.append(email)
    return out


def delete_env(paths: AndroidPaths, email: str) -> bool:
    name = avd_name(email)
    directory = avd_dir(paths, email)
    existed = directory.exists()
    shutil.rmtree(directory, ignore_errors=True)
    try:
        (paths.avd / f"{name}.ini").unlink()
    except FileNotFoundError:
        pass
    return existed


def rename_env(paths: AndroidPaths, old_email: str, new_email: str) -> bool:
    """账号改邮箱时把虚拟设备一并改名；没有旧环境返回 False。"""
    old_dir = avd_dir(paths, old_email)
    if not old_dir.exists() or old_email == new_email:
        return False
    new_dir = avd_dir(paths, new_email)
    if new_dir.exists():
        raise InstallError("目标邮箱已有 Android 环境")
    old_dir.replace(new_dir)
    try:
        (paths.avd / f"{avd_name(old_email)}.ini").unlink()
    except FileNotFoundError:
        pass
    ensure_avd(paths, new_email)
    return True
