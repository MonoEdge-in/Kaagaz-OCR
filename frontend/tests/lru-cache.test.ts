// Critical #5: the viewer keeps the PDFs of the last few documents only. A
// PDF on screen (held) is never destroyed; the least recently used one that
// nothing holds goes first.

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

describe("LruCache", () => {
  it("makes each value once while it is kept", () => {
    const { lru, hold, made } = cache();
    expect(hold("a")).toBe("pdf a");
    lru.release("a", "pdf a");
    expect(hold("a")).toBe("pdf a");
    expect(made).toEqual(["a"]);
  });

  it("drops the least recently used value nothing holds, beyond the limit", () => {
    const { lru, hold, dropped } = cache();
    for (const key of ["a", "b"]) lru.release(key, hold(key));
    lru.release("a", hold("a")); // a is now the most recent
    lru.release("c", hold("c"));
    expect(dropped).toEqual(["pdf b"]);
    lru.release("d", hold("d"));
    expect(dropped).toEqual(["pdf b", "pdf a"]);
  });

  it("never drops a held value, and drops it once released and pushed out", () => {
    const { lru, hold, dropped } = cache();
    hold("a");
    hold("b");
    hold("c");
    expect(dropped).toEqual([]); // all three on screen: over the limit for now
    lru.release("a", "pdf a");
    expect(dropped).toEqual([]); // released, kept until something new needs room
    lru.release("d", hold("d"));
    expect(dropped).toEqual(["pdf a"]);
  });

  it("counts holders: a value stays held until every holder releases it", () => {
    const { lru, hold, dropped } = cache(1);
    hold("a");
    hold("a");
    lru.release("a", "pdf a");
    lru.release("b", hold("b"));
    expect(dropped).toEqual([]);
    lru.release("a", "pdf a");
    hold("c");
    expect(dropped).toEqual(["pdf a", "pdf b"]);
  });

  it("forgets a failed value without dropping it, and an old holder cannot release the new one", () => {
    const { lru, hold, dropped, made } = cache(1);
    hold("a");
    lru.forget("a", "pdf a");
    const again = lru.hold("a", () => "pdf a, second try");
    lru.release("a", "pdf a"); // the first holder lets go of the failed one
    hold("b");
    expect(again).toBe("pdf a, second try");
    expect(dropped).toEqual([]); // the second try is still held
    expect(made).toEqual(["a", "b"]);
  });
});
