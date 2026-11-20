"""Android 模拟器运行环境：SDK 目录布局、按需下载安装、每个账号的 AVD。

运行环境不打进镜像：模拟器加系统镜像解压后约 9 GiB，每次发版都让部署机重新拉一遍
不划算。面板第一次需要时从 Google 官方仓库下载到数据目录（``data/android``），
之后长期复用。下载地址、大小和 SHA-256 都固定在 PACKAGES 里，换版本时一并更新。

目录布局（都在 ``data/android`` 下）::

    sdk/emulator/                    模拟器
    sdk/platform-tools/              adb
    sdk/system-images/android-37.2/google_apis_playstore_ps16k/x86_64/
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


SYSTEM_IMAGE_TARGET = "android-37.2"
SYSTEM_IMAGE_DIR = f"system-images/{SYSTEM_IMAGE_TARGET}/google_apis_playstore_ps16k/x86_64"
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
        key="system-image-37.2-google_apis_playstore_ps16k-x86_64-r7",
        label="Android 17 系统镜像（Google Play，API 37.2）",
        url=REPO + "sys-img/google_apis_playstore/x86_64-playstore-ps16k-37.2_r07.zip",
        size=2_453_971_990,
        sha256="ddcc2271a79c18eec4a74a38fb398f8980830641defdbeade920396a7e42bb1c",
        strip_prefix="x86_64/",
        dest=SYSTEM_IMAGE_DIR,
    ),
)

TOTAL_DOWNLOAD = sum(p.size for p in PACKAGES)
# 系统镜像包（解压后是 system.img/vendor.img 等分区镜像），安装后可能要做镜像层处理。
SYSTEM_IMAGE_PACKAGE = "system-image-37.2-google_apis_playstore_ps16k-x86_64-r7"
# 新系统镜像解压后占 8,774,608,505 字节，约为压缩包的 3.6 倍。
INSTALL_SPACE_FACTOR = 4.6  # 压缩包与解压目录在安装时并存
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


def image_mask_enabled() -> bool:
    """是否在安装后改写官方系统镜像；必须显式 ``ANDROID_IMAGE_MASK=1`` 才启用。

    官方 Play 镜像是 dm-verity + AVB 签名的可引导 super，改任何分区字节都会让
    客户机校验失败、系统起不来；不能用改图片段的方式“伪装真机”。默认保持原厂
    镜像，设备标识由 AVD 配置与启动参数提供。
    """
    return os.environ.get("ANDROID_IMAGE_MASK", "0").strip().lower() in ("1", "true", "yes")


def image_mask_paths(paths: AndroidPaths) -> Path:
    return paths.root / "image-mask"


def _read_mask_marker(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _post_install_mask(paths: AndroidPaths) -> None:
    """系统镜像装好后按开关决定是否做镜像层处理。

    处理失败只记录日志，不推翻已完成的安装：原厂镜像本身就能启动，`system.img`
    会先备份到 ``data/android/image-mask/``，回滚即“用 .orig 覆盖回去”。
    """
    if not image_mask_enabled():
        return
    pkg = next((p for p in PACKAGES if p.key == SYSTEM_IMAGE_PACKAGE), None)
    if pkg is None:
        return
    image = paths.sdk / pkg.dest / "system.img"
    if not image.exists():
        console.log(f"镜像层处理跳过：{image} 不存在")
        return
    marker_dir = image_mask_paths(paths)
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / "system.json"
    recorded = _read_mask_marker(marker)
    if recorded.get("source_sha256") == pkg.sha256:
        return
    try:
        from server import android_image

        result = android_image.SystemImageMasker(image, backup_dir=marker_dir).run()
    except Exception as exc:  # noqa: BLE001
        console.log(f"镜像层处理失败，保留原厂镜像：{exc}")
        return
    marker.write_text(json.dumps({
        "source_sha256": pkg.sha256,
        "image": str(image),
        "backup": str(result.backup) if result.backup else "",
        "substituted": result.report.substituted,
        "stripped": result.stripped,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(result.report.substituted.values())
    console.log(f"系统镜像已处理：{total} 处 prop 命中，原文件备份在 "
                f"{result.backup or marker_dir}")


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
        need = int(sum(p.size for p in pending) * INSTALL_SPACE_FACTOR) + 2 * 1024 ** 3
        free = self._disk_free(paths.root)
        if free < need:
            raise InstallError(
                f"磁盘空间不足：需要约 {need / 1024 ** 3:.1f} GB，当前只剩 {free / 1024 ** 3:.1f} GB"
            )
        for pkg in pending:
            self._install_one(pkg)
        _post_install_mask(paths)

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


# ---------------------------------------------------------------- 设备档案（Google 官方 check-in）

# 模拟器和 Google Play 运行在 Play 支持的 Google 系统镜像上。对外展示的「机型」如果
# 自造（官方镜像里不存在），Play 认证会直接失败；这里向 Google 官方 check-in 接口按
# 指定机型取一份真实存在的设备档案，档案里的参数（代号、系统版本、指纹）才是 Play
# 认可的，客户机的品牌/型号/序列号仍由账号自己的 identity 决定。
CHECKIN_URL = "https://android.googleapis.com/checkin"
CHECKIN_USER_AGENT = "Android-Checkin/2.0"
DEVICE_PROFILE_FILE = "device_profile.json"

ACCOUNT_FILE = "account.json"
IDENTITY_FILE = "identity.json"

# AVD 里的设备代号必须是 Google 官方 profile：Play 认证按它查机型，自造代号会失败。
DEVICE_MODEL = "Pixel 8 Pro"
SERIAL_ALPHABET = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"


@dataclass(frozen=True)
class DeviceIdentity:
    brand: str
    manufacturer: str
    model: str
    device: str
    serial: str
    imei: str
    meid: str
    android_id: str
    bluetooth: str
    wifi_mac: str

    def as_dict(self) -> dict[str, str]:
        return {
            "brand": self.brand, "manufacturer": self.manufacturer,
            "model": self.model, "device": self.device, "serial": self.serial,
            "imei": self.imei, "meid": self.meid, "android_id": self.android_id,
            "bluetooth": self.bluetooth, "wifi_mac": self.wifi_mac,
        }


def _digest(seed: str) -> bytes:
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _decimal_digits(seed: str, n: int) -> str:
    out = []
    i = 0
    while len(out) < n:
        out.extend(str(b % 10) for b in _digest(f"{seed}#{i}"))
        i += 1
    return "".join(out[:n])


def _luhn_tail(prefix: str) -> str:
    """补一位 Luhn 校验位，让 IMEI 过得了常见的格式校验。"""
    total = 0
    for pos, ch in enumerate(reversed(prefix)):
        digit = int(ch)
        if pos % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def device_identity(email: str) -> DeviceIdentity:
    """按账号稳定地生成一套标识；同一邮箱每次得到相同结果。

    机型固定为 Google 官方 Pixel 8 Pro：代号必须能被 Play 认证识别，不能自造。
    """
    seed = email.strip().lower()
    imei = "35" + _decimal_digits(seed + ":imei", 12)
    meid_hex = _digest(f"{seed}:meid").hex()[:14]
    bluetooth_bytes = _digest(f"{seed}:bt")[:3]
    wifi_bytes = _digest(f"{seed}:wifi")
    # 两个 MAC 的首字节都清低位再置本地管理位：像设备厂按批次分配的地址。
    bluetooth = "0" + "2:00:00:" + ":".join(f"{b:02x}" for b in bluetooth_bytes)
    wifi_mac = ":".join(f"{b:02x}" for b in _local_mac(wifi_bytes))
    serial_bytes = _digest(f"{seed}:serial").hex().upper()
    return DeviceIdentity(
        brand="google", manufacturer="Google", model=DEVICE_MODEL, device="husky",
        serial="".join(SERIAL_ALPHABET[int(c, 16) % len(SERIAL_ALPHABET)] for c in serial_bytes[:12]),
        imei=imei + _luhn_tail(imei),
        meid="A" + "".join(SERIAL_ALPHABET[int(c, 16) % 16] for c in meid_hex),
        android_id=_digest(f"{seed}:android-id").hex()[:16],
        bluetooth=bluetooth,
        wifi_mac=wifi_mac,
    )


def _local_mac(raw: bytes) -> bytes:
    first = (raw[0] | 0x02) & 0xFE
    return bytes([first, *raw[1:6]])


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field(number: int, payload: bytes) -> bytes:
    """protobuf 的 length-delimited 字段。"""
    return _varint(number << 3 | 2) + _varint(len(payload)) + payload


def checkin_request(identity: DeviceIdentity, account: str) -> bytes:
    """最小可用的 checkin 请求：account、设备 id、型号、系统版本、时间区。"""
    request = _field(1, b"2.0")
    request += _field(2, identity.android_id.encode())
    request += _field(4, _field(1, identity.device.encode()))
    request += _field(4, _field(2, identity.model.encode()))
    request += _field(4, _field(3, b"google"))
    request += _field(4, _field(5, SYSTEM_IMAGE_TARGET.encode()))
    request += _field(6, account.strip().lower().encode())
    return request


def parse_checkin_response(data: bytes) -> dict:
    """取 checkin 响应里设备档案的文本字段；未知字段直接跳过。"""
    def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
        value = shift = 0
        while True:
            byte = buf[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value, pos
            shift += 7

    def walk(buf: bytes) -> list[tuple[int, bytes]]:
        fields = []
        pos = 0
        while pos < len(buf):
            tag, pos = read_varint(buf, pos)
            number, wire = tag >> 3, tag & 7
            if wire == 2:
                length, pos = read_varint(buf, pos)
                payload = buf[pos:pos + length]
                pos += length
            elif wire == 0:
                _, pos = read_varint(buf, pos)
                payload = b""
            else:
                break
            fields.append((number, payload))
        return fields

    profile = {}
    for number, payload in walk(data):
        if number != 1:
            continue
        for sub_number, value in walk(payload):
            profile[f"checkin_{sub_number}"] = value.hex()
    return profile


def device_profile(account: str, *, timeout: float = 20.0) -> dict:
    """向 Google check-in 取一份按账号的设备档案；失败时返回空字典，不阻断启动。"""
    import urllib.request

    identity = device_identity(account)
    request = urllib.request.Request(
        CHECKIN_URL, data=checkin_request(identity, account),
        headers={"User-Agent": CHECKIN_USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return parse_checkin_response(response.read())


def save_device_profile(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_device_profile(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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


SCREEN_KEYS = ("hw.lcd.width", "hw.lcd.height", "hw.lcd.density")


def avd_config(name: str, identity: DeviceIdentity | None = None,
               screen: tuple[int, int, int] | None = None) -> str:
    """账号 AVD 配置：官方可认证机型 + 账号自己的标识。

    设备代号必须是 Google 官方 profile（当前 Pixel 8 Pro / husky）：模拟器内核按它
    写 ro.product.*，Play 认证拿 (代号, 镜像, 系统指纹) 去查机型；改代号就会在商店
    报设备认证失败。屏幕尺寸首次启动取环境变量，之后随账号长期保留。
    """
    identity = identity or device_identity(name)
    cores = _env_int("ANDROID_EMULATOR_CORES", 4, 1, 16)
    ram = _env_int("ANDROID_EMULATOR_RAM_MB", 2048, 1024, 16384)
    width, height, density = screen or (720, 1600, 320)
    lines = {
        "AvdId": name,
        "avd.ini.displayname": identity.model,
        "avd.ini.encoding": "UTF-8",
        "abi.type": "x86_64",
        "hw.cpu.arch": "x86_64",
        "hw.cpu.ncore": str(cores),
        "hw.ramSize": str(ram),
        "hw.lcd.width": str(width),
        "hw.lcd.height": str(height),
        "hw.lcd.density": str(density),
        "hw.device.name": identity.device,
        "hw.device.manufacturer": identity.manufacturer,
        "hw.manufacturer": identity.manufacturer,
        "hw.model": identity.model,
        "hw.board": identity.device,
        "hw.brand": identity.brand,
        "hw.device": identity.device,
        "hw.keyboard": "yes",
        "hw.mainKeys": "no",
        "hw.gpu.enabled": "yes",
        "hw.gpu.mode": "swiftshader_indirect",
        # 真机都有扬声器、麦克风和前后摄像头；"none" 本身就是模拟器特征。
        "hw.audioInput": "yes",
        "hw.audioOutput": "yes",
        "hw.camera.back": "emulated",
        "hw.camera.front": "emulated",
        # 真机的传感器、GPS 与通话能力；关掉这些等于告诉应用“这里是模拟器”。
        "hw.sensors.accelerometer": "yes",
        "hw.sensors.gyroscope": "yes",
        "hw.sensors.magnetometer": "yes",
        "hw.sensors.proximity": "yes",
        "hw.sensors.light": "yes",
        "hw.sensors.pressure": "yes",
        "hw.sensors.humidity": "yes",
        "hw.sensors.temperature": "yes",
        "hw.gps": "yes",
        "hw.gsmSim": "yes",
        "hw.arc": "no",
        "showDeviceFrame": "no",
        "disk.dataPartition.size": "8G",
        "image.sysdir.1": SYSTEM_IMAGE_SYSDIR,
        "tag.id": "google_apis_playstore",
        "tag.display": "Google Play",
        "PlayStore.enabled": "true",
        "target": SYSTEM_IMAGE_TARGET,
        "hw.initialOrientation": "portrait",
        "fastboot.forceColdBoot": "yes",
    }
    return "".join(f"{k}={v}\n" for k, v in lines.items())


def existing_screen(device: Path) -> tuple[int, int, int] | None:
    """读已存在 AVD 的屏幕参数；没有完整的三项时返回 None。"""
    path = device / "config.ini"
    try:
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key in SCREEN_KEYS and value.isdigit():
                values[key] = int(value)
    except OSError:
        return None
    if len(values) != len(SCREEN_KEYS):
        return None
    return values["hw.lcd.width"], values["hw.lcd.height"], values["hw.lcd.density"]


def guest_properties(identity: DeviceIdentity) -> list[tuple[str, str]]:
    """开机后经 adb 补写的属性 (属性名, 值)。

    Android 16+ 只允许 shell 用户改自己命名空间里的属性；``ro.*`` 全部归 init
    所有，``setprop ro.xxx`` 一律被拒（即使值是同一个）。所以这里只列真正可写
    的 SIM 状态类属性，``ro.*`` 由 AVD 配置与 ``-prop`` 参数在开机时注入。
    """
    return [
        ("gsm.sim.serial", identity.serial),
        ("gsm.sim.imei", identity.imei),
        ("gsm.sim.meid", identity.meid),
        ("gsm.sim.operator.alpha", ""),
        ("gsm.sim.operator.numeric", ""),
        ("gsm.sim.operator.iso-country", ""),
        ("gsm.operator.alpha", ""),
        ("gsm.operator.numeric", ""),
    ]


def emulator_props(identity: DeviceIdentity) -> list[tuple[str, str]]:
    """以 ``-prop`` 交给模拟器的属性（只在启动时注入，开机后不能再改）。

    这些都是真实 Pixel 的设备标识。``ro.kernel.qemu``、``ro.hardware`` 一类由
    模拟器内核与镜像决定，``-prop`` 覆盖不了，也不该在这里假装能改。
    """
    return [
        ("ro.serialno", identity.serial),
        ("ro.boot.serialno", identity.serial),
        ("ro.product.model", identity.model),
        ("ro.product.brand", identity.brand),
        ("ro.product.manufacturer", identity.manufacturer),
        ("ro.product.device", identity.device),
        ("ro.product.board", identity.device),
    ]


def screen_size(paths: AndroidPaths, email: str) -> tuple[int, int, int]:
    """账号 AVD 的屏幕 (宽, 高, 密度)；设备还没建时用默认值。"""
    return existing_screen(avd_dir(paths, email) / SYSTEM_IMAGE_TARGET) or (720, 1600, 320)


def ensure_avd(paths: AndroidPaths, email: str) -> str:
    """建好（或刷新配置）账号的虚拟设备，返回 AVD 名。数据盘由模拟器首次启动时创建。"""
    name = avd_name(email)
    directory = avd_dir(paths, email)
    identity = device_identity(email)
    # Android 14 的盘在账号目录顶层。跨系统版本使用独立目录，旧系统可原地回滚。
    device = directory / SYSTEM_IMAGE_TARGET
    device.mkdir(parents=True, exist_ok=True)
    screen = existing_screen(device) or (
        _env_int("ANDROID_EMULATOR_WIDTH", 720, 480, 2160),
        _env_int("ANDROID_EMULATOR_HEIGHT", 1600, 854, 3840),
        _env_int("ANDROID_EMULATOR_DENSITY", 320, 160, 640),
    )
    (device / "config.ini").write_text(avd_config(name, identity, screen), encoding="utf-8")
    (directory / ACCOUNT_FILE).write_text(json.dumps({"email": email}), encoding="utf-8")
    (directory / IDENTITY_FILE).write_text(
        json.dumps({"email": email, **identity.as_dict()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (paths.avd / f"{name}.ini").write_text(
        f"avd.ini.encoding=UTF-8\npath={device}\ntarget={SYSTEM_IMAGE_TARGET}\n", encoding="utf-8",
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
