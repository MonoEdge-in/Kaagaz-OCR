// Ultra low cost (#488, council 4 Nov 2026): a third reader, Gemini read as briefly as possible
// with a hard ceiling on what one page may cost. The reader's words and hint; the fields it
// sets itself hidden while it reads (the draft keeps them); Ensemble off; the ceiling field in
// rupees, sent in paise, following like every scan setting; the save bar, reset, status and
// the defaults' counts with it only while it reads; the Cost per page panel's lines; the
// server's words for its refusals; and no money on an organisation admin's or member's screen.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiSettingsForm } from "@/components/admin/AiSettingsPanel";
import { CEILING_RANGE, REFUSALS, TOKENS_RANGE } from "@/components/operator/logic";
import * as models from "@/components/operator/models";
import {
  CEILING_HINT,
  ENSEMBLE_NOT_WITH_ULC,
  READERS,
  READER_WORDS,
  ULC_HIDDEN,
  ULC_HINT,
  aiBody,
  aiErrors,
  ceilingOf,
  changesText,
  checkRefusal,
  costAsk,
  costChange,
  defaultsImpact,
  draftOf,
  follows,
  inheritedText,
  parseCeiling,
  pickedReader,
  readerOf,
  resetSection,
  sameSettings,
  scanSettings,
  statusText,
  ulcWords,
  withCost,
} from "@/components/operator/models";
import { AiFields, fieldId, firstProblem, refusalOf } from "@/components/operator/OrgAiSettings";
import { DefaultsSummary } from "@/components/operator/PlatformDefaults";
import { orgDraftOf, mineDraftOf } from "@/lib/aiSettings";
import { ApiError, postAccuracyCheck, postScanCost, putAiDefaults, putOrgAi } from "@/lib/api";
import type { AiDraft } from "@/components/operator/models";
import type { AiDefaults, AiEnv, AiSettings, AiTaskSetting, AiValues, OperatorModel, ScanCost, UlcCost } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

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
  ulc_ceiling_paise: null,
  backup_reader: null, // Changed on purpose (#664): the backup reader
  backup_model: null,
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
  ulc_ceiling_paise: 40,
  switch_ceiling_paise: null,
  mistral_model: "mistral-ocr-4-1",
  mistral_ready: true,
  mistral_missing: null,
  mistral_models: [{ id: "mistral-ocr-4-1", name: "Mistral OCR 4.1", usd_per_page: 0.004, has_blocks: true }],
  mistral_status: { stopped: false, code: null, since: null, documents_waiting: 0 },
};
const COUNTS: AiDefaults["orgs_inheriting"] = {
  scan_models: 4,
  chat_models: 7,
  scan_thinking: 1,
  scan_resolution: 2,
  careful_thinking: 3,
  careful_resolution: 6,
  page_max_output_tokens: 8,
  chat_levels: 7,
  dynamic: 5,
  scan_reader: 3,
  mistral_model: 2,
  daily_page_limit: 1,
  ulc_ceiling_paise: 9,
  backup_reader: 2, // Changed on purpose (#664)
};
const ULC = values({ scan_reader: "ultra_low_cost" });
const GEMINI_REPLY = { defaults: values(), env: ENV };

const fields = (
  props: { platform?: boolean; own?: AiValues; defaults?: AiValues; draft?: AiDraft; errors?: Record<string, string>; catalogue?: OperatorModel[] } = {},
) => {
  const own = props.own ?? values();
  return renderToStaticMarkup(
    createElement(AiFields, {
      platform: props.platform ?? false,
      customerId: props.platform ? null : "org-1",
      catalogue: props.catalogue ?? [],
      saved: own,
      defaults: props.defaults ?? values(),
      env: ENV,
      maxAgents: 3,
      draft: props.draft ?? draftOf(own),
      errors: props.errors ?? {},
      busy: false,
      onChange: () => undefined,
    }),
  );
};
const optionsOf = (html: string, id: string) => {
  const select = html.match(new RegExp(`<select id="${id}"[\\s\\S]*?</select>`))?.[0] ?? "";
  return [...select.matchAll(/<option[^>]*>([^<]*)<\/option>/g)].map((m) => m[1]);
};
const describedByOf = (html: string, tag: string, id: string) =>
  html.match(new RegExp(`<${tag} id="${id}"[^>]*aria-describedby="([^"]*)"`))?.[1] ?? "";
