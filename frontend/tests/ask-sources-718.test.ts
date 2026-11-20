// #718 Sources by file and #719 the files searched (design boards 1 and 2). The small decisions
// in node; the rows rendered with react-dom in the small fake DOM (tests/fake-dom.ts) and pressed.
// Answers saved before keep their sources one by one.

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { folderPathText, pageCiteText, sourceFiles } from "@/components/chat/answer";
import type { Answer, Citation, FilePage, Message, SearchedFile } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

vi.mock("next/link", async () => {
  const { createElement: make } = await import("react");
  return { default: ({ href, children }: { href: string; children: unknown }) => make("a", { href }, children as never) };
});

const page = (page_no: number, citations: number[] = []): FilePage => ({
  page_no,
  regions: [{ kind: "box", page: page_no, box: [0.1, 0.2, 0.3, 0.4] }],
  snippet: `Survey No. 44, Wakad. Total area 0.81 H.R. (page ${page_no})`,
  citations,
});

const citation = (n: number, document_id: string, page_no: number): Citation => ({
  n,
  kind: "piece",
  ref_id: `p${n}`,
  document_id,
  document_name: `${document_id}.pdf`,
  detected_type: "pdf",
  page_no,
  regions: [{ kind: "box", page: page_no, box: [0.1, 0.2, 0.3, 0.4] }],
  snippet: "Total area 0.81",
  label: "verified",
  rank: n,
});

const searchedFile = (n: number, used = false): SearchedFile => ({
  document_id: `s${n}`,
  name: `File ${n}.pdf`,
  used,
  path: ["Land & Legal"],
  pages: [page(1)],
});

const answered = (extra: Partial<Answer> = {}): Answer => ({
  version: 2,
  status: "answered",
  label: "verified",
  opening: null,
  sentences: [{ text: "The land measures 0.81 hectare.", citations: [1, 2] }],
  citations: [citation(1, "deed", 1), citation(2, "deed", 3)],
  closest_documents: [],
  route: "search",
  normalized_question: "area of survey 44",
  model_used: true,
  documents: [
    {
      document_id: "deed",
      title: "7-12 Extract Survey 44 Wakad.pdf",
      why: "",
      citations: [1, 2],
      path: ["Land & Legal", "Land records"],
      pages: [page(1, [1]), page(3, [2]), page(4)],
    },
  ],
  ...extra,
});

/** The answer as saved before #718: its documents without a path or pages. */
const savedBefore = (answer: Answer): Answer => ({
  ...answer,
  documents: answer.documents?.map(({ document_id, title, why, citations }) => ({ document_id, title, why, citations })),
});

const message = (answer: Answer): Message => ({
  id: "a1",
  role: "assistant",
  text: "",
  created_at: "2026-11-20T10:00:00Z",
  feedback: null,
  pin: null,
  answer,
});

describe("sources by file, decided in node", () => {
  it("groups only a new answer's files; one saved before has none", () => {
    expect(sourceFiles(answered()).map((file) => [file.document_id, file.pages.length])).toEqual([["deed", 3]]);
    expect(sourceFiles(savedBefore(answered()))).toEqual([]);
    expect(sourceFiles({})).toEqual([]);
  });

  it("says the path, Shared with you for a hidden folder, nothing at the top level", () => {
    expect(folderPathText(["Land & Legal", "Sale deeds"])).toBe("Land & Legal › Sale deeds");
    expect(folderPathText(null)).toBe("Shared with you");
    expect(folderPathText([])).toBe("");
    expect(folderPathText(undefined)).toBe("");
  });

  it("names a page by its citation numbers, else Also matches", () => {
    expect(pageCiteText({ citations: [1, 3] })).toBe("[1] [3]");
    expect(pageCiteText({ citations: [] })).toBe("Also matches");
  });
});

describe("an answer's sources, rendered", () => {
  const render = async (answer: Answer) => {
    const { AnswerView } = await import("@/components/chat/AnswerView");
    return renderToStaticMarkup(createElement(AnswerView, { message: message(answer), question: "area of survey 44" }));
  };

  it("lists a new answer's sources by file, the first open, with the footnote", async () => {
    const html = await render(answered({ searched_files: [searchedFile(1, true), searchedFile(2)] }));
    expect(html).toContain("Sources · 1 file");
    expect(html).toContain("Land &amp; Legal › Land records");
    expect(html).toContain("3 pages");
    expect(html).toContain('aria-label="Hide pages of 7-12 Extract Survey 44 Wakad.pdf"');
    expect(html).toContain("[1]");
    expect(html).toContain("Also matches");
    expect(html).toContain("Open goes to the exact spot.");
    expect(html).toContain("/documents/deed?page=4");
    expect(html).toContain("Searched 2 files");
    expect(html).not.toContain("Most relevant"); // the cards one by one are an old answer's
  });

  it("shows an answer saved before as it did: its cited sources one by one, no file rows", async () => {
    const html = await render(savedBefore(answered()));
    expect(html).not.toContain("Sources · 1 file");
    expect(html).not.toContain("Searched");
    expect(html).toContain("Most relevant");
  });
});

// --- pressed, in the fake DOM ---

let doc: FakeDocument;
let root: Root | null = null;

async function show(element: ReturnType<typeof createElement>) {
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(element));
  return container;
}

const all = () => doc.body.all();
const byLabel = (label: string) => all().find((element) => element.getAttribute("aria-label") === label) ?? null;
const button = (words: string) =>
  all().find((element) => element.tagName === "BUTTON" && element.textContent.trim() === words) ?? null;
const press = (element: FakeElement | null) => act(async () => void fire(element!, "click", { button: 0 }));

beforeEach(() => {
  doc = install();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
});

describe("the file rows and the files searched, pressed", () => {
  it("hides and shows a file's pages with its arrow", async () => {
    const { FileSources } = await import("@/components/chat/FileSources");
    await show(createElement(FileSources, { files: sourceFiles(answered()), terms: [] }));
    const arrow = byLabel("Hide pages of 7-12 Extract Survey 44 Wakad.pdf");
    expect(arrow?.getAttribute("aria-expanded")).toBe("true");
    expect(byLabel("Pages of 7-12 Extract Survey 44 Wakad.pdf")).not.toBeNull();
    await press(arrow);
    expect(arrow?.getAttribute("aria-expanded")).toBe("false");
    expect(arrow?.getAttribute("aria-label")).toBe("Show pages of 7-12 Extract Survey 44 Wakad.pdf");
    expect(byLabel("Pages of 7-12 Extract Survey 44 Wakad.pdf")).toBeNull();
  });

  it("opens the closest files, the first 7, then Show 2 more, the cited ones Used", async () => {
    const { SearchedFiles } = await import("@/components/chat/FileSources");
    const files = Array.from({ length: 9 }, (_, n) => searchedFile(n + 1, n === 0));
    await show(createElement(SearchedFiles, { files, terms: [] }));
    expect(byLabel("Files searched for this answer")).toBeNull();
    await press(button("Searched 9 files"));
    const list = byLabel("Files searched for this answer");
    expect(list?.textContent).toContain("The 9 closest files, best first.");
    expect(all().filter((element) => element.getAttribute("aria-label")?.startsWith("Show pages of"))).toHaveLength(7);
    expect(all().filter((element) => element.textContent === "Used")).toHaveLength(1);
    await press(button("Show 2 more"));
    expect(all().filter((element) => element.getAttribute("aria-label")?.startsWith("Show pages of"))).toHaveLength(9);
    expect(button("Show 2 more")).toBeNull();
  });
});
