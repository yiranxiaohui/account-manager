import { describe, expect, test } from "bun:test";
import { matchPlan, otherPlanCount, parsePlanOrgs, planLabel, planTone, planTooltip } from "./plan.js";

const ORGS = JSON.stringify([
  { uuid: "o-pro", name: "Personal", plan: "pro", rate_limit_tier: "default_claude_ai" },
  { uuid: "o-ent", name: "Acme", plan: "enterprise", rate_limit_tier: "manual_tier_3" },
  { uuid: "o-api", name: "Console", plan: "api", rate_limit_tier: "auto_trust_tier_a" },
]);

describe("plan labels", () => {
  test("maps known plans and keeps unknown keys", () => {
    expect(planLabel("max_20x")).toBe("Max 20x");
    expect(planLabel("pro")).toBe("Pro");
    expect(planLabel("")).toBe("");
    expect(planLabel("future_plan")).toBe("future_plan");
    expect(planTone("max_5x")).toBe("highlight");
    expect(planTone("free")).toBe("muted");
  });
});

describe("plan filter", () => {
  test("groups Max tiers and Team/Enterprise", () => {
    expect(matchPlan("max_20x", "max")).toBe(true);
    expect(matchPlan("max", "max")).toBe(true);
    expect(matchPlan("pro", "max")).toBe(false);
    expect(matchPlan("enterprise", "team")).toBe(true);
    expect(matchPlan("free", "free")).toBe(true);
    expect(matchPlan("", "none")).toBe(true);
    expect(matchPlan("unknown", "none")).toBe(true);
    expect(matchPlan("api", "none")).toBe(true);
    expect(matchPlan("pro", "none")).toBe(false);
  });
});

describe("plan orgs", () => {
  test("parses JSON text defensively", () => {
    expect(parsePlanOrgs(ORGS)).toHaveLength(3);
    expect(parsePlanOrgs([{ uuid: "x" }])).toEqual([{ uuid: "x" }]);
    expect(parsePlanOrgs("not json")).toEqual([]);
    expect(parsePlanOrgs("{}")).toEqual([]);
    expect(parsePlanOrgs(null)).toEqual([]);
  });

  test("tooltip lists every org and marks the authorized one", () => {
    const text = planTooltip({ plan: "pro", plan_orgs: ORGS, org_uuid: "o-pro", plan_checked_at: "T" });
    expect(text.split("\n")).toEqual([
      "套餐：Pro",
      "Personal（已授权）：Pro · default_claude_ai",
      "Acme：Enterprise · manual_tier_3",
      "Console：API · auto_trust_tier_a",
      "识别于 T",
    ]);
  });

  test("counts other distinct plans, ignoring Console API orgs", () => {
    expect(otherPlanCount({ plan: "pro", plan_orgs: ORGS })).toBe(1);
    expect(otherPlanCount({ plan: "enterprise", plan_orgs: ORGS })).toBe(1);
    expect(otherPlanCount({ plan: "max_20x", plan_orgs: "[]" })).toBe(0);
  });
});
