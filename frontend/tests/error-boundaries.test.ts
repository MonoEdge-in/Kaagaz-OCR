// #246 A8: an operator console screen that throws gets its own error page inside the console,
// and a throw in the PDF viewer shows in the viewer's pane only, never the whole document screen.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ConsoleError from "@/app/operator/(console)/error";
import { ViewerBoundary, ViewerFailed } from "@/components/viewer/ViewerBoundary";

describe("the operator console's error page", () => {
  it("says what happened, offers Try again and the way back, and shows the error's words", () => {
    const error = Object.assign(new Error("Cannot read properties of null (reading 'name')"), { digest: "77" });
    const html = renderToStaticMarkup(createElement(ConsoleError, { error, retry: () => undefined }));
    expect(html).toContain("This console screen hit a problem");
    expect(html).toMatch(/<button type="button" class="btn btn-primary">Try again<\/button>/);
    expect(html).toContain('href="/operator"');
    expect(html).toContain("<summary>Details</summary>");
    expect(html).toContain("(reading &#x27;name&#x27;) (77)");
  });
});

describe("the PDF viewer's error boundary", () => {
  it("catches a throw into its own state", () => {
    expect(ViewerBoundary.getDerivedStateFromError()).toEqual({ failed: true });
  });

  it("shows the viewer's children until then", () => {
    const html = renderToStaticMarkup(createElement(ViewerBoundary, { download: null, children: "the viewer" }));
    expect(html).toBe("the viewer");
  });

  it("offers Try again and still the download", () => {
    const html = renderToStaticMarkup(createElement(ViewerFailed, { onRetry: () => undefined, download: "/f" }));
    expect(html).toContain('role="alert"');
    expect(html).toContain("The page viewer hit a problem");
    expect(html).toContain("Try again");
    expect(html).toContain('href="/f"');
    const none = renderToStaticMarkup(createElement(ViewerFailed, { onRetry: () => undefined, download: null }));
    expect(none).not.toContain("Download");
  });

  it("wraps the document screen's PDF viewer, keyed to the document", () => {
    const view = readFileSync(join(__dirname, "../app/(app)/documents/[id]/DocumentView.tsx"), "utf8");
    expect(view).toMatch(/<ViewerBoundary key=\{detail\.id\} download=\{download\}>\s*<PdfDocumentViewer/);
  });
});
