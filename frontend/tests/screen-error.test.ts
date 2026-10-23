// A signed-in screen that throws (app/(app)/error.tsx): a calm page inside the shell with Try
// again and the error's words under Details, never Next's bare "This page couldn't load".

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ScreenError from "@/app/(app)/error";
import GlobalError from "@/app/global-error";

const render = (error: Error & { digest?: string }) =>
  renderToStaticMarkup(createElement(ScreenError, { error, retry: () => undefined }));

describe("the screen error page", () => {
  it("says what happened, offers Try again and a way out, and shows the error's words", () => {
    const html = render(new Error("Cannot read properties of undefined (reading 'map')"));
    expect(html).toContain("This screen hit a problem");
    expect(html).toMatch(/<button type="button" class="btn btn-primary">Try again<\/button>/);
    expect(html).toContain('href="/documents"');
    expect(html).toContain("<summary>Details</summary>");
    expect(html).toContain("Cannot read properties of undefined (reading &#x27;map&#x27;)");
  });

  it("adds a server error's id, the one thing Next lets through", () => {
    const error = Object.assign(new Error("An error occurred in the Server Components render."), { digest: "4126" });
    expect(render(error)).toContain("(4126)");
  });
});

// A throw in the shell itself (app/global-error.tsx): the same, as a whole page of its own.
describe("the whole-app error page", () => {
  it("is a document of its own with Try again, a way out and the error's words", () => {
    const html = renderToStaticMarkup(
      createElement(GlobalError, { error: new Error("UploadsProvider failed"), retry: () => undefined }),
    );
    expect(html).toMatch(/^<html lang="en"><head><title>Something went wrong · Kaagaz<\/title><\/head><body><style>/);
    expect(html).toContain("<h1>Kaagaz hit a problem</h1>");
    expect(html).toContain('<button type="button">Try again</button>');
    expect(html).toContain('href="/documents"');
    expect(html).toContain("UploadsProvider failed");
  });
});
