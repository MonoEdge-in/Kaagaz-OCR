// Auto-filing (feature 28, council 15 Oct 2026): the org admin's switch, shown only once
// the platform allowed it; "Filed into Invoices · Undo" and "Not filed" in the uploads tray
// and on the document screen; the Activity words, with "Kaagaz" as who did it; the super
// admin's allow and stop; and no cost or technical word in any of it.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AUTOFILE_HELP,
  AUTOFILE_LABEL,
  AUTOFILE_NOT_AVAILABLE,
  describeAction,
  settingsErrorText,
  whoText,
} from "@/components/admin/logic";
import { AutofileSwitch } from "@/components/admin/SettingsPanel";
import {
  FILED_HERE_TEXT,
  NOT_FILED_TEXT,
  UNDONE_TEXT,
  filedText,
  undoErrorText,
} from "@/components/documents/batchTray";
import { buildTree } from "@/components/documents/folderTree";
import { AutofileNote, BatchTray } from "@/components/documents/UploadArea";
import { autofileConfirm } from "@/components/operator/confirms";
import { actionWords } from "@/components/operator/platform";
import { ApiError, setOrgAutofile, undoAutofile, updateSettings } from "@/lib/api";
import type { AuditEntry, DocumentSummary, Organisation } from "@/lib/types";

const INVOICES = "0b6f7c1e-2d3a-4b5c-8d9e-0f1a2b3c4d5e";
const DOC = "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5e";
const COST = /spen[dt]|cost|budget|price|paid|[$₹]|\bRs\b|\bUSD\b/i;
const TECHNICAL = /gemini|claude|openrouter|\bOCR\b|\bDPI\b|\d+\s?%|tokens?\b|\bmodel\b/i;

const doc = (autofile: DocumentSummary["autofile"], folderId: string | null = INVOICES): DocumentSummary => ({
  id: DOC,
  display_name: "bill.pdf",
  detected_type: "pdf",
  size_bytes: 1000,
  status: "ready",
  failure_code: null,
  status_message: "Ready. You can ask about it.",
  page_count: 1,
  created_at: "2026-10-15T10:00:00Z",
  uploaded_by_name: null,
  folder_id: folderId,
  tags: [],
  deleted_at: null,
  version_no: 1,
  family_id: DOC,
  can_delete: true,
  attempts: 1,
  max_attempts: 5,
  newer_version: null,
  progress: null,
  time_left: null,
  doc_type: "invoice",
  languages: [],
  pages_unreadable: 0,
  pages_uncertain: 0,
  values_to_review: 0,
  autofile,
});

const tray = (document: DocumentSummary, folders = [{ id: INVOICES, name: "Invoices", parent_id: null, created_at: "" }]) =>
  renderToStaticMarkup(
    createElement(BatchTray, {
      entries: [{ key: "a", name: "bill.pdf", size: 1000, state: { kind: "done", documentId: DOC, document } }],
      views: {},
      stored: [],
      tooMany: false,
      onClearFinished: () => undefined,
      onRestore: () => undefined,
      onRetry: () => undefined,
      tree: buildTree(folders),
      onUndone: () => undefined,
      canReread: true,
    }),
  );

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The requests made, with their method and JSON body. */
function recordFetch(): { method: string; url: string; body: unknown }[] {
  const sent: { method: string; url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    sent.push({ method: init.method ?? "GET", url, body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify({}), { status: 200 });
  });
  return sent;
}

