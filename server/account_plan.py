"""账号套餐落库：把检测 / 授权时拿到的组织列表换算成套餐写进 accounts。

检测 sessionKey、导入 SK、接管取 sk、列组织、OAuth 授权都会拿到组织列表，
统一经这里写库；拿不到组织列表（失效、被拦截、请求失败）时保留上一次的结果。
"""
from __future__ import annotations

import json

from account_manager import plan
from server import db


def record(conn, email: str, orgs, now: str) -> dict:
    """按组织列表更新账号套餐，返回要合并进账号行的字段；没有组织信息时返回 {}。"""
    items = plan.org_items(orgs)
    if not items:
        return {}
    row = db.get_account(conn, email)
    if row is None:
        return {}
    primary = plan.primary_plan(items, row.get("org_uuid") or None)
    text = json.dumps(items, ensure_ascii=False)
    db.update_account_plan(conn, email, primary, text, now)
    return {"plan": primary, "plan_orgs": text, "plan_checked_at": now}


def refresh_primary(conn, email: str) -> None:
    """授权的组织变了之后，按已保存的组织列表重算主套餐（不发请求）。"""
    row = db.get_account(conn, email)
    if row is None or not row.get("plan_orgs"):
        return
    try:
        items = json.loads(row["plan_orgs"])
    except (TypeError, ValueError):
        return
    if not isinstance(items, list):
        return
    primary = plan.primary_plan([i for i in items if isinstance(i, dict)],
                                row.get("org_uuid") or None)
    if primary and primary != row.get("plan"):
        db.update_account_plan(conn, email, primary, row["plan_orgs"],
                               row.get("plan_checked_at") or "")
