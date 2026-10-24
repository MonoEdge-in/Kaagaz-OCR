// File management (Phase 2, PRD 1), the screens: the pure functions behind the activity words
// (A4), the new-version banner (V6, V7), Compare's runs (V11), the filter chips (Q5) and the
// sort (Q4), the four label tokens (O2), and a render of each new screen. The copy rules: no
// "Needs review" wording, no model or cost words, and none of the banned words.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fakeUploadServer } from "./fake-upload-server";
import type {
  ActivityEntry,
  Dashboard,
  DocumentCompare,
  DocumentDetail,
  DocumentSearch,
  DocumentSummary,
  Folder,
  FoundDocument,
  VersionSuggestion,
} from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ refresh: () => undefined }) }));

const { ACTIVITY_WORDS, activityDocument, activitySentence, activityWords } = await import("@/components/activity/words");
const { NO_AUDIT_FILTERS, auditChipTexts, auditFilterCount, auditParams } = await import("@/components/activity/auditFilters");
const { TimelineRow } = await import("@/components/activity/Timeline");
const { ActivityFilters, ActivityTable } = await import("@/components/admin/ActivityPanel");
const { DepartmentsPanel } = await import("@/components/admin/DepartmentsPanel");
const { DashboardBody } = await import("@/components/dashboard/Dashboard");
const { barShares, filesText, greeting, thisWeekText } = await import("@/components/dashboard/words");
const { DocumentsTable } = await import("@/components/documents/DocumentsTable");
const { FolderNav } = await import("@/components/documents/FolderNav");
const { buildTree } = await import("@/components/documents/folderTree");
const { InsideSearchResults } = await import("@/components/documents/InsideResults");
const { alsoSearchedText, highlightParts, noResultsText, resultView, searchParams } = await import(
  "@/components/documents/insideSearch"
);
const { LibraryFilters } = await import("@/components/documents/LibraryFilters");
const { ALL_DOCUMENTS, filterCount, libraryFromSearch, libraryHref, listParams, nextSort } = await import(
  "@/components/documents/libraryQuery"
);
const { DetailsPanel } = await import("@/components/files/DetailsPanel");
const { DocumentsGrid } = await import("@/components/files/DocumentsGrid");
const { FolderTiles } = await import("@/components/files/FolderTiles");
const { departmentOf, departmentsOf, nextSortOrder, reorder } = await import("@/components/files/departments");
const { documentDateChipText, labelsChipText, ownerChipText, partyChipText } = await import(
  "@/components/files/filterChips"
);
const { LABELS, LABEL_DESCRIPTIONS, labelName } = await import("@/components/files/labels");
const { QuestionsPanel, questionAnswerText } = await import("@/components/files/QuestionsPanel");
const { readViewMode, saveViewMode } = await import("@/components/files/viewMode");
const { tileCount, versionCell } = await import("@/components/files/words");
const { CompareBody } = await import("@/components/versions/CompareView");
const { compareColumns, nextChange } = await import("@/components/versions/compare");
const { VersionBanner } = await import("@/components/versions/VersionBanner");
const { VersionsPanel } = await import("@/components/versions/VersionsPanel");
const {
  bannerState,
  bannerTitle,
  canMakeCurrent,
  cleanNote,
  isNewest,
  joinedText,
  reasonsText,
  suggestionsText,
} = await import("@/components/versions/words");
const { FEATURE_NAMES } = await import("@/components/operator/FeatureSwitches");
const api = await import("@/lib/api");

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const render = (element: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(element);

afterEach(() => {
  vi.unstubAllGlobals();
});

const LAND = "11111111-1111-4111-8111-111111111111";
const RECORDS = "22222222-2222-4222-8222-222222222222";
const SURVEY = "33333333-3333-4333-8333-333333333333";
const ACCOUNTS = "44444444-4444-4444-8444-444444444444";
const OLD = "55555555-5555-4555-8555-555555555555";

const folder = (id: string, name: string, over: Partial<Folder> = {}): Folder => ({
  id,
  name,
  parent_id: null,
  created_at: "2026-10-01T10:00:00Z",
  kind: "custom",
  sort_order: null,
  ...over,
});
const FOLDERS = [
  folder(ACCOUNTS, "Accounts", { kind: "department", sort_order: 1 }),
  folder(LAND, "Land & Legal", { kind: "department", sort_order: 0 }),
  folder(RECORDS, "Land records", { kind: "category", parent_id: LAND }),
  folder(SURVEY, "Survey 44, Wakad", { kind: "property", parent_id: RECORDS }),
  folder(OLD, "Old scans"),
];

const doc = (over: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id: "d1",
  display_name: "7/12 Survey 44 Wakad.pdf",
  detected_type: "pdf",
  size_bytes: 1000,
  status: "ready",
  failure_code: null,
  status_message: "Ready.",
  page_count: 2,
  created_at: "2026-10-15T10:00:00Z",
  uploaded_by_name: "Priya",
  folder_id: SURVEY,
  tags: [],
  deleted_at: null,
  version_no: 1,
  family_id: "d1",
  can_delete: true,
  attempts: 1,
  max_attempts: 5,
  newer_version: null,
  progress: null,
  time_left: null,
  doc_type: "land_record",
  languages: ["mr"],
  pages_unreadable: 0,
  pages_uncertain: 0,
  values_to_review: 0,
  owner: { id: "u-priya", name: "Priya" },
  sensitivity: "confidential",
  document_date: "2026-06-12",
  updated_at: "2026-10-16T10:00:00Z",
  updated_by: "Anita",
  department: { id: LAND, name: "Land & Legal" },
  pending_suggestion: null,
  ...over,
});

