// The operator's Models and AI spend tabs, an organisation's AI settings and the platform
// defaults (model choice, council 11 Oct 2026, MC2, MC3, MS1, MS2, SP3, SP4; features 25-27,
// council 15 Oct 2026, C10, C12): money and model words, the AI settings form's states and
// checks, the per-area status, refusals next to their field, the step-up code, the focus,
// the spend periods and totals, and what the tabs never show.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { modelConfirm, modelFocusId } from "@/components/operator/confirms";
import { operatorErrorMessage } from "@/components/operator/logic";
import { ModelsTable } from "@/components/operator/ModelsView";
import {
  aiBody,
  aiErrors,
  canEnable,
  changesText,
  defaultsImpact,
  draftOf,
  followDefaults,
  follows,
  inheritedText,
  inrText,
  jobText,
  modelAction,
  monthSpentText,
  notEnabledReason,
  offered,
  pageCost,
  pageCostText,
  parseBudget,
  per1000Text,
  periodRange,
  pickerText,
  priceText,
  refusalField,
  resetSection,
  sameAsDefault,
  spendTotals,
  statusText,
  toggled,
  toggledLevel,
  usd,
  usesText,
} from "@/components/operator/models";
import type { AiErrors } from "@/components/operator/models";
import { AiFields, CODE_ID, fieldId, firstProblem, refusalOf } from "@/components/operator/OrgAiSettings";
import { DefaultsSummary } from "@/components/operator/PlatformDefaults";
import { SpendTable } from "@/components/operator/SpendView";
import { ApiError } from "@/lib/api";
import type { AiDefaults, AiValues, OperatorModel, OperatorUsageRow, OrgInsight } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

const model = (overrides: Partial<OperatorModel> = {}): OperatorModel => ({
  id: "google/gemini-3.1-pro-preview",
  name: "Gemini 3.1 Pro",
  price_in_usd_per_m: 2,
  price_out_usd_per_m: 12,
  providers: ["google-vertex"],
  efforts: ["low", "high"],
  expires_on: null,
  eligible: true,
  seen_at: "2026-10-11T00:00:00Z",
  verified: true,
  verified_at: "2026-10-11T00:00:00Z",
  tokens_per_page_in: 1300,
  tokens_per_page_out: 800,
  ledger_tokens_in: null,
  ledger_tokens_out: null,
  enabled_at: null,
  chat_eligible: true,
  chat_verified: true,
  vendor_allowed: true,
  ...overrides,
});

const row = (overrides: Partial<OperatorUsageRow> = {}): OperatorUsageRow => ({
  organisation_id: "o-1",
  organisation_name: "Sharma Traders",
  model: "google/gemini-3.1-pro-preview",
  calls: 10,
  cached_calls: 2,
  input_tokens: 15000,
  output_tokens: 4000,
  reasoning_tokens: 900,
  cost_usd: 0.12,
  cost_estimated: false,
  cost_part_unknown: false,
  ...overrides,
});

