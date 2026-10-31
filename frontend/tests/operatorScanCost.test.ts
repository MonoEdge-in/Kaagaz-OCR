// The scan cost settings, the cost per page and the accuracy (#463, Tushar, 31 Oct 2026):
// Minimal for the scan levels only; picture detail and the pages read again settings on
// the platform defaults and an organisation's AI settings (null follows, as scan_thinking),
// grouped First read then Pages read again, each saying whether it is its own or followed,
// with a way back; the save bar and the defaults' counts with them; the Cost per page panel
// (the measured cost, the change from what is saved, the pages read cleanly, an
// organisation's accuracy check against max settings), asked once the draft rests, the last
// reply winning; and the Save confirmation with the cost and the accuracy.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  APPLIES_NOTE,
  CHECK_LABEL,
  CHECK_ON_ORG,
  CHECK_PAGES_HINT,
  CHECK_PAGE_CHOICES,
  CHECK_RUNNING,
  CLEAN_HINT,
  COST_DELAY_MS,
  COST_FAILED,
  COST_NOT_MEASURED,
  COST_WORKING,
  INR_PER_USD,
  LEVELS,
  NO_PAGES,
  OTHER_SETTINGS,
  TOO_MANY_CHECKS,
  accuracyWords,
  aiBody,
  askScanCost,
  changesText,
  checkEffect,
  checkRefusal,
  checkRunning,
  cleanText,
  costAsk,
  costChange,
  costWords,
  defaultsImpact,
  draftOf,
  follows,
  inheritedText,
  resetSection,
  sameSettings,
  scanSettings,
  withCost,
} from "@/components/operator/models";
import { STEP_UP_FAILED } from "@/components/operator/logic";
import { AiFields } from "@/components/operator/OrgAiSettings";
import { DefaultsSummary } from "@/components/operator/PlatformDefaults";
import { ApiError, getLatestAccuracyCheck, postAccuracyCheck, postScanCost, putAiDefaults, putOrgAi } from "@/lib/api";
import { createPoller } from "@/lib/poll";
import type { AccuracyCheck, AiDefaults, AiValues, OperatorModel, ScanCost } from "@/lib/types";

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
  chat_levels: null,
  chat_default_level: null,
  dynamic: null,
  ...overrides,
});
const ENV = {
  scan_model: "m/env-scan",
  chat_model: "m/env-chat",
  scan_thinking: "low",
  scan_resolution: "high",
  careful_thinking: "high",
  careful_resolution: "low",
};
const DEFAULTS = values({ chat_levels: ["low"], chat_default_level: "low", scan_resolution: "low", careful_thinking: "minimal" });
const COUNTS: AiDefaults["orgs_inheriting"] = {
  scan_models: 4,
  chat_models: 7,
  scan_thinking: 1,
  scan_resolution: 2,
  careful_thinking: 3,
  careful_resolution: 6,
  chat_levels: 7,
  dynamic: 5,
};
const catalogue: OperatorModel[] = [];

const check = (overrides: Partial<AccuracyCheck> = {}): AccuracyCheck => ({
  id: "c-1",
  status: "done",
  settings: { model: "m/one", scan_thinking: "low", scan_resolution: "high", careful_thinking: "high", careful_resolution: "high" },
  pages_checked: 20,
  pages_failed: 0,
  word_match: 0.981,
  numbers_match: 0.99,
  max_pages: 20,
  cost_usd: 0.3,
  started_at: "2026-10-31T08:55:00Z",
  finished_at: "2026-10-31T09:00:00Z",
  ...overrides,
});

const measured = (overrides: Partial<ScanCost> = {}): ScanCost => ({
  days: 30,
  first_read: { model: "m/one", thinking: "low", resolution: "high", usd_per_page: 0.0021, pages: 1240 },
  re_read: { model: "m/two", thinking: "high", resolution: "high", usd_per_page: 0.015, pages: 52, share: 0.042 },
  usd_per_page: 0.00273,
  quality: { clean_share: 0.94, pages: 1240, max_clean_share: 0.97, max_pages: 60 },
  accuracy: null,
  ...overrides,
});
const unmeasured = () => measured({ first_read: { ...measured().first_read, usd_per_page: null, pages: 0 }, usd_per_page: null });