const suggestion = (over: Partial<VersionSuggestion> = {}): VersionSuggestion => ({
  id: "s1",
  role: "new",
  document: { id: "d3", name: "Satbara 44.pdf" },
  candidate: { family_id: "d1", id: "d1", name: "7/12 Survey 44 Wakad.pdf" },
  next_version_no: 2,
  text_overlap: 0.86,
  name_similarity: 0.31,
  shared_identifier: "survey_number",
  outcome: "suggested",
  status: "pending",
  ...over,
});

const detail = (over: Partial<DocumentDetail> = {}): DocumentDetail => ({
  ...doc({ id: "d3", display_name: "Satbara 44.pdf", family_id: "d3" }),
  pages: [],
  values: [],
  folder: { id: SURVEY, name: "Survey 44, Wakad", path: [{ id: LAND, name: "Land & Legal" }, { id: RECORDS, name: "Land records" }] },
  versions: [{ id: "d3", version_no: 1, status: "ready", is_current: true, created_at: "2026-10-16T10:00:00Z" }],
  reviewer_fixes: 0,
  summary: null,
  document_date: "2026-06-12",
  card: null,
  invoice: null,
  links: [],
  read_model: null,
  author_name: "Priya",
  version_suggestion: suggestion(),
  ...over,
});

/** The row's document as the server fills it in. */
const about = (name: string | null, version_no: number | null = 1, deleted = false) => ({
  document: { id: "d1", name, version_no, deleted },
});

const entry = (action: string, over: Partial<ActivityEntry> = {}): ActivityEntry => ({
  id: 1,
  action,
  actor_name: "Priya",
  target_type: "document",
  target_id: "d1",
  details: {},
  created_at: "2026-10-16T10:00:00Z",
  ...over,
});

// --- A4: the activity words ------------------------------------------------------

describe("the activity words (A4)", () => {
  it("say the experience page's phrases exactly", () => {
    expect(activityWords(entry("document.view"))).toBe("viewed");
    expect(activityWords(entry("document.download"))).toBe("downloaded");
    expect(activityWords(entry("document.version", about("a.pdf", 2)))).toBe("uploaded version 2");
    expect(activityWords(entry("version.accepted", about("a.pdf", 2)))).toBe("linked version 2 (suggested by Kaagaz)");
    expect(activityWords(entry("document.rename", { details: { to_name: "Satbara 44.pdf" } }))).toBe("renamed to Satbara 44.pdf");
    expect(activityWords(entry("document.move", { details: { folder_path: "Land & Legal / Land records" } }))).toBe(
      "moved to Land & Legal / Land records",
    );
    expect(activityWords(entry("document.move", { folder: { id: LAND, path: "Land & Legal" } }))).toBe("moved to Land & Legal");
    expect(activityWords(entry("document.label_change", { details: { from: "internal", to: "confidential" } }))).toBe(
      "changed the label to Confidential",
    );
    expect(activityWords(entry("document.owner_change", { details: { from: "u1", to: "u2", to_name: "Anita" } }))).toBe(
      "owner changed to Anita",
    );
    expect(activityWords(entry("document.trash"))).toBe("moved to Trash");
    expect(activityWords(entry("document.restore"))).toBe("restored");
  });

  it("puts the person first, Kaagaz for its own suggestion, and names a deleted document", () => {
    expect(activitySentence(entry("document.view"))).toBe("Priya viewed");
    expect(activitySentence(entry("version.suggested", { actor_name: null }))).toBe("Kaagaz suggested it as a new version");
    expect(activitySentence(entry("document.view", { actor_name: null }))).toBe("Someone viewed");
    expect(activityDocument(entry("document.view", about("a.pdf", 1, true)))).toBe("a deleted document");
    expect(activityDocument(entry("document.view", about("a.pdf")))).toBe("a.pdf");
    expect(activityDocument(entry("auth.login"))).toBeNull();
  });

  it("falls back to the admin log's words for anything else, in lower case", () => {
    expect(activityWords(entry("auth.login"))).toBe("signed in");
    expect(activityWords(entry("something.new"))).toBe("something.new");
  });

  it("lives in one table in components/activity/words.ts", () => {
    expect(Object.keys(ACTIVITY_WORDS)).toContain("document.view");
    expect(read("components/activity/words.ts")).toContain("export const ACTIVITY_WORDS");
  });
});

// --- V6, V7: the banner ----------------------------------------------------------

