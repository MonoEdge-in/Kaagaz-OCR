// The reading pause (#478, Tushar, 3 Nov 2026): the super admin's daily page limit on an
// organisation's AI settings and as the platform default (own or inherited, a plain-words hint,
// the pages read today), its checks and save words; the budget's words now that reaching it
// pauses reading; and the org admin's "Pages read today: n of limit." (never money).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { pagesTodayLine } from "@/components/admin/logic";
import {
  DAILY_PAGES_HINT,
  DAILY_PAGES_RANGE,
  aiBody,
  aiErrors,
  changesText,
  defaultsImpact,
  draftOf,
  follows,
  followDefaults,
  inheritedText,
  pagesTodayText,
  parseDailyPages,
  refusalField,
} from "@/components/operator/models";
import { AiFields, fieldId, firstProblem } from "@/components/operator/OrgAiSettings";
import { DefaultsSummary } from "@/components/operator/PlatformDefaults";
import type { AiDefaults, AiEnv, AiValues } from "@/lib/types";

const values = (overrides: Partial<AiValues> = {}): AiValues => ({
  scan_models: [],
  scan_default: null,
  chat_models: [],
  chat_default: null,
  scan_thinking: null,
  scan_resolution: null,
  careful_thinking: null,
  careful_resolution: null,
  page_max_output_tokens: null,
  chat_levels: null,
  chat_default_level: null,
  dynamic: null,
  scan_reader: null,
  mistral_model: null,
  daily_page_limit: null,
  ulc_ceiling_paise: null, // Changed on purpose (#488): the most a page may cost under ultra low cost
  ...overrides,
});
const ENV: AiEnv = {
  scan_model: "m/env-scan",
  chat_model: "m/env-chat",
  scan_thinking: "low",
  scan_resolution: "high",
  careful_thinking: "high",
  careful_resolution: "low",
  page_max_output_tokens: 65536,
  scan_reader: "openrouter",
  mistral_model: "mistral-ocr-4-1",
  mistral_ready: true,
  mistral_missing: null,
  mistral_models: [],
  mistral_status: { stopped: false, code: null, since: null, documents_waiting: 0 },
};
const COUNTS: AiDefaults["orgs_inheriting"] = {
  scan_models: 1,
  chat_models: 1,
  scan_thinking: 1,
  scan_resolution: 1,
  careful_thinking: 1,
  careful_resolution: 1,
  page_max_output_tokens: 1,
  chat_levels: 1,
  dynamic: 1,
  scan_reader: 1,
  mistral_model: 1,
  daily_page_limit: 6,
  ulc_ceiling_paise: 6, // Changed on purpose (#488)
};

const fields = (props: { platform?: boolean; own?: AiValues; defaults?: AiValues; today?: { pages: number; cost_usd: number | null } }) =>
  renderToStaticMarkup(
    createElement(AiFields, {
      platform: props.platform ?? false,
      customerId: props.platform ? null : "org-1",
      today: props.today,
      catalogue: [],
      saved: props.own ?? values(),
      defaults: props.defaults ?? values(),
      env: ENV,
      maxAgents: 3,
      draft: draftOf(props.own ?? values()),
      errors: {},
      busy: false,
      onChange: () => undefined,
    }),
  );
const fieldOf = (html: string) => html.match(new RegExp(`<input id="${fieldId("daily_page_limit")}"[^>]*>`))?.[0] ?? "";

