// Who reads the pages (#473, Tushar, 2 Nov 2026): Gemini through OpenRouter or Mistral OCR,
// per organisation and as the platform default, and which Mistral model; Mistral offered
// only once it is set up on the server; the Gemini fields kept but marked unused while
// Mistral reads; the banner while Mistral is stopped; Mistral's fixed price in the Cost per
// page panel; and the save, reset, refusal and defaults' counts with both fields.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  CHECK_MISTRAL_NOT_SET_UP,
  aiBody,
  changesText,
  checkEffect,
  checkRefusal,
  costChange,
  costWords,
  defaultsImpact,
  draftOf,
  follows,
  levelNote,
  pickedReader,
  readerOf,
  resetSection,
  sameSettings,
  tokensWords,
} from "@/components/operator/models";
import { AiFields, MistralStopped, fieldId, refusalOf } from "@/components/operator/OrgAiSettings";
import { DefaultsSummary } from "@/components/operator/PlatformDefaults";
import { ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { AiErrors } from "@/components/operator/models";
import type { AiDefaults, AiEnv, AiValues, MistralStatus, ScanCost } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

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
  daily_page_limit: null, // Changed on purpose (#478): the daily page limit
  ulc_ceiling_paise: null, // Changed on purpose (#488): the most a page may cost under ultra low cost
  ...overrides,
});
const NOT_STOPPED: MistralStatus = { stopped: false, code: null, since: null, documents_waiting: 0 };
const env = (overrides: Partial<AiEnv> = {}): AiEnv => ({
  scan_model: "m/env-scan",
  chat_model: "m/env-chat",
  scan_thinking: "low",
  scan_resolution: "high",
  careful_thinking: "high",
  careful_resolution: "low",
  page_max_output_tokens: 65536,
  scan_reader: "openrouter",
  ulc_ceiling_paise: 40,
  mistral_model: "mistral-ocr-4-1",
  mistral_ready: true,
  mistral_missing: null,
  mistral_models: [
    { id: "mistral-ocr-4-1", name: "Mistral OCR 4.1", usd_per_page: 0.004, has_blocks: true },
    { id: "mistral-ocr-2512", name: "Mistral OCR 3", usd_per_page: 0.002, has_blocks: false },
  ],
  mistral_status: NOT_STOPPED,
  ...overrides,
});
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
  daily_page_limit: 1, // Changed on purpose (#478)
  ulc_ceiling_paise: 1, // Changed on purpose (#488)
};

const fields = (
  props: { platform?: boolean; own?: AiValues; defaults?: AiValues; env?: AiEnv; errors?: AiErrors } = {},
) => {
  const own = props.own ?? values();
  return renderToStaticMarkup(
    createElement(AiFields, {
      platform: props.platform ?? false,
      customerId: props.platform ? null : "org-1",
      catalogue: [],
      saved: own,
      defaults: props.defaults ?? values(),
      env: props.env ?? env(),
      maxAgents: 3,
      draft: draftOf(own),
      errors: props.errors ?? {},
      busy: false,
      onChange: () => undefined,
    }),
  );
};
const MISTRAL = values({ scan_reader: "mistral" });
const optionsOf = (html: string, id: string) => {
  const select = html.match(new RegExp(`<select id="${id}"[\\s\\S]*?</select>`))?.[0] ?? "";
  return [...select.matchAll(/<option[^>]*>([^<]*)<\/option>/g)].map((m) => m[1]);
};
const describedByOf = (html: string, id: string) => html.match(new RegExp(`<select id="${id}"[^>]*aria-describedby="([^"]*)"`))?.[1] ?? "";

const fixed = (overrides: Partial<ScanCost> = {}): ScanCost => ({
  days: 30,
  first_read: { model: "mistral/mistral-ocr-4-1", thinking: "none", resolution: "page", usd_per_page: null, pages: 0 },
  re_read: null,
  usd_per_page: null,
  quality: { clean_share: null, pages: 0, max_clean_share: null, max_pages: 0 },
  tokens: { input_per_page: null, output_per_page: null, output_p99: null, limit: 65536, limit_hits: 0, pages: 0 },
  accuracy: null,
  fixed_price: true,
  fixed_usd_per_page: 0.004,
  ...overrides,
});