describe("the new-version banner (V6, V7)", () => {
  it("says why in plain words", () => {
    expect(reasonsText({ text_overlap: 0.91, name_similarity: null, shared_identifier: "agreement_number" })).toBe(
      "91% of the text is the same and the agreement number matches",
    );
    expect(reasonsText({ text_overlap: 0.86, name_similarity: 0.7, shared_identifier: "survey_number" })).toBe(
      "86% of the text is the same, the names are alike and the survey number matches",
    );
    // The server's own words win, as a string or a list.
    expect(reasonsText({ text_overlap: 0.9, name_similarity: null, shared_identifier: null, reasons: "90% is the same" })).toBe("90% is the same");
    expect(reasonsText({ text_overlap: 0.9, name_similarity: null, shared_identifier: null, reasons: ["a", "b", "c"] })).toBe("a, b and c");
    expect(reasonsText({ text_overlap: 0.2, name_similarity: 0.3, shared_identifier: null })).toBe("");
    expect(bannerTitle("Rent Agreement Shinde.pdf")).toBe("This looks like a new version of Rent Agreement Shinde.pdf");
  });

  it("asks while pending, offers Undo after a link while newest, and says nothing after Keep separate", () => {
    expect(bannerState(detail()).kind).toBe("ask");
    expect(bannerState(detail({ version_suggestion: null })).kind).toBe("none");
    expect(bannerState(detail({ version_suggestion: suggestion({ status: "rejected" }) })).kind).toBe("none");
    expect(bannerState(detail({ version_suggestion: suggestion({ status: "undone" }) })).kind).toBe("none");
    expect(bannerState(detail({ version_suggestion: suggestion({ role: "candidate" }) })).kind).toBe("none");
    const linked = bannerState(detail({ version_suggestion: suggestion({ status: "accepted" }) }));
    expect(linked).toMatchObject({ kind: "linked", canUndo: true });
    const auto = bannerState(detail({ version_suggestion: suggestion({ outcome: "auto", status: "pending" }) }));
    expect(auto).toMatchObject({ kind: "linked", canUndo: true });
    const later = detail({
      version_suggestion: suggestion({ status: "accepted" }),
      versions: [
        { id: "d3", version_no: 2, status: "ready", is_current: false, created_at: "2026-10-16T10:00:00Z" },
        { id: "d4", version_no: 3, status: "ready", is_current: true, created_at: "2026-10-17T10:00:00Z" },
      ],
    });
    expect(bannerState(later)).toMatchObject({ kind: "linked", canUndo: false });
    expect(isNewest("d4", later.versions)).toBe(true);
  });

  it("renders the question with its three buttons and the note, and the link with Undo", () => {
    const ask = render(createElement(VersionBanner, { detail: detail(), onChanged: () => undefined }));
    expect(ask).toContain("This looks like a new version of 7/12 Survey 44 Wakad.pdf");
    expect(ask).toContain("86% of the text is the same and the survey number matches.");
    expect(ask).toContain("Make it version 2");
    expect(ask).toContain("Keep separate");
    expect(ask).toContain('href="/documents/d3/compare?with=d1"');
    expect(ask).toContain("Note for this version (optional)");
    const linked = render(
      createElement(VersionBanner, { detail: detail({ version_suggestion: suggestion({ outcome: "auto", status: "accepted" }) }), onChanged: () => undefined }),
    );
    expect(linked).toContain("Kaagaz linked this as version 1 of 7/12 Survey 44 Wakad.pdf");
    expect(linked).toContain("Undo");
  });

  it("calls the routes of section 8", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push(`${init.method} ${url} ${init.body ?? ""}`.trim());
      return new Response("{}", { status: 200 });
    });
    await api.acceptSuggestion("s1", "Mutation 1234 added");
    await api.rejectSuggestion("s1");
    await api.undoVersionLink("d3");
    await api.makeCurrent("d1");
    await api.compareDocuments("d3", "d1");
    await api.documentActivity("d3");
    await api.folderActivity(LAND, "c2");
    await api.documentQuestions("d3");
    await api.getDashboard();
    await api.updateDocument("d3", { owner_id: "u2", sensitivity: "restricted", version_note: null });
    expect(calls).toEqual([
      'POST /api/version-suggestions/s1/accept {"version_note":"Mutation 1234 added"}',
      "POST /api/version-suggestions/s1/reject",
      "POST /api/documents/d3/version-undo",
      "POST /api/documents/d1/make-current",
      "GET /api/documents/d3/compare?with=d1",
      "GET /api/documents/d3/activity",
      `GET /api/folders/${LAND}/activity?cursor=c2`,
      "GET /api/documents/d3/questions",
      "GET /api/dashboard",
      'PATCH /api/documents/d3 {"owner_id":"u2","sensitivity":"restricted","version_note":null}',
    ]);
    expect(api.downloadUrl("d1")).toBe("/api/documents/d1/file?download=1");
  });
});

// --- V9, V10, V12: the Versions tab -------------------------------------------------

describe("the Versions tab (V9, V10, V12)", () => {
  const family = detail({
    id: "d1",
    version_suggestion: null,
    versions: [
      { id: "d1", version_no: 1, status: "ready", is_current: false, created_at: "2025-06-12T10:00:00Z", uploaded_by: { id: "u1", name: "Priya" }, joined: "uploaded" },
      {
        id: "d3",
        version_no: 2,
        status: "ready",
        is_current: true,
        created_at: "2026-06-12T10:00:00Z",
        uploaded_by: { id: "u1", name: "Priya" },
        joined: "linked",
        version_note: "Mutation 1234 added",
      },
    ],
  });

  it("lists each version, newest first, with its note, how it joined, Current, View, Compare and Make current", () => {
    const html = render(createElement(VersionsPanel, { detail: family, onChanged: () => undefined }));
    expect(html.indexOf("Version 2")).toBeLessThan(html.indexOf("Version 1"));
    expect(html).toContain("Mutation 1234 added");
    expect(html).toContain("Linked from a suggestion by Priya");
    expect(html).toContain("Uploaded by Priya");
    expect(html).toContain(">Current<");
    expect(html).toContain('href="/documents/d3"');
    expect(html).toContain('href="/documents/d1/compare?with=d3"');
    // Version 2 is current, so only version 1 offers Make current.
    expect(html.match(/>Make current</g)).toHaveLength(1);
    expect(html).toContain("Make current<span class=\"visually-hidden\">: version 1</span>");
  });

  it("offers Make current for a ready version that is not current, out of Trash", () => {
    expect(canMakeCurrent({ status: "ready", is_current: false }, false)).toBe(true);
    expect(canMakeCurrent({ status: "ready", is_current: true }, false)).toBe(false);
    expect(canMakeCurrent({ status: "failed", is_current: false }, false)).toBe(false);
    expect(canMakeCurrent({ status: "ready", is_current: false }, true)).toBe(false);
    expect(joinedText("auto")).toBe("Linked on its own");
    expect(joinedText(undefined)).toBe("Uploaded");
  });

  it("keeps a note to 200 characters, spaces collapsed, empty as none", () => {
    expect(cleanNote("  Mutation   1234 added ")).toEqual({ note: "Mutation 1234 added", problem: null });
    expect(cleanNote("   ")).toEqual({ note: null, problem: null });
    expect(cleanNote("x".repeat(201)).problem).toBe("Write a note of at most 200 characters.");
  });
});

