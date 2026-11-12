// A fake /api for the layout checks: the browser only ever talks to this app, which forwards
// /api to the backend (next.config.ts), so the page's own requests are answered here instead
// and no backend or sign-in is needed. Shapes follow lib/types.ts; every list the screens ask
// for and that is not named below comes back empty.

import type { Page, Route } from "@playwright/test";
import type { Dashboard, DocumentSummary, Folder, Me, UploadLimits } from "../lib/types";

const LAND = "11111111-1111-4111-8111-111111111111";
const FINANCE = "22222222-2222-4222-8222-222222222222";

const FOLDERS: Folder[] = [
  { id: LAND, name: "Land & Legal", parent_id: null, created_at: "2026-10-01T10:00:00Z", kind: "department", sort_order: 0 },
  { id: FINANCE, name: "Finance", parent_id: null, created_at: "2026-10-01T10:00:00Z", kind: "department", sort_order: 1 },
];

const ME: Me = {
  user: {
    id: "u-priya",
    email: "priya@example.com",
    display_name: "Priya Kulkarni",
    role: "admin",
    must_change_password: false,
    totp_enabled: true,
    two_step_turned_off: false,
    can_delete_forever: false,
    email_confirmed: true,
  },
  customer: { id: "c1", name: "Wakad Land Records" },
  session: { scope: "app" },
  providers: [],
};

const hoursAgo = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString();

const doc = (id: string, over: Partial<DocumentSummary>): DocumentSummary =>
  ({
    id,
    display_name: `${id}.pdf`,
    detected_type: "pdf",
    size_bytes: 903_270,
    status: "ready",
    failure_code: null,
    status_message: "Ready.",
    page_count: 2,
    created_at: hoursAgo(30),
    updated_at: hoursAgo(2),
    updated_by: "Anita Deshpande",
    uploaded_by_name: "Anita Deshpande",
    folder_id: LAND,
    tags: [],
    deleted_at: null,
    version_no: 1,
    family_id: id,
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
    words_unsure: 0,
    owner: { id: "u-anita", name: "Anita Deshpande" },
    sensitivity: "confidential",
    document_date: "2026-10-02",
    ...over,
  }) as DocumentSummary;

/** Every kind of row the compact list draws, with long words where they can be long. */
export const DOCUMENTS: DocumentSummary[] = [
  doc("d1", {
    display_name: "7-12 Utara Survey No 45 Wakad with mutation entries 2007 to 2026 and the sale deed.pdf",
    values_to_review: 17,
    pages_unreadable: 2,
    pending_suggestion: { id: "s1", role: "candidate" },
  } as Partial<DocumentSummary>),
  doc("d2", { display_name: "Ferfar 2187 Survey 44.pdf", version_no: 3 }),
  doc("d3", {
    display_name: "HDFC current account statement April to September 2026.pdf",
    status: "clean",
    status_message: "Reading page 2 of 3.",
    progress: { step: "reading", done: 1, total: 3 },
    doc_type: "bank_statement",
    sensitivity: "restricted",
  }),
  doc("d4", {
    display_name: "Invoice INV-2026-00431 Shree Ganesh Traders.pdf",
    doc_type: "invoice",
    sensitivity: "internal",
    folder_id: FINANCE,
    owner: { id: "u-r", name: "Rahul" },
  }),
  doc("d5", {
    display_name: "Scanned register page 12.jpg",
    detected_type: "jpeg",
    status: "failed",
    status_message: "None of this file could be read. Upload a sharper scan.",
    doc_type: null,
  }),
  doc("d6", { display_name: "Rent agreement Flat 402.pdf", doc_type: "contract", sensitivity: "public", document_date: null }),
];

const LIMITS: UploadLimits = {
  pdf_bytes: 50 * 1024 * 1024,
  image_bytes: 20 * 1024 * 1024,
  sheet_bytes: 10 * 1024 * 1024,
  max_pages: 100,
  readers: [
    { id: "standard", title: "Standard", hint: "Most documents, at a low cost.", available: true, reason: null, default: true },
    { id: "careful", title: "Careful", hint: "Faint or handwritten pages, read twice.", available: true, reason: null },
  ],
  sort_available: true,
};

const DASHBOARD: Dashboard = {
  tiles: { documents: 1240, documents_this_week: 12, departments: 2, new_versions_this_month: 3, versions_to_confirm: 4 },
  by_type: [
    { doc_type: "land_record", count: 40 },
    { doc_type: "invoice", count: 10 },
    { doc_type: "bank_statement", count: 6 },
  ],
  departments: [
    { id: LAND, name: "Land & Legal", sort_order: 0, files: 120 },
    { id: FINANCE, name: "Finance", sort_order: 1, files: 64 },
  ],
  not_in_department: 8,
  recent: DOCUMENTS.slice(0, 4).map((d) => ({
    id: d.id,
    display_name: d.display_name,
    doc_type: d.doc_type,
    sensitivity: d.sensitivity ?? "internal",
    version_no: d.version_no,
    status: d.status,
    created_at: d.created_at,
  })),
  activity: [],
};

/** Answers the page's /api requests; `documents` is what the list shows ([] for the empty state). */
export async function fakeApi(page: Page, documents: DocumentSummary[] = DOCUMENTS): Promise<void> {
  await page.route("**/api/**", (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api/, "");
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/auth/session") return json({ signed_in: true, me: ME });
    if (path === "/auth/me") return json(ME);
    if (path === "/documents") return json({ items: documents, total: documents.length });
    if (path === "/folders") return json({ items: FOLDERS });
    if (path === "/upload-limits") return json(LIMITS);
    if (path === "/dashboard") return json(DASHBOARD);
    if (path === "/review") return json({ items: [], total: 23 });
    if (path === "/people") return json({ items: [{ id: "u-anita", name: "Anita Deshpande" }] });
    if (route.request().method() !== "GET") return json({});
    if (path === "/ai-settings") return json({ tasks: [], can_edit_organisation: false });
    return json({ items: [], total: 0 });
  });
}