describe("the org admin's switch", () => {
  it("is hidden until the platform allows auto-filing, then a checkbox with its plain help", () => {
    const hidden = renderToStaticMarkup(createElement(AutofileSwitch, { granted: false, on: false, onChange: () => undefined }));
    const shown = renderToStaticMarkup(createElement(AutofileSwitch, { granted: true, on: true, onChange: () => undefined }));
    expect(hidden).toBe("");
    expect(shown).toMatch(/<input type="checkbox"[^>]* checked=""/);
    expect(shown).toContain(AUTOFILE_LABEL);
    expect(shown).toContain("Files already uploaded stay where they are.");
    expect(shown).toContain("Files you put in a folder yourself are never moved.");
  });

  it("says a switch the platform has not allowed in plain words", () => {
    expect(settingsErrorText(new ApiError(409, "not_granted", "x"))).toBe(AUTOFILE_NOT_AVAILABLE);
    expect(AUTOFILE_NOT_AVAILABLE).toBe("Auto-filing isn't available for your organisation yet.");
    expect(settingsErrorText(new ApiError(400, "bad_request", "The limit can be at most 5 MB."))).toBe(
      "The limit can be at most 5 MB.",
    );
    expect(settingsErrorText(new TypeError("fetch failed"))).toMatch(/Check your connection/);
  });

  it("sends the switch only when the form has it, so leaving it out keeps it as it is", async () => {
    const sent = recordFetch();
    await updateSettings(3);
    await updateSettings(null, true);
    expect(sent.map((request) => [request.method, request.url, request.body])).toEqual([
      ["PATCH", "/api/admin/settings", { max_upload_mb: 3 }],
      ["PATCH", "/api/admin/settings", { max_upload_mb: null, autofile_on: true }],
    ]);
  });
});

describe("a filed file in the uploads tray", () => {
  it("says where it was filed, links to that folder and offers Undo", () => {
    const html = tray(doc("filed"));
    expect(html).toContain(`<a href="/documents?folder=${INVOICES}">Filed into Invoices</a> · <button`);
    expect(html).toMatch(/>Undo<span class="visually-hidden"> the filing of bill\.pdf<\/span><\/button>/);
  });

  it("names the whole path, or a folder not loaded yet", () => {
    expect(filedText("Archive / Invoices")).toBe("Filed into Archive / Invoices");
    expect(tray(doc("filed"), [])).toContain(">Filed into a folder</a>");
  });

  it("says when Kaagaz could not tell where it belongs, and nothing when it had nothing to do", () => {
    expect(tray(doc("not_filed", null))).toContain(NOT_FILED_TEXT.replace("'", "&#x27;")); // as HTML
    expect(NOT_FILED_TEXT).toBe(
      "Not filed. Kaagaz couldn't tell which folder this belongs in. You can move it to a folder.",
    );
    for (const nothing of [doc(null, null), doc(undefined, null)]) {
      const html = tray(nothing);
      expect(html).not.toMatch(/Filed into|Not filed|Undo/);
    }
  });
});

describe("Undo", () => {
  it("is offered on the document screen too, with no link: the folder is shown there", () => {
    const html = renderToStaticMarkup(
      createElement(AutofileNote, {
        document: { id: DOC, autofile: "filed" },
        name: "bill.pdf",
        filed: FILED_HERE_TEXT,
        folderHref: null,
        onUndone: () => undefined,
      }),
    );
    expect(html).toMatch(/^<p[^>]*>Filed here by Kaagaz · <button/);
    expect(html).not.toContain("<a ");
  });

  it("moves the family back with one POST, and says what happened in plain words", async () => {
    const sent = recordFetch();
    await undoAutofile(DOC);
    expect(sent).toEqual([{ method: "POST", url: `/api/documents/${DOC}/autofile/undo`, body: undefined }]);
    expect(UNDONE_TEXT).toBe("Moved back. It is not in a folder now. The folder stays.");
    const moved = new ApiError(409, "already_moved", "This document was moved since. Nothing was changed.");
    expect(undoErrorText(moved)).toBe("This document was moved since. Nothing was changed.");
    expect(undoErrorText(new TypeError("fetch failed"))).toMatch(/Check your connection/);
  });
});