const inputOf = (html: string, id: string) => html.match(new RegExp(`<input id="${id}"[^>]*/>`))?.[0] ?? "";
const escaped = (text: string) => text.replace(/'/g, "&#x27;");

const ulc = (overrides: Partial<UlcCost> = {}): UlcCost => ({
  ceiling_paise: 40,
  ceiling_usd: 0.4 / 88,
  pages: 1240,
  usd_per_page: 0.31 / 88,
  top_usd_per_page: 0.38 / 88,
  limit_hits: 37,
  ...overrides,
});
const reply = (block: UlcCost | null | "absent" = ulc(), overrides: Partial<ScanCost> = {}): ScanCost => ({
  days: 30,
  first_read: { model: "google/gemini-3-flash", thinking: "minimal", resolution: "high", usd_per_page: 0.31 / 88, pages: 1240 },
  re_read: null,
  usd_per_page: 0.31 / 88,
  quality: { clean_share: 0.9, pages: 1240, max_clean_share: null, max_pages: 0 },
  tokens: { input_per_page: 1200, output_per_page: 300, output_p99: 900, limit: 65536, limit_hits: 0, pages: 1240 },
  accuracy: null,
  fixed_price: false,
  fixed_usd_per_page: null,
  ...(block === "absent" ? {} : { ulc: block }),
  ...overrides,
});

describe("ultra low cost: the reader's words", () => {
  it("is the third reader, named with its model, with a plain hint while it reads", () => {
    expect(READERS).toEqual(["openrouter", "mistral", "ultra_low_cost"]);
    expect(READER_WORDS.ultra_low_cost).toBe("Ultra low cost (Gemini)");
    expect(ULC_HINT).toBe(
      "Each page is read as briefly as possible and never costs more than the most you set. Hard handwriting may read less well.",
    );
    const org = fields();
    expect(optionsOf(org, "ai-scan-reader")).toEqual(["Platform default (Gemini)", "Gemini", "Mistral OCR", "Ultra low cost (Gemini)"]);
    expect(org).not.toContain(escaped(ULC_HINT));
    const own = fields({ own: ULC });
    expect(own).toContain(`<span id="ai-scan-reader-hint" class="hint">${ULC_HINT}</span>`);
    expect(describedByOf(own, "select", "ai-scan-reader").split(" ")).toContain("ai-scan-reader-hint");
    // Followed, it says so without brackets in brackets.
    expect(optionsOf(fields({ defaults: ULC }), "ai-scan-reader")[0]).toBe("Platform default (Ultra low cost)");
    expect(fields({ defaults: ULC })).toContain(ULC_HINT);
    expect(optionsOf(fields({ platform: true, own: ULC }), "ai-scan-reader")[0]).toBe("The server&#x27;s reader (Gemini)");
  });

  it("follows the reader actually used, and picking another lets go of the ceiling", () => {
    expect(readerOf(draftOf(values()), ULC, ENV, false)).toBe("ultra_low_cost");
    expect(readerOf(draftOf(values()), ULC, ENV, true)).toBe("openrouter");
    const draft = draftOf(values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 }));
    expect(draft.ulc_ceiling).toBe("0.30");
    expect(pickedReader(draft, "openrouter", values(), ENV, false)).toMatchObject({ scan_reader: "openrouter", ulc_ceiling: "" });
    expect(pickedReader(draft, "mistral", values(), ENV, false)).toMatchObject({ scan_reader: "mistral", ulc_ceiling: "" });
    expect(pickedReader(draft, "", values(), ENV, false)).toMatchObject({ scan_reader: null, ulc_ceiling: "" });
    expect(pickedReader(draft, "", ULC, ENV, false)).toMatchObject({ scan_reader: null, ulc_ceiling: "0.30" });
    // Ultra low cost has no Mistral model.
    const mistral = draftOf(values({ scan_reader: "mistral", mistral_model: "mistral-ocr-4-1" }));
    expect(pickedReader(mistral, "ultra_low_cost", values(), ENV, false)).toMatchObject({ scan_reader: "ultra_low_cost", mistral_model: null });
  });
});