describe("money and model words", () => {
  it("says dollars plainly, a fraction of a cent as under a cent, and estimates in words", () => {
    expect(usd(1234.5)).toBe("$1,234.50");
    expect(usd(0)).toBe("$0.00");
    expect(usd(0.004)).toBe("< $0.01");
    expect(usd(null)).toBe("Not known");
    expect(usd(0.12, true)).toBe("≈ $0.12 (estimated)");
    expect(priceText(0.1)).toBe("$0.10");
    expect(priceText(0.075)).toBe("$0.075");
  });

  it("formats dollars with the one shared formatter (R6)", () => {
    const source = readFileSync(join(__dirname, "..", "components/operator/models.ts"), "utf8");
    // Changed on purpose (board 9, 16 Oct 2026): the same import also brings PART_NOT_KNOWN.
    expect(source).toMatch(/import \{[^}]*\bformatUsd\b[^}]*\} from "@\/lib\/format";/);
    expect(source).not.toMatch(/style: "currency", currency: "USD" \}\)/);
  });

  it("estimates a page from verify-models' tokens (MC3), a rough one, none before it ran", () => {
    expect(pageCost(model())?.usd).toBeCloseTo((1300 * 2 + 800 * 12) / 1_000_000);
    expect(pageCost(model())?.rough).toBe(true);
    expect(pageCost(model({ tokens_per_page_in: null }))).toBeNull();
    expect(pageCostText(model())).toBe("about $0.01 a page (rough estimate)");
    expect(pageCostText(model({ tokens_per_page_in: null }))).toBe("Cost not measured yet");
  });

  it("says a scan model's cost per 1,000 pages for the AI settings (board 9)", () => {
    expect(per1000Text(model())).toBe("$12.20 per 1,000 pages (rough estimate)");
    expect(per1000Text(model({ ledger_tokens_in: 10_000, ledger_tokens_out: 2_000 }))).toBe("$44.00 per 1,000 pages");
    expect(per1000Text(model({ tokens_per_page_in: null }))).toBe("Cost not measured yet");
  });

  it("says a budget in rough rupees at the one fixed rate, and nothing without a budget", () => {
    expect(inrText(120)).toBe("≈ ₹10,560 a month (at ₹88 per $)");
    expect(inrText(null)).toBeNull();
    expect(inrText(undefined)).toBeNull();
    expect(readFileSync(join(__dirname, "..", "components/operator/models.ts"), "utf8")).toMatch(/\/\/ ponytail: .*\n.*\nexport const INR_PER_USD = /);
  });

  it("says this month's spend from the insights, and nothing when it is not known", () => {
    const org = { month_cost_usd: 58.1, month_cost_estimated: false, month_cost_part_unknown: false } as OrgInsight;
    expect(monthSpentText(org)).toBe("This month: $58.10 used");
    expect(monthSpentText({ ...org, month_cost_estimated: true, month_cost_part_unknown: true })).toBe(
      "This month: ≈ $58.10 (estimated) used (part not known)",
    );
    expect(monthSpentText({ ...org, month_cost_usd: null })).toBeNull();
    expect(monthSpentText(undefined)).toBeNull();
  });

  it("estimates a page from the last 30 days of scans first (R3)", () => {
    const scanned = model({ ledger_tokens_in: 10_000, ledger_tokens_out: 2_000 });
    expect(pageCost(scanned)).toEqual({ usd: (10_000 * 2 + 2_000 * 12) / 1_000_000, rough: false });
    expect(pageCostText(scanned)).toBe("about $0.04 a page");
    expect(pageCostText(model({ ledger_tokens_in: 100, ledger_tokens_out: 10 }))).toBe("under $0.01 a page");
  });

  // Changed on purpose (features 25-27, 15 Oct 2026): a model verified for answering only
  // can be enabled too, as the server.
  it("enables a model that can scan or answer, and says why not", () => {
    expect([canEnable(model()), modelAction(model())]).toEqual([true, "enable"]);
    expect(modelAction(model({ enabled_at: "2026-10-11T00:00:00Z" }))).toBe("disable");
    expect(modelAction(model({ verified: false }))).toBe("enable"); // it can still answer
    expect(modelAction(model({ verified: false, chat_verified: false }))).toBeNull();
    expect(notEnabledReason(model({ eligible: false, chat_eligible: false }))).toMatch(/catalogue's rules/);
    expect(notEnabledReason(model({ verified: false, chat_verified: false }))).toMatch(/verify-models --record/);
    expect(notEnabledReason(model())).toBeNull();
    expect([usesText(model()), usesText(model({ chat_verified: false })), usesText(model({ verified: false }))]).toEqual([
      "Scanning and answering",
      "Scanning",
      "Answering",
    ]);
  });

  it("asks a fresh code to enable or disable, and focuses the model's name after", () => {
    const enable = modelConfirm(model(), true);
    const disable = modelConfirm(model({ enabled_at: "2026-10-11T00:00:00Z" }), false);
    expect([enable.stepUp, disable.stepUp, enable.danger, disable.danger]).toEqual([true, true, false, true]);
    expect(enable.focusId).toBe(modelFocusId(model().id));
    expect(modelFocusId("google/gemini-3.1-pro-preview")).toBe("model-name-google-gemini-3-1-pro-preview");
  });

  it("says each refusal of a model or AI settings change in plain words", () => {
    const said = (code: string, status = 409) => operatorErrorMessage(new ApiError(status, code, "Something went wrong."));
    expect(said("not_verified")).toMatch(/verify-models/);
    expect(said("chat_not_verified")).toMatch(/answer prompt/);
    expect(said("not_eligible")).toMatch(/rules/);
    expect(said("in_use")).toMatch(/every organisation and the defaults/);
    expect(said("unavailable")).toMatch(/not enabled on the platform/);
    // Changed on purpose (features 25-27, 15 Oct 2026): three areas have a default now.
    expect(said("default_not_allowed")).toMatch(/its own ticked list/);
    expect(said("level_not_supported")).toMatch(/thinking levels/);
    expect(said("needs_two_models_scan")).toMatch(/two scanning models/);
    expect(said("needs_two_models_chat")).toMatch(/two answering models/);
  });
});

