"""系统镜像层的模拟器特征清洗：改写 prop、处理 AVB 尾部。

Google 官方 Play 系统镜像（``sys-img/google_apis_playstore/x86_64-playstore-ps16k``）
解压后是一张 GPT 盘：``vbmeta`` 分区 + ``super`` 分区（Android 动态分区）。
``super`` 里的 ``vendor`` 分区的 ``build.prop`` 带着 ``ro.kernel.qemu=1``、
``ro.hardware.power=ranchu`` 这类模拟器标记，``system`` 分区带着 ``dev-keys``
指纹。客户机起来后 ``ro.*`` 归 init 所有，adb 的 ``setprop`` 一律被拒，
只有在这里改才生效。

本模块只碰镜像文件，不启动模拟器；所有入口都能用合成镜像在测试里驱动。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

GPT_SIGNATURE = b"EFI PART"
AVB_MAGIC = b"AVB0"
PROP_LINE = re.compile(r"^([A-Za-z0-9_.\-]+)=(.*)$")


class MaskError(Exception):
    pass


# 真机 Pixel 8 Pro（husky）release 形态的取值，指纹格式为
# ``brand/product/device:version/id/incremental:type/tags``。
RELEASE_FINGERPRINT = "google/husky/husky:16/BP2A.250705.008/12909901:user/release-keys"
RELEASE_ID = "BP2A.250705.008"
RELEASE_BRAND = "google"
RELEASE_MODEL = "husky"

# 官方 Play 镜像里实际存在、且能改掉的模拟器痕迹。
EMULATOR_PROP_RULES: tuple[tuple[str, str], ...] = (
    ("ro.kernel.qemu", "0"),
    ("ro.boot.qemu", "0"),
    ("ro.hardware.power", "husky"),
    ("ro.hardware.virtual_device", "0"),
    ("ro.boot.hardware", "husky"),
    ("ro.hardware", "husky"),
    ("ro.build.tags", "release-keys"),
    ("ro.system.build.tags", "release-keys"),
    ("ro.vendor.build.tags", "release-keys"),
    ("ro.build.type", "user"),
    ("ro.system.build.type", "user"),
    ("ro.vendor.build.type", "user"),
    ("ro.build.display.id", RELEASE_ID),
    ("ro.build.id", RELEASE_ID),
    ("ro.build.version.incremental", "12909901"),
    ("ro.debuggable", "0"),
    ("ro.secure", "1"),
    ("ro.adb.secure", "1"),
    ("ro.product.device", "husky"),
    ("ro.product.system.device", "husky"),
    ("ro.vendor.product.device", "husky"),
    ("ro.product.brand", RELEASE_BRAND),
    ("ro.product.system.brand", RELEASE_BRAND),
    ("ro.vendor.product.brand", RELEASE_BRAND),
    ("ro.product.model", "Pixel 8 Pro"),
    ("ro.product.system.model", "Pixel 8 Pro"),
    ("ro.vendor.product.model", "Pixel 8 Pro"),
    ("ro.product.name", "husky"),
    ("ro.product.system.name", "husky"),
    ("ro.vendor.product.name", "husky"),
    ("ro.product.manufacturer", "Google"),
    ("ro.product.system.manufacturer", "Google"),
    ("ro.vendor.product.manufacturer", "Google"),
    ("ro.product.board", "husky"),
    ("ro.build.product", "husky"),
    ("ro.system.build.product", "husky"),
    ("ro.vendor.build.product", "husky"),
    ("ro.system.build.id", RELEASE_ID),
    ("ro.vendor.build.id", RELEASE_ID),
    ("ro.system.build.version.incremental", "12909901"),
    ("ro.vendor.build.version.incremental", "12909901"),
    ("ro.system_ext.build.tags", "release-keys"),
    ("ro.system_ext.build.type", "user"),
    ("ro.system_ext.build.fingerprint", RELEASE_FINGERPRINT),
)

# 各分区各有一份指纹，不一致本身就会被 GMS 当成改机，统一成同一句。
FINGERPRINT_KEYS = (
    "ro.build.fingerprint",
    "ro.system.build.fingerprint",
    "ro.vendor.build.fingerprint",
    "ro.system_ext.build.fingerprint",
    "ro.product.build.fingerprint",
    "ro.odm.build.fingerprint",
    "ro.bootimage.build.fingerprint",
    "ro.vendor_dlkm.build.fingerprint",
    "ro.system_dlkm.build.fingerprint",
)

PARTITION_FILES = ("system.img", "vendor.img", "system_ext.img", "product.img",
                   "system_dlkm.img")


def masking_rules(fingerprint: str = RELEASE_FINGERPRINT) -> dict[str, str]:
    rules = dict(EMULATOR_PROP_RULES)
    rules.update({key: fingerprint for key in FINGERPRINT_KEYS})
    return rules


def rewrite_props(text: str, rules: dict[str, str]) -> tuple[str, list[tuple[str, str, str]]]:
    """按行改写 ``key=value`` 形式的 prop，返回新文本与改动 ``(键, 旧值, 新值)``。

    注释、空行、键的顺序都原样保留；镜像里缺失的键追加到末尾。``init`` 按顺序
    读这些行，追加在末尾不会改变已有属性的生效顺序。
    """
    changes: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    out: list[str] = []
    for raw in text.splitlines(keepends=True):
        newline = raw[len(raw.rstrip("\r\n")):] or ("\n" if "\n" in raw else "")
        body = raw[:len(raw) - len(newline)] if newline else raw
        match = PROP_LINE.match(body)
        if match and match.group(1) in rules:
            key, old = match.group(1), match.group(2)
            new = rules[key]
            seen.add(key)
            if new != old:
                changes.append((key, old, new))
                body = f"{key}={new}"
        out.append(body + newline)
    for key, new in rules.items():
        if key in seen:
            continue
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{key}={new}\n")
        changes.append((key, "", new))
    return "".join(out), changes


# ---------------------------------------------------------------- GPT 与 AVB


@dataclass(frozen=True)
class GptEntry:
    index: int
    name: str
    start_lba: int
    end_lba: int

    @property
    def offset(self) -> int:
        return self.start_lba * 512

    @property
    def size(self) -> int:
        return (self.end_lba - self.start_lba + 1) * 512


def read_gpt(path: Path) -> list[GptEntry]:
    """读 GPT 分区表；没有 GPT（单独的分区镜像）时返回空列表。"""
    with open(path, "rb") as f:
        f.seek(512)
        header = f.read(92)
        if len(header) < 92 or not header.startswith(GPT_SIGNATURE):
            return []
        entries_lba = struct.unpack_from("<Q", header, 72)[0]
        count = struct.unpack_from("<I", header, 80)[0]
        entry_size = struct.unpack_from("<I", header, 84)[0]
        if not entries_lba or not count or entry_size < 128:
            return []
        f.seek(entries_lba * 512)
        raw = f.read(count * entry_size)
    entries: list[GptEntry] = []
    for i in range(count):
        entry = raw[i * entry_size:(i + 1) * entry_size]
        if len(entry) < 128 or entry[:16] == bytes(16):
            continue
        start, end = struct.unpack_from("<QQ", entry, 32)
        name = entry[56:128].decode("utf-16-le", "replace").rstrip("\0")
        entries.append(GptEntry(i, name, start, end))
    return entries


@dataclass(frozen=True)
class VbmetaInfo:
    """AVB 描述符里与分区尾巴有关的信息。"""

    partition: str
    kind: str
    image_size: int = 0
    tree_offset: int = 0
    tree_size: int = 0
    fec_offset: int = 0
    fec_size: int = 0
    rollback_index_location: int = 0

    @property
    def trailer_end(self) -> int:
        """分区里“数据 + 哈希树 + FEC”的总长度；0 表示没有这类描述符。"""
        return max(self.image_size, self.tree_offset + self.tree_size,
                   self.fec_offset + self.fec_size)


def parse_vbmeta(data: bytes) -> list[VbmetaInfo]:
    """解析 AVB 头部与描述符。

    布局见 ``avbtool``：256 字节头部，后跟 authentication block 与 auxiliary
    block，再往后是描述符（``tag``、``num_bytes_following``、预留 8 字节 offset）。
    未知的 descriptor tag 按长度跳过，不认识的版本也不会误判。
    """
    if not data.startswith(AVB_MAGIC):
        raise MaskError("不是 AVB 镜像：缺少 AVB0 魔数")
    auth_size = struct.unpack_from("<Q", data, 12)[0]
    aux_size = struct.unpack_from("<Q", data, 20)[0]
    pos = 256 + auth_size + aux_size
    infos: list[VbmetaInfo] = []
    while pos + 16 <= len(data):
        tag, length, _offset = struct.unpack_from("<QII", data, pos)
        if tag == 0 or length < 16 or pos + length > len(data):
            break
        body = data[pos:pos + length]
        if tag == 1:  # AvbHashDescriptor: partition_name[32] at 16, image_size at 48
            name = body[16:48].split(b"\0", 1)[0].decode("utf-8", "replace")
            image_size = struct.unpack_from("<Q", body, 48)[0]
            infos.append(VbmetaInfo(name, "hash", image_size))
        elif tag == 2:  # AvbHashtreeDescriptor
            name = body[20:52].split(b"\0", 1)[0].decode("utf-8", "replace")
            image_size = struct.unpack_from("<Q", body, 56)[0]
            tree_offset = struct.unpack_from("<Q", body, 64)[0]
            tree_size, _data_block, _hash_block = struct.unpack_from("<III", body, 72)
            fec_offset = fec_size = 0
            if len(body) >= 120:
                fec_offset, fec_size = struct.unpack_from("<QQ", body, 104)
            infos.append(VbmetaInfo(name, "hashtree", image_size, tree_offset, tree_size,
                                    fec_offset, fec_size))
        elif tag == 3:  # AvbChainPartitionDescriptor
            name = body[16:48].split(b"\0", 1)[0].decode("utf-8", "replace")
            rollback = struct.unpack_from("<I", body, 48)[0]
            infos.append(VbmetaInfo(name, "chain", rollback_index_location=rollback))
        pos += length
    return infos


def strip_vbmeta_bytes(data: bytes) -> tuple[list[str], bytes]:
    """把哈希树与 FEC 抹掉，保留头部、签名与描述符（分区表还看得见）。"""
    notes: list[str] = []
    out = bytearray(data)
    for info in parse_vbmeta(data):
        end = info.trailer_end
        if info.kind == "hashtree" and end and end < len(out) and any(out[end:]):
            notes.append(f"清零 {info.partition} 哈希树/FEC（{end} 字节起）")
            out[end:] = bytes(len(out) - end)
    return notes, bytes(out)


def avbtool_path(tool: str | Path | None = None) -> str | None:
    if tool:
        path = Path(tool)
        return str(path) if path.exists() else None
    return shutil.which("avbtool")


def resign_vbmeta(avbtool: str | Path, key: Path, vbmeta: Path,
                  algorithm: str = "SHA256_RSA4096") -> None:
    """用给定私钥重签裸 vbmeta（与 Google testkey 的 RSA-4096 + SHA-256 对称）。"""
    size = vbmeta.stat().st_size
    cmd = [str(avbtool), "add_hash_footer", "--image", str(vbmeta),
           "--partition_size", str(size), "--partition_name", "vbmeta",
           "--key", str(key), "--algorithm", algorithm, "--rollback_index", "0"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MaskError((proc.stderr or proc.stdout).strip() or "avbtool 重签失败")


# ---------------------------------------------------------------- 分区里的 prop


@dataclass
class MaskReport:
    substituted: dict[str, int] = field(default_factory=dict)
    identical: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)

    missing: list[str] = field(default_factory=list)


@dataclass
class ImageMaskResult:
    image: Path
    backup: Path | None
    report: MaskReport
    stripped: list[str] = field(default_factory=list)
    vbmeta_unsigned: Path | None = None


def mask_prop_bytes(data: bytes, rules: dict[str, str]) -> tuple[bytes, dict[str, int]]:
    """在分区镜像里原地替换 ``key=旧值`` 形式的行。

    prop 文本夹在 EROFS 的压缩块里，直接按 ASCII 串找 ``键=`` 后跟到下一个
    ``NUL``/换行。新旧值不等长时把剩余字节用空格补齐，文件总大小不变，EROFS
    的块布局与 inode 大小都不受影响。
    """
    raw = bytes(data)
    out = bytearray(raw)
    counts: dict[str, int] = {}
    for key, new in rules.items():
        pattern = re.compile(rb"(?<![A-Za-z0-9_.-])" + re.escape(key.encode())
                             + rb"=[^\x00\n]*")
        hits = 0
        encoded = new.encode()
        for match in pattern.finditer(raw):
            start, end = match.span()
            old = raw[start + len(key) + 1:end]
            if old == encoded:
                hits += 1
                continue
            room = end - start - len(key) - 1
            value = encoded[:room].ljust(room, b" ")
            out[start:end] = key.encode() + b"=" + value
            hits += 1
        if hits:
            counts[key] = hits
    return bytes(out), counts


def backup_path(image: Path, backup_dir: Path) -> Path:
    return Path(backup_dir) / (Path(image).name + ".orig")


def mask_image_file(image: Path, *, rules: dict[str, str] | None = None,
                    backup_dir: Path | None = None, dry_run: bool = False,
                    patch_bytes: bool = False) -> ImageMaskResult:
    """处理单个镜像文件（GPT 盘或单独的分区映像）。

    - ``patch_bytes=False``（默认）：先把文件解成“结构可见”的字节再改，只适合
      未压缩的分区（``vbmeta`` 盘）。真正的 super 子分区由
      :class:`SystemImageMasker` 负责。
    - ``dry_run``：只算改动和备份，不写回。
    """
    image = Path(image)
    rules = masking_rules() if rules is None else rules
    backup_dir = Path(backup_dir) if backup_dir else image.parent / "backup"
    original = image.read_bytes()
    entries = read_gpt(image)
    ranges = [(e.offset, e.size, e.name) for e in entries] or [(0, len(original), image.name)]
    report = MaskReport()
    work = bytearray(original)
    if patch_bytes:
        for (offset, size, name) in ranges:
            data = bytes(original[offset:offset + size])
            changed, counts = mask_prop_bytes(data, rules)
            if counts:
                work[offset:offset + size] = changed
                report.files.append(f"{name}: {sum(counts.values())} 处")
                for key, n in counts.items():
                    report.substituted[key] = report.substituted.get(key, 0) + n
            else:
                report.identical.append(name)
    stripped: list[str] = []
    vbmeta_unsigned = None
    vbmeta_entry = next((e for e in entries if e.name == "vbmeta"), None)
    if vbmeta_entry is not None:
        vb = bytes(original[vbmeta_entry.offset:vbmeta_entry.offset + vbmeta_entry.size])
        if vb.startswith(AVB_MAGIC):
            notes, cleaned = strip_vbmeta_bytes(vb)
            stripped.extend(notes)
            vbmeta_unsigned = image.with_name(image.name + ".vbmeta.unsigned")
            vbmeta_unsigned.write_bytes(cleaned)
            work[vbmeta_entry.offset:vbmeta_entry.offset + vbmeta_entry.size] = (
                cleaned + bytes(vbmeta_entry.size - len(cleaned)))
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_path(image, backup_dir)
    if not dry_run:
        if not backup.exists():
            backup.write_bytes(original)
        image.write_bytes(work)
        record_hash(backup, backup_dir)
    return ImageMaskResult(image=image, backup=backup if backup.exists() else None,
                           report=report, stripped=stripped,
                           vbmeta_unsigned=vbmeta_unsigned)


def record_hash(backup: Path, backup_dir: Path) -> str:
    """在备份目录的清单里登记备份文件的 SHA-256，便于以后核对原始镜像。"""
    digest = hashlib.sha256(Path(backup).read_bytes()).hexdigest()
    manifest = Path(backup_dir) / "masked-images.sha256"
    lines = []
    if manifest.exists():
        lines = [ln for ln in manifest.read_text().splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.endswith(f"  {Path(backup).name}")]
    lines.append(f"{digest}  {Path(backup).name}")
    manifest.write_text("\n".join(lines) + "\n")
    return digest


# ---------------------------------------------------------------- super 布局
#
# 官方 Play 镜像的 ``system.img`` 是 GPT 盘：开头 ``vbmeta``（1 MiB），随后是
# ``super``。super 的 liblp 元数据在发布时被裁掉，只剩零填充的保留块和几页
# 孤立的旧元数据；各分区数据实际从 ``super + 2 MiB`` 起连续排布。
#
# 真实数据区里，横跨块边界的 ``key=value`` 不能按块独立替换，所以整盘先按
# 字节流扫描一次，把命中位置与等长替换记下来（重叠命中取并集），再一次性
# 按块重写。这样既不改文件长度，也不会被 EROFS 的压缩块布局影响。

LP_AVB_OFFSET = 0x100000
LP_DATA_OFFSET = 0x300000
LP_SHIFT = LP_DATA_OFFSET - LP_AVB_OFFSET
CMDLINE_TOKENS: tuple[tuple[str, str], ...] = (
    ("ro.build.tags=dev-keys", "ro.build.tags=release-keys  "),
    ("ro.debuggable=1", "ro.debuggable=0"),
    ("androidboot.hardware=ranchu", "androidboot.hardware=husky "),
    ("androidboot.qemu", "androidboot.wifi_sec  "),
    ("ro.hardware=ranchu", "ro.hardware=husky  "),
    ("qemu_pipe", "virtio  "),
    ("ranchu", "husky "),
    ("goldfish", "virtio  "),
    ("emu64xa", "husky   "),
)


@dataclass(frozen=True)
class SuperLayout:
    """``system.img``（GPT + super）的布局。"""

    gpt: tuple[GptEntry, ...]
    offset: int
    size: int
    data_offset: int
    shift: int


def _read_range(f, offset: int, length: int) -> bytes:
    f.seek(offset)
    data = f.read(length)
    if len(data) != length:
        raise MaskError("读取镜像失败：文件被截断")
    return data


def _copy_range(src, out, offset: int, end: int, chunk: int) -> int:
    total = 0
    while offset < end:
        n = min(chunk, end - offset)
        out.write(_read_range(src, offset, n))
        offset += n
        total += n
    return total


def read_super_layout(image: Path) -> SuperLayout:
    """从 GPT 里定位 ``super``，数据起点固定在其后 3 MiB。

    官方镜像的 liblp 元数据被裁成一份“可引导 super”：0–2 MiB 是 super 自己的
    AVB 尾部（哈希树 + FEC），2–3 MiB 是 liblp 几何头、槽位元数据与备份副本，
    3 MiB 起才是分区数据。这里按这个固件约定取偏移；没有 ``super`` 分区的镜像
    直接报错，避免把别的盘当成 Play 系统镜像。
    """
    image = Path(image)
    entries = tuple(read_gpt(image))
    super_part = next((e for e in entries if e.name == "super"), None)
    if super_part is None:
        raise MaskError("不是官方 Play 系统镜像：GPT 里没有 super 分区")
    data_offset = super_part.offset + LP_DATA_OFFSET
    return SuperLayout(entries, super_part.offset, super_part.size, data_offset,
                       data_offset - super_part.offset)


@dataclass(frozen=True)
class Replacement:
    start: int
    end: int
    data: bytes
    key: str = ""


def find_replacements(data: bytes, rules: dict[str, str], offset: int = 0) -> list[Replacement]:
    """在 ``data`` 里找 ``key=旧值`` 形式的属性值，返回等长替换。

    旧值向后延伸到 NUL 或换行；新值超出原长度时截断、不足时补空格，
    保证 ``end - start`` 与替换长度一致。属性键前面不能紧贴字母、数字、
    ``_``、``.``、``-``，避免误中 ``ro.foo_ro.bar`` 之类的前缀。
    """
    out: list[Replacement] = []
    taken: list[tuple[int, int]] = []
    for key, new in rules.items():
        needle = key.encode() + b"="
        encoded = new.encode()
        pos = data.find(needle)
        while pos >= 0:
            prev = data[pos - 1] if pos > 0 else 0
            if prev == 0 or not (chr(prev).isalnum() or prev in b"_.-"):
                start = pos + len(needle)
                end = start
                limit = min(len(data), start + len(encoded) + 8192)
                while end < limit and data[end] not in b"\x00\n":
                    end += 1
                if not any(start < t_end and t_start < end for t_start, t_end in taken):
                    taken.append((start, end))
                    value = encoded[:end - start]
                    if value != data[start:end]:
                        out.append(Replacement(offset + start, offset + end,
                                               value.ljust(end - start, b" ", ), key))
            pos = data.find(needle, pos + 1)
    out.sort(key=lambda r: r.start)
    return out


def apply_replacements(chunk: bytes, chunk_start: int,
                       replacements: list[Replacement]) -> bytes:
    """把与 ``chunk`` 相交的替换应用上去（替换可能跨块边界）。"""
    out = bytearray(chunk)
    view_end = chunk_start + len(chunk)
    for rep in replacements:
        lo = max(rep.start, chunk_start)
        hi = min(rep.end, view_end)
        if lo >= hi:
            continue
        out[lo - chunk_start:hi - chunk_start] = rep.data[lo - rep.start:hi - rep.start]
    return bytes(out)


def shift_super_data(image: Path, *, dry_run: bool = False) -> int:
    """把 ``super`` 的分区数据整体前移覆盖头部（原地、按块倒序）。"""
    image = Path(image)
    layout = read_super_layout(image)
    shift = layout.shift
    if shift <= 0:
        return 0
    if dry_run:
        return layout.size - shift
    block = 8 << 20
    end = layout.offset + layout.size
    with open(image, "r+b") as f:
        cursor = end
        while cursor > layout.data_offset:
            n = min(block, cursor - layout.data_offset)
            start = cursor - n
            f.seek(start)
            chunk = f.read(n)
            f.seek(start - shift)
            f.write(chunk)
            cursor = start
        f.seek(end - shift)
        f.write(bytes(shift))
    return layout.size - shift


def zero_vbmeta_trailer(image: Path, vbmeta: GptEntry, *, dry_run: bool = False) -> list[str]:
    """清空 ``vbmeta`` 里哈希树/FEC 的字节（保留签名头与描述符）。"""
    with open(image, "r+b" if not dry_run else "rb") as f:
        f.seek(vbmeta.offset)
        raw = f.read(vbmeta.size)
        if not raw.startswith(AVB_MAGIC):
            return []
        notes, cleaned = strip_vbmeta_bytes(raw)
        if not dry_run and cleaned != raw:
            f.seek(vbmeta.offset)
            f.write(cleaned + bytes(vbmeta.size - len(cleaned)))
    return notes


# ---------------------------------------------------------------- 整盘预演


def find_cmdline_tokens(data: bytes, offset: int = 0) -> list[Replacement]:
    """内核 cmdline 在镜像里的模拟器痕迹（``console=ttyS0``、``ranchu`` 等）。

    模拟器把 ``-prop ro.build.tags=dev-keys`` 这类参数拼进 cmdline 交给内核，
    客户机起来后只读；官方镜像里这些字串以 NUL 结尾、带前导空格的独立单词
    形式出现。替换为等长的真机取值，余下用空格补足。
    """
    out: list[Replacement] = []
    for old, new in CMDLINE_TOKENS:
        data_old = old.encode()
        encoded = new.encode()
        if len(encoded) > len(data_old):
            continue
        pos = data.find(data_old)
        while pos >= 0:
            prev = data[pos - 1] if pos > 0 else 0
            nxt = data[pos + len(data_old)] if pos + len(data_old) < len(data) else 0
            if (prev in (0, 0x20) and nxt in (0, 0x20, 0x0A)):
                out.append(Replacement(offset + pos, offset + pos + len(data_old),
                                       encoded.ljust(len(data_old), b" "), old))
            pos = data.find(data_old, pos + 1)
    return out


def plan_mask(image: Path, data_end: int | None = None, block: int = 1 << 20) -> dict:
    """预演变掩：统计各 prop 出现次数、``dev-keys``/``ranchu`` 残留与所在偏移。

    只按块顺序读，不写文件；用于在真机上验证“改得到哪些字节”。``offsets``
    里只保留每个键的前几个命中，避免报告过大。
    """
    rules = masking_rules()
    counts: dict[str, int] = {}
    offsets: dict[str, list[int]] = {}
    emulator_hits: list[tuple[int, str]] = []
    total = 0
    with open(image, "rb") as f:
        pos = 0
        while True:
            chunk = f.read(block)
            if not chunk:
                break
            if data_end is not None and pos >= data_end:
                break
            total += len(chunk)
            for key in rules:
                needle = key.encode()
                idx = chunk.find(needle)
                while idx >= 0:
                    counts[key] = counts.get(key, 0) + 1
                    if len(offsets.setdefault(key, [])) < 4:
                        offsets[key].append(pos + idx)
                    idx = chunk.find(needle, idx + 1)
            for token in (b"dev-keys", b"ranchu", b"emu64xa", b"sdk_gphone"):
                idx = chunk.find(token)
                while idx >= 0:
                    if len(emulator_hits) < 40:
                        emulator_hits.append((pos + idx, token.decode()))
                    idx = chunk.find(token, idx + 1)
            pos += len(chunk)
    return {"bytes": total, "counts": counts, "offsets": offsets,
            "emulator_hits": emulator_hits}


@dataclass
class SystemImageMasker:
    """``system.img``（GPT + vbmeta + super）的系统镜像处理器。

    官方 Play 镜像的 super 是 AVB 签名的可引导 super：super 起始的 0–2 MiB 是
    它自己的 AVB 尾部（哈希树 + FEC），2–3 MiB 是 liblp 几何头与分区表，3 MiB
    起才是分区数据。分区（system/vendor/product…）又各自带 dm-verity 哈希树，
    ``vbmeta`` 通过链式描述符把三者串在一起。

    这个处理器做的是**实验性**的深度改写：

    - 把 3 MiB 起的分区数据整体前移 2 MiB，覆盖 super 自身的 AVB 尾部与 liblp
      头，让分区数据从 super + 1 MiB 处连续开始；
    - 在数据区里按字节流扫出 ``prop`` 命中并做等长替换；
    - 清空外层 ``vbmeta`` 里已失效的哈希树/FEC 字节，保留签名头与描述符。

    整张新盘先写到临时文件再原子替换，内存峰值不超过一个 ``chunk``。

    **默认不要用**：prop 字节一变，分区自己的 dm-verity 哈希树就失效；改掉
    super 尾部又会让 super 自己的 AVB 校验失败。两者都需要解锁 bootloader 才
    能绕过，而 Play 认证还没结束（Google 还会用服务端设备档案与硬件 attestation
    核对）。因此 ``android_sdk`` 只在显式 ``ANDROID_IMAGE_MASK=1`` 时才会调用
    这里；``mask_props=False`` 仅用来验证数据前移本身。
    """
    image: Path
    backup_dir: Path | None = None
    rules: dict[str, str] | None = None
    dry_run: bool = False
    mask_props: bool = True
    chunk: int = 8 << 20
    vbmeta_strip: bool = True

    def available(self) -> bool:
        return Path(self.image).exists()

    def run(self) -> ImageMaskResult:
        image = Path(self.image)
        if not self.available():
            raise MaskError(f"系统镜像不存在：{image}")
        layout = read_super_layout(image)
        vbmeta = next((e for e in layout.gpt if e.name == "vbmeta"), None)
        if vbmeta is None:
            raise MaskError("不是官方 Play 系统镜像：GPT 里没有 vbmeta 分区")
        size = image.stat().st_size
        rules = masking_rules() if self.rules is None else self.rules
        backup_dir = Path(self.backup_dir) if self.backup_dir else image.parent / "backup"
        backup = backup_path(image, backup_dir)
        report = MaskReport()
        if not self.dry_run:
            backup_dir.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copyfile(image, backup)
            record_hash(backup, backup_dir)

        replacements: list[Replacement] = []
        if self.mask_props and rules:
            replacements = self._scan(image, layout, rules)
        work = image.with_name(image.name + ".masking")
        written = 0
        try:
            with open(image, "rb") as src, open(work, "wb") as out:
                copied = _copy_range(src, out, 0, layout.offset, self.chunk)
                written += copied
                # 旧 super 头部被数据区覆盖，长度不变。
                zeroed = min(layout.shift, max(0, size - layout.offset))
                while zeroed > 0:
                    n = min(self.chunk, zeroed)
                    out.write(bytes(n))
                    zeroed -= n
                    written += n
                pos = layout.data_offset
                while pos < size:
                    n = min(self.chunk, size - pos)
                    chunk = _read_range(src, pos, n)
                    rel = pos - layout.shift
                    out.seek(rel)
                    if replacements:
                        chunk = apply_replacements(chunk, rel, replacements)
                        for rep in replacements:
                            # 替换偏移是前移后的文件偏移，命中按起始字节计数一次。
                            if pos <= rep.start + layout.shift < pos + n:
                                report.substituted[rep.key] = (
                                    report.substituted.get(rep.key, 0) + 1)
                    out.write(chunk)
                    pos += n
                    written += n
                out.truncate(size)
            if self.dry_run:
                work.unlink(missing_ok=True)
            else:
                os.replace(work, image)
        finally:
            work.unlink(missing_ok=True)
        notes = zero_vbmeta_trailer(image, vbmeta, dry_run=self.dry_run) if self.vbmeta_strip else []
        if report.substituted:
            report.files.append(f"分区数据: {sum(report.substituted.values())} 处")
        else:
            report.identical.append("super")
        report.missing = [k for k in rules if k not in report.substituted]
        return ImageMaskResult(image=image,
                               backup=backup if (not self.dry_run and backup.exists()) else None,
                               report=report, stripped=notes)

    def _scan(self, image: Path, layout: SuperLayout,
              rules: dict[str, str]) -> list[Replacement]:
        """顺序扫描数据区；块边界留一个窗口，避免漏掉被切断的键值。"""
        size = image.stat().st_size
        window = max(len(k.encode()) for k in rules) + max(len(v.encode()) for v in rules.values()) + 8
        found: list[Replacement] = []
        with open(image, "rb") as f:
            pos = layout.data_offset
            carry = b""
            while pos < size:
                f.seek(pos)
                chunk = f.read(min(self.chunk, size - pos))
                if not chunk:
                    break
                rel = pos - layout.shift
                buf = carry + chunk
                start = rel - len(carry)
                for rep in find_replacements(buf, rules, start) + find_cmdline_tokens(buf, start):
                    found.append(rep)
                carry = buf[-window:]
                pos += len(chunk)
        return found
