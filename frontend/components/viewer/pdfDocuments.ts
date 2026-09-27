// Loads each PDF once per document id and keeps it, so moving between pages,
// or showing several source cards from one document, fetches the file once.
// PDF.js is imported here on first use only, in the browser: it needs browser
// APIs and must never load during server rendering.

import type { PDFDocumentProxy } from "pdfjs-dist";
import { fileUrl } from "@/lib/api";

const cache = new Map<string, Promise<PDFDocumentProxy>>();

async function open(documentId: string): Promise<PDFDocumentProxy> {
  const pdfjs = await import("pdfjs-dist");
  if (!pdfjs.GlobalWorkerOptions.workerSrc) {
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      "pdfjs-dist/build/pdf.worker.min.mjs",
      import.meta.url,
    ).toString();
  }
  return pdfjs.getDocument({ url: fileUrl(documentId), withCredentials: true }).promise;
}

/** The PDF for one document, loaded at most once. A failed load is forgotten, so it can retry. */
export function loadPdf(documentId: string): Promise<PDFDocumentProxy> {
  const known = cache.get(documentId);
  if (known) return known;
  const loading = open(documentId);
  cache.set(documentId, loading);
  loading.catch(() => {
    if (cache.get(documentId) === loading) cache.delete(documentId);
  });
  return loading;
}