describe("Activity", () => {
  const entry = (action: string, actor: string | null, details: Record<string, unknown> = {}): AuditEntry => ({
    id: 1,
    action,
    actor_name: actor,
    target_type: "document",
    target_id: null,
    details,
    created_at: "2026-10-15T10:00:00Z",
  });

  it("puts each auto-filing action in words", () => {
    expect(describeAction("document.autofile")).toBe("Kaagaz filed a document");
    expect(describeAction("document.autofile_undo")).toBe("Undid automatic filing");
    expect(describeAction("folder.create", { parent_id: null, auto: true })).toBe("Kaagaz made a folder");
    expect(describeAction("folder.create", { parent_id: null })).toBe("Added a folder");
    expect(describeAction("settings.autofile_on")).toBe("Turned auto-filing on");
    expect(describeAction("settings.autofile_off")).toBe("Turned auto-filing off");
    expect(describeAction("org.autofile_grant")).toBe("Auto-filing allowed");
    expect(describeAction("org.autofile_revoke")).toBe("Auto-filing stopped");
  });

  it("says Kaagaz did what it did by itself, and a dash for a removed person", () => {
    expect(whoText(entry("document.autofile", null))).toBe("Kaagaz");
    expect(whoText(entry("folder.create", null, { auto: true }))).toBe("Kaagaz");
    expect(whoText(entry("org.autofile_grant", null, { by: "operator" }))).toBe("Kaagaz");
    expect(whoText(entry("folder.create", null))).toBe("—"); // a person since removed
    expect(whoText(entry("document.autofile_undo", "Asha"))).toBe("Asha");
  });
});

describe("the super admin's allow and stop", () => {
  const org: Organisation = {
    id: "org-1",
    name: "Sharma Traders",
    status: "active",
    disabled_at: null,
    delete_after: null,
    created_at: "2026-09-01T00:00:00Z",
    users: { admins: 1, reviewers: 0, members: 2 },
    documents: 3,
    pages: 9,
    storage_bytes: 1000,
    last_activity_at: null,
    autofile_granted: false,
    autofile_on: false,
  };

  it("asks first, saying files already uploaded stay where they are, and stopping turns the switch off", async () => {
    const allow = autofileConfirm(org, true);
    const stop = autofileConfirm({ ...org, autofile_granted: true }, false);
    expect(`${allow.title} ${allow.effect}`).toBe(
      "Let Sharma Traders file new uploads into folders by type? Its admin can then turn auto-filing on in" +
        " Settings. Files already uploaded stay where they are.",
    );
    expect(stop.title).toBe("Stop Sharma Traders filing uploads by type?");
    expect(stop.effect).toMatch(/^Its switch turns off too\./);
    expect([allow.stepUp, stop.stepUp, allow.danger, stop.danger]).toEqual([false, false, false, true]);
    const sent = recordFetch();
    await stop.run("", "");
    await setOrgAutofile("org-1", true);
    expect(sent.map((request) => [request.method, request.url, request.body])).toEqual([
      ["PUT", "/api/operator/orgs/org-1/autofile", { granted: false }],
      ["PUT", "/api/operator/orgs/org-1/autofile", { granted: true }],
    ]);
    expect(actionWords("org.autofile_grant")).toBe("Allowed auto-filing");
    expect(actionWords("org.autofile_revoke")).toBe("Stopped auto-filing");
  });
});

describe("the words of auto-filing", () => {
  it("hold no cost and no technical word", () => {
    const texts = [
      AUTOFILE_LABEL,
      AUTOFILE_HELP,
      AUTOFILE_NOT_AVAILABLE,
      NOT_FILED_TEXT,
      UNDONE_TEXT,
      FILED_HERE_TEXT,
      filedText("Invoices"),
      ...["document.autofile", "document.autofile_undo", "settings.autofile_on", "settings.autofile_off"].map((a) =>
        describeAction(a),
      ),
      ...["org.autofile_grant", "org.autofile_revoke"].map((a) => describeAction(a)),
      describeAction("folder.create", { auto: true }),
      whoText({ action: "document.autofile", actor_name: null, details: {} }),
    ];
    for (const text of texts) {
      expect(text, text).not.toMatch(COST);
      expect(text, text).not.toMatch(TECHNICAL);
    }
  });
});