describe("the daily page limit field", () => {
  it("says whether the limit is the organisation's own or the platform's, in plain words", () => {
    const inherited = fields({ defaults: values({ daily_page_limit: 2000 }), today: { pages: 1240, cost_usd: 0.42 } });
    expect(inherited).toContain("Daily page limit");
    expect(fieldOf(inherited)).toContain('placeholder="Platform default (2,000 pages)"');
    expect(inherited).toContain(`id="${fieldId("daily_page_limit")}-origin" class=`);
    expect(inherited).toContain(DAILY_PAGES_HINT.replace("'", "&#x27;"));
    expect(inherited).toContain("Pages read today: 1,240 of 2,000 ($0.42)."); // the limit in effect (#478 review)
    expect(fields({ today: { pages: 1240, cost_usd: 0.42 } })).toContain("Pages read today: 1,240 ($0.42).");
    expect(inherited).toContain("When it&#x27;s reached, reading pauses until next month"); // pauses, never "stops"
    expect(inherited).not.toContain("reading stops");
    const id = fieldId("daily_page_limit");
    expect(fieldOf(inherited)).toContain(`aria-describedby="${id}-origin ${id}-hint"`); // read with its marks

    const own = fields({ own: values({ daily_page_limit: 500 }) });
    expect(fieldOf(own)).toContain('value="500"');
    expect(own).toContain("Own setting");
    expect(own).toContain("Use platform default: Daily page limit");
    expect(fields({ platform: true })).toContain('placeholder="No limit"');
    expect(inheritedText("daily_page_limit", values(), ENV, [])).toBe("no limit");
    const summary = (defaults: AiValues) =>
      renderToStaticMarkup(createElement(DefaultsSummary, { reply: { defaults, env: ENV, orgs_inheriting: COUNTS }, catalogue: [] }));
    expect(summary(values())).toContain("Daily page limit</dt><dd>No limit");
    expect(summary(values({ daily_page_limit: 800 }))).toContain("Daily page limit</dt><dd>800 pages");
  });

  it("has its own Reading limit box after Answering, not inside Scanning (QA U6)", () => {
    for (const html of [fields({}), fields({ platform: true })]) {
      const box = html.search(/<fieldset class="[^"]*area[^"]*"[^>]*><legend>Reading limit<\/legend>/);
      expect(box).toBeGreaterThan(html.indexOf("<legend>Answering"));
      expect(html.indexOf(`<input id="${fieldId("daily_page_limit")}"`)).toBeGreaterThan(box);
      expect(html).not.toMatch(/<h3[^>]*>Reading limit<\/h3>/);
    }
    expect(fields({}).indexOf("<legend>Reading limit")).toBeLessThan(fields({}).indexOf("<legend>Ensemble"));
    const view = readFileSync(join(__dirname, "../components/operator/OrgAiSettings.tsx"), "utf8");
    expect(view).toContain(
      "Scanning and answering will both follow the platform defaults, and Ensemble is off. The daily page limit and budget stay. Save to apply.",
    );
  });

  it("says No limit on the platform, and Remove the limit once one is set (QA U7)", () => {
    const id = fieldId("daily_page_limit");
    const origin = (html: string) => html.match(new RegExp(`id="${id}-origin"[^>]*>([^<]*)<`))?.[1];
    expect(origin(fields({ platform: true }))).toBe("No limit");
    const set = fields({ platform: true, own: values({ daily_page_limit: 800 }) });
    expect(origin(set)).toBe("Set here");
    expect(set).toContain('aria-label="Remove the limit: Daily page limit"');
    expect(set).not.toContain("Use the server&#x27;s setting: Daily page limit");
    // The other platform settings keep their words.
    expect(fields({ platform: true })).toMatch(
      new RegExp(`id="${fieldId("page_max_output_tokens")}-origin"[^>]*>The server&#x27;s setting<`),
    );
    expect(origin(fields({}))).toBe("Platform default"); // an organisation's is unchanged
  });

  it("is a whole number of pages from 1 to 1,000,000, or empty", () => {
    expect(parseDailyPages("")).toBeNull();
    expect(parseDailyPages(" 2000 ")).toBe(2000);
    for (const bad of ["0", "1000001", "1.5", "-3", "lots"]) expect(parseDailyPages(bad)).toBeUndefined();
    const draft = { ...draftOf(values()), daily_page_limit: "0" };
    expect(aiErrors(draft, false).daily_page_limit).toBe(DAILY_PAGES_RANGE);
    expect(firstProblem(aiErrors(draft, false))).toBe("daily_page_limit");
    expect(refusalField("pages_out_of_range")).toBe("daily_page_limit");
  });

  it("saves, says what changes and stays when the areas follow the platform again", () => {
    const own = values({ daily_page_limit: 2000 });
    expect(aiBody(draftOf(own))).toEqual(own);
    const before = draftOf(values());
    expect(changesText(before, { ...before, daily_page_limit: "2000" }, [])).toEqual(["Daily page limit: 2,000 pages"]);
    expect(followDefaults(draftOf(own)).daily_page_limit).toBe("2000"); // like the budget, its own
    expect(follows(own).scan).toBe("all"); // not a scan setting
    expect(defaultsImpact(values(), own, COUNTS).lead).toBe("This save changes, for 6 organisations still on platform defaults:");
    expect(pagesTodayText({ pages: 3, cost_usd: null })).toBe("Pages read today: 3.");
    expect(pagesTodayText({ pages: 1240, cost_usd: 0.42 }, 2000)).toBe("Pages read today: 1,240 of 2,000 ($0.42).");
    expect(pagesTodayText({ pages: 3, cost_usd: null }, null)).toBe("Pages read today: 3.");
    // QA C4 (4 Nov 2026): at the limit, the operator is told reading is paused, and who can lift it.
    const paused = " Reading is paused until the limit resets, or until you raise it.";
    expect(pagesTodayText({ pages: 2000, cost_usd: null }, 2000)).toBe(`Pages read today: 2,000 of 2,000.${paused}`);
    expect(pagesTodayText({ pages: 2001, cost_usd: 0.5 }, 2000)).toBe(`Pages read today: 2,001 of 2,000 ($0.50).${paused}`);
    expect(pagesTodayText({ pages: 1999, cost_usd: null }, 2000)).not.toContain("paused");
  });
});

describe("the org admin's pages today", () => {
  it("says the pages read today against the limit, and nothing without one; never money", () => {
    expect(pagesTodayLine({ reading_today: { pages: 1240, limit: 2000 } })).toBe("Pages read today: 1,240 of 2,000.");
    expect(pagesTodayLine({ reading_today: { pages: 1240, limit: null } })).toBeNull();
    expect(pagesTodayLine({})).toBeNull();
  });

  // Changed on purpose (QA C4, 4 Nov 2026): never "tomorrow morning", which is wrong when the limit is raised.
  it("says reading is paused until the limit resets or is raised, once it is reached (#478 review)", () => {
    const paused = "Reading is paused until the limit resets, or until it is raised.";
    expect(pagesTodayLine({ reading_today: { pages: 2000, limit: 2000 } })).toBe(`Pages read today: 2,000 of 2,000. ${paused}`);
    expect(pagesTodayLine({ reading_today: { pages: 2003, limit: 2000 } })).toBe(`Pages read today: 2,003 of 2,000. ${paused}`);
    expect(pagesTodayLine({ reading_today: { pages: 1999, limit: 2000 } })).not.toContain("paused");
  });
});
