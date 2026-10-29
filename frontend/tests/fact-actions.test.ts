// #453 (owner, 29 Oct 2026): "Give 3 dots or something better, in that I can edit the value if not
// right and can verify directly, single." On the summary card, a reviewer's value not checked yet
// has one ⋯ button at the row's end that says so and opens Verify, Edit value… and Show on page;
// each is one POST /review/{value_id}. Members keep a quiet mark and no menu.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FACT_TEXT, SUMMARY_TEXT, SummaryCard, resolveFact, withResolved } from "@/app/(app)/documents/[id]/SummaryCard";
import { MAX_TYPED_LENGTH } from "@/components/review/review";
import { labelTip } from "@/components/viewer/labels";
import type { DocumentCard, DocumentDetail, ExtractedValue, Region } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const VIEW_CSS = css("app/(app)/documents/[id]/DocumentView.module.css");
const rule = (selector: string) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\:]/g, "\\$&");
  return new RegExp(`(?:^|\\n|\\})\\s*${escaped} \\{([^}]*)\\}`).exec(VIEW_CSS)?.[1] ?? "";
};

afterEach(() => vi.unstubAllGlobals());

const at = (y: number): Region => ({ kind: "box", page: 1, box: [0.1, y, 0.5, y + 0.02] });
const value = (id: string, text: string, y: number, label: "verified" | "needs_review") =>
  ({ id, field: "owner_name", value: text, raw: text, label, label_reason: "ocr_script", region: at(y) }) as ExtractedValue;
const OWNERS = ["राम शंकर पाटील", "सीता देवी"];
const card: DocumentCard = {
  version: 1,
  run_id: null,
  model: "x",
  prompt: "p",
  doc_type: "land_record",
  languages: ["mr"],
  title: null,
  summary: "A land record.",
  document_date: null,
  parties: OWNERS.map((name, i) => ({ role: "owner", field: "owner_name", name, name_latin: null, page: 1, region: at(0.1 * (i + 1)) })),
  identifiers: [],
  key_facts: [],
  dropped_quotes: 0,
};
const detail = (values: ExtractedValue[]) =>
  ({ id: "d1", status: "ready", understanding: false, card, values, pages: [], reviewer_fixes: 0 }) as unknown as DocumentDetail;
const render = (shown: DocumentDetail, reviewer: boolean) =>
  renderToStaticMarkup(
    createElement(SummaryCard, { detail: shown, active: null, onShow: () => undefined, reviewer, onResolved: () => undefined, onStale: () => undefined }),
  );
const waiting = [value("v1", OWNERS[0]!, 0.1, "needs_review"), value("v2", OWNERS[1]!, 0.2, "verified")];