describe("ultra low cost: the fields it sets itself", () => {
  const hidden = [
    'id="ai-scan-thinking"',
    'id="ai-page-max-output-tokens"',
    'id="ai-dynamic"',
    'id="ai-careful-thinking"',
    'id="ai-careful-resolution"',
  ];

  it("hides thinking, the output limit, Cost saving and pages read again, and says why", () => {
    expect(ULC_HIDDEN).toBe("Thinking, the output limit and pages read again are set by ultra low cost. They come back if you switch.");
    for (const html of [fields({ own: ULC }), fields({ defaults: ULC }), fields({ platform: true, own: ULC })]) {
      expect(html).toContain(`<p class="hint">${ULC_HIDDEN}</p>`);
      for (const field of hidden) expect(html).not.toContain(field);
      expect(html).not.toMatch(/<h3[^>]*>Pages read again<\/h3>/);
      expect(html).not.toContain(">Cost saving<");
      // The model and its picture detail still choose how the page is read.
      expect(html).toContain("Models its scans may be read with");
      expect(html).toContain('id="ai-scan-resolution"');
      expect(html).toMatch(/<h3[^>]*>First read<\/h3>/);
    }
    const gemini = fields();
    expect(gemini).not.toContain(ULC_HIDDEN);
    for (const field of hidden) expect(gemini).toContain(field);
  });

  it("keeps their values in the draft, so they come back on a switch", () => {
    const own = values({ scan_reader: "ultra_low_cost", scan_thinking: "high", page_max_output_tokens: 8192, dynamic: true });
    expect(aiBody(draftOf(own))).toEqual(own);
    expect(pickedReader(draftOf(own), "openrouter", values(), ENV, false)).toMatchObject({
      scan_thinking: "high",
      page_max_output_tokens: "8192",
      dynamic: true,
    });
  });

  it("turns Ensemble off, and says why", () => {
    expect(ENSEMBLE_NOT_WITH_ULC).toBe("Ensemble isn't used while ultra low cost reads the pages.");
    const html = fields({ own: ULC });
    expect(html).toContain(`<span id="ai-ensemble-ulc" class="hint">${escaped(ENSEMBLE_NOT_WITH_ULC)}</span>`);
    expect(html).toContain('<input type="checkbox" disabled="" aria-describedby="ai-ensemble-hint ai-ensemble-ulc"/>');
    expect(fields()).not.toContain("ai-ensemble-ulc");
  });

  it("never stops a save at a hidden output limit, nor says its range in the panel (#488 review)", () => {
    const typed = { ...draftOf(ULC), page_max_output_tokens: "100" };
    expect(aiErrors(typed, false, "ultra_low_cost")).toEqual({});
    expect(aiErrors({ ...typed, scan_reader: "mistral" as const }, false, "mistral")).toEqual({});
    expect(aiErrors(typed, false, "openrouter")).toEqual({ page_max_output_tokens: TOKENS_RANGE });
    const html = fields({ own: ULC, draft: typed });
    expect(html).not.toContain('id="ai-page-max-output-tokens"');
    expect(html).not.toContain(TOKENS_RANGE);
    expect(html).toContain("Working out the cost…");
    expect(fields({ draft: { ...draftOf(values()), page_max_output_tokens: "100" } })).toContain(TOKENS_RANGE);
  });

  it("hides the tokens part of the panel, which talks of the hidden limit", () => {
    expect(fields({ own: ULC })).not.toMatch(/<h3[^>]*>Tokens a page<\/h3>/);
    expect(fields()).toMatch(/<h3[^>]*>Tokens a page<\/h3>/);
  });
});

