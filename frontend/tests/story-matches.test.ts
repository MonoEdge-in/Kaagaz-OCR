import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getStoryMatches, query } from "@/lib/api";
import {
  findStoryMatches,
  latestOnly,
  MAX_STORY_CARDS,
  storyCards,
  storyMatchesFrom,
  storyMatchParams,
  storyStatusText,
} from "@/lib/storyMatches";
import type { StoryMatch } from "@/lib/types";

const A = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";
const B = "b1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";
const C = "c1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";
const D = "d1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";
const FOLDER = "0b4f7a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b";

function match(id: string, band: StoryMatch["band"], name = `doc-${id.slice(0, 1)}.pdf`): StoryMatch {
  return { document_id: id, display_name: name, doc_type: "land_record", band };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("storyMatchParams", () => {
  it("sends the trimmed question, cut at 1,000 characters, and nothing else without a scope", () => {
    expect(query(storyMatchParams("  Daregaon jamin 7/12  ")!)).toBe("?q=Daregaon+jamin+7%2F12");
    expect(storyMatchParams("x".repeat(1200))?.q).toHaveLength(1000);
  });

  it("asks nothing for an empty question or a scope that matches nothing", () => {
    expect(storyMatchParams("   ")).toBeNull();
    expect(storyMatchParams("loan", { tag_ids: [] })).toBeNull();
    expect(storyMatchParams("loan", { document_ids: [] })).toBeNull();
  });

  it("sends the scope fields by the API's names; include_subfolders only with a folder", () => {
    const params = storyMatchParams("loan", {
      folder_id: FOLDER,
      include_subfolders: true,
      tag_ids: [A, B],
      doc_types: ["land_record"],
      date_from: "2018-01-01",
      date_to: "2018-12-31",
    });
    expect(query(params!)).toBe(
      `?q=loan&folder_id=${FOLDER}&include_subfolders=true&tag_ids=${A}&tag_ids=${B}` +
        "&doc_types=land_record&date_from=2018-01-01&date_to=2018-12-31",
    );
    expect(storyMatchParams("loan", { include_subfolders: true })?.include_subfolders).toBeUndefined();
  });

  it("is a plain GET of /api/me/story-matches with the session cookie and no CSRF header", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify({ story_matches: [] }), { status: 200 });
    });
    const controller = new AbortController();
    await getStoryMatches(storyMatchParams("papa loan")!, controller.signal);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toBe("/api/me/story-matches?q=papa+loan");
    expect(calls[0]!.init.method).toBe("GET");
    expect(calls[0]!.init.credentials).toBe("same-origin");
    expect(calls[0]!.init.headers).toEqual({});
    expect(calls[0]!.init.signal).toBe(controller.signal);
  });
});

describe("storyMatchesFrom", () => {
  it("is empty for anything but a list", () => {
    for (const raw of [undefined, null, "match", 3, { story_matches: [] }]) {
      expect(storyMatchesFrom(raw)).toEqual([]);
    }
  });

  it("drops malformed items, unknown bands and repeats; an unknown type becomes null", () => {
    const raw = [
      null,
      "x",
      { document_id: "../admin", display_name: "a", doc_type: null, band: "match" },
      { document_id: A, display_name: "  ", doc_type: null, band: "match" },
      { document_id: A, display_name: "a.pdf", doc_type: null, band: "strong" },
      { document_id: A, display_name: "a.pdf", doc_type: 3, band: "maybe" },
      { document_id: A, display_name: "a.pdf", doc_type: "passport", band: "maybe", score: 0.9 },
      { document_id: A, display_name: "again.pdf", doc_type: null, band: "match" },
    ];
    expect(storyMatchesFrom(raw)).toEqual([
      { document_id: A, display_name: "a.pdf", doc_type: null, band: "maybe" },
    ]);
  });

  it("puts every match before every maybe, keeps the API's order otherwise, and keeps at most three", () => {
    const raw = [match(A, "maybe"), match(B, "match"), match(C, "maybe"), match(D, "match")];
    expect(storyMatchesFrom(raw).map((m) => m.document_id)).toEqual([B, D, A]);
    expect(storyMatchesFrom(raw)).toHaveLength(MAX_STORY_CARDS);
  });
});

