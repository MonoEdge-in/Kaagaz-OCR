// #468 B3: a search or a list that finds nothing says what to do next. A search with no
// result offers the other search with the same words and Ask with them filled in (never sent);
// Activity tells "nothing matches these filters" (with Clear filters) from "nothing yet", and
// refuses a From later than To on the field.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ActivityDates, ActivityEmpty } from "@/components/admin/ActivityPanel";
import { DATES_BACKWARDS, NO_ACTIVITY, NO_ACTIVITY_MATCHES, activityEmptyText, datesError } from "@/components/admin/logic";
import { questionFromSearch } from "@/components/chat/logic";
import { InsideSearchResults } from "@/components/documents/InsideResults";
// Changed on purpose (#468 review, PT7): askWithWordsHref sits next to askHref in chat/logic.
import { askWithWordsHref } from "@/components/chat/logic";
import { askAboutLabel } from "@/components/documents/insideSearch";
import { NameResults } from "@/components/search/SearchScreen";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
const noop = () => undefined;

describe("Ask about the words: filled in, never sent", () => {
  it("opens /chat with the words in the box and no send=1", () => {
    const href = askWithWordsHref("rent paid & GST");
    expect(href).toBe("/chat?q=rent%20paid%20%26%20GST");
    expect(href).not.toContain("send=1");
    expect(questionFromSearch(href.slice("/chat".length))).toBe("rent paid & GST"); // what ChatScreen puts in the box
  });

  it("names the words on the button, cut short when they are long", () => {
    expect(askAboutLabel("rent")).toBe("Ask Kaagaz about “rent”");
    const long = "a".repeat(80);
    expect(askAboutLabel(long)).toBe(`Ask Kaagaz about “${"a".repeat(40)}…”`);
  });
});

describe("a search with no result is never a dead end", () => {
  it("inside documents: Clear search, Search names instead and Ask Kaagaz about the words", () => {
    const html = renderToStaticMarkup(
      createElement(InsideSearchResults, {
        found: { documents: [] },
        error: null,
        q: "rent",
        noResults: "No document holds these words.",
        datesIgnored: false,
        onRetry: noop,
        onClear: noop,
        onSearchNames: noop,
      }),
    );
    expect(html).toContain("No document holds these words.");
    expect(html).toContain(">Clear search</button>");
    expect(html).toContain(">Search names instead</button>");
    expect(html).toContain('href="/chat?q=rent"');
    expect(html).toContain("Ask Kaagaz about “rent”");
  });

  it("names: Search inside documents instead and Ask Kaagaz about the words", () => {
    const html = renderToStaticMarkup(
      createElement(NameResults, { names: { items: [], total: 0 }, error: null, q: "mehta", onRetry: noop, onSearchInside: noop }),
    );
    expect(html).toContain("No document is named like this.");
    expect(html).toContain(">Search inside documents instead</button>");
    expect(html).toContain('href="/chat?q=mehta"');
  });

  it("both screens switch with the same words, by their own route or state", () => {
    expect(read("components/search/SearchScreen.tsx")).toMatch(/onSearchNames=\{\(\) => router\.replace\(searchHref\(q, "names"\)\)\}/);
    expect(read("components/search/SearchScreen.tsx")).toMatch(/onSearchInside=\{\(\) => router\.replace\(searchHref\(q, "inside"\)\)\}/);
    expect(read("components/documents/DocumentsView.tsx")).toMatch(/onSearchNames=\{\(\) => setModeChoice\("names"\)\}/);
  });
});

describe("Activity: filtered empty is not 'nothing yet'", () => {
  it("says which, in plain words", () => {
    expect(NO_ACTIVITY).toBe("No activity yet.");
    expect(NO_ACTIVITY_MATCHES).toBe("No activity matches these filters.");
    expect(activityEmptyText(false)).toBe(NO_ACTIVITY);
    expect(activityEmptyText(true)).toBe(NO_ACTIVITY_MATCHES);
  });

  it("admin: Clear filters only when filters are set", () => {
    const filtered = renderToStaticMarkup(createElement(ActivityEmpty, { filtered: true, onClear: noop }));
    expect(filtered).toContain(NO_ACTIVITY_MATCHES);
    expect(filtered).toContain(">Clear filters</button>");
    const none = renderToStaticMarkup(createElement(ActivityEmpty, { filtered: false, onClear: noop }));
    expect(none).toContain(NO_ACTIVITY);
    expect(none).not.toContain("Clear filters");
    expect(read("components/admin/ActivityPanel.tsx")).not.toContain("No activity yet.");
  });

  it("operator: the same words, and Clear filters while the organisation or action is set", () => {
    const view = read("components/operator/ActivityView.tsx");
    expect(view).not.toContain('"No activity matches."');
    expect(view).toContain("activityEmptyText(filtered)");
    expect(view).toMatch(/filtered && \(\s*<button type="button" className="btn" onClick=\{clearFilters\}>\s*Clear filters/);
  });
});

describe("Activity's dates: From later than To is refused on the field", () => {
  it("datesError says so only for a range that ends before it starts", () => {
    expect(DATES_BACKWARDS).toBe("From must be on or before To.");
    expect(datesError("2026-10-10", "2026-10-01")).toBe(DATES_BACKWARDS);
    expect(datesError("2026-10-01", "2026-10-01")).toBeNull();
    expect(datesError("2026-10-01", "")).toBeNull();
    expect(datesError("", "2026-10-01")).toBeNull();
  });

  it("marks From invalid with the words under it", () => {
    const html = renderToStaticMarkup(createElement(ActivityDates, { from: "2026-10-10", to: "2026-10-01", onApply: noop }));
    expect(html).toMatch(/<input id="[^"]+-from"[^>]*aria-invalid="true"[^>]*aria-describedby="[^"]+-from-error"/);
    expect(html).toMatch(/id="[^"]+-from-error" class="error-text" role="alert">From must be on or before To\.</);
    const fine = renderToStaticMarkup(createElement(ActivityDates, { from: "2026-10-01", to: "2026-10-10", onApply: noop }));
    expect(fine).not.toContain("aria-invalid");
  });

  it("never applies a backwards range; the words sit on the field just changed, which keeps the focus", () => {
    const panel = read("components/admin/ActivityPanel.tsx");
    expect(panel).toMatch(/if \(datesError\(nextFrom, nextTo\)\) return;[^\n]*\n\s*onApply\(/);
    expect(panel).toMatch(/onChange=\{\(event\) => apply\(event\.target\.value, to, "from"\)\}/);
    expect(panel).toMatch(/onChange=\{\(event\) => apply\(from, event\.target\.value, "to"\)\}/);
  });
});