describe("ultra low cost: the ceiling field", () => {
  const ID = "ai-ulc-ceiling-paise";

  it("shows only while ultra low cost reads, right under the reader, in rupees", () => {
    expect(fieldId("ulc_ceiling_paise")).toBe(ID);
    expect(fields()).not.toContain(`id="${ID}"`);
    const html = fields({ own: ULC });
    expect(html).toContain(`<label for="${ID}" class="label">Most a page may cost</label>`);
    expect(html.indexOf(`id="${ID}"`)).toBeGreaterThan(html.indexOf('id="ai-scan-reader"'));
    expect(html.indexOf(`id="${ID}"`)).toBeLessThan(html.search(/<h3[^>]*>First read<\/h3>/));
    const input = inputOf(html, ID);
    for (const part of ['type="number"', 'inputMode="decimal"', 'min="0.20"', 'max="0.55"', 'step="0.01"', 'placeholder="Platform default (₹0.40)"'])
      expect(input, part).toContain(part);
    expect(CEILING_HINT).toBe(
      "From ₹0.20 to ₹0.55. A page that would cost more is read by Kaagaz's own reader instead and marked for a check.",
    );
    expect(html).toContain(`<span id="${ID}-hint" class="hint">${escaped(CEILING_HINT)}</span>`);
    expect(describedByOf(html, "input", ID)).toBe(`${ID}-origin ${ID}-hint`);
    expect(fields({ defaults: ULC })).toContain(`id="${ID}"`);
  });

  it("says what it follows, on an organisation and on the platform", () => {
    expect(inputOf(fields({ defaults: values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 25 }) }), ID)).toContain(
      'placeholder="Platform default (₹0.25)"',
    );
    // The #488 review: the platform's says the server's setting, like the other fields.
    expect(inputOf(fields({ platform: true, own: ULC }), ID)).toContain('placeholder="The server&#x27;s setting (₹0.40)"');
    expect(inheritedText("ulc_ceiling_paise", values(), ENV, [])).toBe("₹0.40");
  });

  it("marks it own or followed, with a way back only on its own", () => {
    const followed = fields({ own: ULC });
    expect(followed).toMatch(new RegExp(`<span id="${ID}-origin"[^>]*>Platform default</span>`));
    expect(followed).not.toContain('aria-label="Use platform default: Most a page may cost"');
    const own = fields({ own: values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 }) });
    expect(inputOf(own, ID)).toContain('value="0.30"');
    expect(own).toMatch(new RegExp(`<span id="${ID}-origin"[^>]*>Own setting</span>`));
    expect(own).toContain('aria-label="Use platform default: Most a page may cost"');
    expect(fields({ platform: true, own: values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 }) })).toContain(
      'aria-label="Use the server&#x27;s setting: Most a page may cost"',
    );
  });

  it("is typed in rupees and sent in paise, empty following", () => {
    expect([parseCeiling(""), parseCeiling(" 0.30 "), parseCeiling("0.2"), parseCeiling("0.40"), parseCeiling(".25"), parseCeiling("0.55")]).toEqual([null, 30, 20, 40, 25, 55]);
    for (const bad of ["0.19", "0.56", "1", "abc", "0.255", "-0.3"]) expect(parseCeiling(bad), bad).toBeUndefined();
    const own = values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 35 });
    expect(aiBody(draftOf(own))).toEqual(own);
    expect(aiBody({ ...draftOf(ULC), ulc_ceiling: "0.2" }).ulc_ceiling_paise).toBe(20);
    expect(aiBody(draftOf(ULC)).ulc_ceiling_paise).toBeNull();
  });

  it("checks the range before Save, next to the field, and focuses it first", () => {
    expect(CEILING_RANGE).toBe("Choose from ₹0.20 to ₹0.55 a page, or leave it empty."); // the server's words (#488 review)
    const errors = aiErrors({ ...draftOf(ULC), ulc_ceiling: "0.56" }, false, "ultra_low_cost");
    expect(errors).toEqual({ ulc_ceiling_paise: CEILING_RANGE });
    expect(aiErrors({ ...draftOf(ULC), ulc_ceiling: "0.56" }, true, "ultra_low_cost")).toEqual({ ulc_ceiling_paise: CEILING_RANGE, chat_levels: "Tick at least one thinking level." });
    expect(firstProblem({ ulc_ceiling_paise: CEILING_RANGE, scan_models: "x" })).toBe("ulc_ceiling_paise");
    const html = fields({ own: ULC, draft: { ...draftOf(ULC), ulc_ceiling: "0.56" }, errors });
    expect(html).toContain(`<span id="${ID}-error" class="error-text" role="alert">${CEILING_RANGE}</span>`);
    expect(inputOf(html, ID)).toContain('aria-invalid="true"');
    expect(describedByOf(html, "input", ID)).toBe(`${ID}-error ${ID}-origin ${ID}-hint`);
  });

  it("says the range once, from logic (#488 review)", () => {
    expect(models).not.toHaveProperty("CEILING_RANGE");
  });

  it("the panel's ceiling is the draft's own, else the one it follows, else ₹0.40", () => {
    expect(ceilingOf(draftOf(values({ ulc_ceiling_paise: 30 })), values({ ulc_ceiling_paise: 25 }), ENV, false)).toBe(30);
    expect(ceilingOf(draftOf(values()), values({ ulc_ceiling_paise: 25 }), ENV, false)).toBe(25);
    expect(ceilingOf(draftOf(values()), values({ ulc_ceiling_paise: 25 }), ENV, true)).toBe(40);
    expect(ceilingOf({ ...draftOf(values()), ulc_ceiling: "9" }, values(), ENV, false)).toBe(40);
    // #564 [V], review of #564: with OCR_ULTRA_LOW_COST on, OCR_PAGE_CEILING_PAISE is the ceiling only
    // where no reader is saved; a saved Ultra low cost with no ceiling keeps ₹0.40.
    const on = { ...ENV, scan_reader: "ultra_low_cost" as const, switch_ceiling_paise: 55 };
    expect(ceilingOf(draftOf(values()), values(), on, true)).toBe(55);
    expect(ceilingOf(draftOf(values()), values(), on, false)).toBe(55);
    expect(ceilingOf(draftOf(ULC), values(), on, true)).toBe(40);
    expect(ceilingOf(draftOf(values()), ULC, on, false)).toBe(40);
    expect(ceilingOf(draftOf(ULC), values(), on, false)).toBe(40);
  });
});

