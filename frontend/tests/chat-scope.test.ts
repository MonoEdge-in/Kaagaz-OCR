import { describe, expect, it } from "vitest";
import { buildTree } from "@/components/documents/folderTree";
import {
  answeredScope,
  chooseScope,
  EVERY_DOCUMENT,
  keptScope,
  restoredScope,
  scopeAnnouncement,
  scopeBody,
  scopeFromBody,
  scopeLockedText,
  scopeText,
  sentScope,
  startScope,
} from "@/components/chat/scope";
import type { ChatScope } from "@/components/chat/scope";
import { askAboutHref, documentFromSearch } from "@/components/chat/logic";
import type { Message, ScopeBody } from "@/lib/types";

const DOC = "0b6f4c2e-8a1d-4d2b-9c3e-1f2a3b4c5d6e";
const ONE_DOCUMENT = { kind: "documents", ids: [DOC] } satisfies ChatScope;
const FOLDER = { kind: "folder", folderId: "f2", subfolders: false } satisfies ChatScope;

const message = (id: string, role: Message["role"], scope: ScopeBody | null = null): Message => ({
  id,
  role,
  text: id,
  created_at: "2026-10-02T10:00:00+05:30",
  answer: null,
  scope,
  feedback: null,
});

// The server saves every field (model_dump): the ones left out as null or false.
const saved = (fields: ScopeBody): ScopeBody => ({
  folder_id: null,
  include_subfolders: false,
  tag_ids: null,
  doc_types: null,
  document_ids: null,
  date_from: null,
  date_to: null,
  ...fields,
});

describe("the scope as POST /api/chat's body, and back", () => {
  it("sends {} for every document, a folder, or documents by id", () => {
    expect(scopeBody(EVERY_DOCUMENT)).toEqual({});
    expect(scopeBody({ ...FOLDER, subfolders: true })).toEqual({ folder_id: "f2", include_subfolders: true });
    expect(scopeBody(ONE_DOCUMENT)).toEqual({ document_ids: [DOC] });
  });

  it("reads a saved body back into the same choice", () => {
    expect(scopeFromBody(null)).toEqual(EVERY_DOCUMENT);
    expect(scopeFromBody(saved({}))).toEqual(EVERY_DOCUMENT);
    expect(scopeFromBody(saved({ folder_id: "f2" }))).toEqual(FOLDER);
    expect(scopeFromBody(saved({ document_ids: [DOC] }))).toEqual(ONE_DOCUMENT);
    // An empty list matches nothing on the server; it is never read as every document.
    expect(scopeFromBody(saved({ document_ids: [] }))).toEqual({ kind: "documents", ids: [] });
  });

  it("keeps a scope with tags, types or dates exactly as it was saved", () => {
    const body: ScopeBody = { folder_id: "f1", tag_ids: ["t1"] };
    expect(scopeFromBody(body)).toEqual({ kind: "other", body });
    expect(scopeBody(scopeFromBody(body))).toBe(body);
  });
});

describe("when a question sends its scope", () => {
  const followUp = answeredScope(startScope(DOC), message("q1", "user", saved({ document_ids: [DOC] })));

  it("sends nothing from a new chat left on every document", () => {
    expect(sentScope(startScope(null))).toBeUndefined();
  });

  it("sends the one document that Ask about this document named", () => {
    expect(sentScope(startScope(DOC))).toEqual({ document_ids: [DOC] });
  });

  it("sends nothing on a follow-up, so the conversation keeps its scope", () => {
    expect(followUp.scope).toEqual(ONE_DOCUMENT);
    expect(sentScope(followUp)).toBeUndefined();
  });

  it("sends a changed scope, widening back to every document too, and still offers the document", () => {
    const widened = chooseScope(followUp, EVERY_DOCUMENT);
    expect(sentScope(widened)).toEqual({});
    expect(widened.documents).toEqual([DOC]);
  });

  it("shows every document after an answer whose question had no scope", () => {
    expect(answeredScope(startScope(null), message("q1", "user")).scope).toEqual(EVERY_DOCUMENT);
  });
});

describe("a reopened conversation", () => {
  it("shows the scope its newest question used, and sends nothing", () => {
    const state = restoredScope([
      message("q1", "user", saved({ document_ids: [DOC] })),
      message("a1", "assistant"),
      message("q2", "user", saved({ folder_id: "f2" })),
      message("a2", "assistant"),
    ]);
    expect(state).toEqual({ scope: FOLDER, changed: false, documents: null, announce: true });
  });

  it("shows every document when nothing was asked or saved, and offers restored documents", () => {
    expect(restoredScope([]).scope).toEqual(EVERY_DOCUMENT);
    expect(restoredScope([message("q1", "user"), message("a1", "assistant")]).scope).toEqual(EVERY_DOCUMENT);
    expect(restoredScope([message("q1", "user", { document_ids: [DOC] })]).documents).toEqual([DOC]);
  });
});

