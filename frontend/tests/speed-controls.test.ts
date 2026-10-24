// The speed controls (package S, 0053): the Speed tab, its three fields with the server's caps,
// "using the server's setting" when nothing is saved, the range check, the confirm with a fresh
// code, the API call shapes, and a layout that fits 320px.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OPERATOR_TABS } from "@/components/operator/OperatorConsole";
import { DEPLOYED, NO_CONTAINERS, SpeedForm, SpeedView, parseSpeed, speedConfirm } from "@/components/operator/SpeedControls";
import { getSpeed, setSpeed } from "@/lib/api";
import type { Speed } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const SPEED: Speed = {
  model_rpm: { value: null, most: 600 },
  documents_at_once: { value: 2, most: 3 },
  pages_at_once: { value: null, most: 8 },
};

afterEach(() => vi.unstubAllGlobals());

describe("the Speed tab", () => {
  it("is the last tab and says where the server's caps come from", () => {
    expect(OPERATOR_TABS.at(-1)).toEqual({ id: "speed", label: "Speed" });
    const html = renderToStaticMarkup(createElement(SpeedView));
    expect(html).toContain("The most this server allows is set when it is deployed. You can only go lower.");
    expect(html).toContain("Adding or removing worker containers is not done here.");
    expect([DEPLOYED, NO_CONTAINERS].every((line) => html.includes(line))).toBe(true);
  });

  it("shows each value in plain words, its cap, and the server's setting when none is saved", () => {
    const html = renderToStaticMarkup(createElement(SpeedForm, { speed: SPEED, onSave: () => {} }));
    const labels = [...html.matchAll(/<label for="([^"]+)">([^<]+)<\/label>/g)].map((m) => [m[1], m[2]]);
    expect(labels).toEqual([
      ["speed-model_rpm", "Model requests per minute (whole platform)"],
      ["speed-documents_at_once", "Documents read at the same time"],
      ["speed-pages_at_once", "Pages of one document read at the same time"],
    ]);
    expect(html).toContain("Using the server&#x27;s setting: 600.");
    expect(html).toContain("The most this server allows is 3. Leave it empty to use the server&#x27;s setting.");
    expect(html).toContain("Using the server&#x27;s setting: 8.");
    expect(html).toMatch(/id="speed-documents_at_once"[^>]*value="2"/);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Save<\/button>/); // nothing changed yet
  });

  it("takes empty (the server's setting) or a whole number from 1 to the cap", () => {
    expect(parseSpeed("", 600)).toBeNull();
    expect(parseSpeed(" 600 ", 600)).toBe(600);
    expect(parseSpeed("1", 3)).toBe(1);
    for (const bad of ["0", "601", "1.5", "-1", "abc", "1e2"]) expect(parseSpeed(bad, 600)).toBeUndefined();
  });

  it("asks first with a fresh code", () => {
    expect(speedConfirm({ model_rpm: 100, documents_at_once: null, pages_at_once: 2 })).toMatchObject({
      title: "Save the speed settings?",
      done: "The speed settings are saved.",
      stepUp: true,
    });
  });

  it("says plainly that at 2 or fewer documents one organisation can take every place", () => {
    const few = "With 2 or fewer documents at the same time, one organisation's large upload can take every place";
    for (const documents of [1, 2]) {
      expect(speedConfirm({ model_rpm: null, documents_at_once: documents, pages_at_once: null }).effect).toContain(few);
    }
    for (const documents of [3, null]) {
      expect(speedConfirm({ model_rpm: null, documents_at_once: documents, pages_at_once: null }).effect).not.toContain(few);
    }
  });
});

describe("the layout at 320px", () => {
  it("stacks full-width fields in one column with no fixed width", () => {
    const html = renderToStaticMarkup(createElement(SpeedForm, { speed: SPEED, onSave: () => {} }));
    expect(html).not.toMatch(/style="/);
    expect(html.match(/class="field"/g)).toHaveLength(3);
    expect(html.match(/<input[^>]*class="input"/g)).toHaveLength(3);
    const rule = (css: string, selector: string) => new RegExp(`\\n${selector} \\{([^}]*)\\}`).exec(css)?.[1] ?? "";
    const operator = read("components/operator/Operator.module.css");
    expect(rule(operator, "\\.dialogForm")).toMatch(/display: flex;[\s\S]*flex-direction: column;/);
    const globals = read("app/globals.css");
    expect(rule(globals, "\\.field")).toContain("min-width: 0;");
    expect(read("components/operator/SpeedControls.tsx")).toContain("className={styles.dialogForm}");
    expect(globals).toMatch(/\.input,\nselect\.input,\ntextarea\.input \{\n {2}width: 100%;/);
  });
});

describe("the API calls", () => {
  it("read and save in the agreed shapes", async () => {
    const sent: { url: string; method: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      sent.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response(JSON.stringify(SPEED), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    await getSpeed();
    await setSpeed({ model_rpm: 100, documents_at_once: null, pages_at_once: 2 }, "123456");
    expect(sent).toEqual([
      { url: "/api/operator/speed", method: "GET", body: undefined },
      {
        url: "/api/operator/speed",
        method: "PUT",
        body: { model_rpm: 100, documents_at_once: null, pages_at_once: 2, code: "123456" },
      },
    ]);
  });
});
