// Each organisation's numbers under the operator's Overview (#387, #377), rendered in node from
// a reply shaped like GET /operator/org-numbers: every way a page was read has its own line (a
// route added later shows under its own name), failed files by reason, AI use with its cost, a
// sortable table with aria-sort, a card per organisation on a phone, and a plain note that
// refused requests are not counted here.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { OperatorConsole } from "@/components/operator/OperatorConsole";
import { ORG_NUMBERS_NOTE, OrgNumbersScreen, ROUTE_WORDS, breakdownLines } from "@/components/operator/OrgNumbers";
import type { OrgNumbersData } from "@/components/operator/OrgNumbers";
import type { LoadState } from "@/components/operator/OverviewView";
import type { OrgNumbers } from "@/lib/types";

const noop = () => undefined;

const row = (id: string, changes: Partial<OrgNumbers> = {}): OrgNumbers => ({
  organisation_id: id,
  documents: 8,
  documents_in_trash: 1,
  pages_read: 12,
  pages_by_route: { text_layer: 4, ai: 4, local: 2, cells: 1, blank: 1 },
  failed_documents: { too_many_pages: 1, infected: 1 },
  active_users: 3,
  questions: 4,
  ai_calls: 6,
  ai_input_tokens: 6000,
  ai_output_tokens: 600,
  ai_cost_usd: 0.04,
  ai_cost_unknown_calls: 2,
  ...changes,
});

const data = (rows: OrgNumbers[], aiEnabled = true): OrgNumbersData => ({
  reply: { from: "2026-09-28", to: "2026-10-27", ai_enabled: aiEnabled, organisations: rows },
  names: new Map([
    ["o1", "Bharat Steel"],
    ["o2", "Amrut Traders"],
  ]),
  loadedAt: Date.parse("2026-10-27T10:00:00Z"),
});

const screen = (state: LoadState<OrgNumbersData>) =>
  renderToStaticMarkup(createElement(OrgNumbersScreen, { days: 30, onDays: noop, state, onRetry: noop }));

describe("each organisation's numbers", () => {
  const html = screen({
    kind: "ready",
    data: data([row("o1"), row("o2", { pages_read: 9, pages_by_route: { ai: 8, a_route_added_later: 1 }, documents_in_trash: 0 })]),
  });

  it("is a sortable table of the organisations, A to Z first", () => {
    expect(html).toContain("Each organisation&#x27;s numbers");
    expect(html).toMatch(/aria-sort="ascending"><button[^>]*>Organisation</);
    expect(html.match(/aria-sort=/g)).toHaveLength(1);
    const headers = [...html.matchAll(/role="columnheader"[^>]*><button type="button"[^>]*>([^<]*)</g)].map((m) => m[1]);
    expect(headers).toEqual(["Organisation", "Files uploaded", "Pages read", "Failed files", "Active people", "Questions", "AI use"]);
    expect(html.indexOf("Amrut Traders")).toBeLessThan(html.indexOf("Bharat Steel"));
    expect(html.match(/data-label="Pages read"/g)).toHaveLength(2);
  });

  it("counts every page read, each way on its own line, a later route under its own name", () => {
    expect(html).toContain("In all: 16 files uploaded, 21 pages read, 8 questions.");
    for (const line of ["PDF&#x27;s own text 4", "AI model 4", "Local reader 2", "Spreadsheet cells 1", "Blank 1", "AI model 8", "a route added later 1"]) {
      expect(html).toContain(`<li>${line}</li>`);
    }
    expect(html).toContain("1 in Trash");
  });

  it("gives the failed files by reason, and AI use with its tokens and cost", () => {
    expect(html).toContain("<li>infected 1</li>");
    expect(html).toContain("<li>too many pages 1</li>");
    expect(html).toContain("6 calls");
    expect(html).toContain("6,000 tokens in, 600 out");
    expect(html).toContain("$0.04, 2 calls cost not known");
  });

  it("says what each figure counts, and that refused requests are not counted", () => {
    expect(html).toContain(ORG_NUMBERS_NOTE.replace(/'/g, "&#x27;"));
    expect(ORG_NUMBERS_NOTE).toContain("Refused requests are not counted here");
  });

  it("orders the ways a page was read: the known ones first, then any other by name", () => {
    expect(breakdownLines({ zeta: 1, blank: 2, ai: 3, alpha: 1, local: 0 }, ROUTE_WORDS)).toEqual(["AI model 3", "Blank 2", "alpha 1", "zeta 1"]);
  });

  it("says AI is off on a keyless server with no AI calls", () => {
    const keyless = screen({ kind: "ready", data: data([row("o1", { ai_calls: 0, ai_cost_usd: null })], false) });
    expect(keyless).toContain("AI is off on this server");
  });

  it("loads, fails with Try again, and says when there is no organisation", () => {
    expect(screen({ kind: "loading" })).toContain("skeleton");
    expect(screen({ kind: "error", message: "The server is busy." })).toContain("The server is busy.");
    expect(screen({ kind: "ready", data: data([]) })).toContain("No organisations yet.");
  });

  it("sits under the Overview on the console's first tab", () => {
    expect(renderToStaticMarkup(createElement(OperatorConsole))).toContain("Each organisation&#x27;s numbers");
  });
});
