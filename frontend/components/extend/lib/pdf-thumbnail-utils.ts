// Copied from extend-hq/ui (https://github.com/extend-hq/ui), MIT licence: see LICENSE in
// this folder. Changed only to fit this app: import paths, lucide-react icons in place of the
// registry's icon placeholder, the radix-ui package, and the PDF engine served by this app.
// Only the shared engine is kept: the viewer uses nothing else here, and the registry's
// document and thumbnail caches were module-level Maps that grow for as long as the tab lives.
import type { PdfEngine } from "@embedpdf/models"

// The engine is a file of this app (hashed under /_next), never a CDN: the CSP allows only this
// origin. Made a full address when the engine starts: its worker runs from a blob: URL, where a
// path alone has nothing to resolve against.
const PDFIUM_WASM_PATH = new URL("@embedpdf/pdfium/pdfium.wasm", import.meta.url).toString()

let sharedEnginePromise: Promise<PdfEngine> | null = null

export function loadSharedPdfEngine() {
  sharedEnginePromise ??= import("@embedpdf/engines/pdfium-worker-engine").then(
    ({ createPdfiumEngine }) =>
      createPdfiumEngine(new URL(PDFIUM_WASM_PATH, window.location.href).toString(), {})
  )

  return sharedEnginePromise
}