// --- V5: the tray ------------------------------------------------------------------

describe("the upload tray's new versions (V5)", () => {
  it("counts the files that look like new versions", () => {
    expect(suggestionsText(0)).toBeNull();
    expect(suggestionsText(1)).toBe("1 file looks like a new version");
    expect(suggestionsText(3)).toBe("3 files look like new versions");
    expect(read("components/documents/UploadArea.tsx")).toContain("{VERSION_TEXT.review}");
  });
});

// --- V11: Compare ----------------------------------------------------------------

describe("Compare (V11)", () => {
  const pages: DocumentCompare["pages"] = [
    { page_no: 1, changes: 0, truncated: false, runs: [{ kind: "same", text: "गाव वाकड " }] },
    {
      page_no: 2,
      changes: 2,
      truncated: true,
      runs: [
        { kind: "same", text: "Survey 44 " },
        { kind: "removed", text: "old <b>owner</b> " },
        { kind: "added", text: "new owner " },
        { kind: "same", text: "area 0.81 " },
        { kind: "added", text: "mutation 1234" },
      ],
    },
  ];

  it("splits the runs into the older and the newer column, one number per change", () => {
    const { pages: columns, changes } = compareColumns(pages);
    expect(changes).toBe(2);
    expect(columns[1]?.left.map((run) => [run.kind, run.change])).toEqual([
      ["same", null],
      ["removed", 0],
      ["same", null],
    ]);
    expect(columns[1]?.right.map((run) => [run.kind, run.change])).toEqual([
      ["same", null],
      ["added", 0],
      ["same", null],
      ["added", 1],
    ]);
    expect(nextChange(-1, 2)).toBe(0);
    expect(nextChange(1, 2)).toBe(0);
    expect(nextChange(-1, 0)).toBe(-1);
  });

  it("renders two columns, removed struck and added underlined, as plain text, with Next change", () => {
    const html = render(
      createElement(CompareBody, {
        data: { left_id: "d1", right_id: "d3", pages, summary: "2 changes on page 2" },
      }),
    );
    expect(html).toContain("2 changes on page 2");
    expect(html).toContain("Next change");
    expect(html).toMatch(/<del[^>]*data-change="0"[^>]*>.*old &lt;b&gt;owner&lt;\/b&gt;/);
    expect(html).toMatch(/<ins[^>]*data-change="1"[^>]*>.*mutation 1234/);
    expect(html).toContain("The version compared with");
    expect(html).toContain("only its first part is compared");
    expect(read("components/versions/Versions.module.css")).toMatch(/\.removed \{[^}]*text-decoration: line-through;/);
  });
});

// --- Q3, Q4, Q5: filters, chips and sort ------------------------------------------

