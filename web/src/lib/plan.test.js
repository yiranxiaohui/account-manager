import { describe, expect, test } from "bun:test";
import {
  matchPlan,
  matchPlanFilter,
  otherPlanCount,
  parsePlanOrgs,
  planFilterOptions,
  planLabel,
  planRank,
  planTone,
  planTooltip,
} from "./plan.js";

const ORGS = JSON.stringify([
  { uuid: "o-pro", name: "Personal", plan: "pro", rate_limit_tier: "default_claude_ai" },
  { uuid: "o-ent", name: "Acme", plan: "enterprise", rate_limit_tier: "manual_tier_3" },
  { uuid: "o-api", name: "Console", plan: "api", rate_limit_tier: "auto_trust_tier_a" },
]);

describe("plan labels", () => {
  test("maps known plans and keeps unknown keys", () => {
    expect(planLabel("max_20x")).toBe("Ultra");
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

describe("subscription filter", () => {
  test("matches groups, exact plans and unrecognized plans", () => {
    expect(matchPlanFilter("max_5x", "all")).toBe(true);
    expect(matchPlanFilter("", undefined)).toBe(true);
    expect(matchPlanFilter("max_5x", "group:max")).toBe(true);
    expect(matchPlanFilter("max_5x", "max_20x")).toBe(false);
    expect(matchPlanFilter("max_20x", "max_20x")).toBe(true);
    expect(matchPlanFilter("max_20x", "max")).toBe(false);
    expect(matchPlanFilter("enterprise", "group:team")).toBe(true);
    expect(matchPlanFilter("enterprise", "team")).toBe(false);
    expect(matchPlanFilter("api", "none")).toBe(true);
    expect(matchPlanFilter(null, "none")).toBe(true);
    expect(matchPlanFilter("free", "none")).toBe(false);
  });

  test("counts each option and hides the empty multiplier-less Max", () => {
    const accounts = [{ plan: "max_20x" }, { plan: "max_5x" }, { plan: "enterprise" }, { plan: "" }];
    const opts = planFilterOptions(accounts);
    const count = (v) => opts.find((o) => o.value === v)?.count;
    expect(count("all")).toBe(4);
    expect(count("group:max")).toBe(2);
    expect(count("max_20x")).toBe(1);
    expect(count("group:team")).toBe(1);
    expect(count("none")).toBe(1);
    expect(opts.some((o) => o.value === "max")).toBe(false);
    expect(planFilterOptions(accounts, "max").some((o) => o.value === "max")).toBe(true);
    expect(planFilterOptions([{ plan: "max" }]).find((o) => o.value === "max").count).toBe(1);
  });

  test("ranks higher plans first", () => {
    const plans = ["free", "", "max_20x", "pro", "enterprise", "max_5x"];
    expect([...plans].sort((a, b) => planRank(b) - planRank(a)))
      .toEqual(["max_20x", "max_5x", "enterprise", "pro", "free", ""]);
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
