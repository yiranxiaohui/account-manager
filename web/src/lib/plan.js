// 账号套餐（后端 account_manager/plan.py 识别）：标识 → 显示名、色调与筛选分组。

export const PLAN_LABELS = {
  free: "Free",
  pro: "Pro",
  max: "Max",
  max_5x: "Max 5x",
  max_20x: "Max 20x",
  team: "Team",
  enterprise: "Enterprise",
  api: "API",
  unknown: "未知",
};

const PLAN_TONES = {
  max: "highlight",
  max_5x: "highlight",
  max_20x: "highlight",
  pro: "primary",
  team: "info",
  enterprise: "info",
};

// 筛选分组：Max 不分倍数，Team 与 Enterprise 合并
const PLAN_GROUPS = {
  max: "max",
  max_5x: "max",
  max_20x: "max",
  pro: "pro",
  team: "team",
  enterprise: "team",
  free: "free",
};

export function planLabel(plan) {
  if (!plan) return "";
  return PLAN_LABELS[plan] || plan;
}

export function planTone(plan) {
  return PLAN_TONES[plan] || "muted";
}

// 套餐筛选：group 为 max / pro / team / free / none（未识别）
export function matchPlan(plan, group) {
  if (group === "none") return !plan || !PLAN_GROUPS[plan];
  return PLAN_GROUPS[plan] === group;
}

// plan_orgs 在库里是 JSON 文本；容错解析成数组（接口也可能已是数组）
export function parsePlanOrgs(value) {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== "string") return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.filter((o) => o && typeof o === "object") : [];
  } catch {
    return [];
  }
}

// 套餐徽章的悬浮说明：每个组织一行；当前授权的组织打标记
export function planTooltip(acct) {
  const orgs = parsePlanOrgs(acct.plan_orgs);
  const lines = [`套餐：${planLabel(acct.plan)}`];
  for (const o of orgs) {
    const mark = acct.org_uuid && o.uuid === acct.org_uuid ? "（已授权）" : "";
    const tier = o.rate_limit_tier ? ` · ${o.rate_limit_tier}` : "";
    lines.push(`${o.name || o.uuid || "组织"}${mark}：${planLabel(o.plan) || "未知"}${tier}`);
  }
  if (acct.plan_checked_at) lines.push(`识别于 ${acct.plan_checked_at}`);
  return lines.join("\n");
}

// 账号还属于其它不同套餐的组织（Console API 组织不算）时的数量，用于徽章上的「+N」
export function otherPlanCount(acct) {
  const plans = new Set(
    parsePlanOrgs(acct.plan_orgs)
      .map((o) => o.plan)
      .filter((p) => p && p !== "api" && p !== acct.plan),
  );
  return plans.size;
}
