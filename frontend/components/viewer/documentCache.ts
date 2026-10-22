"use client";

// Page sizes for answer previews come from getDocument. Several source cards
// often point at one document, so each document is fetched once and kept for
// the session of this tab. A failed fetch is forgotten, so it can retry.

import { getDocument } from "@/lib/api";
import type { DocumentDetail } from "@/lib/types";

const cache = new Map<string, Promise<DocumentDetail>>();

export function loadDocument(id: string): Promise<DocumentDetail> {
  const known = cache.get(id);
  if (known) return known;
  const loading = getDocument(id);
  cache.set(id, loading);
  loading.catch(() => {
    if (cache.get(id) === loading) cache.delete(id);
  });
  return loading;
}