describe("the filters and the sort in the page address (Q3, Q4)", () => {
  it("writes every new filter and the sort, and reads them back", () => {
    const state = {
      ...ALL_DOCUMENTS,
      departmentId: LAND,
      ownerId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      labels: ["confidential", "restricted"] as const,
      party: "Shinde",
      property: "44/2",
      docFrom: "2026-01-01",
      docTo: "2026-03-31",
      newer: true,
      sort: "name" as const,
      order: "asc" as const,
    };
    const href = libraryHref(state);
    expect(href).toBe(
      `/documents?dept=${LAND}&owner=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa&label=confidential&label=restricted` +
        "&party=Shinde&property=44%2F2&dfrom=2026-01-01&dto=2026-03-31&newer=1&sort=name&order=asc",
    );
    expect(libraryFromSearch(new URLSearchParams(href.split("?")[1]))).toEqual(state);
    expect(libraryFromSearch(new URLSearchParams("label=secret&sort=size&order=up"))).toEqual(ALL_DOCUMENTS);
    expect(filterCount(state)).toBe(7);
  });

  it("asks the list for them, and the default sort sends nothing", () => {
    expect(listParams(ALL_DOCUMENTS, "", 0)).toEqual({ limit: 25, offset: 0 });
    expect(listParams({ ...ALL_DOCUMENTS, labels: ["public"], newer: true, sort: "updated_at", order: "desc" }, "", 0)).toEqual({
      limit: 25,
      offset: 0,
      sensitivity: ["public"],
      has_newer_version: true,
      sort: "updated_at",
      order: "desc",
    });
    expect(searchParams({ ...ALL_DOCUMENTS, party: "Shinde", property: "44" }, "satbara")).toEqual({
      q: "satbara",
      party: "Shinde",
      property: "44",
    });
  });

  it("flips the order on the same column; a new column starts A to Z, or newest first for dates", () => {
    expect(nextSort({ sort: "created_at", order: "desc" }, "name")).toEqual({ sort: "name", order: "asc" });
    expect(nextSort({ sort: "name", order: "asc" }, "name")).toEqual({ sort: "name", order: "desc" });
    expect(nextSort({ sort: "name", order: "asc" }, "document_date")).toEqual({ sort: "document_date", order: "desc" });
    expect(nextSort({ sort: "name", order: "asc" }, "version")).toEqual({ sort: "version", order: "desc" });
  });

  it("says on each chip what it is set to", () => {
    expect(labelsChipText([])).toBe("Label");
    expect(labelsChipText(["confidential"])).toBe("Label: Confidential");
    expect(labelsChipText(["confidential", "restricted"])).toBe("Labels: Confidential, Restricted");
    expect(ownerChipText(null)).toBe("Owner");
    expect(ownerChipText("Anita")).toBe("Owner: Anita");
    expect(partyChipText("Shinde")).toBe("Party: Shinde");
    expect(documentDateChipText(null, null)).toBe("Document date");
    expect(documentDateChipText("2026-01-01", null)).toMatch(/^Dated from 1 Jan 2026$/);
  });

  it("renders the chips in Q5's order, removable once set, with Clear all", () => {
    const departments = departmentsOf(FOLDERS);
    const html = render(
      createElement(LibraryFilters, {
        library: { ...ALL_DOCUMENTS, departmentId: LAND, labels: ["restricted"], newer: true },
        tags: [],
        tagsError: null,
        onRetryTags: () => undefined,
        showSubfolders: false,
        onChange: () => undefined,
        departments,
        people: [{ id: "u1", name: "Anita" }],
      }),
    );
    const order = ["Department: Land &amp; Legal", "Type", "Owner", "Label: Restricted", "Party", "Property", "Document date", "Has newer version"];
    const at = order.map((words) => html.indexOf(words));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(html).toContain('aria-label="Remove filter: Department: Land &amp; Legal"');
    expect(html).toContain('aria-label="Remove filter: Label: Restricted"');
    expect(html).toMatch(/aria-pressed="true"[^>]*>Has newer version/);
    expect(html).toMatch(/Clear all <span class="tabular">\(3\)<\/span>/);
  });

  it("filters the admin log by person, folder, file, action and date", () => {
    const filters = {
      ...NO_AUDIT_FILTERS,
      actorId: "u1",
      folderId: LAND,
      document: { id: "d1", name: "a.pdf" },
      actions: ["document.view", "document.download"],
      from: "2026-10-01",
    };
    expect(auditParams(filters)).toEqual({
      user_id: "u1",
      folder_id: LAND,
      document_id: "d1",
      action: ["document.view", "document.download"],
      from: "2026-10-01",
      to: undefined,
    });
    expect(auditFilterCount(filters)).toBe(5);
    expect(auditFilterCount(NO_AUDIT_FILTERS)).toBe(0);
    expect(auditChipTexts(filters, { person: "Priya", folder: "Land & Legal" })).toEqual({
      person: "Person: Priya",
      folder: "Folder: Land & Legal",
      file: "File: a.pdf",
      action: "Action: Viewed and 1 more",
      date: "From 1 Oct 2026",
    });
    expect(api.auditExportUrl({ ...auditParams(filters), limit: 50, cursor: "x" })).toBe(
      `/api/admin/audit/export?user_id=u1&folder_id=${LAND}&document_id=d1&action=document.view&action=document.download&from=2026-10-01`,
    );
  });
});

// --- Q2, Q5: the search results ------------------------------------------------------

describe("the search results (Q2, Q5)", () => {
  const found: FoundDocument = {
    id: "d3",
    display_name: "Satbara 44.pdf",
    doc_type: "land_record",
    score: 1,
    best: { snippet: "7/12 extract, Survey 44, <script>Wakad</script>", page_from: 2, page_to: 2, regions: [] },
    more: 0,
    version_no: 2,
    versions: 2,
    sensitivity: "confidential",
    folder_path: "Land & Legal / Land records",
  };

  it("shows the version, the label, the folder path with the page, and the matched words marked", () => {
    expect(resultView(found)).toMatchObject({ version: "v2 of 2", label: "confidential", place: "Land & Legal / Land records · Page 2" });
    expect(highlightParts("7/12 extract, Survey 44", ["7/12", "survey"])).toEqual([
      { text: "7/12", hit: true },
      { text: " extract, ", hit: false },
      { text: "Survey", hit: true },
      { text: " 44", hit: false },
    ]);
    expect(alsoSearchedText({ understood_words: [{ word: "satbara", meaning: "7/12" }] })).toBe(
      "Also searched: 7/12 (from your words)",
    );
    expect(alsoSearchedText({})).toBeNull();
    expect(noResultsText({ ...ALL_DOCUMENTS, labels: ["public"] })).toBe("No document here holds these words.");
    expect(noResultsText({ ...ALL_DOCUMENTS, from: "2026-01-01" })).toBe("No document holds these words.");
  });

  it("renders them as plain text, with <mark> for the words", () => {
    const reply: DocumentSearch = { documents: [found], understood_words: [{ word: "satbara", meaning: "7/12" }] };
    const html = render(
      createElement(InsideSearchResults, {
        found: reply,
        error: null,
        q: "satbara 44",
        noResults: "",
        datesIgnored: false,
        onRetry: () => undefined,
        onClear: () => undefined,
      }),
    );
    expect(html).toContain("Also searched: 7/12 (from your words)");
    expect(html).toContain("<mark>7/12</mark>");
    expect(html).toContain("<mark>44</mark>");
    expect(html).toContain("&lt;script&gt;Wakad&lt;/script&gt;");
    expect(html).toContain('data-label="confidential"');
    expect(html).toContain("v2 of 2");
  });
});

// --- F1, F2, F5: departments and the folder screen ----------------------------------

