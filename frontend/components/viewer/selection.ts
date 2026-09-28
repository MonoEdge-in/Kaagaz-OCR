// Which page and spot the document screen opens on. A link from a source card
// or a review item carries ?page=n&region=<encoded>; anything that does not fit
// this document is ignored, and the screen opens on its first page.

import { decodeRegion, parsePageParam } from "@/lib/regions";
import type { DocumentDetail, ExtractedValue, Region } from "@/lib/types";

export interface Selection {
  pageNo: number;
  region: Region | null;
  valueId: string | null;
}

export function sameRegion(a: Region, b: Region): boolean {
  if (a.kind === "box" && b.kind === "box") {
    return a.page === b.page && a.box.every((n, i) => Math.abs(n - (b.box[i] ?? NaN)) < 1e-6);
  }
  if (a.kind === "cell" && b.kind === "cell") {
    return a.page === b.page && a.row === b.row && a.column === b.column;
  }
  return false;
}

export function initialSelection(
  detail: Pick<DocumentDetail, "pages" | "values">,
  pageParam: string | null,
  regionParam: string | null,
): Selection | null {
  const pageNumbers = detail.pages.map((page) => page.page_no);
  const decoded = decodeRegion(regionParam);
  const region = decoded && pageNumbers.includes(decoded.page) ? decoded : null;
  // The highlight must be visible, so its page wins over a different ?page=.
  const pageNo = region?.page ?? parsePageParam(pageParam, pageNumbers) ?? pageNumbers[0];
  if (pageNo === undefined) return null;
  const value = region ? detail.values.find((v) => sameRegion(v.region, region)) : undefined;
  return { pageNo, region, valueId: value?.id ?? null };
}

export function selectValue(value: ExtractedValue): Selection {
  return { pageNo: value.region.page, region: value.region, valueId: value.id };
}
