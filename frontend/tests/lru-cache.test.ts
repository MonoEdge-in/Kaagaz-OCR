// Critical #5: the viewer keeps the PDFs of the last few documents only. A
// PDF on screen (held) is never destroyed; the least recently used one that
// nothing holds goes first. Room is made once the current work is done (a
// microtask), so a screen that lets go and holds again in one go keeps it.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { LruCache } from "@/components/viewer/lruCache";

function cache(keep = 2) {
  const dropped: string[] = [];
  const made: string[] = [];
  const lru = new LruCache<string>(keep, (value) => dropped.push(value));
  const hold = (key: string) =>
    lru.hold(key, () => {
      made.push(key);
      return `pdf ${key}`;
    });
  return { lru, hold, dropped, made };
}

/** Once the cache has made room (its microtasks have run). */
const settled = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("LruCache", () => {
  it("makes each value once while it is kept", () => {
    const { lru, hold, made } = cache();
    expect(hold("a")).toBe("pdf a");
    lru.release("a", "pdf a");
    expect(hold("a")).toBe("pdf a");
    expect(made).toEqual(["a"]);
  });

  it("drops the least recently used value nothing holds, beyond the limit", async () => {
    const { lru, hold, dropped } = cache();
    for (const key of ["a", "b"]) lru.release(key, hold(key));
    lru.release("a", hold("a")); // a is now the most recent
    lru.release("c", hold("c"));
    await settled();
    expect(dropped).toEqual(["pdf b"]);
    lru.release("d", hold("d"));
    await settled();
    expect(dropped).toEqual(["pdf b", "pdf a"]);
  });

  it("never drops a held value, and drops it once released and pushed out", async () => {
    const { lru, hold, dropped } = cache();
    hold("a");
    hold("b");
    hold("c");
    await settled();
    expect(dropped).toEqual([]); // all three on screen: over the limit for now
    lru.release("a", "pdf a");
    await settled();
    expect(dropped).toEqual([]); // released, kept until something new needs room
    hold("d");
    await settled();
    expect(dropped).toEqual(["pdf a"]);
  });

  it("counts holders: a value stays held until every holder releases it", async () => {
    const { lru, hold, dropped } = cache(1);
    hold("a");
    hold("a");
    lru.release("a", "pdf a");
    hold("b");
    await settled();
    expect(dropped).toEqual([]);
    lru.release("a", "pdf a");
    lru.release("b", "pdf b");
    hold("c");
    await settled();
    expect(dropped).toEqual(["pdf a", "pdf b"]);
  });

  it("forgets a failed value without dropping it, and an old holder cannot release the new one", async () => {
    const { lru, hold, dropped, made } = cache(1);
    hold("a");
    lru.forget("a", "pdf a");
    const again = lru.hold("a", () => "pdf a, second try");
    lru.release("a", "pdf a"); // the first holder lets go of the failed one
    hold("b");
    await settled();
    expect(again).toBe("pdf a, second try");
    expect(dropped).toEqual([]); // the second try is still held
    expect(made).toEqual(["a", "b"]);
  });

  // An effect run again (a resize, React's StrictMode) releases every card on
  // screen and holds it again in one go: none of them may be destroyed. The
  // review queue shows 20 crop cards at once.
  it("drops nothing when every holder lets go and holds again in one go", async () => {
    const { lru, hold, dropped, made } = cache(2);
    for (const key of ["a", "b", "c"]) hold(key);
    for (const key of ["a", "b", "c"]) lru.release(key, `pdf ${key}`);
    for (const key of ["a", "b", "c"]) hold(key);
    await settled();
    expect(made).toEqual(["a", "b", "c"]);
    expect(dropped).toEqual([]);
  });
});

describe("PdfPage", () => {
  // Held per document, not per drawing: a new size or page redraws, and only
  // a new document lets go of the old one.
  it("holds its document in an effect of its own, keyed on the document only", () => {
    const page = readFileSync(join(__dirname, "..", "components", "viewer", "PdfPage.tsx"), "utf8");
    expect(page).toMatch(/const held = holdPdf\(documentId\);[^}]*releasePdf\(documentId, held\)[^}]*\}, \[documentId\]\);/);
    expect(page.match(/holdPdf\(/g)).toHaveLength(1);
  });
});
