// Dynamic and more than one reader (features 26 and 27, 15 Oct 2026): "Read with" puts
// Dynamic first when the server offers it, adds "More than one reader…" when the
// organisation may use it, ticks 2 or 3 readers by name (the rest disabled, saying why),
// tags the main reader and sends the reading whole; and a value's vote in plain words
// ("2 of 3 readers read …"), with no model name and no money.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  ENSEMBLE,
  READERS_CAP,
  agentDisabled,
  agentsHint,
  mainReader,
  offersChoice,
  readAgainReading,
  readerOptions,
  readingToSend,
  startAgents,
  toggleAgent,
} from "@/components/documents/readers";
import { ReaderSelect } from "@/components/documents/UploadArea";
import { REASON_FILTERS } from "@/components/review/review";
import { reasonText, voteText } from "@/components/viewer/labels";
import type { EnsembleOffer, UploadReader } from "@/lib/types";

const dynamic: UploadReader = {
  id: "dynamic",
  title: "Dynamic",
  hint: "Picks the right reading for each page.",
  available: true,
  reason: null,
  default: true,
};
const standard: UploadReader = {
  id: "google/gemini-3.8-flash",
  title: "Standard",
  hint: "Right for most documents.",
  available: true,
  reason: null,
  default: false,
};
const careful: UploadReader = {
  id: "google/gemini-3.1-pro-preview",
  title: "Careful",
  hint: "Takes more care with faint, handwritten or crowded pages.",
  available: true,
  reason: null,
  default: false,
};
const READERS = [dynamic, standard, careful];
const ENSEMBLE_OFFER: EnsembleOffer = {
  readers: [
    { id: "google/gemini-3.8-flash", name: "Gemini 3.8 Flash" },
    { id: "anthropic/claude-sonnet-5.5", name: "Sonnet 5.5" },
    { id: "google/gemini-3.1-pro-preview", name: "Gemini 3.1 Pro" },
    { id: "google/gemini-3.9-lite", name: "Gemini 3.9 Lite" },
  ],
  max: 3,
  main: "google/gemini-3.8-flash",
};
const [FLASH, SONNET, PRO, LITE] = ENSEMBLE_OFFER.readers.map((reader) => reader.id) as [string, string, string, string];

describe("Read with: Dynamic", () => {
  it("is the default and is sent as nothing; another pick is sent as its model", () => {
    expect(readingToSend(READERS, null, null, [])).toBeUndefined();
    expect(readingToSend(READERS, null, "dynamic", [])).toBeUndefined();
    expect(readingToSend(READERS, null, careful.id, [])).toEqual({ read_model: careful.id });
    expect(readingToSend(READERS, null, standard.id, [])).toEqual({ read_model: standard.id });
  });

  it("is sent as read_mode when picked for a Read again", () => {
    expect(readAgainReading(READERS, null, "dynamic", [])).toEqual({ read_mode: "dynamic" });
    expect(readAgainReading(READERS, null, null, [])).toBeUndefined(); // keeps the document's reading
  });
});

