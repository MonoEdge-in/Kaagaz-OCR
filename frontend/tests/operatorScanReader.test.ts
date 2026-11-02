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
  aiBody,
  changesText,
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
    expect(follows(values({ scan_reader: "openrouter" })).scan).toBe("some");
    expect(follows(values({ mistral_model: "mistral-ocr-4-1" })).scan).toBe("some");
    // Reading with Gemini itself, it needs no Mistral model to have its own settings.
    const gemini = values({ scan_models: ["m/one"], scan_thinking: "high", scan_resolution: "low", careful_thinking: "high" });
    const allOwn = { ...gemini, careful_resolution: "high", page_max_output_tokens: 8192, dynamic: false } as const;
    expect(follows(values({ ...allOwn, scan_reader: "openrouter" })).scan).toBe("none");
    expect(follows(values({ ...allOwn, scan_reader: "mistral" })).scan).toBe("some");
  });

  it("picking gemini nulls mistral_model", () => {
    const draft = draftOf(values({ scan_reader: "mistral", mistral_model: "mistral-ocr-2512" }));
    expect(pickedReader(draft, "openrouter")).toMatchObject({ scan_reader: "openrouter", mistral_model: null });
    expect(pickedReader(draft, "")).toMatchObject({ scan_reader: null, mistral_model: "mistral-ocr-2512" });
    expect(pickedReader(draftOf(values()), "mistral")).toMatchObject({ scan_reader: "mistral", mistral_model: null });
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
    expect(changesText(draftOf(values()), after, [])).toEqual(["Who reads the pages: Mistral OCR", "Mistral model: Mistral OCR 3"]);
    expect(changesText(after, draftOf(values()), [])).toEqual([
      "Who reads the pages: platform default",
      "Mistral model: platform default",
    ]);
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
    expect(summary).toMatch(/<dt[^>]*>Who reads the pages<\/dt><dd>the server&#x27;s reader \(Gemini\)<span[^>]*>Followed by 3 organisations/);
  });
});