describe("the Models table", () => {
  it("says eligible, verified and enabled in words, with the price and the cost a page", () => {
    const unverified = { verified: false, eligible: false, chat_eligible: false, chat_verified: false };
    const html = renderToStaticMarkup(
      createElement(ModelsTable, {
        models: [model(), model({ id: "a/b", name: "Unverified", vendor_allowed: false, ...unverified })],
        onChange: () => undefined,
      }),
    );
    for (const text of ["Gemini 3.1 Pro", "google-vertex", "$2.00 in, $12.00 out", "about $0.01 a page (rough estimate)"]) {
      expect(html).toContain(text);
    }
    expect(html).toContain("Scanning: Yes, answering: Yes");
    expect(html).toContain("Scanning: No, answering: No");
    expect(html).toContain('aria-label="Enable Gemini 3.1 Pro"');
    // R13: every cell says its column when the table reflows into cards.
    expect(html.match(/<td role="cell"(?![^>]*data-label)/g)).toBeNull();
    expect(html).not.toContain('aria-label="Enable Unverified"');
    expect(html).toContain("Does not pass the catalogue&#x27;s rules now.");
    expect(html.match(/its maker is not on the allowed list/g)).toHaveLength(1); // S1: only a/b
  });
});

// --- AI settings (features 25-27) --------------------------------------------------------

const values = (overrides: Partial<AiValues> = {}): AiValues => ({
  scan_models: [],
  scan_default: null,
  chat_models: [],
  chat_default: null,
  scan_thinking: null,
  chat_levels: null,
  chat_default_level: null,
  dynamic: null,
  ...overrides,
});
const ENV = { scan_model: "google/env-scan", chat_model: "google/env-chat", scan_thinking: "low" };
const DEFAULTS = values({
  scan_models: ["m/one", "m/two"],
  scan_default: "m/two",
  chat_levels: ["low", "medium"],
  chat_default_level: "low",
  dynamic: true,
});
const COUNTS: AiDefaults["orgs_inheriting"] = { scan_models: 4, chat_models: 7, scan_thinking: 1, chat_levels: 7, dynamic: 5 };
const catalogue = [
  model({ id: "m/one", name: "One", enabled_at: "2026-10-11T00:00:00Z", chat_verified: false }),
  model({ id: "m/two", name: "Two", enabled_at: "2026-10-11T00:00:00Z" }),
  model({ id: "m/off", name: "Off" }),
  model({ id: "x/far", name: "Far", enabled_at: "2026-10-11T00:00:00Z", vendor_allowed: false }),
];