describe("ultra low cost: the server's refusals", () => {
  it("says the ceiling's range next to the field, in the server's words", () => {
    const caught = new ApiError(400, "ceiling_out_of_range", "Choose from ₹0.20 to ₹0.55 a page, or leave it empty.");
    expect(REFUSALS.ceiling_out_of_range).toBe(caught.message);
    expect(refusalOf(caught)).toEqual({ field: "ulc_ceiling_paise", message: caught.message });
  });

  it("says a model that can't read briefly next to the reader, in the server's words", () => {
    const words = "Ultra low cost needs a Gemini model with a known price that can read with minimal thinking.";
    const caught = new ApiError(409, "ulc_needs_gemini", words);
    expect(REFUSALS.ulc_needs_gemini).toBe(words);
    expect(refusalOf(caught)).toEqual({ field: "scan_reader", message: words });
    // An accuracy check refused so is never "a check is already running".
    expect(checkRefusal(caught)).toBe(words);
  });
});

describe("ultra low cost: follow, reset, status and the save bar", () => {
  it("counts the ceiling as a scan setting only while ultra low cost reads", () => {
    expect(follows(values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 }), "openrouter").scan).toBe("some");
    // The #488 review: following a reader, a ceiling counts only when that reader is ultra low cost.
    expect(follows(values({ ulc_ceiling_paise: 30 }), "ultra_low_cost").scan).toBe("some");
    expect(follows(values({ ulc_ceiling_paise: 30 }), "openrouter").scan).toBe("all");
    expect(follows(values({ ulc_ceiling_paise: 30 }), "mistral").scan).toBe("all");
    // Reading with Gemini or Mistral itself, it needs no ceiling to have its own settings.
    const gemini: Partial<AiValues> = {
      scan_models: ["m/one"],
      scan_thinking: "high",
      scan_resolution: "low",
      careful_thinking: "high",
      careful_resolution: "high",
      page_max_output_tokens: 8192,
      dynamic: false,
    };
    expect(follows(values({ ...gemini, scan_reader: "openrouter" }), "openrouter").scan).toBe("none");
    // Changed on purpose (#664): reading with Mistral, the backup reader is a scan setting of its own too.
    const mistral: Partial<AiValues> = { ...gemini, scan_reader: "mistral", mistral_model: "mistral-ocr-4-1", backup_reader: "off" };
    expect(follows(values(mistral), "openrouter").scan).toBe("none");
    // Under ultra low cost a Mistral model left over doesn't count; the ceiling does.
    expect(follows(values({ ...gemini, scan_reader: "ultra_low_cost" }), "openrouter").scan).toBe("some");
    expect(follows(values({ ...gemini, scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 }), "openrouter").scan).toBe("none");
    expect(statusText(values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 }), "openrouter")).toBe(
      "Scanning: partly own settings · Answering: platform defaults",
    );
  });

  it("resets the ceiling with Scanning", () => {
    expect(aiBody(resetSection(draftOf(values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 })), "scan"))).toEqual(values());
  });

  it("lists the ceiling in the save bar only while ultra low cost reads", () => {
    const saved = draftOf(values());
    const after = draftOf(values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 }));
    expect(changesText(saved, after, [], GEMINI_REPLY)).toEqual(["Who reads the pages: Ultra low cost", "Most a page may cost: ₹0.30"]);
    expect(changesText(after, saved, [], GEMINI_REPLY)).toEqual(["Who reads the pages: platform default"]);
    // Following a platform that reads with ultra low cost, the ceiling back to its default is a change.
    expect(changesText(after, saved, [], { defaults: ULC, env: ENV })).toEqual([
      "Who reads the pages: platform default",
      "Most a page may cost: platform default",
    ]);
    // A Mistral model let go of is no change worth saying under ultra low cost.
    const mistral = draftOf(values({ scan_reader: "mistral", mistral_model: "mistral-ocr-4-1" }));
    expect(changesText(mistral, draftOf(ULC), [], GEMINI_REPLY)).toEqual(["Who reads the pages: Ultra low cost"]);
  });

  it("says how many organisations a defaults change of the ceiling reaches, and shows it in the summary while it reads", () => {
    expect(defaultsImpact(ULC, values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 }), COUNTS)).toEqual({
      lead: "This save changes, for 9 organisations still on platform defaults:",
      areas: ["Most a page may cost"],
    });
    const summary = (defaults: AiValues) =>
      renderToStaticMarkup(createElement(DefaultsSummary, { reply: { defaults, env: ENV, orgs_inheriting: COUNTS }, catalogue: [] }));
    expect(summary(values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 }))).toMatch(
      /<dt[^>]*>Who reads the pages<\/dt><dd>Ultra low cost<span[^>]*>Followed by 3 organisations[\s\S]*<dt[^>]*>Most a page may cost<\/dt><dd>₹0.30<span[^>]*>Followed by 9 organisations/,
    );
    expect(summary(ULC)).toMatch(/<dt[^>]*>Most a page may cost<\/dt><dd>₹0.40<span/);
    expect(summary(values())).not.toContain(">Most a page may cost<");
  });

  it("names OCR_ULTRA_LOW_COST's reader and ceiling as the server's while nothing is saved (#564 [V])", () => {
    const env: AiEnv = { ...ENV, scan_reader: "ultra_low_cost", switch_ceiling_paise: 55 };
    const html = renderToStaticMarkup(
      createElement(DefaultsSummary, { reply: { defaults: values(), env, orgs_inheriting: COUNTS }, catalogue: [] }),
    );
    expect(html).toMatch(
      /<dt[^>]*>Who reads the pages<\/dt><dd>Ultra low cost[\s\S]*<dt[^>]*>Most a page may cost<\/dt><dd>₹0.55<span/,
    );
    expect(inheritedText("scan_reader", values(), ENV, [])).toBe(READER_WORDS.openrouter); // off: as before
    expect(inheritedText("ulc_ceiling_paise", ULC, env, [])).toBe("₹0.40"); // review of #564: a saved reader's
  });

  it("saves the ceiling with the settings, and asks the cost and the check with it", async () => {
    const sent: { url: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      sent.push({ url, body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response(JSON.stringify(reply()), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const own = values({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 });
    await putAiDefaults(own, "123456");
    await putOrgAi("org-1", { ...own, ensemble: false, ai_budget_usd: null }, "654321");
    await postScanCost(costAsk("org-1", own));
    await postAccuracyCheck("org-1", { ...scanSettings(aiBody({ ...draftOf(own), ulc_ceiling: "0.25" })), pages: 20 }, "123456");
    await postScanCost(costAsk("org-1", ULC));
    expect(sent[0]?.body).toMatchObject({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 });
    expect(sent[1]?.body).toMatchObject({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 });
    // Changed on purpose (#488 review): the cost and the check are asked at the draft's ceiling;
    // none (null) is the organisation's own, else the platform's (the server's side).
    expect(sent[2]?.body).toMatchObject({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 30 });
    expect(sent[3]?.body).toMatchObject({ scan_reader: "ultra_low_cost", ulc_ceiling_paise: 25, pages: 20 });
    expect(sent[4]?.body).toHaveProperty("ulc_ceiling_paise", null);
    // A changed ceiling is a changed cost question: the draft is asked again.
    expect(sameSettings(own, { ...own, ulc_ceiling_paise: 25 })).toBe(false);
    const flat = read("components/operator/OrgAiSettings.tsx").replace(/\s+/g, " ");
    expect(flat).toMatch(/costAsk\(customerId, \{[^}]*mistral_model, ulc_ceiling_paise,? \}\)/);
    expect(flat).toMatch(/\[ customerId, differs, badLimit, finished,[^\]]*mistral_model, ulc_ceiling_paise,? \]/);
  });
  afterEach(() => vi.unstubAllGlobals());
});

