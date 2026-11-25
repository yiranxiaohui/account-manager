"""账号用量额度落库：检测 sessionKey 时顺带取到的各组织（工作空间）用量摘要写进 accounts。

一个账号可能属于多个组织（如同一所学校的两个 Enterprise 工作空间），每个组织的额度不同：

- usage_orgs：各组织用量摘要的 JSON 列表（每项带 checked_at），按组织列表顺序排列；
- usage / usage_checked_at：当前显示的那个组织的摘要，列表直接展示、按它排序；
- usage_org_uuid：手动切换显示的组织。

显示哪个组织：手动切换的组织 → 已授权 OAuth 的组织 → 套餐最高的组织（见 plan.usage_org）。
某个组织取不到用量（失效、被拦截、接口异常）时保留它上一次的结果。
"""
from __future__ import annotations

import json

from account_manager import plan
from server import db


def _json(text, kind):
    if not text or not isinstance(text, str):
        return kind()
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return kind()
    return value if isinstance(value, kind) else kind()


def org_items(row: dict) -> list[dict]:
    """账号已识别的组织列表（plan_orgs）。"""
    return plan.org_items(_json(row.get("plan_orgs"), list))


def shown_org(row: dict, items: list[dict] | None = None) -> dict | None:
    """当前显示用量的组织；没有能查用量的组织时为 None。"""
    if items is None:
        items = org_items(row)
    return plan.usage_org(items, row.get("usage_org_uuid") or None, row.get("org_uuid") or None)


def _cached(row: dict) -> dict[str, dict]:
    """已缓存的各组织用量：uuid → 摘要。旧数据只有 usage（单个组织）时用它补上。"""
    entries: dict[str, dict] = {}
    for item in _json(row.get("usage_orgs"), list):
        if isinstance(item, dict) and item.get("org_uuid"):
            entries[str(item["org_uuid"])] = item
    legacy = _json(row.get("usage"), dict)
    uuid = str(legacy.get("org_uuid") or "")
    if uuid and uuid not in entries:
        entries[uuid] = {**legacy, "checked_at": legacy.get("checked_at")
                         or row.get("usage_checked_at") or ""}
    return entries


def _save(conn, email: str, row: dict, entries: dict[str, dict]) -> dict:
    """按组织列表整理缓存、选出显示的组织后写库，返回要合并进账号行的字段。"""
    items = org_items(row)
    if items:
        # 账号已不在的组织不再保留
        order = {item["uuid"]: i for i, item in enumerate(items)}
        kept = sorted((e for uuid, e in entries.items() if uuid in order),
                      key=lambda e: order[e["org_uuid"]])
    else:
        kept = list(entries.values())
    org = shown_org(row, items)
    if org is not None:
        current = next((e for e in kept if e["org_uuid"] == org["uuid"]), None)
        usage = json.dumps(current, ensure_ascii=False) if current else ""
        checked_at = (current or {}).get("checked_at") or ""
    else:
        usage = row.get("usage") or ""
        checked_at = row.get("usage_checked_at") or ""
    usage_orgs = json.dumps(kept, ensure_ascii=False)
    db.update_account_usage(conn, email, usage, checked_at, usage_orgs)
    return {
        "usage": usage,
        "usage_checked_at": checked_at,
        "usage_orgs": usage_orgs,
        "usage_org_uuid": row.get("usage_org_uuid") or "",
    }


def record(conn, email: str, summaries, now: str) -> dict:
    """写入检测时取到的各组织用量摘要，返回要合并进账号行的字段；一个都没取到时返回 {}。

    要在套餐（plan_orgs）落库之后调用，这样能按最新的组织列表整理缓存。
    """
    fresh = [s for s in (summaries or ()) if isinstance(s, dict) and s.get("org_uuid")]
    if not fresh:
        return {}
    row = db.get_account(conn, email)
    if row is None:
        return {}
    entries = _cached(row)
    for summary in fresh:
        entries[str(summary["org_uuid"])] = {**summary, "checked_at": now}
    return _save(conn, email, row, entries)


def select(conn, email: str, org_uuid: str, summary, now: str) -> dict:
    """切换显示用量的组织；summary 是刚查到的该组织用量（查不到为 None，沿用缓存）。

    返回要合并进账号行的字段；账号不存在时返回 {}。
    """
    if not db.set_account_usage_org(conn, email, org_uuid):
        return {}
    row = db.get_account(conn, email)
    entries = _cached(row)
    if isinstance(summary, dict) and summary.get("org_uuid") == org_uuid:
        entries[org_uuid] = {**summary, "checked_at": now}
    return _save(conn, email, row, entries)


def refresh_shown(conn, email: str) -> None:
    """授权的组织变了之后，按缓存重算显示的组织（不发请求）。"""
    row = db.get_account(conn, email)
    if row is None or not (row.get("usage") or row.get("usage_orgs")):
        return
    _save(conn, email, row, _cached(row))
