// Issue 368 (27 Oct 2026): with nothing to say, the status bar takes no space. The bar is 0
// high while no child of it has content (app/globals.css, `.app-statusbar > :not(:empty)`),
// so the uploads line, there on every screen for its live region, must render empty when idle.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { statusBarText } from "@/components/documents/batchTray";
import { LineText } from "@/components/documents/UploadArea";

describe("the uploads line in the status bar", () => {
  it("is an empty element with no uploads, so the bar collapses", () => {
    const idle = statusBarText([], {}, 0);
    expect(idle).toBe("");
    expect(renderToStaticMarkup(createElement("p", { "aria-live": "polite" }, createElement(LineText, { text: idle })))).toBe(
      '<p aria-live="polite"></p>',
    );
  });

  it("says its words, in full in the tooltip, once there are some", () => {
    expect(renderToStaticMarkup(createElement(LineText, { text: "Uploading deed.pdf" }))).toBe(
      '<span title="Uploading deed.pdf">Uploading deed.pdf</span>',
    );
  });
});
