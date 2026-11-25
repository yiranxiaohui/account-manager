// 账号用量额度（后端 account_manager/usage.py 的摘要，检测 sessionKey 时顺带刷新）：
// {org_uuid, org_name, windows: [{kind, model, percent, severity, resets_at, active}], spend}

const KIND_LABELS = { session: "5 小时", weekly_all: "7 天" };
const KIND_SHORT = { session: "5h", weekly_all: "7d" };
const SEVERITY_TONE = { normal: "success", warning: "warning", critical: "danger" };

// usage 在库里是 JSON 文本；容错解析成对象（接口也可能已是对象）
export function parseUsage(value) {
  let data = value;
  if (typeof value === "string") {
    if (!value) return null;
    try {
      data = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const windows = Array.isArray(data.windows)
    ? data.windows.filter((w) => w && typeof w === "object" && Number.isFinite(w.percent))
    : [];
  const spend = data.spend && typeof data.spend === "object" ? data.spend : null;
  return { ...data, windows, spend };
}

// 完整名称：5 小时 / 7 天 / 7 天 · Fable
export function windowLabel(w) {
  if (KIND_LABELS[w.kind]) return KIND_LABELS[w.kind];
  if (w.kind === "weekly_scoped") return w.model ? `7 天 · ${w.model}` : "7 天（单模型）";
  return w.model ? `${w.kind} · ${w.model}` : String(w.kind || "未知");
}

// 表格里的短名称：5h / 7d / 模型名
export function windowShort(w) {
  if (KIND_SHORT[w.kind]) return KIND_SHORT[w.kind];
  return w.model || String(w.kind || "?");
}

function timeOf(iso) {
  if (!iso) return NaN;
  return new Date(iso).getTime();
}

// 窗口已过重置时间：上次检测的百分比已经作废（新窗口从 0 开始），需要重新检测
export function windowExpired(w, now = Date.now()) {
  const t = timeOf(w.resets_at);
  return Number.isFinite(t) && t <= now;
}

// 当前百分比：已过重置时间的窗口按 0 算
export function windowPercent(w, now = Date.now()) {
  return windowExpired(w, now) ? 0 : Math.max(0, Math.round(w.percent));
}

export function usageTone(item, now = Date.now()) {
  if (item.resets_at !== undefined && windowExpired(item, now)) return "muted";
  if (SEVERITY_TONE[item.severity]) return SEVERITY_TONE[item.severity];
  if (item.percent >= 100) return "danger";
  if (item.percent >= 75) return "warning";
  return "success";
}

// 「3 小时 12 分后重置」「2 天 5 小时后重置」
export function resetIn(iso, now = Date.now()) {
  const t = timeOf(iso);
  if (!Number.isFinite(t)) return "";
  const s = Math.round((t - now) / 1000);
  if (s <= 0) return "已重置";
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))} 分钟后重置`;
  if (s < 86400) {
    const m = Math.floor((s % 3600) / 60);
    return `${Math.floor(s / 3600)} 小时${m ? ` ${m} 分` : ""}后重置`;
  }
  const h = Math.floor((s % 86400) / 3600);
  return `${Math.floor(s / 86400)} 天${h ? ` ${h} 小时` : ""}后重置`;
}

export function formatMoney(amount, currency = "USD") {
  if (!Number.isFinite(amount)) return "";
  const symbol = { USD: "$", EUR: "€", GBP: "£", CNY: "¥", JPY: "¥" }[currency];
  const text = amount.toFixed(2);
  return symbol ? `${symbol}${text}` : `${text} ${currency}`;
}

// 表格展示的窗口：有任意窗口时总是带上 5 小时和 7 天（没出现的 5 小时窗口表示还没开始，按 0 显示），
// 再接各模型的周限额
export function displayWindows(usage) {
  if (!usage || !usage.windows.length) return [];
  const rows = [...usage.windows];
  for (const kind of ["weekly_all", "session"]) {
    if (!rows.some((w) => w.kind === kind)) {
      rows.unshift({ kind, model: "", percent: 0, severity: "normal", resets_at: "", active: false,
        missing: true });
    }
  }
  const order = { session: 0, weekly_all: 1 };
  return rows.sort((a, b) => (order[a.kind] ?? 2) - (order[b.kind] ?? 2));
}

// 排序用：5 小时 / 7 天里较高的百分比（不看单模型周限额，否则 100% 的 Fable 会盖过一切）；
// 没有限额窗口时用用量额度的百分比；没有数据为 null
export function usageSortValue(acct, now = Date.now()) {
  const usage = parseUsage(acct.usage);
  if (!usage) return null;
  const main = usage.windows.filter((w) => w.kind === "session" || w.kind === "weekly_all");
  if (main.length) return Math.max(...main.map((w) => windowPercent(w, now)));
  if (usage.windows.length) return Math.max(...usage.windows.map((w) => windowPercent(w, now)));
  if (usage.spend && Number.isFinite(usage.spend.percent)) return usage.spend.percent;
  return null;
}

// 单行摘要（检测成功的提示用）：「5h 46% · 7d 51% · Fable 100%」「额度 $0.37 / $1.00」
export function usageSummary(value, now = Date.now()) {
  const usage = parseUsage(value);
  if (!usage) return "";
  const parts = displayWindows(usage).map((w) => `${windowShort(w)} ${windowPercent(w, now)}%`);
  if (usage.spend) parts.push(`额度 ${spendText(usage.spend)}`);
  return parts.join(" · ");
}

export function spendText(spend) {
  const used = formatMoney(spend.used, spend.currency);
  return spend.limit === null || spend.limit === undefined
    ? `已用 ${used}`
    : `${used} / ${formatMoney(spend.limit, spend.currency)}`;
}

// 悬浮说明：每个窗口一行（百分比 + 重置时间），再加用量额度、组织与检测时间
export function usageTooltip(acct, now = Date.now()) {
  const usage = parseUsage(acct.usage);
  if (!usage) return "还没有额度数据：检测 sessionKey 时会顺带获取";
  const lines = [];
  for (const w of displayWindows(usage)) {
    if (w.missing) {
      lines.push(`${windowLabel(w)}：未开始`);
      continue;
    }
    const reset = resetIn(w.resets_at, now);
    const pct = windowExpired(w, now) ? `已重置（上次 ${Math.round(w.percent)}%）` : `已用 ${Math.round(w.percent)}%`;
    lines.push(`${windowLabel(w)}：${pct}${reset && !windowExpired(w, now) ? `，${reset}` : ""}${w.active ? "（当前生效）" : ""}`);
  }
  if (usage.spend) {
    lines.push(`用量额度：${spendText(usage.spend)}${usage.spend.limit ? `（${usage.spend.percent || 0}%）` : ""}`);
  }
  if (!lines.length) lines.push("该组织没有返回用量限额（Free 套餐不提供）");
  if (usage.org_name) lines.push(`组织：${usage.org_name}`);
  if (acct.usage_checked_at) lines.push(`获取于 ${acct.usage_checked_at}`);
  return lines.join("\n");
}
