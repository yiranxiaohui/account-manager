"""系统镜像清洗模块（server/android_image.py）的单元测试。

所有镜像都在临时目录里现拼，不依赖下载；只验证字节层面的约定：GPT 解析、
AVB 描述符、属性替换的等长性与跨块边界、整盘前移与备份记录。
"""
import hashlib
import struct
from pathlib import Path

import pytest

from server import android_image as ai
from server.android_sdk import SYSTEM_IMAGE_SYSDIR


def gpt_image(parts: list[tuple[str, int, int]], total: int = 0x600000) -> bytes:
    """拼一张最小 GPT：保护 MBR + 主头 + 分区项表。"""
    data = bytearray(total)
    entries_lba, entry_size, count = 2, 128, 32
    header = bytearray(92)
    header[0:8] = ai.GPT_SIGNATURE
    struct.pack_into("<Q", header, 72, entries_lba)
    struct.pack_into("<I", header, 80, count)
    struct.pack_into("<I", header, 84, entry_size)
    data[512:512 + 92] = header
    for i, (name, start, end) in enumerate(parts):
        row = bytearray(entry_size)
        row[0:16] = bytes([i + 1]) * 16
        struct.pack_into("<QQ", row, 32, start, end)
        row[56:128] = name.encode("utf-16-le").ljust(72, b"\0")
        off = entries_lba * 512 + i * entry_size
        data[off:off + entry_size] = row
    return bytes(data)


def vbmeta_blob(*, tree_offset: int = 0x4000, tree_size: int = 0x1000,
                fec_size: int = 0x800, image_size: int = 0x4000) -> bytes:
    """拼一个只有一条 hashtree 描述的 AVB 镜像。"""
    head = bytearray(256)
    head[0:4] = ai.AVB_MAGIC
    struct.pack_into("<Q", head, 12, 64)    # auth block
    struct.pack_into("<Q", head, 20, 128)   # aux block
    body = bytearray(120)
    struct.pack_into("<Q", body, 0, 2)
    struct.pack_into("<I", body, 8, 120)
    body[20:25] = b"system"
    struct.pack_into("<Q", body, 56, image_size)
    struct.pack_into("<Q", body, 64, tree_offset)
    struct.pack_into("<I", body, 72, tree_size)
    struct.pack_into("<I", body, 76, 4096)
    struct.pack_into("<I", body, 80, 4096)
    struct.pack_into("<Q", body, 104, tree_offset + tree_size)
    struct.pack_into("<Q", body, 112, fec_size)
    data = bytes(head) + bytes(64) + bytes(128) + bytes(body)
    total = tree_offset + tree_size + fec_size
    return data.ljust(total, b"\x77")


def test_rewrite_props_keeps_order_and_comments():
    text = "# header\na=1\r\nb=2\nro.hardware=ranchu\nc=3"
    out, changes = ai.rewrite_props(text, {"ro.hardware": "husky", "ro.debuggable": "0"})
    assert out.startswith("# header\na=1\r\nb=2\nro.hardware=husky\nc=3")
    assert out.endswith("ro.debuggable=0\n")
    assert ("ro.hardware", "ranchu", "husky") in changes
    assert ("ro.debuggable", "", "0") in changes


def test_rewrite_props_is_idempotent():
    text = "ro.hardware=husky\nro.kernel.qemu=0\n"
    out, changes = ai.rewrite_props(text, {"ro.hardware": "husky", "ro.kernel.qemu": "0"})
    assert out == text
    assert changes == []


def test_mask_prop_bytes_keeps_length_and_pads():
    raw = b"\x00ro.hardware=ranchu\x00tail"
    out, counts = ai.mask_prop_bytes(raw, {"ro.hardware": "husky"})
    assert len(out) == len(raw)
    assert counts == {"ro.hardware": 1}
    assert b"ro.hardware=husky \x00tail" in out
    assert out.endswith(b"\x00tail")


def test_mask_prop_bytes_ignores_prefixed_keys_and_nul_values():
    raw = b"ro.foo_ro.hardware=ranchu\nx\x00"
    out, counts = ai.mask_prop_bytes(raw, {"ro.hardware": "husky"})
    assert counts == {}
    assert out == raw


