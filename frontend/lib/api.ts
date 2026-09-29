// The one place the app calls the backend. Every call goes to /api (forwarded to
// FastAPI by next.config.ts), sends the session cookie, and turns an error reply
// into an ApiError with the backend's short code.

import { queryString } from "@/components/documents/libraryQuery";
import type { DocumentListParams } from "@/components/documents/libraryQuery";
import type {
  AdminLoginResult,
  AdminUser,
  AuditEntry,
  ChatResponse,
  Conversation,
  ConversationSummary,
  CustomerSettings,
  DocumentDetail,
  DocumentList,
  DocumentSummary,
  Folder,
  Me,
  PasswordReset,
  ReviewChoice,
  ReviewItem,
  ReviewList,
  Role,
  SheetCells,
  Stats,
  Tag,
  UploadResult,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// State-changing requests carry this header; the backend refuses them without it,
// which a cross-site form cannot add (CSRF).
const CSRF_HEADER = { "X-Requested-With": "kaagaz" };

async function errorFrom(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string } };
    return new ApiError(
      response.status,
      body.error?.code ?? "error",
      body.error?.message ?? "Something went wrong.",
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
  if (!response.ok) throw await errorFrom(response);
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

/** The admin screen's sign-in. Without a code, a first sign-in gets the 2FA set-up. */
export const adminLogin = (email: string, password: string, code?: string) =>
  request<AdminLoginResult>("POST", "/auth/admin/login", {
    email,
    password,
    ...(code === undefined ? {} : { code }),
  });
export const adminEnrol = (enrolToken: string, code: string) =>
  request<Me>("POST", "/auth/admin/enrol", { enrol_token: enrolToken, code });
export const changePassword = (current: string, next: string) =>
  request<Me>("POST", "/auth/password", { current_password: current, new_password: next });
export const logoutEverywhere = () => request<void>("POST", "/auth/logout-everywhere");

// --- documents -----------------------------------------------------------------

// The list's lists and booleans (tag_id, include_subfolders) need queryString();
// see the merge note in components/documents/libraryQuery.ts.
export const listDocuments = (params: DocumentListParams = {}) =>
  request<DocumentList>("GET", `/documents${queryString(params)}`);
export const getDocument = (id: string) => request<DocumentDetail>("GET", `/documents/${id}`);
/** Moves the document, with every version of it, to Trash (PRD 1, F5). */
export const deleteDocument = (id: string) => request<void>("DELETE", `/documents/${id}`);
/** Brings a document in Trash back, with every version of it. */
export const restoreDocument = (id: string) =>
  request<DocumentSummary>("POST", `/documents/${id}/restore`);
/** Admins only, and only from Trash (409 not_in_trash otherwise). Every version goes. */
export const deleteDocumentForever = (id: string) =>
  request<void>("DELETE", `/documents/${id}/permanent`);
/** Rename or move. An explicit folder_id: null moves the document to the top level. */
export const updateDocument = (
  id: string,
  changes: { display_name?: string; folder_id?: string | null },
) => request<DocumentSummary>("PATCH", `/documents/${id}`, changes);
/** Replace the document's tags with exactly these. */
export const setDocumentTags = (id: string, tagIds: string[]) =>
  request<{ items: Tag[] }>("PUT", `/documents/${id}/tags`, { tag_ids: tagIds });
export const getSheetCells = (id: string, pageNo: number, rowFrom = 1, rowTo = 200) =>
  request<SheetCells>(
    "GET",
    `/documents/${id}/pages/${pageNo}/cells${query({ row_from: rowFrom, row_to: rowTo })}`,
  );

/**
 * Upload one file with progress (fetch cannot report upload progress, XHR can),
 * into a folder when one is given, else at the top level.
 */
export function uploadDocument(
  file: File,
  onProgress: (fraction: number) => void,
  folderId?: string,
): Promise<UploadResult> {
  const form = new FormData();
  if (folderId) form.append("folder_id", folderId);
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
  onProgress: (fraction: number) => void,
): Promise<UploadResult> {
  const form = new FormData();
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
        new ApiError(xhr.status, error?.code ?? "error", error?.message ?? "Upload failed."),
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
/** Admins only; the server checks the role. */
export const deleteTag = (id: string) => request<void>("DELETE", `/tags/${id}`);

// --- chat ----------------------------------------------------------------------

export const ask = (question: string, conversationId?: string) =>
  request<ChatResponse>("POST", "/chat", {
    question,
    ...(conversationId ? { conversation_id: conversationId } : {}),
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

// --- review --------------------------------------------------------------------

export const listReview = (params: { limit?: number; offset?: number; document_id?: string } = {}) =>
  request<ReviewList>("GET", `/review${query(params)}`);
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