describe("storyCards", () => {
  it("shows the name, the type and the band in words, linking to the document", () => {
    const [card] = storyCards([match(A, "maybe", "<b>7-12</b>.pdf")]);
    expect(card).toEqual({
      id: A,
      name: "<b>7-12</b>.pdf",
      type: "Land record",
      band: "Might match your story",
      href: `/documents/${A}`,
      lead: false,
      openText: "Open",
    });
  });

  it("leads with the one strong match, first and with Open document; it never opens by itself", () => {
    const cards = storyCards([match(A, "maybe"), match(B, "match"), match(C, "maybe")]);
    expect(cards.map((card) => [card.id, card.lead, card.openText, card.band])).toEqual([
      [B, true, "Open document", "Matches your story"],
      [A, false, "Open", "Might match your story"],
      [C, false, "Open", "Might match your story"],
    ]);
  });

  it("leads with nothing when two cards match, or none does", () => {
    expect(storyCards([match(A, "match"), match(B, "match")]).some((card) => card.lead)).toBe(false);
    expect(storyCards([match(A, "maybe")]).some((card) => card.lead)).toBe(false);
  });
});

describe("storyStatusText", () => {
  it("says how many, once, and nothing for none", () => {
    expect(storyStatusText(0)).toBe("");
    expect(storyStatusText(1)).toBe("1 document matches your stories.");
    expect(storyStatusText(3)).toBe("3 documents match your stories.");
  });
});

describe("latestOnly", () => {
  it("lets only the newest request show", () => {
    const requests = latestOnly();
    const first = requests.next();
    expect(requests.isLatest(first)).toBe(true);
    const second = requests.next();
    expect(requests.isLatest(first)).toBe(false);
    expect(requests.isLatest(second)).toBe(true);
  });
});

describe("findStoryMatches", () => {
  it("returns the checked matches of the reply", async () => {
    const get = vi.fn(async () => ({ story_matches: [match(A, "maybe"), { band: "match" }, match(B, "match")] }));
    const found = await findStoryMatches("daregaon jamin", {}, undefined, get);
    expect(found.map((m) => m.document_id)).toEqual([B, A]);
    expect(get).toHaveBeenCalledWith({ q: "daregaon jamin" }, undefined);
  });

  it("never throws and logs nothing: a failure, an abort or a strange reply is no matches", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((name) =>
      vi.spyOn(console, name).mockImplementation(() => undefined),
    );
    const failing = vi.fn(async () => {
      throw new Error("down");
    });
    expect(await findStoryMatches("loan", {}, undefined, failing)).toEqual([]);
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      init.signal?.throwIfAborted();
      return new Response("{}", { status: 200 });
    });
    expect(await findStoryMatches("loan", {}, controller.signal)).toEqual([]);
    expect(await findStoryMatches("loan", {}, undefined, vi.fn(async () => null))).toEqual([]);
    expect(await findStoryMatches("loan", {}, undefined, vi.fn(async () => "match"))).toEqual([]);
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });

  it("makes no call for an empty question", async () => {
    const get = vi.fn(async () => ({ story_matches: [] }));
    expect(await findStoryMatches("  ", {}, undefined, get)).toEqual([]);
    expect(get).not.toHaveBeenCalled();
  });
});

describe("the story box's source", () => {
  const files = ["../lib/storyMatches.ts", "../components/stories/StoryMatches.tsx"];

  it("logs nothing and never writes markup", () => {
    for (const file of files) {
      const source = readFileSync(new URL(file, import.meta.url), "utf8");
      expect(source).not.toMatch(/console\.|innerHTML|dangerouslySetInnerHTML/);
    }
  });
});