def test_mask_prop_bytes_truncates_longer_values():
    old = b"google/sdk_gphone64_x86_64/emu64xa:17/12000000:user/dev-keys   "
    raw = b"ro.build.fingerprint=" + old + b"\n"
    out, _ = ai.mask_prop_bytes(raw, {"ro.build.fingerprint": ai.RELEASE_FINGERPRINT})
    assert len(out) == len(raw)
    start = raw.index(b"=") + 1
    room = len(old)
    assert out[start:start + room] == ai.RELEASE_FINGERPRINT.encode()[:room]
    assert b"dev-keys" not in out


def test_read_gpt_reports_entries():
    data = gpt_image([("vbmeta", 2048, 4095), ("super", 6144, 12287)])
    path = Path("/tmp") / "gpt-probe.img"
    path.write_bytes(data)
    entries = ai.read_gpt(path)
    assert [(e.name, e.offset, e.size) for e in entries] == [
        ("vbmeta", 2048 * 512, 2048 * 512), ("super", 6144 * 512, 6144 * 512)]


def test_read_gpt_plain_image_return_empty(tmp_path):
    path = tmp_path / "plain.img"
    path.write_bytes(bytes(8192))
    assert ai.read_gpt(path) == []


def test_parse_vbmeta_hashtree_bounds():
    blob = vbmeta_blob()
    infos = ai.parse_vbmeta(blob)
    assert len(infos) == 1
    info = infos[0]
    assert (info.partition, info.kind) == ("system", "hashtree")
    assert info.image_size == 0x4000
    assert info.tree_offset == 0x4000 and info.tree_size == 0x1000
    assert info.fec_offset == 0x5000 and info.fec_size == 0x800
    assert info.trailer_end == 0x5800


def test_strip_vbmeta_bytes_zeroes_only_trailer():
    blob = vbmeta_blob().ljust(0x9000, b"\x77")
    notes, cleaned = ai.strip_vbmeta_bytes(blob)
    assert len(cleaned) == len(blob)
    trailer = ai.parse_vbmeta(blob)[0].trailer_end
    assert cleaned[:trailer] == blob[:trailer]
    assert cleaned[trailer:] == bytes(len(blob) - trailer)
    assert notes and "system" in notes[0]


def test_find_replacements_handles_longer_shorter_and_identical():
    raw = (b"ro.build.tags=dev-keys       \n"
           b"ro.hardware=ranchu\n"
           b"ro.boot.hardware=ranchu\n"
           b"ro.product.model=sdk_gphone64_x86_64\n")
    rules = {"ro.build.tags": "release-keys", "ro.hardware": "husky",
             "ro.product.model": "husky", "ro.secure": "1"}
    reps = ai.find_replacements(raw, rules)
    keys = {r.key for r in reps}
    assert keys == {"ro.build.tags", "ro.hardware", "ro.product.model"}
    for rep in reps:
        assert len(rep.data) == rep.end - rep.start
    changed = ai.apply_replacements(raw, 0, reps)
    assert len(changed) == len(raw)
    assert b"ro.build.tags=release-keys" in changed
    assert changed.count(b"\nro.hardware=husky") == 1
    assert changed.count(b"\nro.boot.hardware=ranchu") == 1


def test_find_replacements_picks_up_trailing_text():
    raw = b"ro.product.model=sdk_gphone64_x86_64/x\n"
    reps = ai.find_replacements(raw, {"ro.product.model": "husky"})
    assert reps and reps[0].data.endswith(b" ")


def test_apply_replacements_across_chunk_boundary():
    raw = b"000000000\nro.hardware=ranchu\nXYZ"
    reps = ai.find_replacements(raw, {"ro.hardware": "husky"})
    assert len(reps) == 1
    cut = reps[0].start + 3
    assert cut < reps[0].end
    first, second = raw[:cut], raw[cut:]
    out = (ai.apply_replacements(first, 0, reps) +
           ai.apply_replacements(second, cut, reps))
    assert len(out) == len(raw)
    assert b"ro.hardware=husky" in out
    assert out.startswith(b"000000000\n") and out.endswith(b"\nXYZ")


def test_plan_mask_counts_hits(tmp_path):
    image = tmp_path / "disk.img"
    image.write_bytes(b"\0" * 4096 + b"ro.hardware=ranchu\n" + b"\0" * 10 +
                      b"ro.kernel.qemu=1\n")
    plan = ai.plan_mask(image, block=4096)
    assert plan["counts"]["ro.hardware"] == 1
    assert plan["counts"]["ro.kernel.qemu"] == 1
    assert (4108, "ranchu") in plan["emulator_hits"]