describe("the AI settings form's rules", () => {
  it("offers each area the enabled models that can do its work, and any already in it", () => {
    expect(offered(catalogue, "scan", []).map((m) => m.id)).toEqual(["m/one", "m/two", "x/far"]);
    expect(offered(catalogue, "chat", []).map((m) => m.id)).toEqual(["m/two", "x/far"]); // One can't answer
    expect(offered(catalogue, "chat", ["m/off"]).map((m) => m.id)).toEqual(["m/two", "m/off", "x/far"]);
  });

  it("keeps each area's default among its ticked models, and the default level among the levels", () => {
    const draft = draftOf(values());
    const both = toggled(toggled(draft, "scan", "m/one", true), "scan", "m/two", true);
    expect([both.scan_models, both.scan_default, both.chat_default]).toEqual([["m/one", "m/two"], "m/one", null]);
    expect(toggled(both, "scan", "m/one", false).scan_default).toBe("m/two");
    expect(toggled(draft, "chat", "m/two", true)).toMatchObject({ chat_models: ["m/two"], chat_default: "m/two", scan_models: [] });
    const levels = toggledLevel(toggledLevel(draft, "high", true), "low", true);
    expect([levels.chat_levels, levels.chat_default_level]).toEqual([["low", "high"], "high"]);
    expect(toggledLevel(levels, "high", false).chat_default_level).toBe("low");
  });

  it("checks the defaults, levels and budget before a code is asked for", () => {
    const draft = draftOf(values({ scan_models: ["m/one"], chat_models: ["m/two"], chat_levels: ["low"] }), { ensemble: false, ai_budget_usd: null });
    expect(aiErrors({ ...draft, budget: "abc" }, false)).toEqual({
      scan_default: "Choose the default scanning model from the ticked models.",
      chat_default: "Choose the default answering model from the ticked models.",
      chat_levels: "Choose the default level from the ticked levels.",
      budget: "Enter dollars above 0, with at most 2 decimals, or leave it empty.",
    });
    expect(aiErrors(draftOf(values()), false)).toEqual({}); // follows the defaults in everything
    expect(aiErrors(draftOf(values()), true)).toEqual({ chat_levels: "Tick at least one thinking level." });
    expect(aiErrors({ ...draftOf(values({ chat_levels: ["low"], chat_default_level: "low" })), budget: "abc" }, true)).toEqual({});
    expect(["", " 12.5 ", "0.01", "9999999999.99"].map(parseBudget)).toEqual([null, 12.5, 0.01, 9999999999.99]);
    for (const bad of ["0", "-5", "1.005", "1e3", "ten", "10000000000"]) expect(parseBudget(bad), bad).toBeUndefined();
    expect(firstProblem({ budget: "x", chat_default: "y" })).toBe("chat_default");
  });

  it("sends no default with an empty list, and no levels as following the platform", () => {
    const draft = { ...draftOf(values({ scan_default: "m/one", chat_default_level: "high" })) };
    expect(aiBody(draft)).toEqual(values());
    expect(aiBody(draftOf(DEFAULTS))).toEqual(DEFAULTS);
  });

  it("says per area whether it follows the platform defaults (C12)", () => {
    expect(statusText(values())).toBe("Scanning: platform defaults · Answering: platform defaults");
    expect(statusText(values({ scan_models: ["m/one"], scan_thinking: "high", dynamic: false }))).toBe(
      "Scanning: own settings · Answering: platform defaults",
    );
    expect(statusText(values({ dynamic: true, chat_models: ["m/two"], chat_levels: ["low"] }))).toBe(
      "Scanning: partly own settings · Answering: own settings",
    );
    expect(follows(values({ chat_levels: ["low"] }))).toEqual({ scan: "all", chat: "some" });
  });

  it("empties both areas and Ensemble for Use platform defaults, and keeps the budget", () => {
    const own = draftOf(values({ scan_models: ["m/one"], scan_default: "m/one", chat_levels: ["low"], dynamic: true }), {
      ensemble: true,
      ai_budget_usd: 50,
    });
    const reset = followDefaults(own);
    expect(aiBody(reset)).toEqual(values());
    expect([reset.ensemble, reset.budget]).toEqual([false, "50"]);
  });

  it("says per section whether it is the platform default, and resets only that section (board 9)", () => {
    const own = draftOf(values({ scan_models: ["m/one"], scan_default: "m/one", chat_levels: ["low"], chat_default_level: "low" }), {
      ensemble: true,
      ai_budget_usd: 50,
    });
    const sections = ["scan", "chat", "ensemble", "budget"] as const;
    expect(sections.map((each) => sameAsDefault(own, each))).toEqual([false, false, false, false]);
    expect(sections.map((each) => sameAsDefault(draftOf(values()), each))).toEqual([true, true, true, true]);
    const scan = resetSection(own, "scan");
    expect(sections.map((each) => sameAsDefault(scan, each))).toEqual([true, false, false, false]);
    expect([scan.chat_levels, scan.ensemble, scan.budget]).toEqual([["low"], true, "50"]);
    expect(sameAsDefault(resetSection(own, "chat"), "chat")).toBe(true);
    expect(resetSection(own, "ensemble")).toEqual({ ...own, ensemble: false });
    expect(resetSection(own, "budget")).toEqual({ ...own, budget: "" });
  });

  it("lists the unsaved changes in words for the save bar, and none for the same values", () => {
    const saved = draftOf(values(), { ensemble: false, ai_budget_usd: 100 });
    expect(changesText(saved, saved, catalogue)).toEqual([]);
    expect(changesText(saved, { ...saved, budget: "100.00" }, catalogue)).toEqual([]); // the same dollars
    const draft = { ...toggled(saved, "scan", "m/one", true), ensemble: true, budget: "120" };
    expect(changesText(saved, draft, catalogue)).toEqual(["Scanning models: One (default)", "Ensemble: on", "Budget: $100.00 to $120.00"]);
    const levels = toggledLevel({ ...saved, dynamic: false, budget: "" }, "high", true);
    expect(changesText(saved, levels, catalogue)).toEqual(["Dynamic: Off", "Answering levels: High; default High", "Budget: $100.00 to none"]);
    const own = draftOf(values({ chat_models: ["m/two"], chat_default: "m/two" }));
    expect(changesText(own, draftOf(values()), catalogue)).toEqual(["Answering models: platform defaults"]);
  });

  it("says what an organisation's people will see in the chat picker, from the draft or the defaults", () => {
    expect(pickerText("Sharma Traders", draftOf(values()), DEFAULTS, ENV, catalogue)).toBe(
      "People at Sharma Traders will see: the server's model (google/env-chat); levels Low, Medium; default Low",
    );
    const own = draftOf(values({ chat_models: ["m/two"], chat_default: "m/two", chat_levels: ["medium", "high"], chat_default_level: "medium" }));
    expect(pickerText("Sharma Traders", own, DEFAULTS, ENV, catalogue)).toBe(
      "People at Sharma Traders will see: Two (default); levels Medium, High; default Medium",
    );
  });

  it("names each model's job for Compare models: the platform defaults first, else what it can do", () => {
    const platform = { defaults: DEFAULTS, env: ENV, orgs_inheriting: COUNTS };
    const pick = (id: string) => catalogue.find((each) => each.id === id) ?? model({ id });
    const [one, two, off, far] = [pick("m/one"), pick("m/two"), pick("m/off"), pick("x/far")];
    expect(jobText(two, platform)).toBe("Scan default (low)"); // no platform level: the server's
    expect(jobText(one, platform)).toBe("Scan only · not used for chat");
    expect(jobText(off, platform)).toBe("Not enabled");
    expect(jobText(far, platform)).toBe("Never used: its maker is not allowed");
    const chatOnly = model({ id: "c/only", enabled_at: "2026-10-11T00:00:00Z", verified: false });
    expect(jobText(chatOnly, platform)).toBe("Chat only · not used for scan");
    expect(jobText(model({ id: "google/env-chat" }), platform)).toBe("Chat default (Low)"); // no platform list: the server's
    const both = { ...DEFAULTS, scan_thinking: "high" as const, chat_models: ["m/two"], chat_default: "m/two", chat_default_level: "medium" as const };
    expect(jobText(two, { ...platform, defaults: both })).toBe("Scan default (High) · chat default (Medium)");
    expect(jobText(two, null)).toBe("Scan and chat");
  });

  it("names what an organisation that follows the platform gets, or the server's own", () => {
    expect(inheritedText("scan_models", DEFAULTS, ENV, catalogue)).toBe("Two (default), One");
    expect(inheritedText("chat_models", DEFAULTS, ENV, catalogue)).toBe("the server's model (google/env-chat)");
    expect(inheritedText("scan_thinking", DEFAULTS, ENV, catalogue)).toBe("the server's level (low)");
    expect(inheritedText("chat_levels", DEFAULTS, ENV, catalogue)).toBe("Low, Medium; default Low");
    expect(inheritedText("dynamic", DEFAULTS, ENV, catalogue)).toBe("On");
  });

  it("says which organisations still on the defaults a platform save changes, per area", () => {
    expect(defaultsImpact(DEFAULTS, DEFAULTS, COUNTS)).toBe("Nothing changes.");
    const next = { ...DEFAULTS, scan_models: ["m/two", "m/one"], dynamic: false, chat_levels: ["low" as const] };
    expect(defaultsImpact(DEFAULTS, next, COUNTS)).toBe(
      "This save changes the Dynamic of 5 organisations, the answering levels of 7 organisations still on the platform " +
        "defaults. Organisations with their own settings keep them.",
    ); // the same models in another order change nothing
    expect(defaultsImpact(DEFAULTS, { ...DEFAULTS, scan_default: "m/one" }, { ...COUNTS, scan_models: 1 })).toMatch(
      /the scanning models of 1 organisation still/,
    );
  });

  it("puts a refusal next to the field it is about, and a refused code at the code", () => {
    const refused = (code: string, status = 409) => refusalOf(new ApiError(status, code, "Something went wrong."));
    expect(refused("needs_two_models_scan").field).toBe("scan_models");
    expect(refused("not_verified").field).toBe("scan_models");
    expect(refused("chat_not_verified").field).toBe("chat_models");
    expect(refused("needs_two_models_chat").field).toBe("chat_models");
    expect(refused("unavailable").field).toBe("form");
    expect(refused("step_up_failed", 403)).toEqual({ field: "code", message: operatorErrorMessage(new ApiError(403, "step_up_failed", "")) });
    expect(refusalOf(new TypeError("offline")).field).toBe("form");
    expect(refusalField("level_not_supported")).toBe("form"); // either area's levels
    expect(fieldId("chat_models")).toBe("ai-chat-models");
  });
});

