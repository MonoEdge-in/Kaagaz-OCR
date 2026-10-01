// A small cache for the viewer's PDFs (pdfDocuments.ts), kept pure so vitest
// can prove it. Each value counts its holders: a screen holds what it shows
// and releases it when it goes. When a new value needs room beyond `keep`,
// the least recently used values that nothing holds are dropped (drop()
// frees them). A held value is never dropped, so while more than `keep` are
// on screen the cache is over its size until they are released.
//
// Room is made once the current work is done (a microtask), not at once: a
// screen that lets go and holds again in one go (an effect run again on a
// resize, or by React's StrictMode) keeps what it shows.

export class LruCache<V> {
  // Oldest first: a Map keeps the order its keys were set in.
  private readonly entries = new Map<string, { value: V; holders: number }>();

  constructor(
    private readonly keep: number,
    private readonly drop: (value: V) => void,
  ) {}

  /** The value for key, made with make() when it is not kept, held until release(). */
  hold(key: string, make: () => V): V {
    const entry = this.entries.get(key) ?? { value: make(), holders: 0 };
    entry.holders += 1;
    this.entries.delete(key);
    this.entries.set(key, entry); // now the most recently used
    queueMicrotask(() => this.trim());
    return entry.value;
  }

  /** One holder lets go of the value it was given. */
  release(key: string, value: V): void {
    const entry = this.entries.get(key);
    if (entry && entry.value === value && entry.holders > 0) entry.holders -= 1;
  }

  /** Stop keeping this value (it failed to load), so the next hold makes it again. */
  forget(key: string, value: V): void {
    if (this.entries.get(key)?.value === value) this.entries.delete(key);
  }

  private trim(): void {
    for (const [key, entry] of this.entries) {
      if (this.entries.size <= this.keep) return;
      if (entry.holders > 0) continue;
      this.entries.delete(key);
      this.drop(entry.value);
    }
  }
}
