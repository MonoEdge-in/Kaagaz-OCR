// CitationChip, AnswerCard, Question, Working, SearchingFor, SourceCard,
// Composer, SuggestionChip and DevanagariKeys, against Components.html
// section 14 and the Ask rules in docs/ui-mapping.md (3.8).

import { createElement as h } from "react";
import { describe, expect, it } from "vitest";
import { AnswerCard, elapsedWords, Question, SearchingFor, Working } from "@/components/ui/AnswerCard/AnswerCard";
import { ButtonLink } from "@/components/ui/Button/Button";
import { CitationChip } from "@/components/ui/Citation/Citation";
import { Composer, DEVANAGARI_KEYS, DevanagariKeys, SuggestionChip } from "@/components/ui/Composer/Composer";
import { SourceCard } from "@/components/ui/SourceCard/SourceCard";
import { VersionChip } from "@/components/ui/VersionChip/VersionChip";
import { render } from "./kit-render";

const noop = () => {};

describe("CitationChip", () => {
  it("opens the page at the spot as a link", () => {
    const html = render(CitationChip, { n: 1, href: "/documents/9?page=2&region=r1", active: true });
    expect(html).toMatch(/^<a class="_cite_\w+ _active_\w+" aria-label="Source 1" aria-current="true" href="\/documents\/9\?page=2&amp;region=r1">1<\/a>$/);
  });

  it("picks its source card as a toggle button", () => {
    expect(render(CitationChip, { n: 2, onSelect: noop })).toMatch(/^<button type="button" class="_cite_\w+" aria-label="Source 2" aria-pressed="false">2<\/button>$/);
  });

  it("only labels a source when it does nothing", () => {
    expect(render(CitationChip, { n: 3 })).toMatch(/^<span class="_cite_\w+" aria-label="Source 3" role="img">3<\/span>$/);
  });
});

describe("AnswerCard", () => {
  it("shows the status and where it came from, then the answer in reading size", () => {
    const html = render(AnswerCard, { status: "verified", meta: "from 2 pages in 1 document" }, "The monthly rent is ₹45,000.");
    expect(html).toMatch(/^<article class="_answer_\w+"><div class="_head_\w+"><span class="_status_\w+ _verified_\w+">/);
    expect(html).toContain('Verified</span><span class="k-small k-muted">from 2 pages in 1 document</span></div><div class="k-read">The monthly rent is ₹45,000.</div></article>');
  });

  it.each(["review", "from", "notfound"] as const)("draws a %s answer", (status) => {
    expect(render(AnswerCard, { status }, "x")).toMatch(new RegExp(`_status_\\w+ _${status}_\\w+`));
  });
});

describe("Question, Working and SearchingFor", () => {
  it("puts the question on the right", () => {
    expect(render(Question, null, "What is the rent?")).toMatch(/^<p class="_question_\w+">What is the rent\?<\/p>$/);
  });

  it("says one line, Working on it, with the time so far, and no steps", () => {
    const html = render(Working, { seconds: 65 });
    expect(html).toMatch(/^<p class="_working_\w+" role="status"><span class="_dots_\w+" aria-hidden="true"><i><\/i><i><\/i><i><\/i><\/span>Working on it<span class="_elapsed_\w+">1:05<\/span><\/p>$/);
    expect(elapsedWords(7)).toBe("0:07");
    expect(elapsedWords(-3)).toBe("0:00");
    expect(elapsedWords(3600)).toBe("60:00");
  });

  it("says what a follow-up was searched as", () => {
    expect(render(SearchingFor, null, "rent Shinde agreement")).toMatch(/^<span class="_searching_\w+">Searching for: rent Shinde agreement<\/span>$/);
  });
});

describe("SourceCard", () => {
  it("shows the crop on paper, then the number, name, version, where and the actions", () => {
    const html = render(SourceCard, {
      n: 1,
      crop: "3. The Licensee shall pay…",
      name: "Rent Agreement Shinde.pdf",
      version: h(VersionChip, null, "v2"),
      meta: "Page 2 · Land & Legal / Agreements",
      actions: h(ButtonLink, { href: "/documents/9?page=2", size: "sm", icon: "open-spot" }, "Open page 2"),
    });
    expect(html).toMatch(/^<article class="_source_\w+" aria-label="Source 1: Rent Agreement Shinde.pdf"><div class="_crop_\w+"><div class="_paper_\w+">3. The Licensee shall pay…<\/div><\/div>/);
    expect(html).toMatch(/<span class="_title_\w+"><span class="_cite_\w+" aria-label="Source 1" role="img">1<\/span><strong class="_name_\w+">Rent Agreement Shinde.pdf<\/strong><span class="_ver_\w+">v2<\/span><\/span>/);
    expect(html).toContain("Open page 2</a></div></article>");
  });

  it("leaves the version out until citations carry it", () => {
    expect(render(SourceCard, { n: 1, crop: "x", name: "a.pdf", meta: "Page 1" })).not.toContain("_ver_");
  });
});

describe("Composer", () => {
  const props = { id: "q", label: "Your question", value: "", onChange: noop, onSend: noop, placeholder: "Ask about your documents" };

  it("is a named box with the tools on the left and Send on the right", () => {
    const html = render(Composer, { ...props, tools: "TOOLS" });
    expect(html).toMatch(/^<div class="_composer_\w+"><textarea id="q" aria-label="Your question" rows="2" placeholder="Ask about your documents"><\/textarea>/);
    expect(html).toMatch(/<div class="_tools_\w+">TOOLS<\/div><button disabled="" type="button" class="_btn_\w+ _primary_\w+"><svg/);
    expect(html).toContain("Send</button></div></div>");
  });

  it("allows Send once there is a question, and shows it asking", () => {
    expect(render(Composer, { ...props, value: "What is the rent?" })).not.toContain("disabled");
    const pending = render(Composer, { ...props, value: "What is the rent?", pending: true });
    expect(pending).toMatch(/aria-busy="true">Asking…<\/button>/);
  });
});

describe("SuggestionChip", () => {
  it("asks its question with one press", () => {
    expect(render(SuggestionChip, { onAsk: noop }, "Rent due this month")).toMatch(/^<button type="button" class="_suggest_\w+">Rent due this month<\/button>$/);
  });
});

describe("DevanagariKeys", () => {
  it("draws the handoff's 43 letters, then Space and Delete", () => {
    expect(DEVANAGARI_KEYS).toHaveLength(43);
    expect(new Set(DEVANAGARI_KEYS).size).toBe(43);
    const html = render(DevanagariKeys, { onKey: noop, onSpace: noop, onErase: noop });
    expect(html).toMatch(/^<div class="_keys_\w+" role="group" aria-label="Devanagari letters"><button type="button" lang="mr">अ<\/button>/);
    expect(html.match(/lang="mr"/g)).toHaveLength(43);
    expect(html).toMatch(/<button type="button" class="_space_\w+">Space<\/button><button type="button" class="_erase_\w+">Delete<\/button><\/div>$/);
  });
});