describe("the AI settings fields", () => {
  const fields = (
    props: { platform?: boolean; own?: AiValues; errors?: AiErrors; busy?: boolean; orgName?: string; spent?: string | null } = {},
  ) => {
    const own = props.own ?? values();
    return renderToStaticMarkup(
      createElement(AiFields, {
        platform: props.platform ?? false,
        orgName: props.orgName,
        spent: props.spent,
        catalogue,
        saved: own,
        defaults: DEFAULTS,
        env: ENV,
        maxAgents: 3,
        draft: draftOf(own, { ensemble: false, ai_budget_usd: 25 }),
        errors: props.errors ?? {},
        busy: props.busy ?? false,
        onChange: () => undefined,
      }),
    );
  };

  // Changed on purpose (board 9, 16 Oct 2026): every section of an organisation says
  // "Same as platform default" or "Changed from platform default", Ensemble and Budget too.
  const tag = (html: string, section: string) => html.match(new RegExp(`<legend>${section}<span[^>]*>([^<]*)</span>`))?.[1];
  it("groups each area in a fieldset with a legend, and marks an area that follows the platform", () => {
    const html = fields();
    expect(html.match(/<fieldset/g)?.length).toBeGreaterThanOrEqual(5);
    expect(html.match(/<legend/g)?.length).toBe(html.match(/<fieldset/g)?.length);
    expect(["Scanning", "Answering", "Ensemble", "Budget"].map((section) => tag(html, section))).toEqual([
      "Same as platform default",
      "Same as platform default",
      "Same as platform default",
      "Changed from platform default", // a $25 budget
    ]);
    expect(html).toContain("None ticked: from platform defaults, Two (default), One.");
    expect(html).toContain("Platform default (the server&#x27;s level (low))");
    expect(html).toContain("Platform default (On)");
    expect(html).toContain("Picks the right reading for each page.");
    const own = fields({ own: values({ scan_models: ["m/one"], scan_default: "m/one", scan_thinking: "high", dynamic: false }) });
    expect([tag(own, "Scanning"), tag(own, "Answering")]).toEqual(["Changed from platform default", "Same as platform default"]);
    expect(own.match(/type="radio"/g)).toHaveLength(1); // the default among the ticked
  });

  it("offers models by name, says a maker that is not allowed, and shows High, Medium, Low", () => {
    const html = fields();
    for (const name of ["One", "Two", "Far"]) expect(html).toContain(`<span>${name}`);
    expect(html).not.toContain("<span>Off");
    expect(html).toContain("its maker is not on the allowed list");
    for (const level of ["Low", "Medium", "High"]) expect(html).toContain(`<span>${level}</span>`);
  });

  it("has Ensemble with the most models, and the budget in dollars, for an organisation only", () => {
    const org = fields();
    expect(org).toContain("Ensemble: models work together");
    expect(org).toContain("Up to 3 models");
    expect(org).toContain("Monthly AI budget, in US dollars");
    const platform = fields({ platform: true });
    expect(platform).not.toMatch(/Ensemble|budget|dollars/);
    expect(platform).toContain("None ticked: the server&#x27;s model (google/env-scan).");
    expect(platform).not.toMatch(/platform default<\/span>|Reset section|People at/);
  });

  it("says a refusal next to its field and points the field at it", () => {
    const html = fields({ errors: { chat_models: "Ensemble needs at least two answering models.", budget: "Bad." } });
    expect(html).toMatch(/id="ai-chat-models-error" class="error-text" role="alert">Ensemble needs/);
    expect(html).toMatch(/id="ai-chat-models"[^>]*aria-describedby="ai-chat-models-error"/);
    // Changed on purpose (16 Oct 2026): a budget's rupees are said with its hint.
    expect(html).toContain('aria-invalid="true" aria-describedby="ai-budget-error ai-budget-hint ai-budget-inr"');
    expect(fields()).not.toContain("-error");
  });

  // Changed on purpose (board 9, 16 Oct 2026): Ensemble and Budget are sections of their own.
  it("locks every control while saving", () => {
    expect(fields({ busy: true }).match(/<fieldset[^>]*disabled=""/g)).toHaveLength(4);
  });

  it("offers Reset section only on a changed section, named by its section", () => {
    const html = fields({ own: values({ chat_models: ["m/two"], chat_default: "m/two" }) });
    expect(html.match(/>Reset section<\/button>/g)).toHaveLength(2); // Answering and the $25 budget
    expect(html).toContain('aria-label="Reset section: Answering"');
    expect(html).toContain('aria-label="Reset section: Budget"');
    expect(html).not.toContain('aria-label="Reset section: Scanning"');
  });

  it("shows each scan model's cost per 1,000 pages, never an answering model's", () => {
    const html = fields();
    expect(html).toContain("<span>One<span");
    expect(html).toContain("$12.20 per 1,000 pages (rough estimate)");
    expect(html.match(/per 1,000 pages/g)).toHaveLength(3); // One, Two and Far for scanning
    expect(html).toContain("<span>Two</span>"); // answering
  });

  it("says what the organisation's people will see, its budget in rupees and this month's spend", () => {
    const html = fields({ orgName: "Sharma Traders", spent: "This month: $58.10 used" });
    expect(html).toContain("People at Sharma Traders will see: the server&#x27;s model (google/env-chat); levels Low, Medium; default Low");
    expect(html).toContain('<span id="ai-budget-inr" class="hint">≈ ₹2,200 a month (at ₹88 per $)</span>');
    expect(html).toContain("This month: $58.10 used");
    expect(fields()).not.toMatch(/People at|This month/);
  });
});

