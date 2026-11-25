"""Claude 账号用量额度：claude.ai /api/organizations/{org}/usage 的响应 → 展示用摘要。

纯函数，不发请求、不碰数据库。这是 claude.ai「设置 → 用量」页背后的接口（未公开文档），
用 sessionKey Cookie 鉴权。实测（2026-11）各套餐的响应：

- Pro / Max：limits[] 列出各限额窗口。kind 为 session（5 小时窗口）、weekly_all
  （7 天、全部模型）、weekly_scoped（7 天、单个模型，模型名在 scope.model.display_name，
  如 Fable）；percent 是 0–100 的整数，severity 为 normal / warning / critical，
  resets_at 是 ISO 时间，is_active 标出当前起作用的那个限额。5 小时窗口还没开始时不出现
  session（旧字段 five_hour 也是 null）。
- Enterprise：limits 为空，额度在 spend：used / limit 为 {amount_minor, currency,
  exponent}，enabled 表示启用了用量额度。
- Free：limits 为空，其余字段全是 null。

旧版响应没有 limits[]，这时退回 five_hour / seven_day / seven_day_opus / seven_day_sonnet
（utilization 为百分比）。
"""
from __future__ import annotations

USAGE_URL = "https://claude.ai/api/organizations/{org_uuid}/usage"

SESSION = "session"
WEEKLY_ALL = "weekly_all"
WEEKLY_SCOPED = "weekly_scoped"

# 窗口排序：5 小时 → 7 天全部模型 → 7 天单个模型 → 其它
_KIND_ORDER = {SESSION: 0, WEEKLY_ALL: 1, WEEKLY_SCOPED: 2}
_SEVERITIES = ("normal", "warning", "critical")

# 旧版字段 → (kind, 模型名)
_LEGACY_FIELDS = (
    ("five_hour", SESSION, ""),
    ("seven_day", WEEKLY_ALL, ""),
    ("seven_day_opus", WEEKLY_SCOPED, "Opus"),
    ("seven_day_sonnet", WEEKLY_SCOPED, "Sonnet"),
)


def usage_url(org_uuid: str) -> str:
    return USAGE_URL.format(org_uuid=org_uuid)


def _percent(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(max(0.0, float(value))))


def _severity(value, percent: int) -> str:
    if value in _SEVERITIES:
        return str(value)
    if percent >= 100:
        return "critical"
    if percent >= 75:
        return "warning"
    return "normal"


def _text(value, limit: int = 64) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""


def _scope_model(scope) -> str:
    if not isinstance(scope, dict):
        return ""
    model = scope.get("model")
    if isinstance(model, dict):
        return _text(model.get("display_name") or model.get("id"))
    surface = scope.get("surface")
    if isinstance(surface, dict):
        return _text(surface.get("display_name") or surface.get("id"))
    return _text(surface)


def _window(kind: str, model: str, percent: int, severity, resets_at, active) -> dict:
    return {
        "kind": kind,
        "model": model,
        "percent": percent,
        "severity": _severity(severity, percent),
        "resets_at": _text(resets_at),
        "active": bool(active),
    }


def _limit_windows(limits) -> list[dict]:
    windows = []
    for item in limits:
        if not isinstance(item, dict):
            continue
        percent = _percent(item.get("percent"))
        if percent is None:
            percent = _percent(item.get("utilization"))
        kind = _text(item.get("kind"))
        if percent is None or not kind:
            continue
        windows.append(_window(kind, _scope_model(item.get("scope")), percent,
                               item.get("severity"), item.get("resets_at"),
                               item.get("is_active")))
    return windows


def _legacy_windows(payload: dict) -> list[dict]:
    windows = []
    for field, kind, model in _LEGACY_FIELDS:
        item = payload.get(field)
        if not isinstance(item, dict):
            continue
        percent = _percent(item.get("utilization"))
        if percent is None:
            continue
        windows.append(_window(kind, model, percent, None, item.get("resets_at"), False))
    return windows


def _money(value) -> float | None:
    """{amount_minor, exponent} → 金额；不是这种结构时返回 None。"""
    if not isinstance(value, dict):
        return None
    minor = value.get("amount_minor")
    if isinstance(minor, bool) or not isinstance(minor, (int, float)):
        return None
    exp = value.get("exponent")
    exp = exp if isinstance(exp, int) and not isinstance(exp, bool) and 0 <= exp <= 6 else 2
    return round(float(minor) / (10 ** exp), 6)


def _spend(payload: dict) -> dict | None:
    """启用了用量额度（Enterprise 等）时的已用 / 上限；没启用返回 None。"""
    spend = payload.get("spend")
    if isinstance(spend, dict) and spend.get("enabled"):
        used, limit = spend.get("used"), spend.get("limit")
        currency = next((_text(m.get("currency"), 8) for m in (used, limit)
                         if isinstance(m, dict) and m.get("currency")), "")
        used_amount = _money(used)
        limit_amount = _money(limit)
        percent = _percent(spend.get("percent"))
        if percent is None and used_amount is not None and limit_amount:
            percent = _percent(used_amount * 100 / limit_amount)
        return {
            "used": used_amount if used_amount is not None else 0.0,
            "limit": limit_amount,
            "currency": currency or "USD",
            "percent": percent or 0,
            "severity": _severity(spend.get("severity"), percent or 0),
        }
    extra = payload.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled"):
        dp = extra.get("decimal_places")
        dp = dp if isinstance(dp, int) and not isinstance(dp, bool) and 0 <= dp <= 6 else 2
        used = extra.get("used_credits")
        used = float(used) / 10 ** dp if isinstance(used, (int, float)) and not isinstance(used, bool) else 0.0
        limit = extra.get("monthly_limit")
        limit = (float(limit) / 10 ** dp
                 if isinstance(limit, (int, float)) and not isinstance(limit, bool) else None)
        percent = _percent(used * 100 / limit) if limit else 0
        return {
            "used": round(used, 6),
            "limit": round(limit, 6) if limit is not None else None,
            "currency": _text(extra.get("currency"), 8) or "USD",
            "percent": percent or 0,
            "severity": _severity(None, percent or 0),
        }
    return None


def summarize(payload, org: dict | None = None) -> dict | None:
    """usage 响应 → 摘要：{org_uuid, org_name, windows: [...], spend}；不是有效响应时返回 None。

    windows 每项为 {kind, model, percent, severity, resets_at, active}，按 5 小时 → 7 天 →
    7 天单模型排序；spend 为 {used, limit, currency, percent, severity} 或 None。
    Free 等没有任何限额数据时 windows 为空、spend 为 None。
    """
    if not isinstance(payload, dict):
        return None
    limits = payload.get("limits")
    windows = _limit_windows(limits) if isinstance(limits, list) else []
    if not windows:
        windows = _legacy_windows(payload)
    windows.sort(key=lambda w: (_KIND_ORDER.get(w["kind"], 9), w["model"].lower()))
    org = org if isinstance(org, dict) else {}
    return {
        "org_uuid": _text(org.get("uuid"), 128),
        "org_name": _text(org.get("name"), 128),
        "windows": windows,
        "spend": _spend(payload),
    }