describe("a value's ⋯ menu on the summary card", () => {
  it("gives a reviewer Verify, Edit value… and Show on page, each with its plain line, on a value not checked yet only", () => {
    const html = render(detail(waiting), true);
    expect(html.match(/aria-haspopup="menu"/g)).toHaveLength(1); // the verified one has its check instead
    const items = [...html.matchAll(/role="menuitem"[^>]*>([^<]+)<span[^>]*>([^<]+)<\/span>/g)].map((m) => [m[1], m[2]]);
    expect(items).toEqual([
      [FACT_TEXT.verify, FACT_TEXT.verifyHelp],
      ["Edit value…", FACT_TEXT.editHelp],
      [FACT_TEXT.show, FACT_TEXT.showHelp],
    ]);
    for (const line of [FACT_TEXT.verifyHelp, FACT_TEXT.editHelp, FACT_TEXT.showHelp]) expect(line).toMatch(/^[A-Z].{10,110}\.$/);
    expect(html.match(/label-mark label-mark-verified/g)).toHaveLength(1);
  });

  it("gives a member no menu: the quiet mark on every value, and the count without Review", () => {
    const html = render(detail(waiting), false);
    expect(html).not.toContain('aria-haspopup="menu"');
    expect(html).not.toContain('role="menuitem"');
    expect(html.match(/class="label-mark"/g)).toHaveLength(1);
    expect(html).toContain(`<span>${SUMMARY_TEXT.notChecked(1)}</span>`);
    expect(html).not.toContain("/review");
  });

  it("names the button by its state and its value, and tells the label and reason in its tooltip", () => {
    const html = render(detail(waiting), true);
    const name = `Owner ${OWNERS[0]}`;
    expect(FACT_TEXT.menu(name)).toBe(`Not checked yet: actions for ${name}`);
    expect(html).toContain(`aria-label="${FACT_TEXT.menu(name)}"`);
    expect(html).toContain(`title="${labelTip("needs_review", "ocr_script")}"`);
    expect(labelTip("needs_review")).toBe("Ready to verify: not checked yet.");
    expect(html).toContain(`aria-label="Actions for ${name}"`);
    // The status is in words as well as the dot, so it is never colour (or a dot) alone.
    expect(VIEW_CSS).toMatch(/\.factMenu::after \{[^}]*background: currentColor;/);
  });

  it("puts every row's end in one slot of one size, the value cut to one line, 44px on touch", () => {
    const html = render(detail(waiting), true);
    expect(html.match(/<span class="[^"]*factEnd[^"]*">/g)).toHaveLength(2);
    expect(html.match(/class="quiet-link [^"]*valueButton[^"]*factValue/g)).toHaveLength(2);
    expect(html).toContain(`title="${OWNERS[0]}"`); // the whole value on hover when it is cut
    expect(rule(".factEnd")).toMatch(/flex: none;[\s\S]*width: var\(--badge-h\);\s*height: var\(--badge-h\);/);
    expect(rule(".factEnd .factMenu")).toMatch(/width: var\(--badge-h\);\s*min-width: var\(--badge-h\);\s*height: var\(--badge-h\);/);
    for (const selector of [".keyFact dt", ".keyFact .factValue"]) {
      expect(rule(selector), selector).toMatch(/min-width: 0;[\s\S]*overflow: hidden;[\s\S]*text-overflow: ellipsis;\s*white-space: nowrap;/);
    }
    expect(VIEW_CSS).toMatch(/@media \(pointer: coarse\) \{\s*\.factEnd,\s*\.factEnd \.factMenu \{\s*width: var\(--h-lg\);\s*min-width: var\(--h-lg\);\s*height: var\(--h-lg\);/);
  });

  it("uses the app's menu (keys, Esc, outside click) and Review's typed-value rules, not new ones", () => {
    const source = read("app/(app)/documents/[id]/SummaryCard.tsx");
    expect(source).toContain('import { RowMenu } from "@/components/admin/RowMenu";');
    expect(source).toContain('checkChoice("typed", entered, null)');
    expect(source).toContain("onSelect: () => void send(value.value, FACT_TEXT.verified)");
    expect(source).toContain("void send(typed, FACT_TEXT.saved)");
    expect(source).toMatch(/if \(event\.key !== "Escape"\) return;\s*event\.preventDefault\(\);\s*cancel\(\);/);
    expect(source).toContain(`maxLength={MAX_TYPED_LENGTH}`);
  });
});

describe("Verify and Save: one POST /review/{value_id} each", () => {
  const reply = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const record = (answer: () => Response) => {
    const sent: { url: string; method: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      sent.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
      return answer();
    });
    return sent;
  };

  it("sends the value as it reads (Verify) or as typed, trimmed (Save): typed, nothing remembered", async () => {
    const verified = { ...waiting[0]!, label: "verified", label_reason: "corrected" };
    const sent = record(() => reply(200, { value: verified, document_id: "d1", document_name: "x", page: {}, check_reasons: [] }));
    expect(await resolveFact("v1", OWNERS[0]!)).toEqual({ ok: true, value: verified });
    await resolveFact("v1", "  राम शंकर पाटिल ");
    expect(sent).toEqual([
      { url: "/api/review/v1", method: "POST", body: { choice: "typed", remember: false, value: OWNERS[0] } },
      { url: "/api/review/v1", method: "POST", body: { choice: "typed", remember: false, value: "राम शंकर पाटिल" } },
    ]);
  });

  it("checks an empty or too long value first, in plain words, and sends nothing", async () => {
    const sent = record(() => reply(200, {}));
    expect(await resolveFact("v1", "   ")).toEqual({ ok: false, message: "Type the correct value first.", stale: false });
    expect(await resolveFact("v1", "x".repeat(MAX_TYPED_LENGTH + 1))).toEqual({
      ok: false,
      message: `Keep the value under ${MAX_TYPED_LENGTH} characters.`,
      stale: false,
    });
    expect(sent).toEqual([]);
  });

  it("shows the server's plain words, and asks for the document again when the value changed (404, 409)", async () => {
    record(() => reply(409, { error: { code: "not_needs_review", message: "This value doesn't need review any more." } }));
    expect(await resolveFact("v1", "x")).toEqual({ ok: false, message: "This value doesn't need review any more.", stale: true });
    record(() => reply(404, { error: { code: "not_found", message: "Not found." } }));
    expect(await resolveFact("v1", "x")).toEqual({ ok: false, message: "Not found.", stale: true });
    record(() => reply(400, { error: { code: "bad_request", message: "Type the correct value." } }));
    expect(await resolveFact("v1", "x")).toEqual({ ok: false, message: "Type the correct value.", stale: false });
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("offline");
    });
    expect(await resolveFact("v1", "x")).toEqual({ ok: false, message: FACT_TEXT.offline, stale: false });
  });
});

describe("after a Verify or Save", () => {
  it("puts the reply's value in place with one more reviewer fix, so the mark, the count and the line follow", () => {
    const shown = detail(waiting);
    expect(render(shown, true)).toContain(`<span>${SUMMARY_TEXT.toVerify(1)}</span>`);
    const after = withResolved(shown, { ...waiting[0]!, label: "verified", label_reason: "corrected" });
    expect(after.values.map((each) => each.label)).toEqual(["verified", "verified"]);
    expect(after.reviewer_fixes).toBe(1);
    expect(shown.values[0]!.label).toBe("needs_review"); // the old detail is left as it was
    const html = render(after, true);
    expect(html).not.toContain("to verify"); // at 0 the line goes
    expect(html).not.toContain('aria-haspopup="menu"');
    expect(html.match(/label-mark label-mark-verified/g)).toHaveLength(2);
  });

  it("shows a saved value as saved, and says it aloud", () => {
    const saved = { ...waiting[0]!, value: "राम शंकर पाटिल", label: "verified", label_reason: "corrected" } as ExtractedValue;
    const html = render(withResolved(detail(waiting), saved), true);
    expect(html).toContain("राम शंकर पाटिल");
    expect(html).not.toContain(OWNERS[0]!);
    expect(FACT_TEXT.verified(OWNERS[0]!)).toBe(`Verified "${OWNERS[0]}".`);
    expect(FACT_TEXT.saved("राम")).toBe('Saved "राम".');
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    expect(view).toContain("detail: withResolved(shown.detail, value)");
    expect(view).toContain("setAnnouncement(message);");
    expect(view).toContain('<p className="visually-hidden" aria-live="polite">');
  });
});
