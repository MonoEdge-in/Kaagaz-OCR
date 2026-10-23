// The PDF viewer (SoW 19 Oct 2026: extend-hq/ui's viewer for PDFs). Each guard reads the source
// or the config, so a change that brings a found bug back fails here: the engine fetched from a
// CDN, a Download or Print that skips the backend's own rules, page sync that drops the screen's
// choice, or the viewer's Tailwind resetting the rest of the app.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { getPathMatch } from "next/dist/shared/lib/router/utils/path-match";
import { describe, expect, it } from "vitest";
import nextConfig from "@/next.config";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

async function cspFor(path: string): Promise<string> {
  for (const rule of (await nextConfig.headers?.()) ?? []) {
    const match = getPathMatch(rule.source, { strict: true, removeUnnamedParams: true });
    if (match(path) === false) continue;
    const csp = rule.headers.find(({ key }) => key.startsWith("Content-Security-Policy"));
    if (csp) return csp.value;
  }
  return "";
}

describe("PDF viewer", () => {
  const wrapper = read("components/viewer/PdfDocumentViewer.tsx");

  it("loads the PDF engine from this app, never a CDN", () => {
    const engine = read("components/extend/lib/pdf-thumbnail-utils.ts");
    expect(engine).toContain('new URL("@embedpdf/pdfium/pdfium.wasm", import.meta.url)');
    expect(engine.replace(/^\/\/.*$/gm, "")).not.toMatch(/https?:\/\//); // code, not comments
  });

  it("is allowed by the content security policy: WebAssembly and Print's blob: frame", async () => {
    const csp = await cspFor("/documents/d-1");
    expect(csp).toMatch(/script-src [^;]*'wasm-unsafe-eval'/);
    expect(csp).not.toMatch(/script-src [^;]*'unsafe-eval'/);
    expect(csp).toMatch(/frame-src 'self' blob:/);
  });

  it("downloads through the screen's logged address, not the viewer's own fetch", () => {
    expect(wrapper).toContain("showDownload={false}");
    expect(wrapper).toMatch(/<a href=\{download\} download/);
    expect(read("app/(app)/documents/[id]/DocumentView.tsx")).toMatch(/download=\{download\}/);
  });

  it("prints from a blob: copy, as the file address answers as an attachment", () => {
    expect(wrapper).toContain("URL.createObjectURL");
    expect(wrapper).not.toMatch(/frame\.src = fileUrl/);
  });

  it("tells the screen only its own page changes, never while steered", () => {
    expect(wrapper).toMatch(/const pageChanged = useCallback\([\s\S]*?\}, \[\]\);/);
    expect(wrapper).toContain("steeringUntil.current");
  });

  it("keeps Tailwind's reset out of the app", () => {
    const css = read("components/extend/extend.css");
    expect(css).not.toMatch(/@import "tailwindcss(\/preflight\.css)?"/);
    expect(css).toContain('@import "tailwindcss/utilities.css" source(none)');
  });
});
