// PRD 7 PR 11b (#604, PR 10c): a version's Undo, or join-again's, that the move rule refuses
// (403 move_changes_access) says the server's sentence: in the upload tray's row (an alert line,
// never swallowed) and in the document page's toast (undoErrorText, batchTray.ts).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { undoErrorText } from "@/components/documents/batchTray";
import { TrayChoice } from "@/components/documents/UploadArea";
import { ApiError } from "@/lib/api";
import type { VersionSuggestion } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const REFUSED = "You can't move this here, because it would change who can see it. Ask an admin.";

let doc: FakeDocument;
let root: Root | null = null;

beforeEach(() => {
  doc = install();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
});

describe("a refused Undo", () => {
  it("in the upload tray: the server's sentence in an alert line", async () => {
    const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
    const { createRoot } = await import("react-dom/client");
    root = createRoot(container as unknown as Element);
    const chosen = {
      message: "Added as version 2 of Lease.pdf.",
      undo: () => Promise.reject(new ApiError(403, "move_changes_access", REFUSED)),
    };
    await act(async () =>
      root!.render(
        createElement(TrayChoice, {
          suggestion: { id: "s1" } as unknown as VersionSuggestion,
          chosen,
          onDone: () => undefined,
          onUndone: () => undefined,
        }),
      ),
    );
    const undo = container.all().find((element) => element.tagName === "BUTTON")!;
    await act(async () => void fire(undo, "click", { button: 0 }));
    await act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));
    const alert = container.all().find((element) => element.getAttribute("role") === "alert");
    expect(alert?.textContent).toBe(REFUSED);
  });

  it("on the document page: the toast says the server's sentence, through undoErrorText", () => {
    expect(undoErrorText(new ApiError(403, "move_changes_access", REFUSED))).toBe(REFUSED);
    expect(undoErrorText(new Error("offline"))).toBe("Could not undo it. Check your connection, then try again.");
    const page = readFileSync(join(__dirname, "../app/(app)/documents/[id]/DocumentView.tsx"), "utf8");
    expect(page).toContain("(caught: unknown) => setToast(undoErrorText(caught)),");
    expect(page).not.toContain("It could not be undone. Check your connection, then try again.");
  });
});
