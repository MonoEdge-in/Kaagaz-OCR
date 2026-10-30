// #453 (owner, 29 Oct 2026): "Give 3 dots or something better, in that I can edit the value if not
// right and can verify directly, single." On the summary card, a reviewer's value not checked yet
// has one ⋯ button at the row's end that says so and opens Verify, Edit value… and Show on page;
// each is one POST /review/{value_id}. Members keep a quiet mark and no menu.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FACT_TEXT, SUMMARY_TEXT, SummaryCard, withResolved } from "@/app/(app)/documents/[id]/SummaryCard";
import { MAX_TYPED_LENGTH, RESOLVE_OFFLINE, sendResolve } from "@/components/review/review";
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
const detail = (values: ExtractedValue[], shown: DocumentCard = card) =>
  ({ id: "d1", status: "ready", understanding: false, card: shown, values, pages: [], reviewer_fixes: 0 }) as unknown as DocumentDetail;
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

  it("names the button by its state and its value, and tells the label and reason in its tooltip", () => {
    const html = render(detail(waiting), true);
    const name = `Owner ${OWNERS[0]}`;
    expect(FACT_TEXT.menu(name)).toBe(`Not checked yet: actions for ${name}`);
    expect(html).toContain(`aria-label="${FACT_TEXT.menu(name)}"`);
    expect(html).toContain(`title="${labelTip("needs_review", "ocr_script")}"`);
    expect(html).toContain(`aria-label="Actions for ${name}"`);
    // The status is in words as well as the dot, so it is never colour (or a dot) alone.
    expect(VIEW_CSS).toMatch(/\.factMenu::after \{[^}]*background: currentColor;/);
  });

  // Review panel, 29 Oct 2026: the row showed the AI card's words while Verify confirmed the
  // stored value, matched to it only by its spot. The row now shows what Verify confirms.
  it("shows the stored value's own words, the ones Verify confirms and Edit starts from", () => {
    const dated = { ...card, document_date: { value: "2007-06-15", date: "2007-06-15", page: 1, region: at(0.5) } };
    const stored = { ...value("v3", "15/06/2007", 0.5, "needs_review"), field: "document_date" } as ExtractedValue;
    const html = render(detail([stored], dated), true);
    expect(html).toMatch(/title="15\/06\/2007"[^>]*>15\/06\/2007</);
    expect(html).toContain(`aria-label="${FACT_TEXT.menu("Document date 15/06/2007")}"`);
    expect(html).not.toContain("2007-06-15");
    const source = read("app/(app)/documents/[id]/SummaryCard.tsx");
    expect(source).toContain("const text = value ? value.value : item.text;");
    expect(source).toContain("onSelect: () => void send(text, FACT_TEXT.verified)");
    expect(source).toContain("setTyped(text);");
    // With no value behind it, the card's own words, and no menu.
    const bare = render(detail([], dated), true);
    expect(bare).toMatch(/title="2007-06-15"[^>]*>2007-06-15</);
    expect(bare).not.toContain('aria-haspopup="menu"');
  });

  it("puts every row's end in one slot of one size, the value cut to one line, 44px on touch", () => {
    const html = render(detail(waiting), true);
    expect(html.match(/<span class="[^"]*factEnd[^"]*">/g)).toHaveLength(2);
    expect(html.match(/class="quiet-link [^"]*valueButton[^"]*factValue/g)).toHaveLength(2);
    expect(html).toContain(`title="${OWNERS[0]}"`); // the whole value on hover when it is cut
    // The slot is sized; the ⋯ button fills it.
    expect(rule(".factEnd")).toMatch(/flex: none;[\s\S]*width: var\(--badge-h\);\s*height: var\(--badge-h\);/);
    expect(rule(".factEnd .factMenu")).toMatch(/width: 100%;\s*min-width: 0;\s*height: 100%;\s*min-height: 0;/);
    expect(rule(".keyFact .factValue")).toMatch(/min-width: 0;[\s\S]*overflow: hidden;[\s\S]*text-overflow: ellipsis;\s*white-space: nowrap;/);
    expect(VIEW_CSS).toMatch(/@media \(pointer: coarse\) \{\s*\.factEnd \{\s*width: var\(--h-lg\);\s*height: var\(--h-lg\);\s*\}/);
  });

  // Review panel, 29 Oct 2026: a name cut to one line kept its rest in a tooltip, which touch and
  // the keyboard never reach. The name wraps; the value is still cut (Show on page shows it, and
  // a screen reader reads it whole).
  it("lets a value's name wrap, so none of it is only in a tooltip", () => {
    expect(rule(".keyFact dt")).not.toMatch(/nowrap|ellipsis/);
    expect(rule(".keyFact dt")).toMatch(/overflow-wrap: anywhere;/);
    expect(render(detail(waiting), true)).toContain("<dt>Owner</dt>");
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
  // As the summary card sends it: typed, no second reading, nothing remembered.
  const typed = (entered: string) => sendResolve("v1", "typed", entered, null, false);

  it("sends the value as it reads (Verify) or as typed, trimmed (Save): typed, nothing remembered", async () => {
    const verified = { ...waiting[0]!, label: "verified", label_reason: "corrected" };
    const item = { value: verified, document_id: "d1", document_name: "x", page: {}, check_reasons: [] };
    const sent = record(() => reply(200, item));
    expect(await typed(OWNERS[0]!)).toEqual({ ok: true, item });
    await typed("  राम शंकर पाटिल ");
    expect(sent).toEqual([
      { url: "/api/review/v1", method: "POST", body: { choice: "typed", remember: false, value: OWNERS[0] } },
      { url: "/api/review/v1", method: "POST", body: { choice: "typed", remember: false, value: "राम शंकर पाटिल" } },
    ]);
  });

  it("checks an empty or too long value first, in plain words, and sends nothing", async () => {
    const sent = record(() => reply(200, {}));
    expect(await typed("   ")).toEqual({ ok: false, message: "Type the correct value first.", stale: false });
    expect(await typed("x".repeat(MAX_TYPED_LENGTH + 1))).toEqual({
      ok: false,
      message: `Keep the value under ${MAX_TYPED_LENGTH} characters.`,
      stale: false,
    });
    expect(sent).toEqual([]);
  });

  it("shows the server's plain words, and asks for the document again when the value changed (404, 409)", async () => {
    record(() => reply(409, { error: { code: "not_needs_review", message: "This value doesn't need review any more." } }));
    expect(await typed("x")).toEqual({ ok: false, message: "This value doesn't need review any more.", stale: true });
    record(() => reply(404, { error: { code: "not_found", message: "Not found." } }));
    expect(await typed("x")).toEqual({ ok: false, message: "Not found.", stale: true });
    record(() => reply(400, { error: { code: "bad_request", message: "Type the correct value." } }));
    expect(await typed("x")).toEqual({ ok: false, message: "Type the correct value.", stale: false });
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("offline");
    });
    expect(await typed("x")).toEqual({ ok: false, message: RESOLVE_OFFLINE, stale: false });
  });

  // Review panel, 29 Oct 2026: one send step for Review and the summary card, with one offline line.
  it("is Review's own send step too, a reading and Remember as it picks them", async () => {
    const sent = record(() => reply(200, {}));
    await sendResolve("v1", "second", "", "15/06/2007", true);
    expect(sent).toEqual([{ url: "/api/review/v1", method: "POST", body: { choice: "second", remember: true } }]);
    expect(await sendResolve("v1", "second", "", null, false)).toEqual({
      ok: false,
      message: "There is no second reading for this value.",
      stale: false,
    });
    expect(read("components/review/ReviewCard.tsx")).toContain(
      "const done = await sendResolve(value.id, choice, typed, value.second_reading, remember);",
    );
    expect(read("app/(app)/documents/[id]/SummaryCard.tsx")).toContain('const done = await sendResolve(value.id, "typed", entered, null, false);');
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

  it("shows a saved value as saved", () => {
    const saved = { ...waiting[0]!, value: "राम शंकर पाटिल", label: "verified", label_reason: "corrected" } as ExtractedValue;
    const html = render(withResolved(detail(waiting), saved), true);
    expect(html).toContain("राम शंकर पाटिल");
    expect(html).not.toContain(OWNERS[0]!);
  });

  // Review panel, 29 Oct 2026: a flagged value is one of the words to review, so after Verify the
  // bar still said "1 word needs a look" and the page drew its flag; and a document asked for
  // before the Verify, answering after it, put the old value back.
  it("asks for the words again, and drops a document asked for before it", () => {
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    const resolved = view.slice(view.indexOf("onResolved={(value, message) => {"), view.indexOf("onStale={"));
    expect(resolved).toMatch(/changes\.current \+= 1;\s*setLoaded\(\(shown\) => \(shown && "detail" in shown \? \{ \.\.\.shown, detail: withResolved\(shown\.detail, value\) \} : shown\)\);\s*review\.reloadWords\(\);/);
    const load = view.slice(view.indexOf("const asked = changes.current;"), view.indexOf("}, [poll, documentId, attempt, reloads]);"));
    expect(load.match(/if \(asked !== changes\.current\) return setReloads\(\(n\) => n \+ 1\);/g)).toHaveLength(2);
    expect(load.indexOf("if (asked !== changes.current)")).toBeLessThan(load.indexOf("setLoaded({ attempt, detail });"));
    expect(read("app/(app)/documents/[id]/WordReview.tsx")).toContain("reloadWords: () => setAsks((n) => n + 1),");
  });
});
