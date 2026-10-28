// Review opened from a document's "Review values (N waiting)" link (/review?document_id=…)
// had no way back to that document (Tushar, 28 Oct 2026).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const QUEUE = read("components/review/ReviewQueue.tsx");

describe("Review opened for one document leads back to it", () => {
  it("links back to that document by its own address, encoded", () => {
    expect(QUEUE).toContain("href: `/documents/${encodeURIComponent(documentId)}`");
  });

  it("names the document, kept from the first list, and says 'the document' until it is known", () => {
    expect(QUEUE).toMatch(/if \(documentId && result\.items\[0\]\) setDocumentName\(result\.items\[0\]\.document_name\);/);
    expect(QUEUE).toContain('name: documentName ?? "the document"');
  });

  it("shows the way back above the heading only when Review was opened for a document", () => {
    expect(QUEUE).toMatch(/\{back \? \(\s*<Link href=\{back\.href\} className=\{styles\.back\}>[\s\S]*?Back to \$\{back\.name\}/);
    expect(QUEUE).toMatch(/const back: Back \| undefined = documentId\s*\?/);
    expect(QUEUE.match(/<Header back=\{back\} \/>/g)).toHaveLength(2);
  });

  it("goes back to the document, not to Documents, once its values are all settled", () => {
    expect(QUEUE).toMatch(/action=\{\s*back \? \(\s*<Link href=\{back\.href\} className="btn">/);
    expect(QUEUE).toContain("Go to Documents");
  });

  it("looks like the document page's own back link, from tokens only", () => {
    const rule = /\n\.back \{([^}]*)\}/.exec(css("components/review/Review.module.css"))?.[1] ?? "";
    expect(rule).toContain("display: inline-flex;");
    expect(rule).toContain("min-height: var(--h-lg);");
    expect(rule).not.toMatch(/#[0-9a-f]{3,6}\b/i);
  });
});