describe("who reads the pages: the save", () => {
  it("round trip of both fields", () => {
    const own = values({ scan_reader: "mistral", mistral_model: "mistral-ocr-2512" });
    expect(aiBody(draftOf(own))).toEqual(own);
    expect(aiBody(draftOf(values()))).toEqual(values());
    expect(follows(values({ scan_reader: "openrouter" }), "openrouter").scan).toBe("some");
    expect(follows(values({ mistral_model: "mistral-ocr-4-1" }), "openrouter").scan).toBe("some");
    // Reading with Gemini itself, it needs no Mistral model to have its own settings.
    const gemini = values({ scan_models: ["m/one"], scan_thinking: "high", scan_resolution: "low", careful_thinking: "high" });
    const allOwn = { ...gemini, careful_resolution: "high", page_max_output_tokens: 8192, dynamic: false } as const;
    expect(follows(values({ ...allOwn, scan_reader: "openrouter" }), "openrouter").scan).toBe("none");
    expect(follows(values({ ...allOwn, scan_reader: "mistral" }), "openrouter").scan).toBe("some");
  });

  it("picking gemini nulls mistral_model", () => {
    const draft = draftOf(values({ scan_reader: "mistral", mistral_model: "mistral-ocr-2512", scan_thinking: "high" }));
    expect(pickedReader(draft, "openrouter", values(), env(), false)).toMatchObject({ scan_reader: "openrouter", mistral_model: null });
    // Changed on purpose (#473): following a platform that reads with Gemini lets go of the model too.
    expect(pickedReader(draft, "", values(), env(), false)).toMatchObject({ scan_reader: null, mistral_model: null });
    expect(pickedReader(draft, "", MISTRAL, env(), false)).toMatchObject({ scan_reader: null, mistral_model: "mistral-ocr-2512" });
    expect(pickedReader(draft, "", MISTRAL, env(), true)).toMatchObject({ scan_reader: null, mistral_model: null }); // the server reads with Gemini
    expect(pickedReader(draftOf(values()), "mistral", values(), env(), false)).toMatchObject({ scan_reader: "mistral", mistral_model: null });
    expect(pickedReader(draft, "openrouter", values(), env(), false).scan_thinking).toBe("high"); // Gemini's settings untouched
    const source = read("components/operator/OrgAiSettings.tsx");
    expect(source).toContain("onPick={(value) => onChange(pickedReader(draft, value, defaults, env, platform))}");
  });

  it("reset clears both", () => {
    const draft = draftOf(values({ scan_reader: "mistral", mistral_model: "mistral-ocr-2512" }));
    expect(aiBody(resetSection(draft, "scan"))).toEqual(values());
  });

  it("refusal focuses scan_reader", () => {
    const refusal = refusalOf(new ApiError(409, "mistral_not_set_up", "Mistral isn't set up on this server yet."));
    expect(refusal).toEqual({ field: "scan_reader", message: "Mistral isn't set up on this server yet." });
    expect(fieldId("scan_reader")).toBe("ai-scan-reader");
    const html = fields({ own: MISTRAL, errors: { scan_reader: refusal.message } });
    expect(describedByOf(html, "ai-scan-reader").split(" ")).toContain("ai-scan-reader-error");
    expect(html).toContain('<span id="ai-scan-reader-error" class="error-text" role="alert">Mistral isn&#x27;t set up on this server yet.</span>');
  });

  it("sameSettings differs on reader alone", () => {
    expect(sameSettings(values(), values({ scan_reader: "mistral" }))).toBe(false);
    expect(sameSettings(values(), values({ mistral_model: "mistral-ocr-2512" }))).toBe(false);
    expect(sameSettings(MISTRAL, values({ scan_reader: "mistral" }))).toBe(true);
  });

  it("lists both in the save bar's changes", () => {
    const after = draftOf(values({ scan_reader: "mistral", mistral_model: "mistral-ocr-2512" }));
    const gemini = { defaults: values(), env: env() };
    expect(changesText(draftOf(values()), after, [], gemini)).toEqual(["Who reads the pages: Mistral OCR", "Mistral model: Mistral OCR 3"]);
    // Changed on purpose (#473): back on Gemini, the Mistral model is no change worth saying.
    expect(changesText(after, draftOf(values()), [], gemini)).toEqual(["Who reads the pages: platform default"]);
    expect(changesText(after, draftOf(values()), [], { defaults: MISTRAL, env: env() })).toEqual([
      "Who reads the pages: platform default",
      "Mistral model: platform default",
    ]);
    expect(read("components/operator/OrgAiSettings.tsx")).toContain("changesText(saved, draft, loaded?.catalogue ?? [], loaded?.reply)");
  });

  it("defaults impact counts reader followers", () => {
    expect(defaultsImpact(values(), MISTRAL, COUNTS)).toEqual({
      lead: "This save changes, for 3 organisations still on platform defaults:",
      areas: ["Who reads the pages"],
    });
    expect(defaultsImpact(values(), values({ scan_reader: "mistral", mistral_model: "mistral-ocr-2512" }), COUNTS).areas).toEqual([
      "Who reads the pages: 3 organisations",
      "Mistral model: 2 organisations",
    ]);
    const summary = renderToStaticMarkup(
      createElement(DefaultsSummary, { reply: { defaults: values(), env: env(), orgs_inheriting: COUNTS }, catalogue: [] }),
    );
    // Changed on purpose (#473): plain "Gemini", and no Mistral model row while the platform reads with Gemini.
    expect(summary).toMatch(/<dt[^>]*>Who reads the pages<\/dt><dd>Gemini<span[^>]*>Followed by 3 organisations/);
    expect(summary).not.toContain(">Mistral model<");
  });

  it("defaults summary shows the Mistral model while the platform reads with Mistral", () => {
    const summary = (defaults: AiValues) =>
      renderToStaticMarkup(createElement(DefaultsSummary, { reply: { defaults, env: env(), orgs_inheriting: COUNTS }, catalogue: [] }));
    expect(summary(MISTRAL)).toMatch(/<dt[^>]*>Mistral model<\/dt><dd>Mistral OCR 4.1<span[^>]*>Followed by 2 organisations/);
    expect(summary(values({ scan_reader: "mistral", mistral_model: "mistral-ocr-2512" }))).toMatch(/<dd>Mistral OCR 3<span/);
  });
});