const fields = (platform: boolean, own: AiValues = values(), latest: AccuracyCheck | null | undefined = undefined) =>
  renderToStaticMarkup(
    createElement(AiFields, {
      platform,
      customerId: platform ? null : "org-1",
      check: latest === undefined ? undefined : { latest, onCheck: () => undefined },
      catalogue,
      saved: own,
      defaults: DEFAULTS,
      env: ENV,
      maxAgents: 3,
      draft: draftOf(own),
      errors: {},
      busy: false,
      onChange: () => undefined,
    }),
  );

/** The options of one select, as their words. */
const optionsOf = (html: string, id: string) => {
  const select = html.match(new RegExp(`<select id="${id}"[\\s\\S]*?</select>`))?.[0] ?? "";
  return [...select.matchAll(/<option[^>]*>([^<]*)<\/option>/g)].map((m) => m[1]);
};
/** A setting's mark: whether its value is its own or followed. */
const originOf = (html: string, id: string) => html.match(new RegExp(`<span id="${id}-origin"[^>]*>([^<]*)</span>`))?.[1];

describe("the scan settings' selects", () => {
  it("offers Minimal for the scan level and pages read again, never in the answering levels", () => {
    for (const platform of [true, false]) {
      const html = fields(platform);
      expect(optionsOf(html, "ai-scan-thinking").slice(1)).toEqual(["Minimal", "Low", "Medium", "High"]);
      expect(optionsOf(html, "ai-careful-thinking").slice(1)).toEqual(["Minimal", "Low", "Medium", "High"]);
      expect(optionsOf(html, "ai-scan-resolution").slice(1)).toEqual(["High", "Low"]);
      expect(optionsOf(html, "ai-careful-resolution").slice(1)).toEqual(["High", "Low"]);
      expect(html).not.toContain("<span>Minimal</span>"); // the answering levels' checkboxes
    }
    expect(LEVELS).toEqual(["low", "medium", "high"]);
  });

  it("labels every select, with a hint under each new one", () => {
    const html = fields(false);
    for (const [id, label] of [
      ["ai-scan-thinking", "Thinking level"],
      ["ai-scan-resolution", "Picture detail"],
      ["ai-dynamic", "Cost saving"],
      ["ai-careful-thinking", "Thinking for pages read again"],
      ["ai-careful-resolution", "Picture detail for pages read again"],
    ])
      expect(html).toContain(`<label for="${id}" class="label">${label}</label>`);
    expect(html).toContain(
      '<span id="ai-scan-resolution-hint" class="hint">Low reads fewer picture details: cheaper, but small handwriting gets harder.</span>',
    );
    for (const id of ["ai-scan-resolution", "ai-careful-thinking", "ai-careful-resolution", "ai-dynamic"])
      expect(html).toMatch(new RegExp(`id="${id}"[^>]*aria-describedby="${id}-origin ${id}-hint"`));
  });

  it("groups the fields as people think of them: First read, then Pages read again", () => {
    const html = fields(false);
    const order = [
      "<h3[^>]*>First read</h3>",
      "Models its scans may be read with",
      'id="ai-scan-thinking"',
      'id="ai-scan-resolution"',
      "<h3[^>]*>Pages read again</h3>",
      'id="ai-dynamic"',
      'id="ai-careful-thinking"',
      'id="ai-careful-resolution"',
      "<h3[^>]*>Cost per page</h3>",
    ].map((part) => html.search(new RegExp(part)));
    expect(order.every((at) => at >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("says what each follows in the existing thinking select's words", () => {
    const platform = fields(true);
    expect(optionsOf(platform, "ai-scan-thinking")[0]).toBe("The server&#x27;s level (low)");
    expect(optionsOf(platform, "ai-scan-resolution")[0]).toBe("The server&#x27;s level (high)");
    expect(optionsOf(platform, "ai-careful-thinking")[0]).toBe("The server&#x27;s level (high)");
    expect(optionsOf(platform, "ai-careful-resolution")[0]).toBe("The server&#x27;s level (low)");
    const org = fields(false);
    expect(optionsOf(org, "ai-scan-resolution")[0]).toBe("Platform default (Low)");
    expect(optionsOf(org, "ai-careful-thinking")[0]).toBe("Platform default (Minimal)");
    expect(optionsOf(org, "ai-careful-resolution")[0]).toBe("Platform default (the server&#x27;s level (low))");
    expect(inheritedText("scan_resolution", DEFAULTS, ENV, catalogue)).toBe("Low");
    expect(inheritedText("careful_thinking", DEFAULTS, ENV, catalogue)).toBe("Minimal");
    expect(inheritedText("careful_resolution", DEFAULTS, ENV, catalogue)).toBe("the server's level (low)");
  });

  it("marks each setting as its own or followed, with a way back only on its own", () => {
    const own = values({ scan_resolution: "low", dynamic: false });
    const org = fields(false, own);
    expect(originOf(org, "ai-scan-resolution")).toBe("Own setting");
    expect(originOf(org, "ai-dynamic")).toBe("Own setting");
    expect(originOf(org, "ai-careful-thinking")).toBe("Platform default");
    expect(org).toContain('aria-label="Use platform default: Picture detail">Use platform default</button>');
    expect(org).toContain('aria-label="Use platform default: Cost saving"');
    expect(org.match(/>Use platform default<\/button>/g)).toHaveLength(2);
    const platform = fields(true, values({ careful_thinking: "medium" }));
    expect(originOf(platform, "ai-careful-thinking")).toBe("Set here");
    expect(originOf(platform, "ai-scan-thinking")).toBe("The server&#x27;s setting");
    expect(platform).toContain('aria-label="Use the server&#x27;s setting: Thinking for pages read again"');
    expect(platform.match(/>Use the server&#x27;s setting<\/button>/g)).toHaveLength(1);
  });

  it("sends null for a setting set back to the platform default", () => {
    const source = read("components/operator/OrgAiSettings.tsx");
    expect(source).toContain("onPick: (value: string) => onChange({ ...draft, [field]: value || null })");
    expect(source).toMatch(/document\.getElementById\(id\)\?\.focus\(\);\s*onPick\(""\);/);
    expect(aiBody({ ...draftOf(values({ scan_resolution: "low" })), scan_resolution: null })).toEqual(values());
  });
});

describe("the scan settings' draft and save", () => {
  const own = values({ scan_thinking: "minimal", scan_resolution: "low", careful_thinking: "medium", careful_resolution: "high" });

  it("carries the new fields in the save, null following", () => {
    expect(aiBody(draftOf(own))).toEqual(own);
    expect(aiBody(draftOf(values()))).toEqual(values());
  });

  it("counts them in the Scanning area and resets them with it", () => {
    expect(follows(values({ scan_resolution: "low" })).scan).toBe("some");
    expect(follows(values({ careful_resolution: "high" })).scan).toBe("some");
    expect(aiBody(resetSection(draftOf(own), "scan"))).toEqual(values());
  });

  it("lists them in the save bar's changes", () => {
    const saved = draftOf(values());
    expect(changesText(saved, draftOf(own), catalogue)).toEqual([
      "Scanning level: Minimal",
      "Picture detail: Low",
      "Thinking for pages read again: Medium",
      "Picture detail for pages read again: High",
    ]);
    expect(changesText(draftOf(own), saved, catalogue)).toEqual([
      "Scanning level: platform default",
      "Picture detail: platform default",
      "Thinking for pages read again: platform default",
      "Picture detail for pages read again: platform default",
    ]);
  });

  it("says how many organisations each reaches, before a defaults save and in the summary", () => {
    expect(defaultsImpact(DEFAULTS, { ...DEFAULTS, scan_resolution: "high", careful_resolution: "high" }, COUNTS)).toEqual({
      lead: "This save changes, for the organisations still on platform defaults:",
      areas: ["Picture detail: 2 organisations", "Picture detail for pages read again: 6 organisations"],
    });
    const html = renderToStaticMarkup(createElement(DefaultsSummary, { reply: { defaults: DEFAULTS, env: ENV, orgs_inheriting: COUNTS }, catalogue }));
    expect(html).toMatch(/<dt[^>]*>Picture detail<\/dt><dd>Low<span[^>]*>Followed by 2 organisations/);
    expect(html).toMatch(/<dt[^>]*>Thinking for pages read again<\/dt><dd>Minimal<span[^>]*>Followed by 3 organisations/);
    expect(html).toMatch(/<dt[^>]*>Picture detail for pages read again<\/dt><dd>the server&#x27;s level \(low\)<span[^>]*>Followed by 6/);
  });

  describe("the API calls", () => {
    afterEach(() => vi.unstubAllGlobals());
    const capture = (reply: unknown, status = 200) => {
      const sent: { url: string; method: string; body: unknown }[] = [];
      vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
        sent.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
        return new Response(JSON.stringify(reply), { status, headers: { "Content-Type": "application/json" } });
      });
      return sent;
    };

    it("save the new fields and ask the cost in the agreed shapes", async () => {
      const sent = capture(measured());
      await putAiDefaults(own, "123456");
      await putOrgAi("org-1", { ...values(), ensemble: false, ai_budget_usd: null }, "654321");
      expect(await postScanCost(costAsk("org-1", draftOf(own)))).toEqual(measured());
      expect(sent[0]?.body).toMatchObject({ scan_resolution: "low", careful_thinking: "medium", careful_resolution: "high", code: "123456" });
      expect(sent[1]?.body).toMatchObject({ scan_thinking: null, scan_resolution: null, careful_thinking: null, careful_resolution: null });
      expect(sent[2]).toEqual({
        url: "/api/operator/scan-cost",
        method: "POST",
        body: { customer_id: "org-1", scan_thinking: "minimal", scan_resolution: "low", careful_thinking: "medium", careful_resolution: "high" },
      });
      expect(costAsk(null, values())).toEqual({
        customer_id: null,
        scan_thinking: null,
        scan_resolution: null,
        careful_thinking: null,
        careful_resolution: null,
      });
    });

    it("start a check with the draft's scan settings and a fresh code, and read the latest", async () => {
      const sent = capture({ id: "c-1" }, 202);
      expect(await postAccuracyCheck("org-1", { ...scanSettings(draftOf(own)), pages: 50 }, "123456")).toEqual({ id: "c-1" });
      expect(sent[0]).toEqual({
        url: "/api/operator/orgs/org-1/accuracy-check",
        method: "POST",
        body: { scan_thinking: "minimal", scan_resolution: "low", careful_thinking: "medium", careful_resolution: "high", pages: 50, code: "123456" },
      });
      capture(check());
      expect(await getLatestAccuracyCheck("org-1")).toEqual(check());
      capture({ error: { code: "not_found", message: "Not found." } }, 404);
      expect(await getLatestAccuracyCheck("org-1")).toBeNull(); // no check yet
    });
  });
});

describe("the Cost per page panel", () => {
  it("says a measured cost large in rupees, then the dollars and the detail", () => {
    expect(INR_PER_USD).toBe(88);
    expect(costWords(measured())).toEqual({
      figure: "About ₹0.24 a page",
      dollars: "$0.0027 a page",
      lines: [
        "First read: $0.0021 a page, measured on 1,240 pages in the last 30 days.",
        "Read again: 4% of pages, $0.0150 each.",
      ],
    });
  });

  it("is never an empty box: working out, not measured, or could not work it out", () => {
    expect(costWords(null)).toEqual({ figure: null, dollars: null, lines: [COST_WORKING] });
    expect(COST_NOT_MEASURED).toBe("Not measured yet at these settings. Scan a few pages with them to see the cost.");
    expect(costWords(unmeasured())).toEqual({ figure: null, dollars: null, lines: [COST_NOT_MEASURED] });
    expect(COST_FAILED).toBe("Couldn't work out the cost right now.");
    expect(costWords("failed")).toEqual({ figure: null, dollars: null, lines: [COST_FAILED] });
    const html = fields(false);
    expect(html).toContain(`<div id="ai-scan-cost" aria-live="polite"><p class="${html.match(/class="([^"]*costSmall[^"]*)"/)?.[1]}">${COST_WORKING}</p></div>`);
    expect(html).toContain(APPLIES_NOTE);
  });

  it("says on its line when the pages read again are not measured, and leaves them out", () => {
    const reRead = { ...measured().re_read!, usd_per_page: null, pages: 0 };
    expect(costWords(measured({ re_read: reRead, usd_per_page: 0.0021 }))).toEqual({
      figure: "About ₹0.18 a page",
      dollars: "$0.0021 a page",
      lines: [
        "First read: $0.0021 a page, measured on 1,240 pages in the last 30 days.",
        "Read again: 4% of pages, not measured yet at these settings, so the cost above leaves them out.",
      ],
    });
    const noShare = { ...measured().re_read!, share: null };
    expect(costWords(measured({ re_read: noShare, usd_per_page: 0.0021 })).lines[1]).toBe(
      "Read again: $0.0150 each, but how many pages need it is not known yet, so the cost above leaves them out.",
    );
    expect(costWords(measured({ re_read: { ...measured().re_read!, share: 0.001 } })).lines[1]).toBe(
      "Read again: under 1% of pages, $0.0150 each.",
    );
  });

  it("says no page is read again when Cost saving is off", () => {
    expect(costWords(measured({ re_read: null, usd_per_page: 0.0021 })).lines[1]).toBe("Cost saving is off, so no page is read again.");
  });

  it("says the change from what is saved, and which is cheaper, in words", () => {
    const at = (usd: number) => measured({ usd_per_page: usd });
    expect(costChange(at(0.31 / 88), at(0.18 / 88))).toEqual({ from: "₹0.31", to: "₹0.18", verdict: "42% cheaper" });
    expect(costChange(at(0.002), at(0.0027))).toEqual({ from: "₹0.18", to: "₹0.24", verdict: "35% dearer" });
    expect(costChange(at(0.002), at(0.002))?.verdict).toBe("about the same");
    for (const pair of [[at(0.002), unmeasured()], [unmeasured(), at(0.002)], [null, at(0.002)], [at(0.002), "failed"]] as const)
      expect(costChange(pair[0], pair[1])).toBeNull();
    const source = read("components/operator/OrgAiSettings.tsx");
    expect(source).toMatch(/Now \{change\.from\} <span aria-hidden="true">→<\/span>\s*<span className="visually-hidden">to<\/span> \{change\.to\} a page: <strong>\{change\.verdict\}<\/strong>/);
  });

  it("asks the saved and the draft settings apart, and the draft only while it differs", () => {
    const source = read("components/operator/OrgAiSettings.tsx");
    expect(source).toContain("askScanCost(costAsk(customerId, saved), postScanCost, setSavedCost), [customerId, saved, finished]");
    expect(source).toContain(
      "askScanCost(costAsk(customerId, { scan_thinking, scan_resolution, careful_thinking, careful_resolution }), postScanCost, setDraftCost)",
    );
    expect(source).toContain("[customerId, differs, finished, scan_thinking, scan_resolution, careful_thinking, careful_resolution]");
    expect(source).toContain("const shown = differs ? draftCost : savedCost;");
    expect(sameSettings(values(), draftOf(values({ scan_models: ["m/one"] })))).toBe(true); // models are not asked
    expect(sameSettings(values(), draftOf(values({ careful_resolution: "low" })))).toBe(false);
  });

  it("sits beside the fields on a wide dialog and under them on a phone", () => {
    const css = read("components/operator/Operator.module.css");
    expect(css).toMatch(/\.scanLayout \{\s*container: scan-area \/ inline-size;/);
    expect(css).toMatch(/@container scan-area \(min-width: 44rem\) \{\s*\.scanGrid \{\s*grid-template-columns: minmax\(0, 1fr\) 22rem;/);
    expect(css).toMatch(/\.costPanel \{[^}]*overflow-wrap: anywhere;/);
    for (const file of ["components/operator/OrgAiSettings.tsx", "components/operator/PlatformDefaults.tsx"])
      expect(read(file), file).toMatch(/<ActionDialog\s+wide/);
    expect(read("components/documents/ActionDialog.module.css")).toMatch(/\.wide \{\s*width: min\(100% - 2 \* var\(--space-4\), 56rem\);/);
  });
});

describe("the accuracy", () => {
  it("says the pages read cleanly at these settings and at max, or that there are not enough scans", () => {
    expect(cleanText(measured())).toBe("Pages read cleanly: 94% at these settings, 97% at max settings (last 30 days)");
    expect(cleanText(measured({ quality: { clean_share: 0.94, pages: 12, max_clean_share: null, max_pages: 0 } }))).toBe(
      "Pages read cleanly: 94% at these settings (last 30 days)",
    );
    expect(cleanText(measured({ quality: { clean_share: null, pages: 0, max_clean_share: 0.97, max_pages: 60 } }))).toBe(
      "Not enough scans at these settings yet.",
    );
    expect(CLEAN_HINT).toBe("Read cleanly means the AI wasn't unsure about any word.");
  });

  it("says a running check, the result at these settings, or that the last was at other settings", () => {
    expect(accuracyWords(null, check({ status: "running", finished_at: null, word_match: null }))).toEqual({ figure: null, line: "Checking 20 pages…" });
    expect(accuracyWords(check(), check())).toEqual({
      figure: "Amounts, dates and numbers: 99% match max settings",
      line: "All words: 98%. Checked on 20 pages, 31 Oct.",
    });
    expect(accuracyWords(null, check())).toEqual({ figure: null, line: OTHER_SETTINGS });
    expect(OTHER_SETTINGS).toBe("Your last check was at different settings.");
    expect(accuracyWords(check({ status: "failed", word_match: null }), check({ status: "failed" })).line).toBe(
      "The last check at these settings did not finish. Check again.",
    );
    expect(accuracyWords(null, null)).toEqual({ figure: null, line: null });
  });

  it("says numbers first, and when the checked pages had none", () => {
    const fifty = check({ pages_checked: 50, max_pages: 50 });
    const shown = accuracyWords(fifty, fifty);
    expect(`${shown.figure}. ${shown.line}`).toBe(
      "Amounts, dates and numbers: 99% match max settings. All words: 98%. Checked on 50 pages, 31 Oct.",
    );
    expect(accuracyWords(check({ numbers_match: null }), check())).toEqual({
      figure: "All words: 98% match max settings",
      line: "No numbers on the checked pages. Checked on 20 pages, 31 Oct.",
    });
  });

  it("offers the check on an organisation's page only, and not while one runs", () => {
    const org = fields(false, values(), null);
    expect(org).toContain(`<button id="ai-check-accuracy" type="button" class="btn">${CHECK_LABEL}</button>`);
    expect(org).toMatch(/<div id="ai-accuracy" aria-live="polite">/);
    expect(org).not.toContain("Check accuracy on an organisation&#x27;s page.");
    const running = fields(false, values(), check({ status: "running", finished_at: null }));
    expect(running).not.toContain('id="ai-check-accuracy"');
    expect(running).toContain("Checking 20 pages…");
    const platform = fields(true);
    expect(platform).not.toContain('id="ai-check-accuracy"');
    expect(platform).toContain("Check accuracy on an organisation&#x27;s page.");
    expect(CHECK_ON_ORG).toBe("Check accuracy on an organisation's page.");
  });

  it("says what a check does and roughly what it costs before it starts", () => {
    expect(checkEffect("Asha Stores", check({ cost_usd: 0.3, pages_checked: 20 }), 20)).toBe(
      "Kaagaz reads 20 recent pages of Asha Stores twice, at these settings and at max, and compares the words. " +
        "It costs about ₹26.40 and takes a few minutes. No text is shown or kept.",
    );
    expect(checkEffect("Asha Stores", null, 20)).toContain("It has a small cost and takes a few minutes.");
    expect(checkEffect("Asha Stores", check({ cost_usd: null }), 50)).toContain("It has a small cost");
    const dialog = read("components/operator/OrgAiSettings.tsx");
    expect(dialog).toContain("effect: checkEffect(org.name, latest, checkPages),");
    expect(dialog).toContain("stepUp: true,");
    expect(dialog).toContain("run: (stepUpCode) => postAccuracyCheck(org.id, { ...scanSettings(draft), pages: checkPages }, stepUpCode),");
    // The confirm's form sits outside the settings' form: its submit never reaches Save.
    expect(dialog.indexOf("<ConfirmDialog")).toBeGreaterThan(dialog.lastIndexOf("</form>"));
  });

  it("lets the operator check 20, 50 or 100 pages, the cost scaling with them", () => {
    expect(CHECK_PAGE_CHOICES).toEqual([20, 50, 100]);
    expect(CHECK_PAGES_HINT).toBe("More pages give a surer answer. 100 is best before a big decision.");
    const last = check({ cost_usd: 0.3, pages_checked: 20 }); // $0.015 a checked page
    expect(CHECK_PAGE_CHOICES.map((pages) => checkEffect("Asha Stores", last, pages).match(/about (₹[\d,.]+)/)?.[1])).toEqual([
      "₹26.40",
      "₹66.00",
      "₹132.00",
    ]);
    expect(checkEffect("Asha Stores", last, 100)).toContain("Kaagaz reads 100 recent pages of Asha Stores twice");
    const dialog = read("components/operator/OrgAiSettings.tsx");
    expect(dialog).toContain('const [checkPages, setCheckPages] = useState<CheckPages>(20);');
    expect(dialog).toMatch(/<legend className="label">Pages to check<\/legend>\s*\{CHECK_PAGE_CHOICES\.map/);
    expect(dialog).toMatch(/type="radio"\s*name="ai-check-pages"\s*checked=\{checkPages === pages\}\s*aria-describedby="ai-check-pages-hint"/);
    expect(read("components/operator/ConfirmDialog.tsx")).toContain("{confirm.fields}");
  });

  it("says why a check was refused: one running, too many in the hour, no pages", () => {
    expect(checkRefusal(new ApiError(409, "check_running", "x"))).toBe(CHECK_RUNNING);
    expect(CHECK_RUNNING).toBe("A check is already running for this organisation.");
    expect(checkRefusal(new ApiError(429, "rate_limited", "x"))).toBe(TOO_MANY_CHECKS);
    expect(TOO_MANY_CHECKS).toBe("Too many checks in the last hour. Try again later.");
    expect(checkRefusal(new ApiError(400, "no_pages", "x"))).toBe(NO_PAGES);
    expect(NO_PAGES).toBe("This organisation has no scanned pages to check yet.");
    expect(checkRefusal(new ApiError(403, "step_up_failed", "x"))).toBe(STEP_UP_FAILED);
    expect(read("components/operator/ConfirmDialog.tsx")).toContain("setFailure((confirm.errorText ?? operatorErrorMessage)(caught));");
  });

  describe("while a check runs", () => {
    afterEach(() => vi.useRealTimers());

    it("asks again until it has finished, then stops", async () => {
      vi.useFakeTimers();
      const replies = [check({ status: "running" }), check({ status: "running" }), check({ status: "done" })];
      const load = vi.fn(async () => replies.shift() ?? null);
      const shown: (AccuracyCheck | null)[] = [];
      const poller = createPoller(
        () => ({ load, onResult: (each) => shown.push(each), onFailure: () => undefined, again: checkRunning, signature: (each) => `${each?.status}` }),
        () => false,
      );
      poller.refresh();
      await vi.advanceTimersByTimeAsync(60_000);
      expect(load).toHaveBeenCalledTimes(3);
      expect(shown.map((each) => each?.status)).toEqual(["running", "running", "done"]);
      expect(vi.getTimerCount()).toBe(0);
      expect(checkRunning(null)).toBe(false);
    });

    it("uses the shared poller, which waits while the tab is hidden", () => {
      const dialog = read("components/operator/OrgAiSettings.tsx");
      expect(dialog).toContain("usePoll<AccuracyCheck | null>({");
      expect(dialog).toContain("again: checkRunning,");
      expect(read("lib/usePoll.ts")).toContain('document.visibilityState === "hidden"');
    });
  });
});

describe("the Save confirmation", () => {
  it("says the cost a page now, and for an organisation the accuracy against max settings", () => {
    expect(withCost("Saved the AI settings of Asha Stores.", measured({ usd_per_page: 0.0021, accuracy: check() }), true)).toBe(
      "Saved the AI settings of Asha Stores. Scans now cost about ₹0.18 a page, and 99% of amounts, dates and numbers match max settings (checked 31 Oct).",
    );
    expect(withCost("Saved.", measured({ usd_per_page: 0.0021, accuracy: check({ numbers_match: null }) }), true)).toBe(
      "Saved. Scans now cost about ₹0.18 a page, and 98% of words match max settings (checked 31 Oct).",
    );
    expect(withCost("Saved the AI settings of Asha Stores.", measured({ usd_per_page: 0.0021 }), true)).toBe(
      "Saved the AI settings of Asha Stores. Scans now cost about ₹0.18 a page. Accuracy against max settings isn't checked yet.",
    );
    expect(withCost("Saved the platform defaults.", measured(), false)).toBe("Saved the platform defaults. Scans now cost about ₹0.24 a page.");
    expect(withCost("Saved the platform defaults.", unmeasured(), false)).toBe(
      "Saved the platform defaults. The cost a page is not measured yet at these settings.",
    );
    expect(withCost("Saved the AI settings of Asha Stores.", { ...unmeasured(), accuracy: check() }, true)).toBe(
      "Saved the AI settings of Asha Stores. The cost a page is not measured yet at these settings. 99% of amounts, dates and numbers match max settings (checked 31 Oct).",
    );
    expect(withCost("Saved the platform defaults.", null, false)).toBe("Saved the platform defaults.");
  });

  it("asks the cost of what was saved before it says so", () => {
    expect(read("components/operator/OrgAiSettings.tsx")).toContain(
      "const cost = await postScanCost(costAsk(org.id, reply.own)).catch(() => null);\n      onSaved(withCost(savedText(org.name, ensembleTurnedOff), cost, true));",
    );
    expect(read("components/operator/PlatformDefaults.tsx")).toContain(
      'const cost = await postScanCost(costAsk(null, next.defaults)).catch(() => null);\n      onSaved(next, withCost("Saved the platform defaults.", cost, false));',
    );
  });
});

describe("asking the cost as the draft changes", () => {
  afterEach(() => vi.useRealTimers());

  it("asks once the draft rests about 400 ms, only for the latest draft", async () => {
    vi.useFakeTimers();
    expect(COST_DELAY_MS).toBe(400);
    const fetch = vi.fn(async () => measured());
    const show = vi.fn();
    const first = askScanCost(costAsk(null, values()), fetch, show);
    vi.advanceTimersByTime(399);
    first(); // the draft changed again before it rested
    askScanCost(costAsk(null, values({ scan_thinking: "minimal" })), fetch, show);
    vi.advanceTimersByTime(399);
    expect(fetch).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(expect.objectContaining({ scan_thinking: "minimal" }));
    await vi.runAllTimersAsync();
    expect(show).toHaveBeenCalledWith(measured());
  });

  it("keeps the last reply: an older reply that comes later is dropped", async () => {
    vi.useFakeTimers();
    let answerOld: (cost: ScanCost) => void = () => undefined;
    const old = new Promise<ScanCost>((resolve) => (answerOld = resolve));
    const fetch = vi.fn().mockReturnValueOnce(old).mockResolvedValueOnce(measured({ usd_per_page: 0.0021 }));
    const show = vi.fn();
    const cancel = askScanCost(costAsk(null, values()), fetch, show);
    vi.advanceTimersByTime(COST_DELAY_MS); // asked, the reply not back yet
    cancel();
    askScanCost(costAsk(null, values({ scan_resolution: "low" })), fetch, show);
    await vi.advanceTimersByTimeAsync(COST_DELAY_MS);
    answerOld(measured());
    await vi.runAllTimersAsync();
    expect(show).toHaveBeenCalledTimes(1);
    expect(show).toHaveBeenCalledWith(measured({ usd_per_page: 0.0021 }));
  });

  it("says a failure once and asks no more until the draft changes", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn(async () => {
      throw new TypeError("offline");
    });
    const show = vi.fn();
    askScanCost(costAsk(null, values()), fetch, show);
    await vi.advanceTimersByTimeAsync(10 * COST_DELAY_MS);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(show).toHaveBeenCalledTimes(1);
    expect(show).toHaveBeenCalledWith("failed");
  });
});

describe("only the operator console", () => {
  it("never shows these settings on an organisation admin's own AI settings", () => {
    const panel = read("components/admin/AiSettingsPanel.tsx");
    for (const word of ["Picture detail", "pages read again", "scan_resolution", "careful_", "Cost per page", "scan-cost", "accuracy-check", "Minimal"])
      expect(panel, word).not.toContain(word);
  });
});
