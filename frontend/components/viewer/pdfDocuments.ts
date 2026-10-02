// Keeps the PDFs of the last few documents shown (KEPT), so moving between
// pages, or showing several source cards from one document, fetches the file
// once. Each PdfPage holds its document while it is on screen and releases it
// when it goes; beyond KEPT, the least recently used PDF that nothing holds
// is destroyed, which frees its memory and its PDF.js worker (lruCache.ts).
// PDF.js is imported here on first use only, in the browser: it needs browser
// APIs and must never load during server rendering. The upload queue also
// counts a PDF's pages here before sending it (countPdfPages).

import type { PDFDocumentProxy } from "pdfjs-dist";
import { fileUrl } from "@/lib/api";
import { LruCache } from "./lruCache";

const KEPT = 4; // PROVISIONAL: a document and a few source cards' documents

const pdfs = new LruCache<Promise<PDFDocumentProxy>>(KEPT, (loading) => {
  loading.then((pdf) => pdf.destroy()).catch(() => undefined);
});

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

/**
 * The PDF for one document, loaded once while it is kept, and held until
 * releasePdf(documentId, it). A failed load is forgotten, so it can retry.
 */
export function holdPdf(documentId: string): Promise<PDFDocumentProxy> {
  return pdfs.hold(documentId, () => {
    const loading = open(documentId);
    loading.catch(() => pdfs.forget(documentId, loading));
    return loading;
  });
}

export function releasePdf(documentId: string, pdf: Promise<PDFDocumentProxy>): void {
  pdfs.release(documentId, pdf);
}