describe("who reads the pages: the field", () => {
  it("is the first field of Scanning, with both readers and what it follows", () => {
    const org = fields();
    expect(org.indexOf('id="ai-scan-reader"')).toBeGreaterThan(0);
    expect(org.indexOf('id="ai-scan-reader"')).toBeLessThan(org.search(/<h3[^>]*>First read<\/h3>/));
    expect(org).toContain('<label for="ai-scan-reader" class="label">Who reads the pages</label>');
    // Changed on purpose (#473): plain "Gemini", with no nested brackets.
    // Changed on purpose (#488): ultra low cost is the third reader.
    expect(optionsOf(org, "ai-scan-reader")).toEqual(["Platform default (Gemini)", "Gemini", "Mistral OCR", "Ultra low cost (Gemini)"]);
    expect(optionsOf(fields({ defaults: MISTRAL }), "ai-scan-reader")[0]).toBe("Platform default (Mistral OCR)");
    expect(optionsOf(fields({ platform: true }), "ai-scan-reader")[0]).toBe("The server&#x27;s reader (Gemini)");
    expect(org).toMatch(/<span id="ai-scan-reader-origin"[^>]*>Platform default<\/span>/);
    expect(fields({ own: MISTRAL })).toMatch(/<span id="ai-scan-reader-origin"[^>]*>Own setting<\/span>/);
    expect(fields({ own: MISTRAL })).toContain('aria-label="Use platform default: Who reads the pages"');
  });

  it("mistral option disabled with linked hint when not set up", () => {
    const html = fields({ env: env({ mistral_ready: false, mistral_missing: "key" }) });
    expect(html).toContain('<option value="mistral" disabled="">Mistral OCR (not set up on this server)</option>');
    expect(describedByOf(html, "ai-scan-reader").split(" ")).toContain("ai-scan-reader-hint");
    // Changed on purpose (#473): plain words for why it can't be chosen.
    expect(html).toContain('<span id="ai-scan-reader-hint" class="hint">Mistral can&#x27;t be chosen yet: the server has no Mistral key.</span>');
  });

  it("hint says key or zdr", () => {
    const zdr = fields({ env: env({ mistral_ready: false, mistral_missing: "zdr" }) });
    expect(zdr).toContain(
      '<span id="ai-scan-reader-hint" class="hint">Mistral can&#x27;t be chosen yet: we haven&#x27;t confirmed that Mistral keeps no copy of our pages.</span>',
    );
    const ready = fields();
    expect(ready).toContain('<option value="mistral">Mistral OCR</option>');
    expect(ready).not.toContain("ai-scan-reader-hint");
  });

  it("saved mistral not set up shows waiting line", () => {
    const html = fields({ own: MISTRAL, env: env({ mistral_ready: false, mistral_missing: "zdr" }) });
    expect(html).toContain(
      // Changed on purpose (#473): says what to do.
      '<span id="ai-scan-reader-waiting" class="error-text">Saved as Mistral, but Mistral isn&#x27;t set up on this server, so this organisation&#x27;s pages are waiting. Choose Gemini to read them now.</span>',
    );
    expect(describedByOf(html, "ai-scan-reader").split(" ")).toContain("ai-scan-reader-waiting");
    expect(fields({ own: MISTRAL })).not.toContain("ai-scan-reader-waiting"); // set up: nothing waits
  });

  it("follows the reader actually used", () => {
    const draft = draftOf(values());
    expect(readerOf(draft, values(), env(), false)).toBe("openrouter");
    expect(readerOf(draft, MISTRAL, env(), false)).toBe("mistral");
    expect(readerOf(draft, MISTRAL, env(), true)).toBe("openrouter"); // the platform follows the server
    expect(readerOf(draftOf(values({ scan_reader: "openrouter" })), MISTRAL, env(), false)).toBe("openrouter");
  });
});

