// The chat composer as one card (chat composer, 16 Oct 2026, Tushar: "like the Claude
// chat UI"): the question on top, a toolbar inside the card with the mode and scope
// chips on the left and the answer settings and Send on the right. Each chip opens a
// native popover panel above it.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement, createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ModeSwitch } from "@/components/chat/ModeSwitch";
import { QuestionBox } from "@/components/chat/QuestionBox";
import { startScope } from "@/components/chat/scope";
import { ScopePicker } from "@/components/chat/ScopePicker";
import { MODE_TEXTS } from "@/lib/chatMode";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("the composer card", () => {
  it("puts the chips, then the settings and Send, in a toolbar inside the card under the question", () => {
    const html = renderToStaticMarkup(
      createElement(QuestionBox, {
        pending: false,
        error: null,
        onAsk: async () => true,
        inputRef: createRef<HTMLTextAreaElement>(),
        texts: MODE_TEXTS.documents,
        tools: createElement("i", null, "TOOLS"),
        settings: createElement("i", null, "SETTINGS"),
      }),
    );
    const box = html.indexOf('class="_composerBox');
    const question = html.indexOf("<textarea", box);
    const toolbar = html.indexOf('class="_toolbar', box);
    expect(box).toBeGreaterThan(-1);
    expect(question).toBeGreaterThan(box);
    expect(toolbar).toBeGreaterThan(question);
    // Changed on purpose (UI audit, 16 Oct 2026): A3, Send shows its words, so they are its name.
    const order = ["TOOLS", "SETTINGS", "<span>Send question</span>"].map((part) => html.indexOf(part, toolbar));
    expect(order.every((at) => at > toolbar)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(html).toContain('placeholder="Ask about your documents"');
    expect(html).toContain("Enter to send, Shift+Enter for a new line");
  });

  it("lets the question grow to about eight lines, then scroll", () => {
    const css = read("components/chat/Chat.module.css");
    expect(css).toMatch(/\.composerInput \{[^}]*max-height: min\(40dvh, calc\(8 \* 1\.55em/);
    expect(css).toMatch(/\.composerBox:has\(\.composerInput:focus\) \{[^}]*border-color: var\(--accent\);/);
  });
});

describe("the toolbar chips", () => {
  it("name the mode on a chip whose panel holds the two modes as radios", () => {
    const html = renderToStaticMarkup(createElement(ModeSwitch, { mode: "stories", locked: null, onChange: () => {} }));
    expect(html).toMatch(/^<button type="button"[^>]* popoverTarget="[^"]+" aria-haspopup="dialog"/);
    expect(html).toContain('<span class="visually-hidden">What to search: </span>');
    expect(html).toMatch(/<span class="[^"]*_toolText[^"]*">Search my stories<\/span>/);
    expect(html).toContain('popover="auto" role="dialog" aria-label="What to search"');
    expect(html.match(/type="radio"/g)).toHaveLength(2);
  });

  it("disable the modes while an answer is on its way, with the reason", () => {
    const html = renderToStaticMarkup(
      createElement(ModeSwitch, { mode: "documents", locked: "You can switch once the answer arrives.", onChange: () => {} }),
    );
    expect(html).toMatch(/<fieldset[^>]* disabled="" aria-describedby="[^"]+-locked"/);
    expect(html).toContain("You can switch once the answer arrives.");
  });

  it("say the scope on a chip, with no separate Asking about line or Change button", () => {
    const html = renderToStaticMarkup(createElement(ScopePicker, { state: startScope(null), locked: null, onChoose: () => {} }));
    expect(html).toContain('<span class="visually-hidden">Asking about: </span>');
    expect(html).toMatch(/<span class="[^"]*_toolText[^"]*">All documents<\/span>/);
    expect(html).toContain('popover="auto" role="dialog" aria-label="Which documents to ask about"');
    expect(html).not.toContain("Change");
    expect(html).toContain("<legend>Ask about</legend>");
  });

  it("open each panel above its chip, close on Enter instead of sending, and move focus inside", () => {
    const menu = read("components/chat/ComposerMenu.tsx");
    expect(menu).toContain('event.key === "Enter" && event.target instanceof HTMLInputElement');
    expect(menu).toContain("event.currentTarget.hidePopover();");
    expect(menu).toContain('menu.querySelector<HTMLElement>("input:checked:enabled")');
    const css = read("components/chat/Chat.module.css");
    expect(css).toMatch(/position-area: top span-right;/);
    expect(css).toMatch(/\.menuEnd \{\s*position-area: top span-left;/);
  });
});