describe("the AI settings dialogs", () => {
  it("ask a fresh code before saving, and clear it after a refusal", () => {
    for (const file of ["components/operator/OrgAiSettings.tsx", "components/operator/PlatformDefaults.tsx"]) {
      const source = read(file);
      expect(source, file).toContain("<CodeField");
      expect(source, file).toContain("id={CODE_ID}");
      expect(source, file).toContain("normalizeCode(code)");
      expect(source, file).toContain('setCode("")');
    }
    expect(CODE_ID).toBe("ai-code");
  });

  it("keeps a save bar at the dialog's foot that lists the changes, with Discard and Save off while nothing changed", () => {
    const dialog = read("components/operator/OrgAiSettings.tsx");
    expect(dialog).toContain("changesText(saved, draft, loaded?.catalogue ?? [])");
    expect(dialog.match(/disabled=\{busy \|\| unchanged\}/g)).toHaveLength(2); // Discard and Save
    expect(dialog).toContain('"No unsaved changes"');
    expect(dialog).toContain("setDraft(saved);");
    expect(dialog).toContain("if (busy || !draft || unchanged) return;");
    const css = read("components/operator/Operator.module.css");
    // Changed on purpose (MonoEdge design system, 16 Oct 2026): the MonoEdge surface token.
    expect(css).toMatch(/\.saveBar \{[^}]*position: sticky;\s*bottom: 0;[^}]*background: var\(--bg-surface\);/);
  });

  it("names both areas when the organisation goes back to the platform defaults", () => {
    expect(read("components/operator/OrgAiSettings.tsx")).toContain(
      "Scanning and answering will both follow the platform defaults, and Ensemble is off. Save to apply.",
    );
  });

  it("returns the focus to the button that opened them", () => {
    const dialog = read("components/operator/OrgAiSettings.tsx");
    expect(dialog).toContain("onClosed={() => document.getElementById(orgAiFocusId(org.id))?.focus()}");
    expect(dialog).toContain('form?.querySelector<HTMLElement>("input, select, button")?.focus()');
    expect(read("components/operator/OrganisationsView.tsx")).toContain("id={orgAiFocusId(org.id)}");
    expect(read("components/operator/PlatformDefaults.tsx")).toContain("onClosed={() => document.getElementById(CHANGE_ID)?.focus()}");
  });

  it("show the platform defaults with how many organisations follow each area", () => {
    const html = renderToStaticMarkup(
      createElement(DefaultsSummary, { reply: { defaults: DEFAULTS, env: ENV, orgs_inheriting: COUNTS }, catalogue }),
    );
    expect(html).toContain("Two (default), One");
    expect(html).toContain("Followed by 1 organisation");
    expect(html).toContain("Followed by 7 organisations");
    expect(read("components/operator/PlatformDefaults.tsx")).toContain("Save and apply to organisations still on defaults");
  });

  it("the old AI models dialog is gone", () => {
    expect(() => read("components/operator/OrgModelsDialog.tsx")).toThrow();
  });
});

