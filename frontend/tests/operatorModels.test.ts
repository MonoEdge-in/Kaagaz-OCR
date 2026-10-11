// The operator's Models and AI spend tabs and the "AI models" dialog (model choice,
// council 11 Oct 2026, MC2, MC3, MS1, MS2, SP3, SP4): money and model words, the dialog's
// states and checks, the spend periods and totals, and what the tabs never show.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { modelConfirm, modelFocusId } from "@/components/operator/confirms";
import { operatorErrorMessage } from "@/components/operator/logic";
import { ModelsTable } from "@/components/operator/ModelsView";
import {
  bodyOf,
  canEnable,
  draftErrors,
  draftOf,
  modelAction,
  notEnabledReason,
  offered,
  pageCost,
  pageCostText,
  parseBudget,
  periodRange,
  priceText,
  spendTotals,
  toggled,
  usd,
} from "@/components/operator/models";
import type { DraftErrors } from "@/components/operator/models";
import { OrgModelsFields } from "@/components/operator/OrgModelsDialog";
import { SpendTable } from "@/components/operator/SpendView";
import { ApiError } from "@/lib/api";
import type { OperatorModel, OperatorUsageRow } from "@/lib/types";

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

  it("estimates a page from verify-models' tokens (MC3), a rough one, none before it ran", () => {
    expect(pageCost(model())?.usd).toBeCloseTo((1300 * 2 + 800 * 12) / 1_000_000);
    expect(pageCost(model())?.rough).toBe(true);
    expect(pageCost(model({ tokens_per_page_in: null }))).toBeNull();
    expect(pageCostText(model())).toBe("about $0.01 a page (rough estimate)");
    expect(pageCostText(model({ tokens_per_page_in: null }))).toBe("Cost not measured yet");
  });

  it("estimates a page from the last 30 days of scans first (R3)", () => {
    const scanned = model({ ledger_tokens_in: 10_000, ledger_tokens_out: 2_000 });
    expect(pageCost(scanned)).toEqual({ usd: (10_000 * 2 + 2_000 * 12) / 1_000_000, rough: false });
    expect(pageCostText(scanned)).toBe("about $0.04 a page");
    expect(pageCostText(model({ ledger_tokens_in: 100, ledger_tokens_out: 10 }))).toBe("under $0.01 a page");
  });

  it("enables only an eligible model verified for the current prompt, and says why not", () => {
    expect([canEnable(model()), modelAction(model())]).toEqual([true, "enable"]);
    expect(modelAction(model({ enabled_at: "2026-10-11T00:00:00Z" }))).toBe("disable");
    expect(modelAction(model({ verified: false }))).toBeNull();
    expect(notEnabledReason(model({ eligible: false }))).toMatch(/catalogue's rules/);
    expect(notEnabledReason(model({ verified: false }))).toMatch(/verify-models --record/);
    expect(notEnabledReason(model())).toBeNull();
  });

  it("asks a fresh code to enable or disable, and focuses the model's name after", () => {
    const enable = modelConfirm(model(), true);
    const disable = modelConfirm(model({ enabled_at: "2026-10-11T00:00:00Z" }), false);
    expect([enable.stepUp, disable.stepUp, enable.danger, disable.danger]).toEqual([true, true, false, true]);
    expect(enable.focusId).toBe(modelFocusId(model().id));
    expect(modelFocusId("google/gemini-3.1-pro-preview")).toBe("model-name-google-gemini-3-1-pro-preview");
  });

  it("says each refusal of a model change in plain words", () => {
    const said = (code: string, status = 409) => operatorErrorMessage(new ApiError(status, code, "Something went wrong."));
    expect(said("not_verified")).toMatch(/verify-models/);
    expect(said("not_eligible")).toMatch(/rules/);
    expect(said("in_use")).toMatch(/every organisation/);
    expect(said("unavailable")).toMatch(/not enabled on the platform/);
    expect(said("default_not_allowed", 400)).toMatch(/default scan model/);
  });
});

describe("the Models table", () => {
  it("says eligible, verified and enabled in words, with the price and the cost a page", () => {
    const html = renderToStaticMarkup(
      createElement(ModelsTable, {
        models: [model(), model({ id: "a/b", name: "Unverified", verified: false, eligible: false })],
        onChange: () => undefined,
      }),
    );
    for (const text of ["Gemini 3.1 Pro", "google-vertex", "$2.00 in, $12.00 out", "about $0.01 a page (rough estimate)"]) {
      expect(html).toContain(text);
    }
    expect(html).toContain("Yes");
    expect(html).toContain("No");
    expect(html).toContain('aria-label="Enable Gemini 3.1 Pro"');
    expect(html).not.toContain('aria-label="Enable Unverified"');
    expect(html).toContain("Does not pass the catalogue&#x27;s rules now.");
  });
});

