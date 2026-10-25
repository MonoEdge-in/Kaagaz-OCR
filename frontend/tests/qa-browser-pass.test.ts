// The QA browser pass (25 Oct 2026): what a real browser showed on the local stack, held.
// - The document page's Details stacked "Ravi Madeup" and Change onto three lines at 1024px:
//   its term column follows the tab's own width now, not the screen's.
// - A failed sign-in, and Verify on a value, dropped keyboard focus to the page.
// - More details said the page count twice ("PDF · 3 pages 3 pages · 180 words").
// - With the AI off, Ask's quoted lines read as a written answer.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnswerView, QUOTED_TEXT } from "@/components/chat/AnswerView";
import { DetailsPanel } from "@/components/files/DetailsPanel";
import type { Answer, DocumentDetail, Message } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const VIEW = "app/(app)/documents/[id]/DocumentView.tsx";

describe("the document page's Details", () => {
  it("stacks term and value when the tab is narrow, whatever the screen", () => {
    const sheet = css("components/files/Files.module.css");
    expect(sheet).toMatch(/\.detailsBox \{\s*container: details \/ inline-size;/);
    expect(sheet).toMatch(/@container details \(max-width: 23\.98rem\) \{\s*\.details \{\s*grid-template-columns: minmax\(0, 1fr\);/);
    // The screen-width switch is gone: beside the viewer at 1024px the tab is narrow too.
    expect(sheet).not.toMatch(/@media \(max-width: 479\.98px\) \{\s*\.details \{/);
  });

  it("puts the Details tab in its own container", () => {
    const detail = {
      id: "d1",
      owner: { id: "u1", name: "Ravi Madeup" },
      sensitivity: "internal",
      folder: null,
      doc_type: null,
      document_date: null,
      uploaded_by_name: "Ravi Madeup",
      created_at: "2026-10-25T05:00:00Z",
      last_change_at: null,
      last_change_by_name: null,
      page_count: 3,
      family_uploaders: [],
    } as unknown as DocumentDetail;
    const html = renderToStaticMarkup(createElement(DetailsPanel, { detail, onSaved: () => undefined, people: [] }));
    expect(html).toMatch(/^<section[^>]*class="[^"]*detailsBox[^"]*"/);
  });
});

describe("keyboard focus after an action", () => {
  it("puts focus back in the field after a failed sign-in", () => {
    const form = read("app/login/SignInForm.tsx");
    const failed = form.slice(form.indexOf("const message = signInErrorMessage(caught);"));
    expect(failed).toMatch(
      /flushSync\(\(\) => \{[\s\S]*?setBusy\(false\);\s*\}\);[\s\S]*?document\.getElementById\(askCode \? FIELD_IDS\.code : FIELD_IDS\.password\)\?\.focus\(\);/,
    );
  });

  it("keeps focus on the value after Verify, or on Values found after Verify all", () => {
    const view = read(VIEW);
    expect(view).toContain("id={valueDomId(value.id)}");
    expect(view).toContain('<h2 id="values-heading" className={styles.valuesHeading} tabIndex={-1}>');
    expect(view).toMatch(/document\.getElementById\(valueDomId\(valueId \?\? ""\)\) \?\? document\.getElementById\("values-heading"\)/);
    const one = view.slice(view.indexOf("const verifyValue = async"), view.indexOf("const verifyAllValues = async"));
    expect(one).toContain("afterVerify(value.id);");
    const all = view.slice(view.indexOf("const verifyAllValues = async"), view.indexOf("const retryValues ="));
    expect(all).toContain("afterVerify(null);");
  });
});

describe("More details", () => {
  it("gives the page count once: from the count line when there is one", () => {
    expect(read(VIEW)).toContain('{!review.status?.summary && <span className="mono">{pagesText}</span>}');
  });
});

describe("an answer the AI did not write", () => {
  const answer = (over: Partial<Answer>): Answer => ({
    version: 2,
    status: "answered",
    label: "needs_review",
    opening: "Good morning, Ravi. Here's what I found.",
    sentences: [{ text: "Total amount: Rs 6299", citations: [1] }],
    citations: [],
    closest_documents: [],
    documents: [],
    assumptions: [],
    route: "search",
    normalized_question: "total amount",
    model_used: false,
    ...over,
  });
  const message = (a: Answer): Message => ({
    id: "a1",
    role: "assistant",
    text: "Total amount: Rs 6299",
    created_at: "2026-10-25T05:00:00Z",
    answer: a,
    feedback: null,
  });
  const render = (a: Answer) =>
    renderToStaticMarkup(createElement(AnswerView, { message: message(a), question: "What is the total amount?" }));

  it("says the lines are quoted, not written, on a search answer made without the AI", () => {
    expect(QUOTED_TEXT).toBe(
      "The AI did not write this answer: these are the lines that best match your question, quoted from your documents. Check that they answer it.",
    );
    expect(render(answer({}))).toContain(QUOTED_TEXT);
  });

  it("says nothing more on a written answer, or a fixed figure that never needs the AI", () => {
    expect(render(answer({ model_used: true }))).not.toContain(QUOTED_TEXT);
    expect(render(answer({ route: "fixed_query" }))).not.toContain(QUOTED_TEXT);
  });
});
