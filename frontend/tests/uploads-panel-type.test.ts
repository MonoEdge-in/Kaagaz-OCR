// Issue 369 (27 Oct 2026): the Uploads panel reads in the body font (the status bar it opens
// from is mono), with mono only on a file's size; and the story prompt is said once for the
// tray, each finished file keeping a short "Add a story" link.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BatchTray, STORY_PROMPT } from "@/components/documents/UploadArea";
import { buildTree } from "@/components/documents/folderTree";
import type { UploadEntry } from "@/components/documents/uploadQueue";

const BATCH = "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5e";
const TRAY = {
  views: {},
  stored: [],
  onClearFinished: () => undefined,
  onRestore: () => undefined,
  onRetry: () => undefined,
  tree: buildTree([]),
  onUndone: () => undefined,
  canReread: true,
};

const finished = (key: string): UploadEntry => ({
  key,
  name: `${key}.pdf`,
  size: 2048,
  batchId: BATCH,
  state: { kind: "done", documentId: `doc-${key}` },
});

function rule(css: string, selector: string): string {
  const start = css.indexOf(`\n${selector} {`);
  return start < 0 ? "" : css.slice(start, css.indexOf("}", start));
}

describe("the Uploads panel", () => {
  it("sets the body font, so the status bar's mono stops at the panel", () => {
    const css = readFileSync(join(__dirname, "..", "components/documents/Documents.module.css"), "utf8");
    expect(rule(css, ".trayPanel")).toMatch(/font-family: var\(--k-font-body\);/);
  });

  it("says the story prompt once, however many files finished", () => {
    const html = renderToStaticMarkup(
      createElement(BatchTray, { ...TRAY, entries: [finished("a"), finished("b"), finished("c")] }),
    );
    expect(html.split(STORY_PROMPT)).toHaveLength(2);
    expect(html).not.toContain("so you can find this later");
    expect(html.match(/>Add a story<span class="visually-hidden"> to [abc]\.pdf<\/span>/g)).toHaveLength(3);
    expect(html).toMatch(/class="muted tabular mono [^"]*">2 KB</);
  });

  it("leaves the prompt out while no file can take a story", () => {
    const sending: UploadEntry = { ...finished("a"), state: { kind: "sending", fraction: 0.5 } };
    expect(renderToStaticMarkup(createElement(BatchTray, { ...TRAY, entries: [sending] }))).not.toContain(STORY_PROMPT);
  });
});