describe("the AI models dialog", () => {
  const saved = { models: ["m/one"], default_scan: "m/one", ai_budget_usd: 50 };
  const catalogue = [
    model({ id: "m/one", name: "One", enabled_at: "2026-10-11T00:00:00Z" }),
    model({ id: "m/two", name: "Two", enabled_at: "2026-10-11T00:00:00Z" }),
    model({ id: "m/off", name: "Off" }),
  ];

  it("offers the enabled models and any the organisation still has", () => {
    expect(offered(catalogue, ["m/off"]).map((m) => m.id)).toEqual(["m/one", "m/two", "m/off"]);
    expect(offered(catalogue, []).map((m) => m.id)).toEqual(["m/one", "m/two"]);
  });

  it("keeps the default among the ticked models", () => {
    const draft = draftOf(saved);
    expect(draft).toEqual({ models: ["m/one"], default_scan: "m/one", budget: "50" });
    const both = toggled(draft, "m/two", true);
    expect(both).toEqual({ ...draft, models: ["m/one", "m/two"] });
    expect(toggled(both, "m/one", false).default_scan).toBe("m/two");
    expect(toggled(toggled(both, "m/one", false), "m/two", false).default_scan).toBeNull();
    expect(toggled({ models: [], default_scan: null, budget: "" }, "m/two", true).default_scan).toBe("m/two");
  });

  it("checks the budget and the default before sending, and sends none of both when no model is ticked", () => {
    expect(["", " 12.5 ", "0.01", "9999999999.99"].map(parseBudget)).toEqual([null, 12.5, 0.01, 9999999999.99]);
    for (const bad of ["0", "-5", "1.005", "1e3", "ten", "10000000000"]) expect(parseBudget(bad), bad).toBeUndefined();
    expect(draftErrors({ models: ["m/one"], default_scan: null, budget: "abc" })).toEqual({
      default_scan: "Choose the default scan model from the ticked models.",
      budget: "Enter dollars above 0, with at most 2 decimals, or leave it empty.",
    });
    expect(draftErrors({ models: [], default_scan: null, budget: "" })).toEqual({ default_scan: null, budget: null });
    expect(bodyOf({ models: [], default_scan: "m/one", budget: "" })).toEqual({
      models: [],
      default_scan: null,
      ai_budget_usd: null,
    });
    expect(bodyOf({ models: ["m/one"], default_scan: "m/one", budget: "25.50" }).ai_budget_usd).toBe(25.5);
  });

  const fields = (draft = draftOf(saved), errors: DraftErrors = { default_scan: null, budget: null }, busy = false) =>
    renderToStaticMarkup(
      createElement(OrgModelsFields, { models: offered(catalogue, saved.models), draft, errors, busy, onChange: () => undefined }),
    );

  it("shows checkboxes for the models, radios for the default and a labelled budget", () => {
    const html = fields();
    expect(html.match(/type="checkbox"/g)).toHaveLength(2);
    expect(html.match(/type="radio"/g)).toHaveLength(1); // only the ticked model can be the default
    expect(html).toContain("<legend");
    expect(html).toContain('for="org-models-budget"');
    expect(html).toContain('aria-describedby="org-models-budget-hint"');
    expect(html).toMatch(/Warn only/);
  });

  it("says what no model means, marks errors and locks everything while saving", () => {
    expect(fields({ models: [], default_scan: null, budget: "" })).toMatch(/platform&#x27;s model, as before/);
    const invalid = fields(draftOf(saved), { default_scan: "Pick one.", budget: "Bad." });
    expect(invalid).toContain('aria-invalid="true"');
    expect(invalid).toContain('aria-describedby="org-models-budget-hint org-models-budget-error"');
    expect(invalid).toContain('id="org-models-default-error"');
    expect(fields(draftOf(saved), { default_scan: null, budget: null }, true).match(/disabled=""/g)?.length).toBeGreaterThanOrEqual(3);
  });

  it("returns the focus to the row's AI models button", () => {
    const dialog = read("components/operator/OrgModelsDialog.tsx");
    expect(dialog).toContain("onClosed={() => document.getElementById(orgModelsFocusId(org.id))?.focus()}");
    expect(read("components/operator/OrganisationsView.tsx")).toContain("id={orgModelsFocusId(org.id)}");
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
  });

  it("shows organisation and model rows with a total and the estimated marker", () => {
    const html = renderToStaticMarkup(createElement(SpendTable, { rows: [row(), row({ organisation_id: "o-2", organisation_name: "Mehta Textiles", cost_estimated: true })] }));
    for (const text of ["Sharma Traders", "Mehta Textiles", "Total", "≈ $0.24 (estimated)", "2 from the page cache"]) {
      expect(html).toContain(text);
    }
    expect(html).toContain("<tfoot");
  });

  it("never shows a person or a document", () => {
    for (const file of ["components/operator/SpendView.tsx", "components/operator/ModelsView.tsx", "components/operator/OrgModelsDialog.tsx"]) {
      expect(read(file), file).not.toMatch(/\.(user_\w+|document_\w+|display_name|email)\b/);
    }
  });
});