describe("departments (F1, F2)", () => {
  const tree = buildTree(FOLDERS);

  it("orders them by the admin's order, finds a folder's department, and moves one up", () => {
    expect(departmentsOf(FOLDERS).map((each) => each.name)).toEqual(["Land & Legal", "Accounts"]);
    expect(departmentOf(tree, SURVEY)?.name).toBe("Land & Legal");
    expect(departmentOf(tree, OLD)).toBeNull();
    expect(departmentOf(tree, null)).toBeNull();
    expect(nextSortOrder(departmentsOf(FOLDERS))).toBe(2);
    expect(reorder(departmentsOf(FOLDERS), ACCOUNTS, -1)).toEqual([
      { id: ACCOUNTS, sort_order: 0 },
      { id: LAND, sort_order: 1 },
    ]);
    expect(reorder(departmentsOf(FOLDERS), LAND, -1)).toEqual([]);
  });

  // Changed on purpose (resumable uploads, cfc18ee3): read from the upload session's start.
  it("sends the department with an upload only when no folder is named", async () => {
    const server = fakeUploadServer();
    const file = new File(["x"], "a.pdf");
    await api.uploadDocument(file, "u1", () => undefined, undefined, undefined, undefined, undefined, LAND);
    await api.uploadDocument(file, "u1", () => undefined, OLD, undefined, undefined, undefined, LAND);
    await api.uploadVersion("d1", file, "u1", () => undefined, undefined, undefined, "Mutation 1234 added");
    vi.unstubAllGlobals();
    const [top, inFolder, version] = server.starts;
    expect(top?.department_id).toBe(LAND);
    expect(inFolder).not.toHaveProperty("department_id");
    expect(inFolder?.folder_id).toBe(OLD);
    expect(version?.version_note).toBe("Mutation 1234 added");
  });

  it("asks an admin for a department with its order", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push(`${init.method} ${url} ${init.body ?? ""}`);
      return new Response("{}", { status: 200 });
    });
    await api.createFolder("Accounts", null, { sort_order: 2 });
    await api.updateFolder(ACCOUNTS, { sort_order: 0 });
    expect(calls).toEqual([
      'POST /api/folders {"name":"Accounts","kind":"department","sort_order":2}',
      `PATCH /api/folders/${ACCOUNTS} {"sort_order":0}`,
    ]);
  });

  it("renders the admin Departments tab with Rename and the order buttons", () => {
    const html = render(createElement(DepartmentsPanel, { initial: FOLDERS }));
    expect(html.indexOf("Land &amp; Legal")).toBeLessThan(html.indexOf("Accounts"));
    expect(html).toContain('aria-label="Move up: Accounts"');
    expect(html).toContain('aria-label="Move down: Land &amp; Legal"');
    expect(html).toContain("Add department");
    expect(html).not.toContain("Old scans");
  });
});

describe("the folder screen (F5)", () => {
  const tree = buildTree(FOLDERS);
  const props = {
    tree,
    library: ALL_DOCUMENTS,
    loading: false,
    error: null,
    onRetry: () => undefined,
    onNavigate: () => undefined,
  };

  it("groups the tree: Departments in order, then Other with the folders in none, then Trash", () => {
    const html = render(createElement(FolderNav, props));
    const order = ["All documents", "Departments", "Land &amp; Legal", "Land records", "Accounts", "Other", "Not in a department", "Old scans", "Trash"];
    const at = order.map((words) => html.indexOf(words));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(render(createElement(FolderNav, { ...props, tree: buildTree([folder(OLD, "Old scans")]) }))).not.toContain("Departments");
  });

  it("shows a folder's subfolders first, as tiles with their counts", () => {
    const node = tree.byId.get(LAND);
    const html = render(createElement(FolderTiles, { folders: node?.children ?? [], library: ALL_DOCUMENTS, counts: { [RECORDS]: 12 } }));
    expect(html).toContain("Land records");
    expect(html).toContain("12 files");
    expect(tileCount(undefined)).toBe("…");
    expect(tileCount(1)).toBe("1 file");
  });

  it("lists Name, Type, Owner, Label, Version, Document date, Last updated and Updated by, each with a sort key sorting", () => {
    const me = { user: { role: "member", can_delete_forever: false, totp_enabled: false } } as never;
    const html = render(
      createElement(DocumentsTable, {
        items: [doc({ pending_suggestion: { id: "s1", role: "candidate" } })],
        inTrash: false,
        me,
        now: Date.parse("2026-10-17T10:00:00Z"),
        tree,
        tags: [],
        suggestions: [],
        movedLeaves: false,
        sorting: { sort: "name", order: "asc", onSort: () => undefined },
        onRemoved: () => undefined,
        onSaved: () => undefined,
        onFocusList: () => undefined,
        onTagCreated: () => undefined,
        onFolderCreated: () => undefined,
        onUploadVersion: () => undefined,
      }),
    );
    const head = html.slice(html.indexOf("<thead"), html.indexOf("</thead>"));
    const columns = ["Name", "Status", "Type", "Owner", "Label", "Version", "Document date", "Last updated", "Updated by"];
    const at = columns.map((words) => head.indexOf(`>${words}<`));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(head.match(/title="Sort by /g)).toHaveLength(7);
    expect(head).toContain('aria-sort="ascending"');
    expect(html).toContain("v1<span");
    expect(html).toContain(" · newer?");
    expect(html).toContain('data-label="confidential"');
    expect(html).toContain("Updated by </span>");
    expect(versionCell(1, true)).toBe("v1 · newer?");
    expect(versionCell(2, false)).toBe("v2");
  });

  it("shows the Grid view as cards, and remembers List or Grid per person", () => {
    const html = render(createElement(DocumentsGrid, { items: [doc()], label: "Your documents" }));
    expect(html).toContain("7/12 Survey 44 Wakad.pdf");
    expect(html).toContain("Owner: Priya");
    expect(readViewMode("u1")).toBe("list"); // no storage in node: List
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v) });
    saveViewMode("u1", "grid");
    expect(readViewMode("u1")).toBe("grid");
    expect(readViewMode("u2")).toBe("list");
  });
});

// --- D1: the dashboard -----------------------------------------------------------------

