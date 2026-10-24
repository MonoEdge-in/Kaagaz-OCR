// "What's this?" (plain words, 25 Oct 2026): one shared, accessible way to explain a setting
// or a feature name in one plain sentence. A real button with aria-expanded and aria-controls
// (Enter, Space and a tap all work), never a hover-only tooltip.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FEATURE_HELP, FEATURE_NAMES, SwitchList } from "@/components/operator/FeatureSwitches";
import { WhatsThis } from "@/components/ui/WhatsThis";
import { TASK_HELP, TASKS } from "@/lib/aiSettings";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("What's this?", () => {
  const html = renderToStaticMarkup(createElement(WhatsThis, { topic: "Ensemble", text: "Reads unsure words again." }));

  it("is a real button, closed at first, that names what it explains", () => {
    expect(html).toMatch(/^<span class="[^"]+"><button type="button" class="[^"]+" aria-expanded="false" aria-controls="([^"]+)">/);
    expect(html).toContain("What&#x27;s this?<span class=\"visually-hidden\"> Ensemble</span></button>");
  });

  it("controls the sentence it shows, hidden until opened", () => {
    const controls = /aria-controls="([^"]+)"/.exec(html)?.[1];
    expect(controls).toBeTruthy();
    expect(html).toContain(`<span id="${controls}" class="`);
    expect(html).toMatch(/hidden="">Reads unsure words again\.<\/span>/);
  });

  it("opens and closes on a click (so Enter and Space too), never on hover", () => {
    const source = read("components/ui/WhatsThis.tsx");
    expect(source).toContain("onClick={() => setOpen((shown) => !shown)}");
    expect(source).not.toMatch(/onMouse|onPointerEnter|onFocus|title=/);
    expect(read("components/ui/WhatsThis.module.css")).toMatch(/\.toggle \{[^}]*min-height: var\(--control-min\);/);
  });
});

describe("where it explains", () => {
  it("explains every feature switch in one plain sentence", () => {
    for (const feature of Object.keys(FEATURE_NAMES)) expect(FEATURE_HELP[feature], feature).toMatch(/^[A-Z].{20,}\.$/);
    const html = renderToStaticMarkup(
      createElement(SwitchList, { switches: [{ feature: "nlq_learning", enabled: true, organisations_using: 3 }], onFlip: () => {} }),
    );
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain(FEATURE_HELP.nlq_learning);
  });

  it("explains each AI setting an admin or member can change", () => {
    for (const task of TASKS) expect(TASK_HELP[task], task).toMatch(/^[A-Z].{20,}\.$/);
    expect(read("components/admin/AiSettingsPanel.tsx")).toContain(
      "<WhatsThis topic={TASK_NAMES[task.task]} text={TASK_HELP[task.task]} />",
    );
  });
});
