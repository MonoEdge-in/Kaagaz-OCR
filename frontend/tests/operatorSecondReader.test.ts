// The second reader (#664, Tushar, 18 Nov 2026): a page Mistral reads no text on (a map, a
// drawing, a page it refuses) is read once more by a model the super admin chooses, normal or
// ultra low cost, per organisation and as the platform default, off until chosen. The picker
// right under the reader choice, its words and options, the draft and the body it saves, what it
// follows, the save bar and the defaults' counts, and the server's refusal next to it.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  SECOND_READER_HINT,
  aiBody,
  changesText,
  defaultsImpact,
  draftOf,
  follows,
  inheritedText,
  pickedSecond,
  refusalField,
  resetSection,
} from "@/components/operator/models";
import { AiFields, fieldId } from "@/components/operator/OrgAiSettings";
import { DefaultsSummary } from "@/components/operator/PlatformDefaults";
import type { AiDraft } from "@/components/operator/models";
import type { AiDefaults, AiEnv, AiValues, OperatorModel } from "@/lib/types";

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
  second_reader: null,
  second_model: null,
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
  second_reader: 6,
};
const scanModel = (id: string, name: string, why: string | null, enabled = true): OperatorModel => ({
  id,
  name,
  price_in_usd_per_m: 0.3,
  price_out_usd_per_m: 2.5,
  providers: ["google-vertex"],
  efforts: ["minimal", "low"],
  expires_on: null,
  eligible: true,
  seen_at: "2026-11-18T00:00:00Z",
  verified: true,
  verified_at: "2026-11-18T00:00:00Z",
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
  enabled_at: enabled ? "2026-11-18T00:00:00Z" : null,
  chat_eligible: false,
  chat_verified: false,
  vendor_allowed: true,
  ulc_ready: why === null,
  ulc_why: why,
  parameters: null,
});
const FLASH = scanModel("google/gemini-3.5-flash", "Gemini 3.5 Flash", null);
const PRO = scanModel("google/gemini-3.1-pro", "Gemini 3.1 Pro", "It can't read with minimal thinking.");
const OLD = scanModel("google/gemini-old", "Gemini Old", null, false); // no longer enabled
const CATALOGUE = [FLASH, PRO, OLD];
const MISTRAL = values({ scan_reader: "mistral" });
const ID = "ai-second-reader";

