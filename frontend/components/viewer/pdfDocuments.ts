// Loads each PDF once per document id and keeps it, so moving between pages,
// or showing several source cards from one document, fetches the file once.
// PDF.js is imported here on first use only, in the browser: it needs browser
// APIs and must never load during server rendering. The upload queue also
// counts a PDF's pages here before sending it (countPdfPages).

import type { PDFDocumentProxy } from "pdfjs-dist";
import { fileUrl } from "@/lib/api";

const cache = new Map<string, Promise<PDFDocumentProxy>>();

async function pdfjsLib() {
  const pdfjs = await import("pdfjs-dist");
  if (!pdfjs.GlobalWorkerOptions.workerSrc) {
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      "pdfjs-dist/build/pdf.worker.min.mjs",
      import.meta.url,
    ).toString();
  }
  return pdfjs;
}

async function open(documentId: string): Promise<PDFDocumentProxy> {
  const pdfjs = await pdfjsLib();
  return pdfjs.getDocument({ url: fileUrl(documentId), withCredentials: true }).promise;
}

/** How many pages a PDF file has, from its bytes. Throws when PDF.js cannot open it. */
export async function countPdfPages(data: ArrayBuffer): Promise<number> {
  const pdfjs = await pdfjsLib();
  const task = pdfjs.getDocument({ data });
  try {
    return (await task.promise).numPages;
  } finally {
    await task.destroy();
  }
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