describe("Read with: more than one reader", () => {
  it("is an option only when the server offers it, and makes a choice of one reading", () => {
    expect(readerOptions([standard], ENSEMBLE_OFFER).map((option) => option.label)).toEqual([
      "Standard",
      "More than one reader…",
    ]);
    expect(readerOptions([standard], null).map((option) => option.id)).toEqual([standard.id]);
    expect(offersChoice([standard], ENSEMBLE_OFFER)).toBe(true);
    expect(offersChoice([standard], null)).toBe(false);
  });

  it("starts with the main reader ticked, needs 2, and keeps the server's order", () => {
    const start = startAgents(ENSEMBLE_OFFER);
    expect(start).toEqual([FLASH]);
    expect(readingToSend(READERS, ENSEMBLE_OFFER, ENSEMBLE, start)).toBeUndefined(); // one is not enough
    expect(agentsHint(ENSEMBLE_OFFER, start)).toBe("Tick 2 to 3 readers.");
    const two = toggleAgent(ENSEMBLE_OFFER, toggleAgent(ENSEMBLE_OFFER, start, PRO), SONNET);
    expect(two).toEqual([FLASH, SONNET, PRO]);
    expect(readingToSend(READERS, ENSEMBLE_OFFER, ENSEMBLE, two)).toEqual({
      read_mode: "ensemble",
      read_agents: [FLASH, SONNET, PRO],
    });
  });

  it("never ticks more than 3: the rest are disabled with the reason", () => {
    const three = [FLASH, SONNET, PRO];
    expect(toggleAgent(ENSEMBLE_OFFER, three, LITE)).toEqual(three);
    expect(agentDisabled(ENSEMBLE_OFFER, three, LITE)).toBe(true);
    expect(agentDisabled(ENSEMBLE_OFFER, three, PRO)).toBe(false); // can be unticked
    expect(READERS_CAP).toBe("Up to 3 readers.");
  });

  it("tags the main reader as the server picks it: the organisation's when ticked, else the first", () => {
    expect(mainReader(ENSEMBLE_OFFER, [FLASH, PRO])).toBe(FLASH);
    expect(mainReader(ENSEMBLE_OFFER, [SONNET, PRO])).toBe(SONNET);
    expect(mainReader(ENSEMBLE_OFFER, [])).toBeNull();
  });

  const render = (agents: string[]) =>
    renderToStaticMarkup(
      createElement(ReaderSelect, {
        readers: [standard],
        picked: ENSEMBLE,
        onPick: () => undefined,
        ensemble: ENSEMBLE_OFFER,
        agents,
        onAgents: () => undefined,
      }),
    );

  it("shows the readers by name in a fieldset with a legend, the main one tagged", () => {
    const html = render([FLASH, PRO]);
    expect(html).toMatch(/<option value="ensemble" selected="">More than one reader…<\/option>/);
    expect(html).toMatch(/<fieldset[^>]*aria-describedby="[^"]+"/);
    expect(html).toMatch(/<legend[^>]*>Readers<\/legend>/);
    for (const reader of ENSEMBLE_OFFER.readers) expect(html).toContain(`<span>${reader.name}</span>`);
    expect(html.match(/Main reader/g)).toHaveLength(1);
    expect(html).toMatch(/Gemini 3.8 Flash<\/span><span[^>]*>Main reader/);
    expect(html).not.toMatch(/\$|₹|price|cost|budget|spend/i);
  });

  it("says why a reader can't be ticked once 3 are", () => {
    const html = render([FLASH, SONNET, PRO]);
    const disabled = html.match(/<input type="checkbox" disabled="" aria-describedby="([^"]+)"/);
    expect(disabled).not.toBeNull();
    expect(html).toContain(`id="${disabled?.[1]}" class="visually-hidden">Up to 3 readers.</p>`);
  });
});

describe("a value's vote, in plain words", () => {
  it("says how many readers read the value, and what the outvoted main reader read", () => {
    const reads = [
      { main: true, text: "27AAKFS88I2L1Z9" },
      { main: false, text: "27AAKFS8812L1Z9" },
      { main: false, text: "27AAKFS8812L1Z9" },
    ];
    expect(voteText({ value: "27AAKFS8812L1Z9", agreed: 2, readers: 3, reads })).toBe(
      "2 of 3 readers read 27AAKFS8812L1Z9. The main reader read 27AAKFS88I2L1Z9.",
    );
    const agreed = reads.map((read) => ({ ...read, text: "1,180.00" }));
    expect(voteText({ value: "1,180.00", agreed: 3, readers: 3, reads: agreed })).toBe("3 of 3 readers read 1,180.00.");
  });

  it("says what each read when there was no majority, and to check the page", () => {
    const reads = [
      { main: true, text: "1,130.00" },
      { main: false, text: "1,180.00" },
      { main: false, text: null },
    ];
    expect(voteText({ value: null, agreed: 1, readers: 3, reads })).toBe(
      "The readers did not agree: the main reader read 1,130.00; another reader read 1,180.00; another reader found nothing here. Check it against the page.",
    );
  });

  it("speaks of readers, never of two", () => {
    expect(reasonText("readers_disagree")).toBe("The readers read different values.");
    expect(REASON_FILTERS.find((choice) => choice.value === "readers_disagree")?.text).toBe("Readers disagree");
  });
});