const fields = (props: { platform?: boolean; own?: AiValues; defaults?: AiValues; draft?: AiDraft; errors?: Record<string, string> } = {}) => {
  const own = props.own ?? values();
  return renderToStaticMarkup(
    createElement(AiFields, {
      platform: props.platform ?? false,
      customerId: props.platform ? null : "org-1",
      catalogue: CATALOGUE,
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
  return [...select.matchAll(/<option([^>]*)>([^<]*)<\/option>/g)].map((m) => (m[1]?.includes("disabled") ? `${m[2]} [disabled]` : m[2]));
};
const escaped = (text: string) => text.replace(/'/g, "&#x27;");

describe("second reader: the picker", () => {
  it("sits right under the reader choice while Mistral reads, with a plain hint", () => {
    expect(fieldId("second_reader")).toBe(ID);
    const html = fields({ own: MISTRAL });
    expect(html).toContain(`<label for="${ID}" class="label">Second reader</label>`);
    expect(html.indexOf(`id="${ID}"`)).toBeGreaterThan(html.indexOf('id="ai-mistral-model"'));
    expect(html.indexOf(`id="${ID}"`)).toBeLessThan(html.indexOf(escaped("Gemini's reading settings are hidden")));
    expect(SECOND_READER_HINT).toBe(
      "When Mistral finds no text on a page (a map, a drawing, a page it can't read), that page alone is read once more by this model, billed like any page. Never when Mistral is down or out of credit.",
    );
    expect(html).toContain(`<span id="${ID}-hint" class="hint">${escaped(SECOND_READER_HINT)}</span>`);
    expect(fields({ defaults: MISTRAL })).toContain(`id="${ID}"`); // Mistral followed
  });

  it("is not on an organisation's screen while another reader reads; always on the platform's", () => {
    expect(fields()).not.toContain(`id="${ID}"`);
    expect(fields({ own: values({ scan_reader: "ultra_low_cost" }) })).not.toContain(`id="${ID}"`);
    // The platform's applies to every organisation that reads with Mistral, whoever the platform reads with.
    expect(fields({ platform: true })).toContain(`id="${ID}"`);
  });

  it("offers off, then each enabled scanning model normal or ultra low cost, and the saved one", () => {
    expect(optionsOf(fields({ own: MISTRAL }), ID)).toEqual([
      "Platform default (Off)",
      "Off",
      "Gemini 3.5 Flash, normal read",
      "Gemini 3.5 Flash, ultra low cost",
      "Gemini 3.1 Pro, normal read",
      "Gemini 3.1 Pro, ultra low cost (not available: It can&#x27;t read with minimal thinking.) [disabled]",
    ]);
    // On the platform, nothing chosen is off: no second "Off".
    expect(optionsOf(fields({ platform: true }), ID).slice(0, 2)).toEqual(["Off", "Gemini 3.5 Flash, normal read"]);
    // A saved model no longer enabled stays listed, so the form shows what is saved.
    const saved = values({ scan_reader: "mistral", second_reader: "openrouter", second_model: OLD.id });
    expect(optionsOf(fields({ own: saved }), ID)).toContain("Gemini Old, normal read");
    expect(fields({ own: saved })).toMatch(new RegExp(`<option value="openrouter:${OLD.id}" selected="">`));
  });

  it("says what the organisation follows", () => {
    const platform = values({ second_reader: "ultra_low_cost", second_model: FLASH.id });
    expect(optionsOf(fields({ own: MISTRAL, defaults: platform }), ID)[0]).toBe("Platform default (Gemini 3.5 Flash, ultra low cost)");
    expect(inheritedText("second_reader", values(), ENV, CATALOGUE)).toBe("Off");
    expect(inheritedText("second_reader", values({ second_reader: "off" }), ENV, CATALOGUE)).toBe("Off");
    expect(inheritedText("second_reader", values({ second_reader: "openrouter", second_model: "google/gone" }), ENV, CATALOGUE)).toBe(
      "google/gone, normal read",
    );
  });
});

describe("second reader: the draft and what it saves", () => {
  it("picks a way and a model together, off, or what it follows", () => {
    const draft = draftOf(MISTRAL);
    expect(pickedSecond(draft, `openrouter:${FLASH.id}`)).toMatchObject({ second_reader: "openrouter", second_model: FLASH.id });
    expect(pickedSecond(draft, `ultra_low_cost:${FLASH.id}`)).toMatchObject({ second_reader: "ultra_low_cost", second_model: FLASH.id });
    expect(pickedSecond(draft, "off")).toMatchObject({ second_reader: "off", second_model: null });
    expect(pickedSecond(pickedSecond(draft, "off"), "")).toMatchObject({ second_reader: null, second_model: null });
    // A model id with a colon in it keeps it.
    expect(pickedSecond(draft, "openrouter:google/gemini-x:free")).toMatchObject({ second_model: "google/gemini-x:free" });
  });

  it("round-trips through the draft and is saved as the pair", () => {
    const own = values({ scan_reader: "mistral", second_reader: "ultra_low_cost", second_model: FLASH.id });
    expect(aiBody(draftOf(own))).toEqual(own);
    expect(aiBody(draftOf(values()))).toMatchObject({ second_reader: null, second_model: null });
  });

  it("counts in Scanning's own settings while Mistral reads, and resets with it", () => {
    const own = values({ scan_reader: "mistral", second_reader: "openrouter", second_model: FLASH.id });
    expect(follows(values({ second_reader: "off" }), "mistral").scan).toBe("some");
    expect(follows(values({ second_reader: "off" }), "openrouter").scan).toBe("all"); // unused while Gemini reads
    expect(resetSection(draftOf(own), "scan")).toMatchObject({ second_reader: null, second_model: null });
  });

  it("is said in the save bar while Mistral reads, and in the defaults' impact with its count", () => {
    const saved = draftOf(MISTRAL);
    const picked = pickedSecond(saved, `openrouter:${FLASH.id}`);
    const reply = { defaults: values(), env: ENV };
    expect(changesText(saved, picked, CATALOGUE, reply)).toEqual(["Second reader: Gemini 3.5 Flash, normal read"]);
    const gemini = draftOf(values());
    expect(changesText(gemini, pickedSecond(gemini, `openrouter:${FLASH.id}`), CATALOGUE, reply)).toEqual([]);
    expect(changesText(saved, pickedSecond(saved, "off"), CATALOGUE, reply)).toEqual(["Second reader: Off"]);
    expect(defaultsImpact(values(), aiBody(pickedSecond(draftOf(values()), `openrouter:${FLASH.id}`)), COUNTS)).toEqual({
      lead: "This save changes, for 6 organisations still on platform defaults:",
      areas: ["Second reader"],
    });
  });

  it("shows the server's refusal of an incomplete pair next to it", () => {
    expect(refusalField("second_reader_incomplete")).toBe("second_reader");
    const html = fields({ own: MISTRAL, errors: { second_reader: "Choose the second reader's model, or turn the second reader off." } });
    expect(html).toContain(`<span id="${ID}-error" class="error-text" role="alert">`);
  });
});

describe("second reader: the platform defaults' summary", () => {
  it("says the platform's second reader and how many organisations follow it", () => {
    const reply: AiDefaults = { defaults: values({ second_reader: "openrouter", second_model: FLASH.id }), env: ENV, orgs_inheriting: COUNTS };
    const html = renderToStaticMarkup(createElement(DefaultsSummary, { reply, catalogue: CATALOGUE }));
    expect(html).toMatch(/Second reader[\s\S]*?Gemini 3.5 Flash, normal read[\s\S]*?6/);
    const off = renderToStaticMarkup(createElement(DefaultsSummary, { reply: { ...reply, defaults: values() }, catalogue: CATALOGUE }));
    expect(off).toMatch(/Second reader[\s\S]*?Off/);
  });
});