describe("ultra low cost: the Cost per page panel", () => {
  it("says the ceiling large, then what was measured, the dearest document and the pages read locally", () => {
    expect(ulcWords(reply(), 40)).toEqual({
      figure: "At most ₹0.40 a page",
      dollars: null,
      lines: [
        "Measured: about ₹0.31 a page on 1,240 pages in the last 30 days.",
        "Highest for one document: ₹0.38 a page.",
        "Read by Kaagaz's own reader to stay within the most a page may cost: 3% of pages.",
      ],
    });
    expect(ulcWords(reply(ulc({ top_usd_per_page: null })), 25).lines.some((line) => line.startsWith("Highest"))).toBe(false);
    expect(ulcWords(reply(), 25).figure).toBe("At most ₹0.25 a page");
  });

  it("says when nothing is measured yet, and is never an empty box", () => {
    const none = "Not measured yet: no page was read with ultra low cost in the last 30 days.";
    for (const each of [reply(ulc({ pages: 0, usd_per_page: null, top_usd_per_page: null, limit_hits: 0 })), reply(null), reply("absent")])
      expect(ulcWords(each, 40)).toEqual({ figure: "At most ₹0.40 a page", dollars: null, lines: [none] });
    expect(ulcWords(null, 40).lines).toEqual(["Working out the cost…"]);
    expect(ulcWords("failed", 40).lines).toEqual(["Couldn't work out the cost right now."]);
  });

  it("prices the change and the save by what was measured under ultra low cost", () => {
    const gemini = reply("absent", { usd_per_page: 0.62 / 88 });
    expect(costChange(gemini, reply())).toEqual({ from: "₹0.62", to: "₹0.31", verdict: "50% cheaper" });
    expect(withCost("Saved.", reply(), false)).toBe("Saved. Scans now cost about ₹0.31 a page.");
    const none = ulc({ pages: 0, usd_per_page: null, top_usd_per_page: null, limit_hits: 0 });
    expect(withCost("Saved.", reply(none), false)).toBe("Saved. The cost a page is not measured yet at these settings.");
  });

  it("is the panel's words while ultra low cost reads", () => {
    const source = read("components/operator/OrgAiSettings.tsx");
    expect(source).toContain("ulcCeiling === null ? costWords(shown) : ulcWords(shown, ulcCeiling)");
    // Rendered on the server, the panel is working out the cost: never an empty box.
    expect(fields({ own: ULC })).toContain("Working out the cost…");
  });
});

