"""账号用量额度落库：检测 sessionKey 时顺带取到的主组织用量摘要写进 accounts。

取不到用量（失效、被拦截、接口异常）时保留上一次的结果。
"""
from __future__ import annotations

import json

from server import db


def record(conn, email: str, usage, now: str) -> dict:
    """写入用量摘要，返回要合并进账号行的字段；没有用量数据时返回 {}。"""
    if not isinstance(usage, dict):
        return {}
    text = json.dumps(usage, ensure_ascii=False)
    if not db.update_account_usage(conn, email, text, now):
        return {}
    return {"usage": text, "usage_checked_at": now}
