"use client";

// Per-page marks for the PDF viewer's one scroll of pages (#454): the page's reading problems
// and my words. Each page is kept once loaded, so a page scrolled past keeps its marks (they
// used to vanish the moment the next page became the screen's page), and the pages beside the
// screen's page are asked for ahead, so the next page's marks are there as it comes into view.
// Asked for only once the screen's page has held for SETTLE_MS: a fast scroll asks for the pages
// it stops on, never every page it passes (the API allows 120 requests a minute).

import { useEffect, useEffectEvent, useRef, useState } from "react";

export const SETTLE_MS = 150;

/** `pageNo` and the pages either side of it in `scroll` (the pages of one scroll; [] for a page at a time). */
export function pagesAround(pageNo: number | null, scroll: readonly number[]): number[] {
  if (pageNo === null) return [];
  return [pageNo, ...scroll.filter((page) => page === pageNo - 1 || page === pageNo + 1)];
}

/**
 * What `load(page)` answered for each of `pages`, asked once each under `version` (SETTLE_MS after
 * `pages` last changed). A new version starts again empty, as a reload or a retry did before. A
 * page whose load failed (rejected) is kept as `failed` says (nothing without it) and asked again
 * the next time it is wanted, not before.
 */
export function usePageCache<T>(
  version: string,
  pages: readonly number[],
  load: (page: number) => Promise<T>,
  failed?: (reason: unknown) => T,
): ReadonlyMap<number, T> {
  const [kept, setKept] = useState<{ version: string; pages: ReadonlyMap<number, T> }>(() => ({ version, pages: new Map() }));
  const asked = useRef(new Set<string>());
  const latest = useRef(version);
  const ask = useEffectEvent((page: number) => {
    const key = `${version}#${page}`;
    if (asked.current.has(key)) return;
    asked.current.add(key);
    const keep = (value: T) =>
      latest.current === version &&
      setKept((now) => ({ version, pages: new Map(now.version === version ? now.pages : []).set(page, value) }));
    load(page).then(keep, (reason: unknown) => {
      asked.current.delete(key);
      if (failed) keep(failed(reason));
    });
  });
  const wanted = pages.join(",");
  useEffect(() => {
    latest.current = version;
    if (!wanted) return;
    const timer = setTimeout(() => wanted.split(",").forEach((page) => ask(Number(page))), SETTLE_MS);
    return () => clearTimeout(timer);
  }, [version, wanted]);
  const [none] = useState<ReadonlyMap<number, T>>(() => new Map()); // until a page of this version comes
  return kept.version === version ? kept.pages : none;
}