describe("a conversation still loading, or that failed to load", () => {
  it("says the conversation keeps its own documents, and sends nothing", () => {
    const state = keptScope();
    expect(scopeText(state.scope, null, null)).toBe("This conversation's documents");
    expect(sentScope(state)).toBeUndefined();
  });

  it("counts picking All documents from it as a change, so {} widens it", () => {
    expect(sentScope(chooseScope(keptScope(), EVERY_DOCUMENT))).toEqual({});
  });

  it("says why the choices are locked while an answer or the conversation is on its way", () => {
    expect(scopeLockedText(true, false)).toBe("You can change this once the answer arrives.");
    expect(scopeLockedText(false, true)).toBe("You can change this once the conversation loads.");
    expect(scopeLockedText(false, false)).toBeNull();
  });
});

describe("what the status line says aloud", () => {
  it("says the scope after a pick or a restore, once its names are known", () => {
    const picked = chooseScope(startScope(null), FOLDER);
    // Changed on purpose (#725): the chip's words alone, which say what is chosen.
    expect(scopeAnnouncement(picked, "Ask in folder: Suppliers › 2026", false)).toBe(
      "Ask in folder: Suppliers › 2026",
    );
    expect(scopeAnnouncement(picked, "One folder", true)).toBe("");
    expect(scopeAnnouncement(restoredScope([]), "All documents", false)).toBe("All documents");
  });

  it("says nothing while a conversation opens, on a new chat, or after an answer", () => {
    expect(scopeAnnouncement(keptScope(), "This conversation's documents", false)).toBe("");
    expect(scopeAnnouncement(startScope(DOC), "1 document", false)).toBe("");
    const answered = answeredScope(chooseScope(startScope(null), FOLDER), message("q1", "user"));
    expect(scopeAnnouncement(answered, "All documents", false)).toBe("");
  });
});

describe("the scope chip's words", () => {
  const tree = buildTree([
    { id: "f1", name: "Suppliers", parent_id: null, created_at: "2026-10-01" },
    { id: "f2", name: "2026", parent_id: "f1", created_at: "2026-10-01" },
  ]);

  it("names every document, a folder by its path, and documents", () => {
    expect(scopeText(EVERY_DOCUMENT, null, null)).toBe("All documents");
    // Changed on purpose (#725): the words say exactly what is chosen, a place with "›".
    expect(scopeText(FOLDER, tree, null)).toBe("Ask in folder: Suppliers › 2026");
    expect(scopeText({ kind: "folder", folderId: "f1", subfolders: true }, tree, null)).toBe(
      "Ask in folder: Suppliers (and its subfolders)",
    );
    expect(scopeText(ONE_DOCUMENT, null, "bill.pdf")).toBe("Ask about file: bill.pdf");
    expect(scopeText({ kind: "documents", ids: ["a", "b", "c"] }, null, null)).toBe("Ask about 3 files");
  });

  it("still says something while names load, or once they are gone", () => {
    expect(scopeText(FOLDER, null, null)).toBe("One folder");
    expect(scopeText({ ...FOLDER, folderId: "gone" }, tree, null)).toBe("A folder that was deleted");
    expect(scopeText(ONE_DOCUMENT, null, null)).toBe("Ask about 1 file"); // changed on purpose (#725)
    expect(scopeText({ kind: "documents", ids: [] }, null, null)).toBe("No documents");
    expect(scopeText({ kind: "other", body: { tag_ids: ["t1"] } }, tree, null)).toBe("Filtered documents");
  });
});

describe("Ask about this document in the address", () => {
  it("reads one document id shaped like a UUID, from the address the viewer links to", () => {
    expect(documentFromSearch(new URL(askAboutHref(DOC), "http://x").search)).toBe(DOC);
    expect(documentFromSearch(`?document=${DOC.toUpperCase()}`)).toBe(DOC);
    for (const search of ["", "?document=", "?document=abc", `?document=${DOC}x`, `?d=${DOC}`]) {
      expect(documentFromSearch(search)).toBeNull();
    }
  });

  it("lets a conversation in the same address win", () => {
    expect(documentFromSearch(`?c=3fa85f64-5717-4562-b3fc-2c963f66afa6&document=${DOC}`)).toBeNull();
  });
});
