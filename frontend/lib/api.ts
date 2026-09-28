// The one place the app calls the backend. Every call goes to /api (forwarded to
// FastAPI by next.config.ts), sends the session cookie, and turns an error reply
// into an ApiError with the backend's short code.

import type {
  AdminUser,
  AuditEntry,
  ChatResponse,
  Conversation,
  ConversationSummary,
  CustomerSettings,
  DocumentDetail,
  DocumentList,
  DocumentStatus,
  Me,
  ReviewChoice,
  ReviewItem,
  ReviewList,
  Role,
  SheetCells,
  Stats,
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

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
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

// --- documents -----------------------------------------------------------------

export const listDocuments = (
  params: { limit?: number; offset?: number; status?: DocumentStatus; q?: string } = {},
) => request<DocumentList>("GET", `/documents${query(params)}`);
export const getDocument = (id: string) => request<DocumentDetail>("GET", `/documents/${id}`);
export const deleteDocument = (id: string) => request<void>("DELETE", `/documents/${id}`);
export const getSheetCells = (id: string, pageNo: number, rowFrom = 1, rowTo = 200) =>
  request<SheetCells>(
    "GET",
    `/documents/${id}/pages/${pageNo}/cells${query({ row_from: rowFrom, row_to: rowTo })}`,
  );

/** Upload one file with progress (fetch cannot report upload progress, XHR can). */
export function uploadDocument(
  file: File,
  onProgress: (fraction: number) => void,
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/documents");
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
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

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
export const getSettings = () => request<CustomerSettings>("GET", "/admin/settings");
export const updateSettings = (maxUploadMb: number | null) =>
  request<CustomerSettings>("PATCH", "/admin/settings", { max_upload_mb: maxUploadMb });
export const listAudit = (params: { limit?: number; offset?: number } = {}) =>
  request<{ items: AuditEntry[]; total: number }>("GET", `/admin/audit${query(params)}`);
export const getStats = () => request<Stats>("GET", "/admin/stats");
