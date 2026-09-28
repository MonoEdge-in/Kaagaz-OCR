"use client";

// Page sizes for answer previews come from getDocument. Several source cards
// often point at one document, so each document is fetched once and kept for
// the session of this tab. A failed fetch is forgotten, so it can retry.

import { useEffect, useState } from "react";
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

export type DocumentState =
  | { status: "loading" }
  | { status: "ready"; document: DocumentDetail }
  | { status: "error" };

/** The cached document detail for one id. */
export function useDocumentDetail(id: string): DocumentState {
  const [state, setState] = useState<{ id: string; value: DocumentState } | null>(null);
  useEffect(() => {
    let cancelled = false;
    loadDocument(id)
      .then((document) => {
        if (!cancelled) setState({ id, value: { status: "ready", document } });
      })
      .catch(() => {
        if (!cancelled) setState({ id, value: { status: "error" } });
      });
    return () => {
      cancelled = true;
    };
  }, [id]);
  return state && state.id === id ? state.value : { status: "loading" };
}
