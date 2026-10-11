// Admin → AI models (model choice MS1, MS2; brief §7): read-only, set by
// MonoEdge, rendered in node from a fixture shaped like GET /admin/models.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ModelsView } from "@/components/admin/ModelsPanel";
import type { OrgModels } from "@/lib/types";

const MODELS: OrgModels = {
  set_by: "operator",
  default_scan: "google/gemini-3.1-pro-preview",
  models: [
    {
      id: "google/gemini-3.1-pro-preview",
      name: "Gemini 3.1 Pro",
      price_in_usd_per_m: 2,
      price_out_usd_per_m: 12,
      is_default_scan: true,
      provider: "google-vertex",
      usd_per_page: 0.05,
      page_hint: "about $0.05 a page",
    },
    {
      id: "anthropic/claude-haiku-5.5",
      name: "Claude Haiku 5.5",
      price_in_usd_per_m: 0.1,
      price_out_usd_per_m: 0.5,
      is_default_scan: false,
      provider: "google-vertex",
      usd_per_page: null,
      page_hint: "Cost not measured yet",
    },
  ],
  ai_budget_usd: 50,
  spent_this_month_usd: 12.4,
};

const render = (models: OrgModels = MODELS) => renderToStaticMarkup(createElement(ModelsView, { models }));

describe("the AI models panel", () => {
  const html = render();

  it("says MonoEdge sets them, in the table's caption, every header scope=col", () => {
    expect(html).toMatch(/<caption[^>]*>Set by MonoEdge\. Ask MonoEdge to change these\.<\/caption>/);
    expect(html.match(/<th scope="col"/g)).toHaveLength(5);
    expect(html.match(/<th[ >]/g)).toHaveLength(5);
  });

  it("shows each model's name, provider and prices per million tokens", () => {
    expect(html).toContain("Gemini 3.1 Pro");
    expect(html).toContain("Claude Haiku 5.5");
    expect(html).toContain("google-vertex");
    for (const price of ["$2.00", "$12.00", "$0.10", "$0.50"]) expect(html).toContain(`>${price}</td>`);
  });

  it("shows about what a page costs, or that it is not measured yet", () => {
    expect(html).toContain("about $0.05 a page");
    expect(html).toContain("Cost not measured yet");
  });

  it("says the cost a page in the server's words, a rough estimate as such (R3)", () => {
    const rough = render({
      ...MODELS,
      models: MODELS.models.map((model) => ({ ...model, page_hint: "under $0.01 a page (rough estimate)" })),
    });
    expect(rough).toContain(">under $0.01 a page (rough estimate)</td>");
  });

  it("marks the default in words beside an icon, once", () => {
    expect(html.match(/<svg[^>]*aria-hidden="true"[^>]*>.*?<\/svg>Default/g)).toHaveLength(1);
  });

  it("shows this month's spend against the budget, or that there is none", () => {
    expect(html).toContain("$12.40 of $50.00 this month");
    const none = render({ ...MODELS, ai_budget_usd: null, spent_this_month_usd: 3 });
    expect(none).toContain("No monthly AI budget is set.");
  });

  it("explains the platform's model when MonoEdge chose none", () => {
    const empty = render({ ...MODELS, models: [] });
    expect(empty).toContain("MonoEdge has not chosen models for your organisation");
    expect(empty).toContain("google/gemini-3.1-pro-preview");
    expect(empty).not.toContain("<table");
  });

  it("takes this month's spend from GET /admin/models alone, not the whole usage report (R13)", () => {
    const source = readFileSync(join(__dirname, "..", "components/admin/ModelsPanel.tsx"), "utf8");
    expect(source).not.toContain("getAdminUsage");
    expect(source).toContain("budgetLine(models)");
  });

  it("names no platform model in keyless mode, where none reads scans (R14)", () => {
    const keyless = render({ ...MODELS, models: [], default_scan: null });
    expect(keyless).toContain("Scans on this server are not read by an AI model.");
    expect(keyless).not.toContain("platform&#x27;s");
  });

  it("is read-only: no control, and no call that changes a model", () => {
    expect(html).not.toMatch(/<(input|select|button|form)\b/);
    const source = readFileSync(join(__dirname, "..", "components/admin/ModelsPanel.tsx"), "utf8");
    expect(source).not.toMatch(/putOperatorOrgModels|"PUT"|"POST"/);
  });
});