describe("who reads the pages: the field", () => {
  it("is the first field of Scanning, with both readers and what it follows", () => {
    const org = fields();
    expect(org.indexOf('id="ai-scan-reader"')).toBeGreaterThan(0);
    expect(org.indexOf('id="ai-scan-reader"')).toBeLessThan(org.search(/<h3[^>]*>First read<\/h3>/));
    expect(org).toContain('<label for="ai-scan-reader" class="label">Who reads the pages</label>');
    expect(optionsOf(org, "ai-scan-reader")).toEqual([
      "Platform default (the server&#x27;s reader (Gemini))",
      "Gemini, through OpenRouter",
      "Mistral OCR",
    ]);
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
    expect(html).toContain('<span id="ai-scan-reader-hint" class="hint">Mistral isn&#x27;t set up on this server: its key is missing.</span>');
  });

  it("hint says key or zdr", () => {
    const zdr = fields({ env: env({ mistral_ready: false, mistral_missing: "zdr" }) });
    expect(zdr).toContain(
      '<span id="ai-scan-reader-hint" class="hint">Mistral isn&#x27;t set up on this server: zero data retention isn&#x27;t confirmed.</span>',
    );
    const ready = fields();
    expect(ready).toContain('<option value="mistral">Mistral OCR</option>');
    expect(ready).not.toContain("ai-scan-reader-hint");
  });

  it("saved mistral not set up shows waiting line", () => {
    const html = fields({ own: MISTRAL, env: env({ mistral_ready: false, mistral_missing: "zdr" }) });
    expect(html).toContain(
      '<span id="ai-scan-reader-waiting" class="error-text">Saved as Mistral, but Mistral isn&#x27;t set up on this server, so this organisation&#x27;s pages wait.</span>',
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
    expect(optionsOf(html, "ai-mistral-model")).toEqual([
      "Platform default (the server&#x27;s model (Mistral OCR 4.1))",
      "Mistral OCR 4.1, about ₹0.35 a page",
      "Mistral OCR 3, about ₹0.18 a page",
    ]);
    expect(optionsOf(fields({ platform: true, own: MISTRAL }), "ai-mistral-model")[0]).toBe("The server&#x27;s model (Mistral OCR 4.1)");
  });

  it("no-blocks hint linked", () => {
    const html = fields({ own: values({ scan_reader: "mistral", mistral_model: "mistral-ocr-2512" }) });
    expect(describedByOf(html, "ai-mistral-model")).toBe("ai-mistral-model-origin ai-mistral-model-hint");
    expect(html).toContain(
      '<span id="ai-mistral-model-hint" class="hint">This model doesn&#x27;t say where each line is on the page, so answers can&#x27;t point to the spot and every amount waits for a person to check it.</span>',
    );
    expect(fields({ own: MISTRAL })).not.toContain("ai-mistral-model-hint"); // 4.1 says where
  });

  it("gemini fields carry the not-used note", () => {
    const note = "Not used while Mistral reads this organisation&#x27;s pages. Kept for when you switch back to Gemini.";
    const html = fields({ own: MISTRAL });
    expect(html.indexOf(note)).toBeGreaterThan(html.indexOf('id="ai-mistral-model"'));
    expect(html.indexOf(note)).toBeLessThan(html.search(/<h3[^>]*>First read<\/h3>/));
    expect(html).toContain('id="ai-scan-thinking"'); // kept where it is
    expect(fields()).not.toContain(note);
  });

  it("ensemble hint", () => {
    const html = fields({ own: MISTRAL });
    expect(html).toContain('<span id="ai-ensemble-mistral" class="hint">Ensemble isn&#x27;t used while Mistral reads the pages.</span>');
    expect(html).toContain('aria-describedby="ai-ensemble-hint ai-ensemble-mistral"');
    expect(fields()).not.toContain("ai-ensemble-mistral");
  });
});

describe("Mistral stopped and Mistral's fixed price", () => {
  it("banner shows stopped words and count", () => {
    const since = "2026-11-02T09:00:00Z";
    const key = renderToStaticMarkup(
      createElement(MistralStopped, { status: { stopped: true, code: "mistral_key", since, documents_waiting: 3 } }),
    );
    expect(key).toContain('role="status"');
    expect(key).toContain(
      `Mistral stopped reading pages at ${formatDateTime(since)} (out of credit or key refused). 3 documents are waiting. They&#x27;re read automatically once Mistral works again.`,
    );
    expect(key).toContain("In Mistral&#x27;s console, check the Mistral key and credit.");
    const down = renderToStaticMarkup(
      createElement(MistralStopped, { status: { stopped: true, code: "mistral", since, documents_waiting: 1 } }),
    );
    expect(down).toContain("(not answering). 1 document is waiting.");
    expect(down).not.toContain("check the Mistral key");
    expect(renderToStaticMarkup(createElement(MistralStopped, { status: NOT_STOPPED }))).toBe("");
    expect(read("components/operator/OrgAiSettings.tsx")).toContain("<MistralStopped status={loaded.reply.env.mistral_status} />");
    expect(read("components/operator/PlatformDefaults.tsx")).toContain("<MistralStopped status={reply.env.mistral_status} />");
  });

  it("fixed price panel has no tokens lines", () => {
    expect(costWords(fixed())).toEqual({ figure: "About ₹0.35 a page", dollars: "$0.0040 a page", lines: ["Mistral's fixed price."] });
    const seen = fixed({ first_read: { ...fixed().first_read, usd_per_page: 0.004, pages: 12 } });
    expect(costWords(seen).lines).toEqual(["Mistral's fixed price.", "First read: $0.0040 a page, measured on 12 pages in the last 30 days."]);
    expect(tokensWords(fixed())).toEqual(["Mistral charges by the page, not by tokens."]);
    expect(levelNote(fixed(), "minimal")).toBeNull();
    expect(read("components/operator/OrgAiSettings.tsx")).toContain("{!fixedPrice && <h3 className={styles.subHeading}>Tokens a page</h3>}");
  });
});
