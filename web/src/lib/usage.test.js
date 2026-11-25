import { describe, expect, test } from "bun:test";
import {
  displayWindows,
  formatMoney,
  parseUsage,
  resetIn,
  spendText,
  usageSortValue,
  usageSummary,
  usageTone,
  usageTooltip,
  windowExpired,
  windowLabel,
  windowPercent,
  windowShort,
} from "./usage.js";

const NOW = Date.parse("2026-11-25T06:00:00Z");
const SOON = "2026-11-25T08:20:00+00:00";
const WEEK = "2026-12-01T06:00:00+00:00";
const PAST = "2026-11-25T05:00:00+00:00";

const MAX = {
  org_uuid: "o-max",
  org_name: "Personal",
  spend: null,
  windows: [
    { kind: "session", model: "", percent: 86, severity: "warning", resets_at: SOON, active: false },
    { kind: "weekly_all", model: "", percent: 51, severity: "normal", resets_at: WEEK, active: false },
    { kind: "weekly_scoped", model: "Fable", percent: 100, severity: "critical", resets_at: WEEK, active: true },
  ],
};
const ENTERPRISE = {
  org_uuid: "o-ent",
  org_name: "Acme",
  windows: [],
  spend: { used: 0.37, limit: 1, currency: "USD", percent: 37, severity: "normal" },
};
const FREE = { org_uuid: "o-free", org_name: "", windows: [], spend: null };

describe("parseUsage", () => {
  test("parses JSON text and objects, rejects junk", () => {
    expect(parseUsage(JSON.stringify(MAX)).windows).toHaveLength(3);
    expect(parseUsage(MAX).org_name).toBe("Personal");
    expect(parseUsage("")).toBeNull();
    expect(parseUsage("{oops")).toBeNull();
    expect(parseUsage("[1]")).toBeNull();
    expect(parseUsage(null)).toBeNull();
    expect(parseUsage({ windows: [null, { kind: "session" }, { kind: "x", percent: 3 }] }).windows)
      .toEqual([{ kind: "x", percent: 3 }]);
  });
});

describe("windows", () => {
  test("labels", () => {
    expect(MAX.windows.map(windowLabel)).toEqual(["5 小时", "7 天", "7 天 · Fable"]);
    expect(MAX.windows.map(windowShort)).toEqual(["5h", "7d", "Fable"]);
    expect(windowLabel({ kind: "weekly_scoped", model: "" })).toBe("7 天（单模型）");
    expect(windowShort({ kind: "monthly", model: "" })).toBe("monthly");
  });

  test("expired windows count as 0 and turn muted", () => {
    const w = { kind: "session", percent: 90, severity: "warning", resets_at: PAST };
    expect(windowExpired(w, NOW)).toBe(true);
    expect(windowPercent(w, NOW)).toBe(0);
    expect(usageTone(w, NOW)).toBe("muted");
    expect(windowPercent(MAX.windows[0], NOW)).toBe(86);
    expect(MAX.windows.map((x) => usageTone(x, NOW))).toEqual(["warning", "success", "danger"]);
    expect(usageTone({ percent: 80 })).toBe("warning");
  });

  test("display rows always include 5h and 7d when the plan has limits", () => {
    const usage = parseUsage({ windows: [MAX.windows[2], MAX.windows[1]] });
    const rows = displayWindows(usage);
    expect(rows.map((w) => [w.kind, !!w.missing])).toEqual([
      ["session", true],
      ["weekly_all", false],
      ["weekly_scoped", false],
    ]);
    expect(displayWindows(parseUsage(ENTERPRISE))).toEqual([]);
    expect(displayWindows(null)).toEqual([]);
  });
});

describe("formatting", () => {
  test("resetIn", () => {
    expect(resetIn(SOON, NOW)).toBe("2 小时 20 分后重置");
    expect(resetIn(WEEK, NOW)).toBe("6 天后重置");
    expect(resetIn("2026-11-25T06:00:30Z", NOW)).toBe("1 分钟后重置");
    expect(resetIn(PAST, NOW)).toBe("已重置");
    expect(resetIn("", NOW)).toBe("");
  });

  test("money and spend", () => {
    expect(formatMoney(0.37, "USD")).toBe("$0.37");
    expect(formatMoney(5, "XYZ")).toBe("5.00 XYZ");
    expect(spendText(ENTERPRISE.spend)).toBe("$0.37 / $1.00");
    expect(spendText({ used: 2, limit: null, currency: "USD" })).toBe("已用 $2.00");
  });

  test("summary line", () => {
    expect(usageSummary(JSON.stringify(MAX), NOW)).toBe("5h 86% · 7d 51% · Fable 100%");
    expect(usageSummary(ENTERPRISE, NOW)).toBe("额度 $0.37 / $1.00");
    expect(usageSummary(FREE, NOW)).toBe("");
    expect(usageSummary("", NOW)).toBe("");
  });
});

describe("sorting", () => {
  test("uses the higher of 5h / 7d, ignoring per-model caps", () => {
    expect(usageSortValue({ usage: JSON.stringify(MAX) }, NOW)).toBe(86);
    const expired = { ...MAX, windows: [{ ...MAX.windows[0], resets_at: PAST }, MAX.windows[1], MAX.windows[2]] };
    expect(usageSortValue({ usage: expired }, NOW)).toBe(51);
    expect(usageSortValue({ usage: { windows: [MAX.windows[2]] } }, NOW)).toBe(100);
    expect(usageSortValue({ usage: ENTERPRISE }, NOW)).toBe(37);
    expect(usageSortValue({ usage: FREE }, NOW)).toBeNull();
    expect(usageSortValue({}, NOW)).toBeNull();
  });
});

describe("tooltip", () => {
  test("lists windows, spend, org and fetch time", () => {
    const text = usageTooltip({ usage: JSON.stringify(MAX), usage_checked_at: "2026-11-25T05:59:00Z" }, NOW);
    expect(text.split("\n")).toEqual([
      "5 小时：已用 86%，2 小时 20 分后重置",
      "7 天：已用 51%，6 天后重置",
      "7 天 · Fable：已用 100%，6 天后重置（当前生效）",
      "组织：Personal",
      "获取于 2026-11-25T05:59:00Z",
    ]);
    expect(usageTooltip({ usage: ENTERPRISE }, NOW).split("\n")[0]).toBe("用量额度：$0.37 / $1.00（37%）");
    expect(usageTooltip({ usage: FREE }, NOW).split("\n")[0]).toContain("没有返回用量限额");
    expect(usageTooltip({}, NOW)).toContain("检测 sessionKey");
    const expired = { windows: [{ ...MAX.windows[0], resets_at: PAST }] };
    expect(usageTooltip({ usage: expired }, NOW).split("\n")[0]).toBe("5 小时：已重置（上次 86%）");
  });
});