def test_find_cmdline_tokens_replaces_emulator_words():
    raw = b"\x00androidboot.hardware=ranchu\x00console=ttyS0\x00" + b"ranchu" + b"\x00"
    reps = ai.find_cmdline_tokens(raw)
    changed = ai.apply_replacements(raw, 0, reps)
    assert len(changed) == len(raw)
    assert b"androidboot.hardware=husky " in changed
    assert b"ranchu" not in changed
    assert b"rogue-ranchu" not in changed


def test_find_cmdline_tokens_ignores_words_with_letters_around():
    raw = b"xranchu\x00ranchux\x00"
    assert ai.find_cmdline_tokens(raw) == []


def _play_disk(path: Path, *, dev_keys: bool = True) -> Path:
    """拼一张与 Play 镜像同布局的盘：vbmeta、super、带数据的 vendor 区。"""
    total = 0x801000
    data = bytearray(total)
    gpt = gpt_image([("vbmeta", 2048, 4095), ("super", 6144, 12287)], total)
    data[:len(gpt)] = gpt
    blob = vbmeta_blob().ljust(2048 * 512, b"\0")
    data[2048 * 512:2048 * 512 + len(blob)] = blob
    tags = b"dev-keys" if dev_keys else b"release"
    body = (b"\0ro.hardware=ranchu\nro.kernel.qemu=1\n"
            + b"\0\x00ro.product.model=sdk_gphone64_x86_64/x\x00"
            + b"\x00androidboot.hardware=ranchu\x00" + tags + b"\x00")
    # 真机镜像的数据从 super + LP_DATA_OFFSET 起；测试盘按同样约定放数据。
    at = 6144 * 512 + ai.LP_DATA_OFFSET
    data[at:at + len(body)] = body
    path.write_bytes(bytes(data))
    return path


def test_system_image_masker_end_to_end(tmp_path):
    image = _play_disk(tmp_path / "system.img")
    backup_dir = tmp_path / "backup"
    result = ai.SystemImageMasker(image, backup_dir=backup_dir).run()
    out = image.read_bytes()
    size = image.stat().st_size
    assert len(out) == size
    # 数据区整体前移 2 MiB 覆盖 super 头部；super 起点之后全是原数据。
    tail = out[6144 * 512:]
    assert b"ro.hardware=husky" in tail
    assert b"ro.kernel.qemu=0\n" in tail
    assert b"androidboot.hardware=husky " in tail
    assert b"ranchu" not in tail
    assert size == 0x801000
    assert result.report.substituted["ro.hardware"] == 1
    assert result.report.substituted["ro.kernel.qemu"] == 1
    assert "ro.build.fingerprint" in result.report.missing
    assert result.backup is not None and result.backup.exists()
    manifest = (backup_dir / "masked-images.sha256").read_text()
    assert hashlib.sha256(result.backup.read_bytes()).hexdigest() in manifest


def test_system_image_masker_shifts_data_down(tmp_path):
    image = _play_disk(tmp_path / "system.img")
    before = image.read_bytes()
    ai.SystemImageMasker(image, backup_dir=tmp_path / "b").run()
    after = image.read_bytes()
    base = 6144 * 512
    needle = before.index(b"ro.hardware=ranchu", base)
    marker = after.index(b"ro.hardware=husky", base)
    # 数据区以分块方式前移；标记前的邻接内容随之搬到前移后的位置。
    # 文件长度不变；前移后的标记一定落在原来数据区之前，即旧头部所在的区间。
    assert needle - marker == ai.LP_DATA_OFFSET
    assert marker < 6144 * 512 + ai.LP_DATA_OFFSET


def test_system_image_masker_dry_run_keeps_file(tmp_path):
    image = _play_disk(tmp_path / "system.img")
    before = image.read_bytes()
    result = ai.SystemImageMasker(image, dry_run=True).run()
    assert image.read_bytes() == before
    assert result.report.substituted
    assert not (tmp_path / "backup").exists()


def test_system_image_masker_rejects_non_super_image(tmp_path):
    path = tmp_path / "plain.img"
    path.write_bytes(bytes(0x100000))
    with pytest.raises(ai.MaskError):
        ai.SystemImageMasker(path).run()


def test_system_image_masker_strips_vbmeta_trailer(tmp_path):
    image = _play_disk(tmp_path / "system.img")
    before = image.read_bytes()
    ai.SystemImageMasker(image, backup_dir=tmp_path / "b").run()
    after = image.read_bytes()
    start = 2048 * 512
    end = 4096 * 512
    assert after[start:start + 256] == before[start:start + 256]
    assert after[end - 64:end] == bytes(64)
