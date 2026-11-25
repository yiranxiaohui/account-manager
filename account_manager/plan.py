"""Claude 账号套餐识别：从 claude.ai /api/organizations 的组织信息推断套餐。

纯函数，不发请求、不碰数据库。实测（2026-11）各套餐的组织字段：

| 套餐       | capabilities 关键项       | rate_limit_tier          | analytics_subscription_plan |
|------------|---------------------------|--------------------------|-----------------------------|
| Free       | chat                      | default_claude_ai        | claude_free                 |
| Pro        | chat, claude_pro          | default_claude_ai        | claude_pro                  |
| Max 20x    | chat, claude_max          | default_claude_max_20x   | claude_max 20x              |
| Enterprise | chat, raven, raven_enterprise（raven_type=enterprise） | manual_tier_3 等 | claude_enterprise |
| Console API| api, api_individual（无 chat） | auto_trust_tier_a 等 | api_individual              |

Max 5x 按同样的规律取 rate_limit_tier 里的 5x；Team 为 raven_type=team / capabilities 含 raven。
"""
from __future__ import annotations

FREE = "free"
PRO = "pro"
MAX = "max"
MAX_5X = "max_5x"
MAX_20X = "max_20x"
TEAM = "team"
ENTERPRISE = "enterprise"
API = "api"
UNKNOWN = "unknown"

PLAN_LABELS = {
    FREE: "Free",
    PRO: "Pro",
    MAX: "Max",
    MAX_5X: "Max 5x",
    MAX_20X: "Max 20x",
    TEAM: "Team",
    ENTERPRISE: "Enterprise",
    API: "API",
    UNKNOWN: "未知",
}

# 一个账号属于多个组织且没有授权过某个组织时，主套餐取排名最高的。
_RANK = {
    MAX_20X: 8, MAX_5X: 7, MAX: 6, ENTERPRISE: 5, TEAM: 4, PRO: 3, FREE: 2, API: 1, UNKNOWN: 0,
}


def _caps(org: dict) -> set[str]:
    caps = org.get("capabilities")
    return {str(c) for c in caps} if isinstance(caps, list) else set()


def _max_multiplier(*hints: str) -> str:
    for hint in hints:
        text = hint.lower()
        if "20x" in text:
            return MAX_20X
        if "5x" in text:
            return MAX_5X
    return MAX


def org_plan(org: dict) -> str:
    """单个组织的套餐标识（见 PLAN_LABELS 的键）。"""
    if not isinstance(org, dict):
        return UNKNOWN
    caps = _caps(org)
    tier = str(org.get("rate_limit_tier") or "")
    analytics = str(org.get("analytics_subscription_plan") or "")
    raven = str(org.get("raven_type") or "").lower()
    if "claude_max" in caps or analytics.startswith("claude_max"):
        return _max_multiplier(tier, analytics)
    if "claude_pro" in caps or analytics == "claude_pro":
        return PRO
    if raven == "enterprise" or "raven_enterprise" in caps or analytics == "claude_enterprise":
        return ENTERPRISE
    if raven == "team" or "raven" in caps or analytics == "claude_team":
        return TEAM
    if "chat" in caps:
        return FREE
    if "api" in caps or analytics.startswith("api"):
        return API
    return UNKNOWN


def org_items(orgs) -> list[dict]:
    """组织列表 → 展示用摘要（不含任何凭据）：uuid / name / plan / rate_limit_tier / billing_type。

    orgs 可以是 /api/organizations 的原始响应（列表或 {"organizations": [...]}），
    也可以是 oauth.summarize_orgs 的摘要；没有 uuid 的条目忽略。
    """
    if isinstance(orgs, dict):
        orgs = orgs.get("organizations")
    if isinstance(orgs, tuple):
        orgs = list(orgs)
    if not isinstance(orgs, list):
        return []
    items = []
    for org in orgs:
        if not isinstance(org, dict) or not org.get("uuid"):
            continue
        known = org.get("plan")
        # 已经是摘要（没有 capabilities）时沿用其中的 plan，不重新推断
        keep = known in PLAN_LABELS and "capabilities" not in org
        items.append({
            "uuid": str(org["uuid"]),
            "name": str(org.get("name") or ""),
            "plan": known if keep else org_plan(org),
            "rate_limit_tier": str(org.get("rate_limit_tier") or ""),
            "billing_type": str(org.get("billing_type") or ""),
        })
    return items


def primary_org(items: list[dict], org_uuid: str | None = None) -> dict | None:
    """账号的主组织：授权过的组织在列表里就用它，否则取套餐排名最高的（同名次取靠前的）。

    Console API 组织（只有 api 能力、不能聊天）只在没有其它组织时才算。
    """
    if not items:
        return None
    if org_uuid:
        for item in items:
            if item.get("uuid") == org_uuid:
                return item
    best = None
    for item in items:
        plan = str(item.get("plan") or UNKNOWN)
        if best is None or _RANK.get(plan, 0) > _RANK.get(str(best.get("plan") or UNKNOWN), 0):
            best = item
    return best


def primary_plan(items: list[dict], org_uuid: str | None = None) -> str:
    """账号的主套餐：主组织（见 primary_org）的套餐。"""
    org = primary_org(items, org_uuid)
    if org is None:
        return ""
    return str(org.get("plan") or UNKNOWN)