describe("the Mistral model and the Gemini fields", () => {
  it("inherited mistral shows the model select", () => {
    expect(fields({ defaults: MISTRAL })).toContain('<select id="ai-mistral-model"');
    expect(fields({ own: MISTRAL })).toContain('<label for="ai-mistral-model" class="label">Mistral model</label>');
    expect(fields()).not.toContain('id="ai-mistral-model"');
  });

  it("model options show rupees", () => {
    const html = fields({ own: MISTRAL });
    // Changed on purpose (#473): what each model gives, and no nested brackets.
    expect(optionsOf(html, "ai-mistral-model")).toEqual([
      "Platform default (Mistral OCR 4.1)",
      "Mistral OCR 4.1: about ₹0.35 a page, shows where each line is",
      "Mistral OCR 3: about ₹0.18 a page, no page spots, every amount checked by a person",
    ]);
    expect(optionsOf(fields({ platform: true, own: MISTRAL }), "ai-mistral-model")[0]).toBe("The server&#x27;s model (Mistral OCR 4.1)");
    // The names are the server's.
    const renamed = env({ mistral_models: env().mistral_models.map((each) => ({ ...each, name: `${each.name} beta` })) });
    expect(optionsOf(fields({ own: MISTRAL, env: renamed }), "ai-mistral-model")).toEqual([
      "Platform default (Mistral OCR 4.1 beta)",
      "Mistral OCR 4.1 beta: about ₹0.35 a page, shows where each line is",
      "Mistral OCR 3 beta: about ₹0.18 a page, no page spots, every amount checked by a person",
    ]);
  });

  it("no-blocks hint linked", () => {
    const html = fields({ own: values({ scan_reader: "mistral", mistral_model: "mistral-ocr-2512" }) });
    expect(describedByOf(html, "ai-mistral-model")).toBe("ai-mistral-model-origin ai-mistral-model-hint");
    expect(html).toContain(
      '<span id="ai-mistral-model-hint" class="hint">This model doesn&#x27;t say where each line is on the page, so answers can&#x27;t point to the spot and every amount waits for a person to check it.</span>',
    );
    expect(fields({ own: MISTRAL })).not.toContain("ai-mistral-model-hint"); // 4.1 says where
  });

  it("gemini fields are hidden while Mistral reads", () => {
    // Changed on purpose (#473): hidden, not kept on show as unused; the draft keeps their values.
    const note = "Gemini&#x27;s reading settings are hidden while Mistral reads the pages. They come back if you switch to Gemini.";
    const geminiOnly = [
      "Models its scans may be read with",
      'id="ai-scan-thinking"',
      'id="ai-scan-resolution"',
      'id="ai-page-max-output-tokens"',
      'id="ai-dynamic"',
      'id="ai-careful-thinking"',
      'id="ai-careful-resolution"',
    ];
    for (const html of [fields({ own: MISTRAL }), fields({ defaults: MISTRAL }), fields({ platform: true, own: MISTRAL })]) {
      expect(html).toContain(`<p class="hint">${note}</p>`);
      expect(html.indexOf(note)).toBeGreaterThan(html.indexOf('id="ai-mistral-model"'));
      expect(html).not.toMatch(/<h3[^>]*>First read<\/h3>/);
      for (const field of geminiOnly) expect(html).not.toContain(field);
    }
    const gemini = fields();
    expect(gemini).not.toContain(note);
    for (const field of geminiOnly) expect(gemini).toContain(field);
  });

  it("ensemble hint", () => {
    const html = fields({ own: MISTRAL });
    expect(html).toContain('<span id="ai-ensemble-mistral" class="hint">Ensemble isn&#x27;t used while Mistral reads the pages.</span>');
    // Changed on purpose (#473): off while Mistral reads.
    expect(html).toContain('<input type="checkbox" disabled="" aria-describedby="ai-ensemble-hint ai-ensemble-mistral"/>');
    expect(fields()).not.toContain("ai-ensemble-mistral");
    expect(fields()).toContain('<input type="checkbox" aria-describedby="ai-ensemble-hint"/>');
  });
});

