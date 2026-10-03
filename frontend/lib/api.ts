// The one place the app calls the backend. Every call goes to /api (forwarded to
// FastAPI by next.config.ts), sends the session cookie, and turns an error reply
// into an ApiError with the backend's short code. A reply saying the session
// has ended (401 not_signed_in) is also told to the session provider, which
// sends the browser to sign in (lib/session.tsx).

import type { DocumentSearchParams } from "@/components/documents/insideSearch";
import type { DocumentListParams } from "@/components/documents/libraryQuery";
import type {
  AdminUser,
  AuditEntry,
  ChatResponse,
  Conversation,
  ConversationSummary,
  CustomerSettings,
  DocumentDetail,
  DocumentList,
  DocumentSearch,
  DocumentSummary,
  FailedDocument,
  Folder,
  Me,
  MyWord,
  PasswordReset,
  ReviewChoice,
  ReviewItem,
  ReviewList,
  ReviewReason,
  Role,
  ScopeBody,
  SheetCells,
  Stats,
  Tag,
  TwoStepSetup,
  UploadLimits,
  UploadResult,
  WordSaved,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown, // the few errors that carry more (409 word_has_meaning)
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let sessionEnded: (() => void) | null = null;

/** Who to tell when a reply says the session has ended; null stops telling. */
export function onSessionEnded(listener: (() => void) | null): void {
  sessionEnded = listener;
}

function noticeSessionEnd(error: ApiError): ApiError {
  if (error.status === 401 && error.code === "not_signed_in") sessionEnded?.();
  return error;
}

// State-changing requests carry this header; the backend refuses them without it,
// which a cross-site form cannot add (CSRF).
const CSRF_HEADER = { "X-Requested-With": "kaagaz" };

async function errorFrom(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string; details?: unknown } };
    return new ApiError(
      response.status,
      body.error?.code ?? "error",
      body.error?.message ?? "Something went wrong.",
      body.error?.details,
    );
  } catch {
    return new ApiError(response.status, "error", "Something went wrong.");
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = method === "GET" ? {} : { ...CSRF_HEADER };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw noticeSessionEnd(await errorFrom(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export type QueryValue = string | number | boolean | readonly (string | number)[] | undefined;

// Array.isArray does not narrow a readonly array type; this guard does.
function isList(value: QueryValue): value is readonly (string | number)[] {
  return Array.isArray(value);
}

/**
 * "?a=1&b=x", in the order given. undefined and "" are left out; an array
 * repeats its key once per item (tag_id=a&tag_id=b); a boolean is "true" or
 * "false", so false is still sent.
 */
export function query(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    const items = isList(value) ? value : [value];
    for (const item of items) {
      if (item !== undefined && item !== "") search.append(key, String(item));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

// --- files the browser loads directly (img src, PDF.js) ----------------------

export const fileUrl = (documentId: string) => `/api/documents/${documentId}/file`;
export const pageImageUrl = (documentId: string, pageNo: number) =>
  `/api/documents/${documentId}/pages/${pageNo}/image`;

// --- auth ----------------------------------------------------------------------

export const login = (email: string, password: string) =>
  request<Me>("POST", "/auth/login", { email, password });
export const logout = () => request<void>("POST", "/auth/logout");
export const me = () => request<Me>("GET", "/auth/me");

/** The admin screen's sign-in. The code is needed only once the admin has turned 2FA on. */
export const adminLogin = (email: string, password: string, code?: string) =>
  request<Me>("POST", "/auth/admin/login", {
    email,
    password,
    ...(code === undefined ? {} : { code }),
  });
/** The signed-in admin's own two-step sign-in: with the current password, a new
 * key; then the first code turns it on. */
export const startTwoStep = (password: string) =>
  request<TwoStepSetup>("POST", "/auth/admin/2fa/start", { password });
export const adminEnrol = (enrolToken: string, code: string) =>
  request<Me>("POST", "/auth/admin/enrol", { enrol_token: enrolToken, code });
/** Turns it off; the server re-signs this browser and signs out the others. */
export const turnOffTwoStep = (password: string) =>
  request<Me>("POST", "/auth/admin/2fa/off", { password });
export const changePassword = (current: string, next: string) =>
  request<Me>("POST", "/auth/password", { current_password: current, new_password: next });
export const logoutEverywhere = () => request<void>("POST", "/auth/logout-everywhere");

// --- documents -----------------------------------------------------------------

export const listDocuments = (params: DocumentListParams = {}) =>
  request<DocumentList>("GET", `/documents${query(params)}`);
export const getDocument = (id: string) => request<DocumentDetail>("GET", `/documents/${id}`);
/** The detail's own summary only, for asking again while it is read. */
export const getDocumentSummary = (id: string) =>
  request<DocumentSummary>("GET", `/documents/${id}/summary`);
/** Search inside documents (PRD 3 Q2), grouped by document. */
export const searchDocuments = (params: DocumentSearchParams) =>
  request<DocumentSearch>("GET", `/search${query(params)}`);
/** Moves the document, with every version of it, to Trash (PRD 1, F5). */
export const deleteDocument = (id: string) => request<void>("DELETE", `/documents/${id}`);
/** Brings a document in Trash back, with every version of it. */
export const restoreDocument = (id: string) =>
  request<DocumentSummary>("POST", `/documents/${id}/restore`);
/**
 * Read the document again from the start (PRD 2, X1): a reviewer or an admin,
 * for a ready, failed or unreadable document out of Trash (403 forbidden or
 * 409 not_reprocessable otherwise). The reply has its new status, received.
 */
export const reprocessDocument = (id: string) =>
  request<DocumentSummary>("POST", `/documents/${id}/reprocess`);
/**
 * Only in an admin session (403 admin_session_required otherwise), and only
 * from Trash (409 not_in_trash otherwise). Every version goes.
 */
export const deleteDocumentForever = (id: string) =>
  request<void>("DELETE", `/documents/${id}/permanent`);
/**
 * Rename or move. An explicit folder_id: null moves the document to the top
 * level. Renaming, moving, tagging and a new version are refused in Trash
 * (409 in_trash).
 */
export const updateDocument = (
  id: string,
  changes: { display_name?: string; folder_id?: string | null },
) => request<DocumentSummary>("PATCH", `/documents/${id}`, changes);
/** Replace the document's tags with exactly these. */
export const setDocumentTags = (id: string, tagIds: string[]) =>
  request<{ items: Tag[] }>("PUT", `/documents/${id}/tags`, { tag_ids: tagIds });
/** The largest file of each kind and the most pages, with the customer's own limit applied. */
export const getUploadLimits = () => request<UploadLimits>("GET", "/upload-limits");
export const getSheetCells = (id: string, pageNo: number, rowFrom = 1, rowTo = 200) =>
  request<SheetCells>(
    "GET",
    `/documents/${id}/pages/${pageNo}/cells${query({ row_from: rowFrom, row_to: rowTo })}`,
  );
/** What each reader read on one page, as read (the viewer's Scanned text panel only). */
export const getPageReadings = (id: string, pageNo: number) =>
  request<PageReadings>("GET", `/documents/${id}/pages/${pageNo}/readings`);

/** This user's story of the document (any version: it belongs to the family); story is null when there is none. */
export const getMyStory = (id: string) =>
  request<{ story: MyStory | null }>("GET", `/documents/${encodeURIComponent(id)}/my-story`);
/** Create (201) or replace (200). The body comes from components/viewer/story.ts (storyBody). */
export const saveMyStory = (id: string, body: { story: string; user_id: string }) =>
  request<{ story: MyStory }>("PUT", `/documents/${encodeURIComponent(id)}/my-story`, body);
/** 204; 404 when there is none. userId: who this tab signed in (409 session_changed otherwise). */
export const deleteMyStory = (id: string, userId: string) =>
  request<void>("DELETE", `/documents/${encodeURIComponent(id)}/my-story${query({ user_id: userId })}`);

/**
 * Upload one file with progress (fetch cannot report upload progress, XHR can),
 * into a folder when one is given, else at the top level. userId is who was
 * signed in when the file was chosen: the server refuses the file (409
 * session_changed) if someone else has signed in since, in another tab.
 */
export function uploadDocument(
  file: File,
  userId: string,
  onProgress: (fraction: number) => void,
  folderId?: string,
): Promise<UploadResult> {
  const form = new FormData();
  if (folderId) form.append("folder_id", folderId);
  form.append("user_id", userId);
  form.append("file", file);
  return sendForm("/api/documents", form, onProgress);
}

/**
 * Upload a new version of a document, with progress. The server gives it the
 * next version number and the family's folder and tags; the reply has the
 * same shape as an upload.
 */
export function uploadVersion(
  documentId: string,
  file: File,
  userId: string,
  onProgress: (fraction: number) => void,
): Promise<UploadResult> {
  const form = new FormData();
  form.append("user_id", userId);
  form.append("file", file);
  return sendForm(`/api/documents/${documentId}/versions`, form, onProgress);
}

function sendForm(
  url: string,
  form: FormData,
  onProgress: (fraction: number) => void,
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-Requested-With", "kaagaz");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as UploadResult);
        return;
      }
      const error = (body as { error?: { code?: string; message?: string } } | null)?.error;
      reject(
        noticeSessionEnd(
          new ApiError(xhr.status, error?.code ?? "error", error?.message ?? "Upload failed."),
        ),
      );
    };
    xhr.onerror = () => reject(new ApiError(0, "network", "Could not reach the server."));
    xhr.send(form);
  });
}

// --- folders and tags ------------------------------------------------------------

export const listFolders = () => request<{ items: Folder[] }>("GET", "/folders");
/** A new folder inside parentId, or at the top level for null. */
export const createFolder = (name: string, parentId: string | null) =>
  request<Folder>("POST", "/folders", { name, ...(parentId ? { parent_id: parentId } : {}) });
/** Rename or move. An explicit parent_id: null moves the folder to the top level. */
export const updateFolder = (id: string, changes: { name?: string; parent_id?: string | null }) =>
  request<Folder>("PATCH", `/folders/${id}`, changes);
export const deleteFolder = (id: string) => request<void>("DELETE", `/folders/${id}`);
export const listTags = () => request<{ items: Tag[] }>("GET", "/tags");
export const createTag = (name: string) => request<Tag>("POST", "/tags", { name });

// --- chat ----------------------------------------------------------------------

/** Left out, `scope` stays the conversation's last one (PRD 3 H2); {} is every document. */
export const ask = (question: string, conversationId?: string, scope?: ScopeBody) =>
  request<ChatResponse>("POST", "/chat", {
    question,
    ...(conversationId ? { conversation_id: conversationId } : {}),
    ...(scope ? { scope } : {}),
  });
export const listConversations = () =>
  request<{ items: ConversationSummary[] }>("GET", "/conversations");
export const getConversation = (id: string) =>
  request<Conversation>("GET", `/conversations/${id}`);
export const sendFeedback = (messageId: string, rating: "up" | "down", reason?: string) =>
  request<void>("POST", `/messages/${messageId}/feedback`, {
    rating,
    ...(reason ? { reason } : {}),
  });

// --- my words (only the signed-in user's) -------------------------------------
// Every write sends user_id, who this tab signed in (409 session_changed if someone else has since).

export const listMyWords = () => request<{ items: MyWord[] }>("GET", "/me/words");
/** The bodies come from components/words/words.ts (addBody, changeBody, retryBody). */
export const addMyWord = (body: object) => request<WordSaved>("POST", "/me/words", body);
export const changeMyWord = (id: string, body: object) =>
  request<Omit<WordSaved, "outcome">>("PATCH", `/me/words/${encodeURIComponent(id)}`, body);
export const deleteMyWord = (id: string, userId: string) =>
  request<void>("DELETE", `/me/words/${encodeURIComponent(id)}${query({ user_id: userId })}`);

// --- review --------------------------------------------------------------------

export const listReview = (
  params: { limit?: number; offset?: number; document_id?: string; reason?: ReviewReason[] } = {},
) => request<ReviewList>("GET", `/review${query(params)}`);
export const resolveReview = (
  valueId: string,
  choice: ReviewChoice,
  remember: boolean,
  value?: string,
) =>
  request<ReviewItem>("POST", `/review/${valueId}`, {
    choice,
    remember,
    ...(choice === "typed" ? { value } : {}),
  });

// --- admin ---------------------------------------------------------------------

export const listUsers = () => request<{ items: AdminUser[] }>("GET", "/admin/users");
export const createUser = (user: {
  email: string;
  display_name: string;
  role: Role;
  password: string;
}) => request<AdminUser>("POST", "/admin/users", user);
export const updateUser = (
  id: string,
  changes: { role?: Role; disabled?: boolean; password?: string },
) => request<AdminUser>("PATCH", `/admin/users/${id}`, changes);
export const unlockUser = (id: string) => request<AdminUser>("POST", `/admin/users/${id}/unlock`);
export const resetUserPassword = (id: string) =>
  request<PasswordReset>("POST", `/admin/users/${id}/reset-password`);
export const resetUser2fa = (id: string) =>
  request<AdminUser>("POST", `/admin/users/${id}/reset-2fa`);
export const getSettings = () => request<CustomerSettings>("GET", "/admin/settings");
export const updateSettings = (maxUploadMb: number | null) =>
  request<CustomerSettings>("PATCH", "/admin/settings", { max_upload_mb: maxUploadMb });
export const listAudit = (params: { limit?: number; offset?: number } = {}) =>
  request<{ items: AuditEntry[]; total: number }>("GET", `/admin/audit${query(params)}`);
export const getStats = () => request<Stats>("GET", "/admin/stats");
/** Documents that failed or can't be read (PRD 3 Z1); Retry is reprocessDocument. */
export const listFailed = (params: { limit?: number; offset?: number } = {}) =>
  request<{ items: FailedDocument[]; total: number }>("GET", `/admin/failed${query(params)}`);