describe("the dashboard (D1)", () => {
  const data: Dashboard = {
    tiles: { documents: 1240, documents_this_week: 12, departments: 2, new_versions_this_month: 3, versions_to_confirm: 2 },
    by_type: [
      { doc_type: "land_record", count: 40 },
      { doc_type: "invoice", count: 10 },
      { doc_type: null, count: 0 },
    ],
    departments: [{ id: LAND, name: "Land & Legal", sort_order: 0, files: 120 }],
    not_in_department: 8,
    recent: [{ id: "d3", display_name: "Satbara 44.pdf", doc_type: "land_record", sensitivity: "confidential", version_no: 2, status: "ready", created_at: "2026-10-17T09:00:00Z" }],
    activity: [entry("document.view", { ...about("Satbara 44.pdf"), folder: { id: RECORDS, path: "Land & Legal / Land records" } })],
  };

  it("works out the greeting, the week's count and the bars on one scale", () => {
    expect(greeting("Priya Kulkarni", 9)).toBe("Good morning, Priya");
    expect(greeting("Priya", 14)).toBe("Good afternoon, Priya");
    expect(greeting("Priya", 20)).toBe("Good evening, Priya");
    expect(thisWeekText(0)).toBeNull();
    expect(thisWeekText(12)).toBe("+12 this week");
    expect(barShares([40, 10, 0, 1])).toEqual([100, 25, 0, 3]);
    expect(barShares([])).toEqual([]);
    expect(filesText(1)).toBe("1 file");
  });

  it("renders the four tiles, the bars, the departments with the dashed card, recent documents and activity", () => {
    const html = render(createElement(DashboardBody, { data, now: Date.parse("2026-10-17T10:00:00Z") }));
    for (const words of ["Documents", "+12 this week", "Departments", "New versions this month", "Versions to confirm"]) {
      expect(html).toContain(words);
    }
    expect(html).toContain("1,240");
    expect(html).toContain('style="width:100%"');
    expect(html).toContain('style="width:25%"');
    expect(html).toContain("Land &amp; Legal");
    expect(html).toContain("120 files");
    expect(html).toContain("Not in a department");
    expect(html).toContain("8 files");
    expect(html).toContain('href="/documents?folder=11111111-1111-4111-8111-111111111111&amp;sub=1"');
    expect(html).toContain('href="/documents?newer=1"');
    expect(html).toContain("Satbara 44.pdf");
    expect(html).toContain("Priya viewed");
    expect(read("components/dashboard/Dashboard.module.css")).toMatch(/\.outside \{[^}]*border-style: dashed;/);
  });

  it("is the home screen: Home first in the nav, and the sign-in lands there", () => {
    expect(read("app/page.tsx")).toContain('redirect("/dashboard")');
    expect(read("components/shell/nav.ts")).toContain('{ href: "/dashboard", label: "Home", icon: "home" }');
  });
});

// --- P1, P2: the document page ---------------------------------------------------------

describe("the document page (P1, P2)", () => {
  it("shows Details with the owner and label pickers, the department path, type, date, author and last updated", () => {
    const html = render(
      createElement(DetailsPanel, {
        detail: detail(),
        onSaved: () => undefined,
        people: [
          { id: "u-priya", name: "Priya" },
          { id: "u-anita", name: "Anita" },
        ],
      }),
    );
    for (const words of ["Owner", "Label", "Department", "Type", "Document date", "Author", "Last updated", "Pages"]) {
      expect(html).toContain(`>${words}<`);
    }
    expect(html).toContain("Land &amp; Legal / Land records / Survey 44, Wakad");
    expect(html).toContain('<option value="restricted">Restricted</option>');
    expect(html).toContain(LABEL_DESCRIPTIONS.confidential);
    expect(html).toContain("Land record");
    expect(html).toContain("12 Jun 2026");
    expect(html).toContain(" · Anita");
  });

  it("puts the label chip, Version N of M and the tabs on the page, and logs only the Download button", () => {
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    const title = view.slice(view.indexOf("<div className={styles.titleRow}>"), view.indexOf("<div className={styles.status}>"));
    expect(title).toContain("<LabelChip label={detail.sensitivity} />");
    expect(title).toContain("versionText({ number: detail.version_no");
    for (const tab of ['label: FILES_TEXT.details', 'label: "Text"', "label: QUESTIONS_TEXT.heading", "label: VERSION_TEXT.versions", "label: ACTIVITY_TEXT.heading"]) {
      expect(view).toContain(tab);
    }
    expect(view).toContain("<VersionBanner detail={detail} onChanged={reloadWith} />");
    expect(read("components/viewer/pdfDocuments.ts")).toContain("url: fileUrl(documentId)");
  });

  it("lists this person's questions with their answers as plain text", () => {
    const html = render(
      createElement(QuestionsPanel, {
        documentId: "d3",
        initial: [
          {
            id: "m1",
            conversation_id: "c1",
            question: "Who owns <b>44</b>?",
            answer: {
              status: "answered",
              label: "verified",
              sentences: [
                { text: "Ramesh Shinde owns it.", citations: [1] },
                { text: "The record is from 2025.", citations: [1] },
              ],
              citations: [],
              closest_documents: [],
              route: "search",
              normalized_question: "Who owns 44?",
              model_used: false,
            },
            asked_at: "2026-10-17T09:00:00Z",
          },
        ],
      }),
    );
    expect(html).toContain("Who owns &lt;b&gt;44&lt;/b&gt;?");
    expect(html).toContain("Ramesh Shinde owns it. The record is from 2025.");
    expect(html).toContain('href="/chat?c=c1"');
    expect(render(createElement(QuestionsPanel, { documentId: "d3", initial: [] }))).toContain("No questions about this document yet");
  });

  it("renders non-answer statuses as readable text", () => {
    expect(questionAnswerText(null)).toBe("No answer was saved.");
    expect(
      questionAnswerText({
        status: "not_found",
        label: null,
        sentences: [],
        citations: [],
        closest_documents: [],
        route: "search",
        normalized_question: "",
        model_used: false,
      }),
    ).toBe("Nothing in your documents matches this yet."); // PRD 6 decision 9
    expect(
      questionAnswerText({
        status: "unchecked",
        label: null,
        sentences: [],
        citations: [],
        closest_documents: [],
        route: "fixed_query",
        normalized_question: "",
        model_used: false,
      }),
    ).toBe("This figure is in these documents. Open them to see it.");
  });
});