describe("AI spend", () => {
  it("chooses whole days, both ends included", () => {
    const now = new Date(2026, 9, 11, 15, 30);
    expect(periodRange("month", now)).toEqual({ from: "2026-10-01", to: "2026-10-11" });
    expect(periodRange("last-month", now)).toEqual({ from: "2026-09-01", to: "2026-09-30" });
    expect(periodRange("7d", now)).toEqual({ from: "2026-10-05", to: "2026-10-11" });
    expect(periodRange("30d", now)).toEqual({ from: "2026-09-12", to: "2026-10-11" });
    expect(periodRange("last-month", new Date(2026, 0, 5))).toEqual({ from: "2025-12-01", to: "2025-12-31" });
  });

  it("adds the rows up: not known when no cost is, estimated when any is", () => {
    const totals = spendTotals([row(), row({ model: "m/two", cost_usd: null, calls: 1, cached_calls: 0 })]);
    expect(totals).toMatchObject({ calls: 11, cached_calls: 2, input_tokens: 30000, cost_usd: 0.12, cost_estimated: false });
    expect(spendTotals([row({ cost_usd: null })]).cost_usd).toBeNull();
    expect(spendTotals([row(), row({ cost_estimated: true })]).cost_estimated).toBe(true);
    // R9: a row with no cost, or with part of it unknown, makes the total's cost part unknown.
    expect(spendTotals([row(), row()]).cost_part_unknown).toBe(false);
    expect(spendTotals([row(), row({ cost_usd: null })]).cost_part_unknown).toBe(true);
    expect(spendTotals([row(), row({ cost_part_unknown: true })]).cost_part_unknown).toBe(true);
  });

  it("shows organisation and model rows with a total and the estimated marker", () => {
    const html = renderToStaticMarkup(createElement(SpendTable, { rows: [row(), row({ organisation_id: "o-2", organisation_name: "Mehta Textiles", cost_estimated: true })] }));
    for (const text of ["Sharma Traders", "Mehta Textiles", "Total", "≈ $0.24 (estimated)", "2 from the page cache"]) {
      expect(html).toContain(text);
    }
    expect(html).toContain("<tfoot");
  });

  it("never shows a person or a document", () => {
    for (const file of [
      "components/operator/SpendView.tsx",
      "components/operator/ModelsView.tsx",
      "components/operator/OrgAiSettings.tsx",
      "components/operator/PlatformDefaults.tsx",
    ]) {
      expect(read(file), file).not.toMatch(/\.(user_\w+|document_\w+|display_name|email)\b/);
    }
  });
});