describe("Mistral stopped and Mistral's fixed price", () => {
  it("banner shows stopped words and count", () => {
    const since = "2026-11-02T09:00:00Z";
    const banner = (status: MistralStatus, overrides: Partial<AiEnv> = {}) =>
      renderToStaticMarkup(createElement(MistralStopped, { env: env({ mistral_status: status, ...overrides }) }));
    const key = banner({ stopped: true, code: "mistral_key", since, documents_waiting: 3 });
    expect(key).toContain('role="status"');
    // Changed on purpose (#473): "since", and the count is every organisation's.
    expect(key).toContain(
      `Mistral hasn&#x27;t read pages since ${formatDateTime(since)} (out of credit or key refused). 3 documents are waiting across all organisations. They&#x27;re read automatically once Mistral works again.`,
    );
    expect(key).toContain("In Mistral&#x27;s console, check the Mistral key and credit.");
    const down = banner({ stopped: true, code: "mistral", since, documents_waiting: 1 });
    expect(down).toContain("(not answering). 1 document is waiting across all organisations.");
    expect(down).not.toContain("check the Mistral key");
    expect(banner(NOT_STOPPED)).toBe("");
    // Changed on purpose (#473): the banner reads the server's setup too.
    expect(read("components/operator/OrgAiSettings.tsx")).toContain("<MistralStopped env={loaded.reply.env} />");
    expect(read("components/operator/PlatformDefaults.tsx")).toContain("<MistralStopped env={reply.env} />");
  });

  it("banner says Mistral isn't set up, with why", () => {
    const status: MistralStatus = { stopped: true, code: "mistral_setup", since: "2026-11-02T09:00:00Z", documents_waiting: 4 };
    const setup =
      "Mistral isn&#x27;t set up on this server, so 4 documents are waiting across all organisations. They&#x27;ll be read once it&#x27;s set up, or switch these organisations to Gemini.";
    const notReady = renderToStaticMarkup(
      createElement(MistralStopped, { env: env({ mistral_status: status, mistral_ready: false, mistral_missing: "key" }) }),
    );
    expect(notReady).toContain(`<p>${setup}</p><p>Mistral can&#x27;t be chosen yet: the server has no Mistral key.</p>`);
    expect(notReady).not.toContain("check the Mistral key");
    // The server not ready says so, whatever the newest wait's code.
    const zdr = renderToStaticMarkup(
      createElement(MistralStopped, {
        env: env({ mistral_status: { ...status, code: "mistral_key" }, mistral_ready: false, mistral_missing: "zdr" }),
      }),
    );
    expect(zdr).toContain(setup);
    expect(zdr).toContain("we haven&#x27;t confirmed that Mistral keeps no copy of our pages.");
    expect(renderToStaticMarkup(createElement(MistralStopped, { env: env({ mistral_status: status }) }))).toContain(setup);
  });

  it("fixed price panel has no tokens lines", () => {
    // Changed on purpose (#473): a list price, the same for every page.
    const list = "Mistral's list price, the same for every page.";
    expect(costWords(fixed())).toEqual({ figure: "About ₹0.35 a page", dollars: "$0.0040 a page", lines: [list] });
    const seen = fixed({ first_read: { ...fixed().first_read, usd_per_page: 0.004, pages: 12 } });
    expect(costWords(seen).lines).toEqual([list, "First read: $0.0040 a page, measured on 12 pages in the last 30 days."]);
    expect(tokensWords(fixed())).toEqual(["Mistral charges by the page, not by tokens."]);
    expect(levelNote(fixed(), "minimal")).toBeNull();
    expect(read("components/operator/OrgAiSettings.tsx")).toContain("{!fixedPrice && <h3 className={styles.subHeading}>Tokens a page</h3>}");
  });

  it("says when the change compares measured Gemini with Mistral's list price", () => {
    const gemini = fixed({
      first_read: { model: "m/one", thinking: "low", resolution: "high", usd_per_page: 0.002, pages: 10 },
      usd_per_page: 0.002,
      fixed_price: false,
      fixed_usd_per_page: null,
    });
    expect(costChange(gemini, fixed())).toEqual({ from: "₹0.18", to: "₹0.35", verdict: "100% dearer (Gemini measured, Mistral list price)" });
    expect(costChange(fixed(), gemini)?.verdict).toBe("50% cheaper (Gemini measured, Mistral list price)");
    expect(costChange(gemini, gemini)?.verdict).toBe("about the same");
    expect(costChange(fixed(), fixed({ fixed_usd_per_page: 0.002 }))?.verdict).toBe("50% cheaper");
  });
});