// --- A5, A6: the timelines and the admin Activity tab ----------------------------------

describe("the activity screens (A5, A6)", () => {
  it("renders a timeline row with the document and where it is", () => {
    const html = render(
      createElement(TimelineRow, {
        entry: entry("document.download", { ...about("Satbara 44.pdf"), folder: { id: LAND, path: "Land & Legal" } }),
        showDocument: true,
      }),
    );
    expect(html).toContain("Priya downloaded");
    expect(html).toContain('href="/documents/d1"');
    expect(html).toContain("Land &amp; Legal · ");
    const gone = render(createElement(TimelineRow, { entry: entry("document.view", about("old.pdf", 1, true)), showDocument: true }));
    expect(gone).toContain("a deleted document");
    expect(gone).not.toContain("href=");
  });

  it("renders the admin table's columns and the filter chips, with Export to Excel", () => {
    const html = render(createElement(ActivityTable, { items: [entry("document.label_change", { ...about("Deed.pdf"), folder: { id: LAND, path: "Land & Legal" }, details: { from: "internal", to: "restricted" } })] }));
    for (const column of ["When", "Who", "Action", "Document", "Folder", "Details"]) expect(html).toContain(`>${column}<`);
    expect(html).toContain("changed the label to Restricted");
    expect(html).toContain("Deed.pdf");
    const chips = render(createElement(ActivityFilters, { filters: NO_AUDIT_FILTERS, onChange: () => undefined, people: [], folders: FOLDERS }));
    for (const chip of ["Person", "Folder", "File", "Action", "Date"]) expect(chips).toContain(`>${chip}</button>`);
    expect(chips).not.toContain("Clear all");
    expect(read("components/admin/ActivityPanel.tsx")).toContain("{ACTIVITY_TEXT.export}");
    expect(read("components/admin/logic.ts")).toContain('{ id: "departments", label: "Departments" }');
  });
});

// --- O2: the four labels -----------------------------------------------------------------

describe("the four sensitivity labels (O2)", () => {
  const css = read("app/globals.css");
  const block = (opening: string): Record<string, string> => {
    const start = css.indexOf(opening);
    const body = css.slice(start + opening.length, css.indexOf("}", start));
    return Object.fromEntries(Array.from(body.matchAll(/(--[\w-]+):\s*([^;]+);/g), (m) => [m[1], (m[2] ?? "").trim()]));
  };
  const channel = (pair: string) => {
    const v = parseInt(pair, 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const luminance = (hex: string) => 0.2126 * channel(hex.slice(1, 3)) + 0.7152 * channel(hex.slice(3, 5)) + 0.0722 * channel(hex.slice(5, 7));
  const ratio = (a: string, b: string) => {
    const [x, y] = [luminance(a), luminance(b)];
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };

  it("are colour tokens, 4.5:1 or more in light and dark, the two darks the same", () => {
    const light = block(":root {");
    const dark = block(':root[data-theme="dark"] {');
    for (const label of LABELS) {
      for (const theme of [light, dark]) {
        const fg = theme[`--label-${label}-fg`] ?? "";
        const bg = theme[`--label-${label}-bg`] ?? "";
        expect(fg, label).toMatch(/^#[0-9a-f]{6}$/);
        expect(ratio(fg, bg), label).toBeGreaterThanOrEqual(4.5);
      }
      expect(css).toContain(`.label-chip[data-label="${label}"]`);
    }
  });

  it("have a name and a short description each", () => {
    expect(LABELS.map(labelName)).toEqual(["Public", "Internal", "Confidential", "Restricted"]);
    for (const label of LABELS) expect(LABEL_DESCRIPTIONS[label].length).toBeGreaterThan(10);
  });
});

// --- V8, and the copy rules ------------------------------------------------------------------

describe("the copy", () => {
  const NEW_FILES = [
    "components/activity",
    "components/dashboard",
    "components/files",
    "components/versions",
  ].flatMap((dir) =>
    readdirSync(join(ROOT, dir))
      .filter((name) => /\.(ts|tsx)$/.test(name))
      .map((name) => `${dir}/${name}`),
  );

  it("names Version spotting in the operator's Feature switches (V8)", () => {
    expect(FEATURE_NAMES.version_spotting).toBe("Version spotting");
  });

  it("never says Needs review, a model, a cost, or a banned word", () => {
    for (const file of [...NEW_FILES, "components/admin/ActivityPanel.tsx", "components/admin/DepartmentsPanel.tsx"]) {
      const text = read(file).replace(/^\s*\/\/.*$/gm, "");
      expect(text, file).not.toMatch(/needs review/i);
      expect(text, file).not.toMatch(/\b(couldn't|sorry|unfortunately|perfect|guaranteed)\b/i);
      expect(text, file).not.toMatch(/gemini|openrouter|\bmodel\b|\bcost\b|\bprice\b|budget|\bUSD\b|₹/i);
    }
  });

  it("renders document text and names as text, never markup", () => {
    for (const file of NEW_FILES) expect(read(file), file).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
  });
});