describe("ultra low cost: never on an organisation admin's or member's screen", () => {
  const MONEY = /[$₹]|\bprice|\bcost|\bbudget|\bspen[dt]|\bpaise\b|\brupee/i;
  const task = (changes: Partial<AiTaskSetting> & Pick<AiTaskSetting, "task">): AiTaskSetting => ({
    effective: "low",
    mine: null,
    organisation: null,
    locked: false,
    allowed: ["none", "low", "high", "max"],
    available: true,
    ...changes,
  });
  // Under ultra low cost the server allows Local reader only and AI at Low (scan_ceiling).
  const SETTINGS: AiSettings = {
    tasks: [
      task({ task: "scan", effective: "low", organisation: "low", allowed: ["none", "low"] }),
      task({ task: "ask", effective: "low", organisation: "low" }),
      task({ task: "ensemble", effective: "off", organisation: "off", allowed: ["off", "on"] }),
    ],
    can_edit_organisation: true,
  };
  const render = (scope: "organisation" | "member") =>
    renderToStaticMarkup(
      createElement(AiSettingsForm, {
        scope,
        settings: SETTINGS,
        draft: scope === "organisation" ? orgDraftOf(SETTINGS) : mineDraftOf(SETTINGS),
        busy: false,
        error: null,
        onChange: () => {},
        onSubmit: () => {},
      }),
    );

  it("says High and Max are not available in the existing words, with no money and no new words", () => {
    const html = render("organisation");
    expect(html).toContain("High, Max: Not available for your organisation.");
    for (const scope of ["organisation", "member"] as const) {
      const shown = render(scope).replace(/<[^>]*>/g, " ");
      expect(shown, scope).not.toMatch(MONEY);
      expect(shown, scope).not.toMatch(/ultra low|ceiling|most a page/i);
    }
  });

  it("is said only in the operator console", () => {
    const files = (dir: string): string[] =>
      readdirSync(join(ROOT, dir)).flatMap((name) => {
        const path = `${dir}/${name}`;
        return statSync(join(ROOT, path)).isDirectory() ? files(path) : /\.tsx?$/.test(name) ? [path] : [];
      });
    const outside = [...files("app"), ...files("components"), ...files("lib")].filter(
      (path) => !path.startsWith("components/operator/") && path !== "lib/types.ts",
    );
    expect(outside.length).toBeGreaterThan(50);
    // The ledger's kind name may be a key of the usage words; what it shows is pinned in admin-usage.test.ts.
    const kindKey = /^\s*ulc_scan: "Reading scanned pages",.*$/m;
    for (const path of outside) {
      expect(read(path).replace(kindKey, ""), path).not.toMatch(/ultra[ _]low|ulc_|ULC_|ceiling_paise/i);
    }
  });
});