describe("the accuracy check while Mistral reads", () => {
  it("says Gemini reads the pages to compare, on a Mistral scope", () => {
    const gemini = "To compare, this check also has Gemini read these pages at its highest settings.";
    expect(checkEffect("Asha Stores", null, 20, true)).toBe(
      "Kaagaz reads 20 recent pages of Asha Stores twice, at these settings and at max, and compares the words. " +
        `${gemini} It has a small cost and takes a few minutes. No text is shown or kept.`,
    );
    expect(checkEffect("Asha Stores", null, 20, false)).not.toContain(gemini);
    expect(read("components/operator/OrgAiSettings.tsx")).toContain("effect: checkEffect(org.name, latest, checkPages, checkMistral),");
  });

  it("says why a check can't start while Mistral isn't set up", () => {
    expect(checkRefusal(new ApiError(409, "mistral_not_set_up", "Mistral isn't set up on this server yet."))).toBe(CHECK_MISTRAL_NOT_SET_UP);
    expect(CHECK_MISTRAL_NOT_SET_UP).toBe(
      "Mistral isn't set up on this server, so this check can't read with Mistral. Choose Gemini as the reader to check now.",
    );
  });
});

// #528 (test server, 6 Nov 2026): the first change to Scanning (Mistral OCR, Ultra low cost or any
// other) added "Reset section" as a new box of the section's <fieldset> while the cost-per-page
// size container inside it changed too, and Chrome then laid the container out 0 px tall: only
// the header and Reset section showed. Reproduced on the production build in Chrome 156; never
// in a <div>, never without the container, never when the fieldset's own children stay the same.
// So the fieldset's children are the same whether or not the section has changed: its legend and
// one box that holds Reset section and the fields.
describe("#528 the Scanning section keeps its fields after a change", () => {
  // The direct children of the fieldset whose legend starts with `title`, as "tag.class".
  const sectionChildren = (html: string, title: string) => {
    const start = html.search(new RegExp(`<fieldset[^>]*><legend>${title}`));
    const tags = [...html.slice(start).matchAll(/<(\/?)([a-z0-9]+)([^>]*?)(\/?)>/g)];
    const children: string[] = [];
    let depth = 0;
    for (const [, close, tag = "", attrs = "", selfClose] of tags) {
      if (close) depth -= 1;
      else {
        if (depth === 1) children.push(`${tag}.${attrs.match(/class="([^"]*)"/)?.[1] ?? ""}`);
        if (!selfClose && !["input", "br", "img", "hr"].includes(tag)) depth += 1;
      }
      if (depth === 0) break;
    }
    return children;
  };
  const render = (draft: Partial<AiValues>, props: { platform?: boolean; env?: AiEnv } = {}) =>
    renderToStaticMarkup(
      createElement(AiFields, {
        platform: props.platform ?? false,
        customerId: props.platform ? null : "org-1",
        catalogue: [],
        saved: values(),
        defaults: values(),
        env: props.env ?? env(),
        maxAgents: 3,
        draft: draftOf(values(draft)),
        errors: {},
        busy: false,
        onChange: () => undefined,
      }),
    );
  // The Scanning fieldset's markup, from its legend to the Answering fieldset.
  const scanning = (html: string) => html.slice(html.search(/<legend>Scanning/), html.search(/<fieldset[^>]*><legend>Answering/));

  it("an organisation choosing Mistral OCR: the reader and the Mistral model, in the same box as before", () => {
    const before = render({});
    const after = render({ scan_reader: "mistral" });
    expect(scanning(after)).toContain("Reset section");
    expect(sectionChildren(after, "Scanning")).toEqual(sectionChildren(before, "Scanning"));
    expect(sectionChildren(after, "Scanning")).toHaveLength(2); // the legend and one box
    expect(scanning(after)).toContain('<select id="ai-scan-reader"');
    expect(scanning(after)).toContain('<select id="ai-mistral-model"');
  });

  it("an organisation choosing Ultra low cost: the ceiling and the model, in the same box as before", () => {
    const before = render({});
    const after = render({ scan_reader: "ultra_low_cost" });
    expect(scanning(after)).toContain("Reset section");
    expect(sectionChildren(after, "Scanning")).toEqual(sectionChildren(before, "Scanning"));
    expect(scanning(after)).toContain('<input id="ai-ulc-ceiling-paise"');
    expect(scanning(after)).toContain('<select id="ai-ulc-model"');
  });

  it("the platform defaults: Mistral's model list and Ultra low cost's ceiling and model show", () => {
    const before = render({}, { platform: true });
    const mistral = render({ scan_reader: "mistral" }, { platform: true });
    const ulc = render({ scan_reader: "ultra_low_cost" }, { platform: true });
    for (const html of [mistral, ulc]) expect(sectionChildren(html, "Scanning")).toEqual(sectionChildren(before, "Scanning"));
    expect(scanning(mistral)).toContain('<select id="ai-mistral-model"');
    expect(scanning(ulc)).toContain('<input id="ai-ulc-ceiling-paise"');
    expect(scanning(ulc)).toContain('<select id="ai-ulc-model"');
  });

  it("Mistral not set up: the reader with Mistral off and why, never an empty section", () => {
    const html = scanning(render({ scan_reader: "openrouter" }, { env: env({ mistral_ready: false, mistral_missing: "key" }) }));
    expect(html).toContain('<option value="mistral" disabled="">Mistral OCR (not set up on this server)</option>');
    expect(html).toContain("Mistral can&#x27;t be chosen yet: the server has no Mistral key.");
  });
});
