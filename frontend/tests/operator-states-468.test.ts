// #468 B2: the operator console never sits blank while it loads (skeleton rows, as
// Organisations has), and every change it makes says so on screen in a Toast, not only to a
// screen reader.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FeatureSwitchesView } from "@/components/operator/FeatureSwitches";
import { LoadingRows } from "@/components/operator/LoadingRows";
import { ModelsView } from "@/components/operator/ModelsView";
import { SessionTimeoutView } from "@/components/operator/SessionTimeout";
import { SpendView } from "@/components/operator/SpendView";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
const SKELETON = /class="delayed [^"]*" aria-hidden="true"><span class="skeleton [^"]*"><\/span>/;

describe("loading: skeleton rows and the words for a screen reader", () => {
  it("LoadingRows shows rows after a short delay and says what is loading", () => {
    const html = renderToStaticMarkup(createElement(LoadingRows, { what: "the models" }));
    expect(html).toContain('<p class="visually-hidden" role="status">Loading the models…</p>');
    expect(html).toMatch(SKELETON);
    expect(html.match(/class="skeleton /g)).toHaveLength(3);
  });

  it.each([
    ["Models", () => createElement(ModelsView), "Loading the models…"],
    ["Spend", () => createElement(SpendView), "Loading the spend…"],
    ["Feature switches", () => createElement(FeatureSwitchesView, { testModel: null, onTestModel: () => undefined }), "Loading the feature switches…"],
    ["Timeout", () => createElement(SessionTimeoutView), "Loading the sign-in timeout…"],
  ])("%s shows skeleton rows before its reply, not a blank page", (_name, view, words) => {
    const html = renderToStaticMarkup(view());
    expect(html).toContain(words);
    expect(html).toMatch(SKELETON);
  });
});

describe("success: a visible Toast for every change", () => {
  it.each([
    ["components/operator/OrganisationsView.tsx", 1], // disable, enable, delete, create, AI settings, rates
    ["components/operator/ModelsView.tsx", 1], // enable, disable, platform defaults
    ["components/operator/FeatureSwitches.tsx", 2], // the platform's switches, one organisation's
    ["components/operator/SessionTimeout.tsx", 2], // the platform's timeout, one organisation's
  ])("%s says each done message in a Toast", (file, toasts) => {
    const source = read(file);
    expect(source.match(/<Toast message=\{(done|toast)\} onClose=\{\(\) => set(Done|Toast)\(null\)\} \/>/g)).toHaveLength(toasts);
    // Never only in a visually hidden line any more.
    expect(source).not.toMatch(/role="status">\s*\{done/);
  });

  it("one organisation's dialogs keep their Toast inside the dialog, above its backdrop", () => {
    for (const file of ["components/operator/FeatureSwitches.tsx", "components/operator/SessionTimeout.tsx"]) {
      const source = read(file);
      expect(source, file).toMatch(/<Toast message=\{done\} onClose=\{\(\) => setDone\(null\)\} \/>\s*<\/div>\s*<\/ActionDialog>/);
    }
  });
});