// #488, 4 Nov 2026: the super admin couldn't choose ultra low cost's model or see why one can't.
const scanModel = (id: string, name: string, why: string | null): OperatorModel => ({
  id,
  name,
  price_in_usd_per_m: 0.3,
  price_out_usd_per_m: 2.5,
  providers: ["google-vertex"],
  efforts: ["minimal", "low"],
  expires_on: null,
  eligible: true,
  seen_at: "2026-11-04T00:00:00Z",
  verified: true,
  verified_at: "2026-11-04T00:00:00Z",
  tokens_per_page_in: 1300,
  tokens_per_page_out: 300,
  ledger_tokens_in: null,
  ledger_tokens_out: null,
  scan_pages: 0,
  scan_pages_costed: 0,
  scan_cost_usd: null,
  scan_cost_unknown_calls: 0,
  answer_questions: 0,
  answer_questions_costed: 0,
  answer_cost_usd: null,
  answer_cost_unknown_calls: 0,
  enabled_at: "2026-11-04T00:00:00Z",
  chat_eligible: false,
  chat_verified: false,
  vendor_allowed: true,
  ulc_ready: why === null,
  ulc_why: why,
  parameters: null, // Changed on purpose (#647): the stored parameters
});
const LITE = scanModel("google/gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite", null);
const PRO = scanModel("google/gemini-3.1-pro", "Gemini 3.1 Pro", "It can't read with minimal thinking.");
const CLAUDE = scanModel("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5", "Not a Gemini model.");

describe("ultra low cost: its model", () => {
  it("offers the scan models while it reads, each not ready one off with its reason", () => {
    const catalogue = [LITE, PRO, CLAUDE];
    expect(fields({ catalogue })).not.toContain(models.ULC_MODEL_LABEL); // not while Gemini reads
    for (const platform of [false, true]) {
      const html = fields({ platform, own: ULC, catalogue });
      expect(html).toContain(`<label for="ai-ulc-model" class="label">${models.ULC_MODEL_LABEL}</label>`);
      expect(optionsOf(html, "ai-ulc-model")).toEqual([
        platform ? "The server&#x27;s model (m/env-scan)" : "Platform default (the server&#x27;s model (m/env-scan))",
        "Gemini 3.5 Flash Lite",
        "Gemini 3.1 Pro (not available: It can&#x27;t read with minimal thinking.)",
        "Claude Sonnet 5.5 (not available: Not a Gemini model.)",
      ]);
      expect(html).toContain('<option value="google/gemini-3.1-pro" disabled="">');
      expect(html).toContain('<option value="google/gemini-3.5-flash-lite">');
      expect(html).not.toContain(models.ULC_NO_MODEL);
    }
    // Its own default is the one shown, with no empty choice.
    const own = fields({ own: { ...ULC, scan_models: [LITE.id], scan_default: LITE.id }, catalogue });
    expect(optionsOf(own, "ai-ulc-model")[0]).toBe("Gemini 3.5 Flash Lite");
  });

  it("says plainly when no model can be used yet", () => {
    expect(models.ULC_NO_MODEL).toBe("No model can be used yet. Ask for the model list to be refreshed (refresh-models).");
    const html = fields({ own: ULC, catalogue: [PRO, CLAUDE] });
    expect(html).toContain(`<span id="ai-ulc-model-none" class="hint">${models.ULC_NO_MODEL}</span>`);
    expect(describedByOf(html, "select", "ai-ulc-model")).toBe("ai-ulc-model-none");
  });

  it("choosing one makes it the default scan model and ticks it", () => {
    const draft = draftOf(values({ scan_reader: "ultra_low_cost", scan_models: [PRO.id], scan_default: PRO.id }));
    expect(models.ulcModelPicked(draft, LITE.id)).toMatchObject({ scan_models: [PRO.id, LITE.id], scan_default: LITE.id });
    expect(models.ulcModelPicked(draftOf(ULC), LITE.id)).toMatchObject({ scan_models: [LITE.id], scan_default: LITE.id });
  });
});

describe("Save never stops without a word", () => {
  it("says a hidden field's problem on the form's line", () => {
    const errors = { scan_default: "Choose the default scanning model from the ticked models." };
    expect(models.shownErrors(errors, () => true)).toEqual(errors);
    expect(models.shownErrors(errors, () => false)).toEqual({ ...errors, form: errors.scan_default });
    expect(models.shownErrors({ ...errors, form: "Kept" }, () => false).form).toBe("Kept");
    expect(models.shownErrors({}, () => false)).toEqual({});
  });

  it("is how both saves check the form", () => {
    for (const path of ["components/operator/OrgAiSettings.tsx", "components/operator/PlatformDefaults.tsx"])
      expect(read(path)).toMatch(/const found = shownErrors\(aiErrors\(/);
  });
});
