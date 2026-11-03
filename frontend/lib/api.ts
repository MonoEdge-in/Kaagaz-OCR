// The one place the app calls the backend. Every call goes to /api (forwarded to
// FastAPI by next.config.ts), sends the session cookie, and turns an error reply
// into an ApiError with the backend's short code. A reply saying the session
// has ended (401 not_signed_in) is also told to the session provider, which
// sends the browser to sign in (lib/session.tsx).

import type { DocumentSearchParams } from "@/components/documents/insideSearch";
import type { DocumentListParams } from "@/components/documents/libraryQuery";
import type { StoryMatchBody } from "./storyMatches";
import type {
  ActivityPage,
  AdminChange,
  AdminCreated,
  AdminUser,
  AiDefaults,
  AiSettings,
  AiTask,
  AnswerQualityReply,
  AnswerWeeksReply,
  AiValues,
  AnswerChoice,
  BatchView,
  Dashboard,
  ReadyQuestion,
  DocumentCompare,
  DocumentQuestion,
  Person,
  Sensitivity,
  BoardReply,
  ChatOptions,
  ChatResponse,
  ClarifyReply,
  ClearCopy,
  Conversation,
  ConversationSummary,
  CustomerSettings,
  DocumentDetail,
  DocumentList,
  DocumentSearch,
  DocumentSummary,
  EnsembleEstimate,
  EnsembleRun,
  EnsembleScope,
  FailedDocument,
  FeatureSwitch,
  FlagState,
  Folder,
  FolderItemView,
  FolderItemsPage,
  FolderLimits,
  FolderUploadItem,
  FolderUploadPlan,
  FolderUploadPlanBody,
  FolderUploadSummary,
  NewFolderItem,
  InsightsReply,
  Me,
  MemberPeriod,
  MembersReply,
  MySpot,
  MyStory,
  MyWord,
  OrganizationWord,
  OperatorMe,
  OperatorModel,
  OperatorMoveOffer,
  OperatorUsageRow,
  OrgAdmin,
  OrgAiReply,
  OrgFeatureSwitch,
  OrgFolderLimits,
  Organisation,
  OrgModelRates,
  OrgNumbersReply,
  OrganisationCreated,
  PageReadings,
  PageSummary,
  DocumentWords,
  PasswordReset,
  PlatformEvent,
  PlatformFolderLimits,
  PlatformHealth,
  LearnedDocument,
  QualityReply,
  ReasonCode,
  ReviewChoice,
  ReviewItem,
  ReviewList,
  ReviewReason,
  ReviewStatus,
  Role,
  VerifyAllResult,
  ScopeBody,
  SessionTimeout,
  SheetCells,
  SourcePin,
  Speed,
  SpeedName,
  Stats,
  Tag,
  TaskLevel,
  TestModel,
  TwoStepSetup,
  UploadLimits,
  UploadReading,
  UploadResult,
  UsageReport,
  WordChange,
  WordFlag,
  WordSaved,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown, // the few errors that carry more (409 word_has_meaning)
    readonly retryAfter?: number, // seconds, from a 429's Retry-After: when to ask again
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

/** What every screen says where a feature switched off for the whole product was (403 feature_off). */
export const FEATURE_OFF = "This is turned off for now.";

/** A refusal because the super admin switched the feature off for everyone. */
export const isFeatureOff = (caught: unknown) => caught instanceof ApiError && caught.code === "feature_off";

async function errorFrom(response: Response): Promise<ApiError> {
  const waitHeader = Number(response.headers.get("Retry-After") ?? "");
  const retryAfter = waitHeader > 0 ? waitHeader : undefined;
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string; details?: unknown } };
    const code = body.error?.code ?? "error";
    return new ApiError(
      response.status,
      code,
      // One calm sentence wherever the message is shown, whatever the server's words.
      code === "feature_off" ? FEATURE_OFF : (body.error?.message ?? "Something went wrong."),
      body.error?.details,
      retryAfter,
    );
  } catch {
    return new ApiError(response.status, "error", "Something went wrong.", undefined, retryAfter);
  }
}

/** A 429's wait in milliseconds (its Retry-After, else fallback); null for any other error. */
export function rateLimitWait(caught: unknown, fallback: number): number | null {
  if (!(caught instanceof ApiError) || caught.status !== 429) return null;
  return caught.retryAfter !== undefined ? caught.retryAfter * 1000 : fallback;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = method === "GET" ? {} : { ...CSRF_HEADER };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
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
/** The Download button's address (A2): only it is logged, and it saves under the document's name. */
export const downloadUrl = (documentId: string) => `${fileUrl(documentId)}?download=1`;
export const pageImageUrl = (documentId: string, pageNo: number) =>
  `/api/documents/${documentId}/pages/${pageNo}/image`;

// --- auth ----------------------------------------------------------------------

/** The one sign-in (16 Oct 2026). An admin also sends the code once the server asks
 * for it (401 code_required), or sets two-step up first (403 mfa_enrol_required). */
export const login = (email: string, password: string, code?: string) =>
  request<Me>("POST", "/auth/login", { email, password, ...(code === undefined ? {} : { code }) });
export const logout = () => request<void>("POST", "/auth/logout");
/** Whether anyone may create an organisation at /signup (SELF_SIGNUP_ENABLED). */
export const signUpOpen = () => request<{ enabled: boolean }>("GET", "/auth/signup");
/** A new organisation and its first admin. The reply is the two-step set-up (admins must
 * have it); the first code (adminEnrol) signs in as that admin. */
export const signUp = (body: { organisation_name: string; name: string; email: string; password: string }) =>
  request<TwoStepSetup>("POST", "/auth/signup", body);
/** Who is signed in, 200 either way (issue 378): a signed-out visit logs no expected 401. */
export const currentSession = () =>
  request<{ signed_in: true; me: Me } | { signed_in: false }>("GET", "/auth/session");

/** The first code from the set-up (sign-in or sign-up) turns two-step on and signs in. */
export const adminEnrol = (enrolToken: string, code: string) =>
  request<Me>("POST", "/auth/admin/enrol", { enrol_token: enrolToken, code });
/** An admin turns their own two-step back on (Tushar, 30 Oct 2026): the password gives the
 * set-up key; then adminEnrol with the first code. Nothing is stored before that code. */
export const startTwoStep = (password: string) =>
  request<TwoStepSetup>("POST", "/auth/admin/2fa/start", { password });
/** An admin turns their own two-step off, with the password and a current code; it stays
 * off until they turn it on again (Tushar, 30 Oct 2026). */
export const turnOffTwoStep = (password: string, code: string) =>
  request<Me>("POST", "/auth/admin/2fa/off", { password, code });
export const changePassword = (current: string, next: string) =>
  request<Me>("POST", "/auth/password", { current_password: current, new_password: next });
export const logoutEverywhere = () => request<void>("POST", "/auth/logout-everywhere");
/** A granted member or reviewer types their password before Delete forever (a 5-minute cookie). */
export const confirmPassword = (password: string) =>
  request<void>("POST", "/auth/confirm-password", { password });

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
 * reading: the reading asked for (a model or Dynamic; one the organisation may not
 * use is 403); none keeps the document's own.
 */
export const reprocessDocument = (id: string, reading?: UploadReading) =>
  request<DocumentSummary>("POST", `/documents/${id}/reprocess`, reading);
/**
 * An admin session with two-step sign-in, or a granted user who just confirmed
 * their password (403 otherwise), and only from Trash (409 not_in_trash
 * otherwise). Every version goes.
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
  changes: {
    display_name?: string;
    folder_id?: string | null;
    owner_id?: string; // O1: the whole family; an active person of the company
    sensitivity?: Sensitivity; // O2: the whole family
    version_note?: string | null; // V9: this version; null clears it
  },
) => request<DocumentSummary>("PATCH", `/documents/${id}`, changes);

// --- file management (PRD 1, Phase 2) -----------------------------------------------

/** D1, D2: the home screen in one request. */
export const getDashboard = () => request<Dashboard>("GET", "/dashboard");
/** Home's questions for this person, each with its answer ready or being made. */
export const getReadyQuestions = () => request<{ items: ReadyQuestion[] }>("GET", "/chat/ready-questions");
/** The company's active people, for the owner picker and the Owner filter. */
export const listPeople = () => request<{ items: Person[] }>("GET", "/people");
/** V6 Make it version N: the document joins the candidate's family, with an optional note (V9). */
export const acceptSuggestion = (id: string, versionNote?: string) =>
  request<DocumentSummary>(
    "POST",
    `/version-suggestions/${encodeURIComponent(id)}/accept`,
    versionNote ? { version_note: versionNote } : undefined,
  );
/** V6 Keep separate: the pair is never suggested again. */
export const rejectSuggestion = (id: string) =>
  request<void>("POST", `/version-suggestions/${encodeURIComponent(id)}/reject`);
/** V7: the link undone while it is still the newest version; 409 not_newest after that. */
export const undoVersionLink = (id: string) => request<DocumentSummary>("POST", `${doc(id)}/version-undo`);
/** V10: this ready version becomes the one search and chat use. */
export const makeCurrent = (id: string) => request<DocumentSummary>("POST", `${doc(id)}/make-current`);
/** V11: the reader text of `withId` (left) against `id` (right), page by page. */
export const compareDocuments = (id: string, withId: string) =>
  request<DocumentCompare>("GET", `${doc(id)}/compare${query({ with: withId })}`);
/** A5: every version of the family, newest first. */
export const documentActivity = (id: string, cursor?: string) =>
  request<ActivityPage>("GET", `${doc(id)}/activity${query({ cursor })}`);
/** A5: the folder and everything below it. */
export const folderActivity = (id: string, cursor?: string) =>
  request<ActivityPage>("GET", `/folders/${encodeURIComponent(id)}/activity${query({ cursor })}`);
/** P2: this person's questions about the document, newest first. */
export const documentQuestions = (id: string) =>
  request<{ items: DocumentQuestion[] }>("GET", `${doc(id)}/questions`);
/**
 * Feature 28: move a family Kaagaz filed back to "Not in a folder"; the folder stays.
 * 409 already_moved when someone moved it since, 409 in_trash in Trash.
 */
export const undoAutofile = (id: string) => request<DocumentSummary>("POST", `/documents/${id}/autofile/undo`);
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
/** One drop of files (PRD 5 Q1): the uploader's own only; 404 for any other or unknown batch. */
export const getBatch = (id: string) => request<BatchView>("GET", `/batches/${encodeURIComponent(id)}`);
/**
 * F5: read one page again, harder (a reviewer or an admin); with `regions` (#385, boxes in page
 * fractions), only those spots of it. The reply is the document, now received.
 */
export const readPageAgain = (id: string, pageNo: number, regions?: readonly (readonly number[])[]) =>
  request<DocumentSummary>(
    "POST",
    `/documents/${id}/pages/${pageNo}/read-again`,
    regions === undefined ? undefined : { regions },
  );
/** F5 "Keep as is": the page leaves the attention list but stays flagged; the reply is the page. */
export const acceptPage = (id: string, pageNo: number) =>
  request<PageSummary>("POST", `/documents/${id}/pages/${pageNo}/accept`);
/** What each reader read on one page, as read (the viewer's Scanned text panel only). */
export const getPageReadings = (id: string, pageNo: number) =>
  request<PageReadings>("GET", `/documents/${id}/pages/${pageNo}/readings`);
/** Your words on the page (package H): the boxes of the lines holding them, and every page with one. */
export const getDocumentWords = (id: string, words: readonly string[]) =>
  request<DocumentWords>("GET", `/documents/${id}/words${query({ w: words })}`);
/** Read clearly: every page's lines in one request (no AI). */
export const getClearCopy = (id: string) => request<ClearCopy>("GET", `/documents/${id}/clear-copy`);

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
 * reading is what was chosen in "Read with" (readers.ts), sent as read_mode or
 * read_model; none means the organisation's default.
 * batchId is the drop it came in (PRD 5 Q1, a UUID the browser made; 409 batch_full
 * past MAX_FILES_PER_BATCH). onSent: the last byte has gone, so Saving starts (U10).
 */
export function uploadDocument(
  file: File,
  userId: string,
  onProgress: (fraction: number) => void,
  folderId?: string,
  reading?: UploadReading,
  batchId?: string,
  onSent?: () => void,
  departmentId?: string, // F2: used by the server when no folder is named
  onWaiting?: UploadWaiting,
): Promise<UploadResult> {
  return resumableUpload(file, {
    file_name: file.name, file_size: file.size, user_id: userId, folder_id: folderId,
    department_id: folderId ? undefined : departmentId, batch_id: batchId, ...readingBody(reading),
  }, onProgress, onSent, onWaiting);
}

/**
 * Upload a new version of a document, with progress. The server gives it the
 * next version number and the family's folder and tags; the reply has the
 * same shape as an upload. reading and onSent as for uploadDocument.
 */
export function uploadVersion(
  documentId: string,
  file: File,
  userId: string,
  onProgress: (fraction: number) => void,
  reading?: UploadReading,
  onSent?: () => void,
  versionNote?: string, // V9: optional, at most 200 characters
  onWaiting?: UploadWaiting,
): Promise<UploadResult> {
  return resumableUpload(file, {
    file_name: file.name, file_size: file.size, user_id: userId,
    version_document_id: documentId, version_note: versionNote, ...readingBody(reading),
  }, onProgress, onSent, onWaiting);
}

type ResumableStart = {
  file_name: string; file_size: number; fingerprint: string; user_id: string; folder_id?: string; department_id?: string;
  batch_id?: string; read_mode?: string; read_model?: string; version_document_id?: string; version_note?: string;
  folder_upload_item_id?: string; // #484: the server takes the folder and batch from the item
};
const UPLOAD_PART_BYTES = 8 * 1024 * 1024;

function readingBody(reading?: UploadReading): Pick<ResumableStart, "read_mode" | "read_model"> {
  return { read_mode: reading?.read_mode, read_model: reading?.read_model };
}

async function digest(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function waitForNetwork(delay: number): Promise<void> {
  // No navigator (Node 20, where CI runs the tests) reads as online; a browser always has one.
  const online = typeof navigator === "undefined" || navigator.onLine !== false;
  if (online) return new Promise((resolve) => setTimeout(resolve, delay));
  return new Promise((resolve) => window.addEventListener("online", () => resolve(), { once: true }));
}

/** Told while an upload call waits to go again (true) and why, then once it goes (false). */
export type UploadWaiting = (waiting: boolean, why?: "connection" | "server") => void;

/** A failing server (5xx) is asked this many more times, then the row fails with Retry. */
const SERVER_RETRIES = 4;

/**
 * Upload calls from this tab (start, each part, finish): a burst of this many, then this many
 * a minute. The API gives a file's upload calls their own budget of RATE_LIMIT_PER_MINUTE
 * (120 in .env.example; tests/request-budget.test.ts reads it), so this pace is never refused
 * and the uploads wait their turn here instead (Vrushit UAT, 25 Oct 2026).
 */
export const UPLOAD_CALLS_PER_MINUTE = 80;

/** A token bucket: a burst of perMinute calls, then perMinute a minute; each call waits its turn. */
export function createTurn(perMinute: number): () => Promise<void> {
  let tokens = perMinute;
  let tokensAt: number | null = null;
  return async () => {
    for (;;) {
      const now = Date.now();
      const elapsed = tokensAt === null ? 0 : Math.max(0, now - tokensAt); // the clock may be set back
      tokens = Math.min(perMinute, tokens + (elapsed * perMinute) / 60_000);
      tokensAt = now;
      if (tokens >= 1) {
        tokens -= 1;
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, ((1 - tokens) * 60_000) / perMinute));
    }
  };
}

/** Wait for this tab's next upload call (shared by every file). */
const uploadTurn = createTurn(UPLOAD_CALLS_PER_MINUTE);

/**
 * Each upload call waits its turn (uploadTurn). Waits out a lost connection, however long; a
 * failing server only SERVER_RETRIES times more (about 30 s), so a server error never leaves a
 * row "waiting" for ever. A 429 is the server's pace, not a failure: the call waits the
 * Retry-After it gave, without counting a try or saying the connection is lost, and goes again.
 */
async function retryWhileOffline<T>(
  work: () => Promise<T>,
  onWaiting?: UploadWaiting,
  turn: () => Promise<void> = uploadTurn,
): Promise<T> {
  let delay = 1000;
  let serverTries = 0;
  for (;;) {
    await turn();
    try { return await work(); }
    catch (error) {
      const paced = rateLimitWait(error, delay);
      if (paced !== null) {
        await waitForNetwork(paced);
        continue;
      }
      const lost = error instanceof ApiError ? error.status === 0 : error instanceof TypeError;
      const busy = error instanceof ApiError && error.status >= 500;
      if (!lost && !(busy && serverTries++ < SERVER_RETRIES)) throw error;
      onWaiting?.(true, lost ? "connection" : "server"); // issue 357: a 5xx is not a lost connection
      await waitForNetwork(delay);
      onWaiting?.(false);
      delay = Math.min(delay * 2, 15000);
    }
  }
}

function putPart(url: string, body: Blob, onProgress: (loaded: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-Requested-With", "kaagaz");
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(event.loaded); };
    xhr.onload = async () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else if (xhr.status === 0) reject(new ApiError(0, "network", "Could not reach the server."));
      else reject(noticeSessionEnd(await errorFrom(new Response(xhr.responseText, {
        status: xhr.status,
        headers: { "Content-Type": "application/json", "Retry-After": xhr.getResponseHeader("Retry-After") ?? "" },
      }))));
    };
    xhr.onerror = () => reject(new TypeError("Network error"));
    xhr.onabort = () => reject(new TypeError("Upload interrupted"));
    xhr.send(body);
  });
}

async function resumableUpload(
  file: File, metadata: Omit<ResumableStart, "fingerprint">, onProgress: (fraction: number) => void,
  onSent?: () => void, onWaiting?: UploadWaiting,
): Promise<UploadResult> {
  // The bytes never go backwards, even when a part is sent again after a lost connection.
  let shown = 0;
  const report = (fraction: number) => {
    if (fraction <= shown) return;
    shown = fraction;
    onProgress(fraction);
  };
  const partCount = Math.ceil(file.size / UPLOAD_PART_BYTES);
  const partHashes: string[] = [];
  for (let number = 0; number < partCount; number++) {
    const start = number * UPLOAD_PART_BYTES;
    partHashes.push(await digest(file.slice(start, Math.min(start + UPLOAD_PART_BYTES, file.size))));
  }
  const fingerprint = await digest(new Blob([partHashes.join("")]));
  const identified = { ...metadata, fingerprint };
  const { candidates } = await retryWhileOffline(
    () => request<{ candidates: { id: string; parts: Record<string, string> }[] }>(
      "POST", "/documents/upload-sessions/candidates", identified,
    ),
    onWaiting,
  );
  let resumeId: string | undefined;
  let savedParts: Record<string, string> = {};
  for (const candidate of candidates) {
    let matches = true;
    for (const [number, expected] of Object.entries(candidate.parts)) {
      const index = Number(number) - 1;
      if (index < 0 || index >= partHashes.length || partHashes[index] !== expected) {
        matches = false; break;
      }
    }
    if (matches) { resumeId = candidate.id; savedParts = candidate.parts; break; }
  }
  const session = await retryWhileOffline(
    () => request<{ id: string; part_size: number; part_count: number }>(
      "POST", "/documents/upload-sessions", { ...identified, resume_session_id: resumeId },
    ),
    onWaiting,
  );
  let sent = 0;
  for (let number = 1; number <= session.part_count; number++) {
    const start = (number - 1) * session.part_size;
    const part = file.slice(start, Math.min(start + session.part_size, file.size));
    const hash = partHashes[number - 1];
    if (savedParts[String(number)] !== hash) {
      await retryWhileOffline(() => putPart(
        `/api/documents/upload-sessions/${session.id}/parts/${number}`,
        part,
        (loaded) => report(Math.min(1, (sent + loaded) / file.size)),
      ), onWaiting);
    }
    sent += part.size;
    report(Math.min(1, sent / file.size));
  }
  onSent?.();
  return retryWhileOffline(
    () => request<UploadResult>("POST", `/documents/upload-sessions/${session.id}/complete`, {}),
    onWaiting,
  );
}

// --- folder uploads (#484) ------------------------------------------------------
// The browser plans the folders, registers every file as an item with its folder, then sends
// each file to its item: the server records the folder per item, so a retry, or a Continue
// after a reload, lands in the same folder. Plan, register and the lists use the normal limits.

const folderUpload = (id: string) => `/folder-uploads/${encodeURIComponent(id)}`;

/** Make (or, for the same id, find again) the folders; folder_ids follow the manifest. */
export const planFolderUpload = (body: FolderUploadPlanBody) => request<FolderUploadPlan>("POST", "/folder-uploads", body);
/** Register up to 1,000 files; a path_key registered before gives back its item unchanged. */
export const registerFolderItems = (id: string, userId: string, items: NewFolderItem[]) =>
  request<{ items: FolderUploadItem[] }>("POST", `${folderUpload(id)}/items`, { user_id: userId, items });
export const getFolderUpload = (id: string) => request<FolderUploadSummary>("GET", folderUpload(id));
/** This person's folder uploads of the last days, newest first. */
export const listFolderUploads = () => request<{ items: FolderUploadSummary[] }>("GET", "/folder-uploads");
/** One page of a folder upload's items in seq order, after `after` (-1: from the start). */
export const listFolderItems = (id: string, view: FolderItemView, after: number, limit: number) =>
  request<FolderItemsPage>("GET", `${folderUpload(id)}/items${query({ view, after, limit })}`);

/** One multipart POST with upload progress (XHR); the reply is JSON. */
function postFile(path: string, form: FormData, onProgress: (fraction: number) => void): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-Requested-With", "kaagaz");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    };
    xhr.onload = async () => {
      if (xhr.status === 0) {
        reject(new ApiError(0, "network", "Could not reach the server."));
        return;
      }
      const reply = new Response(xhr.responseText, {
        status: xhr.status,
        headers: { "Content-Type": "application/json", "Retry-After": xhr.getResponseHeader("Retry-After") ?? "" },
      });
      if (xhr.status >= 200 && xhr.status < 300) resolve((await reply.json()) as UploadResult);
      else reject(noticeSessionEnd(await errorFrom(reply)));
    };
    xhr.onerror = () => reject(new TypeError("Network error"));
    xhr.onabort = () => reject(new TypeError("Upload interrupted"));
    xhr.send(form);
  });
}

/**
 * Send one file of a folder upload to its item. Up to 8 MiB it is one call to POST /documents
 * at the folder upload's own pace (`turn`); a bigger file takes the resumable path at the upload
 * pace. Neither names a folder or a batch: the server takes both from the item. A lost
 * connection, a failing server (5xx) and a 429's Retry-After are waited out as for any upload
 * (retryWhileOffline), always for the same item.
 */
export function uploadFolderItem(
  file: File,
  itemId: string,
  userId: string,
  onProgress: (fraction: number) => void,
  turn: () => Promise<void>,
  reading?: UploadReading,
  onWaiting?: UploadWaiting,
): Promise<UploadResult> {
  if (file.size > UPLOAD_PART_BYTES) {
    return resumableUpload(
      file,
      { file_name: file.name, file_size: file.size, user_id: userId, folder_upload_item_id: itemId, ...readingBody(reading) },
      onProgress,
      undefined,
      onWaiting,
    );
  }
  return retryWhileOffline(
    () => {
      const form = new FormData();
      form.append("file", file, file.name);
      form.append("user_id", userId);
      form.append("folder_upload_item_id", itemId);
      if (reading?.read_mode) form.append("read_mode", reading.read_mode);
      if (reading?.read_model) form.append("read_model", reading.read_model);
      return postFile("/documents", form, onProgress);
    },
    onWaiting,
    turn,
  );
}

// --- folders and tags ------------------------------------------------------------

export const listFolders = () => request<{ items: Folder[] }>("GET", "/folders");
/** A new folder inside parentId, or at the top level for null. F1: a department is an admin's, top level only. */
export const createFolder = (
  name: string,
  parentId: string | null,
  department?: { sort_order: number },
) =>
  request<Folder>("POST", "/folders", {
    name,
    ...(parentId ? { parent_id: parentId } : {}),
    ...(department ? { kind: "department", sort_order: department.sort_order } : {}),
  });
/** Rename, move or order. An explicit parent_id: null moves the folder to the top level. */
export const updateFolder = (
  id: string,
  changes: { name?: string; parent_id?: string | null; sort_order?: number | null },
) => request<Folder>("PATCH", `/folders/${id}`, changes);
export const deleteFolder = (id: string) => request<void>("DELETE", `/folders/${id}`);
export const listTags = () => request<{ items: Tag[] }>("GET", "/tags");
export const createTag = (name: string) => request<Tag>("POST", "/tags", { name });

// --- chat ----------------------------------------------------------------------

/**
 * Left out, `scope` stays the conversation's last one (PRD 3 H2); {} is every document.
 * `choice`: the composer's answer settings (features 25-27); left out, the organisation's.
 * `reply`: the open clarifying question this answers (PRD 6 B2).
 */
export const ask = (
  question: string,
  conversationId?: string,
  scope?: ScopeBody,
  choice?: AnswerChoice,
  reply?: ClarifyReply,
  signal?: AbortSignal,
) =>
  request<ChatResponse>(
    "POST",
    "/chat",
    {
      question,
      ...(conversationId ? { conversation_id: conversationId } : {}),
      ...(scope ? { scope } : {}),
      ...(choice ?? {}),
      ...(reply ? { reply } : {}),
    },
    signal,
  );
/** The answer settings the organisation allows (features 25-27): names and levels, never prices. */
export const getChatOptions = () => request<ChatOptions>("GET", "/chat/options");
/**
 * The user's own documents whose story the question tells again (S3): beside
 * ask() for the side cards, or instead of it for "Search my stories", never
 * through it. POST, so what someone remembers never sits in a URL. The reply is
 * checked by lib/storyMatches.ts.
 */
export const postStoryMatches = (body: StoryMatchBody, signal?: AbortSignal) =>
  request<unknown>("POST", "/me/story-matches", body, signal);
export const listConversations = () =>
  request<{ items: ConversationSummary[] }>("GET", "/conversations");
export const getConversation = (id: string) =>
  request<Conversation>("GET", `/conversations/${id}`);
export const renameConversation = (id: string, title: string) =>
  request<ConversationSummary>("PATCH", `/conversations/${id}`, { title });
export const deleteConversation = (id: string) => request<void>("DELETE", `/conversations/${id}`);
/** "Most relevant" (ranked sources, 16 Oct 2026): this source on top of the answer, kept as relevance feedback. */
export const pinSource = (messageId: string, pin: SourcePin, previousRank: number) =>
  request<void>("POST", `/messages/${messageId}/pin`, { ...pin, previous_rank: previousRank });
/** Useful or Not useful (PRD 6 F1). `notUseful` comes from the Not useful form, which shows
 * the admin notice above Send (FeedbackControls.tsx), so the server is told it was shown. */
export const sendFeedback = (
  messageId: string,
  rating: "up" | "down",
  notUseful?: { reason?: string; codes: ReasonCode[] },
) =>
  request<void>("POST", `/messages/${messageId}/feedback`, {
    rating,
    ...(notUseful
      ? { reason_codes: notUseful.codes, notice_shown: true, ...(notUseful.reason ? { reason: notUseful.reason } : {}) }
      : {}),
  });

// --- my words (only the signed-in user's) -------------------------------------
// Every write sends user_id, who this tab signed in (409 session_changed if someone else has since).

export const listMyWords = () => request<{ items: MyWord[] }>("GET", "/me/words");
/** The bodies come from components/words/words.ts (addBody, changeBody, retryBody). */
export const addMyWord = (body: object) => request<WordSaved>("POST", "/me/words", body);
export const changeMyWord = (id: string, body: object) =>
  request<Omit<WordSaved, "outcome">>("PATCH", `/me/words/${encodeURIComponent(id)}`, body);
/** My words on one page of a document, oldest first (the viewer's markers). */
export const getMyWordsOnPage = (documentId: string, page: number) =>
  request<{ items: MySpot[] }>("GET", `/documents/${encodeURIComponent(documentId)}/my-words${query({ page })}`);
export const deleteMyWord = (id: string, userId: string) =>
  request<void>("DELETE", `/me/words/${encodeURIComponent(id)}${query({ user_id: userId })}`);
export const listOrganizationWords = () => request<{ items: OrganizationWord[] }>("GET", "/organization/words");
export const decideOrganizationWord = (id: string, decision: "approve" | "reject", userId: string) =>
  request<{ item: OrganizationWord }>("POST", `/organization/words/${encodeURIComponent(id)}/decision`, { decision, user_id: userId });

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
/** A refused Verify all is asked again this many times, each after the 429's Retry-After. */
const VERIFY_ALL_PACED_TRIES = 3;
/**
 * Verify all on the review queue: every value its filters show, verified on the server in one
 * call. A 429 is the limiter refusing before anything ran, so the call waits the Retry-After it
 * gave (1 s without one) and goes again, a few times; then the 429 is the caller's to show.
 */
export async function verifyAllReview(filters: { document_id?: string; reason?: ReviewReason[] }) {
  for (let tries = 0; ; tries++) {
    try {
      return await request<VerifyAllResult>("POST", "/review/verify-all", filters);
    } catch (caught) {
      const wait = rateLimitWait(caught, 1000);
      if (wait === null || tries >= VERIFY_ALL_PACED_TRIES) throw caught;
      await new Promise((resolve) => setTimeout(resolve, wait));
    }
  }
}

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
/** Allow or stop a member's or reviewer's Delete forever; allowing needs your two-step sign-in. */
export const setDeleteForever = (id: string, allowed: boolean) =>
  request<AdminUser>("PUT", `/admin/users/${id}/delete-forever`, { allowed });
export const getSettings = () => request<CustomerSettings>("GET", "/admin/settings");
/** ``autofileOn`` left out: the switch is left as it is (feature 28). */
export const updateSettings = (maxUploadMb: number | null, autofileOn?: boolean) =>
  request<CustomerSettings>("PATCH", "/admin/settings", {
    max_upload_mb: maxUploadMb,
    ...(autofileOn === undefined ? {} : { autofile_on: autofileOn }),
  });
/** A5: the admin log, filtered (stage 3); `cursor` is the reply's `next`. The older reply pages by offset. */
export type AuditParams = {
  limit?: number;
  offset?: number;
  cursor?: string;
  user_id?: string; // who did it
  folder_id?: string; // with its subfolders
  document_id?: string;
  action?: readonly string[];
  from?: string; // YYYY-MM-DD, both days included
  to?: string;
};
export const listAudit = (params: AuditParams = {}) =>
  request<ActivityPage>("GET", `/admin/audit${query(params)}`);
/** A6 Export to Excel: the filtered rows as CSV, streamed; logged as audit.export. */
export const auditExportUrl = (params: AuditParams) =>
  `/api/admin/audit/export${query({ ...params, limit: undefined, offset: undefined, cursor: undefined })}`;
export const getStats = () => request<Stats>("GET", "/admin/stats");
/** #386: each member of the admin's own organisation with what they did in the period. */
export const getMemberNumbers = (period: MemberPeriod) =>
  request<MembersReply>("GET", `/admin/members${query({ period })}`);
/** Documents that failed or can't be read (PRD 3 Z1); Retry is reprocessDocument. */
export const listFailed = (params: { limit?: number; offset?: number } = {}) =>
  request<{ items: FailedDocument[]; total: number }>("GET", `/admin/failed${query(params)}`);
/** Answer quality (PRD 6 F2): Not useful answers given after the notice, and weekly counts. */
export const getAnswerQuality = (params: { status: "open" | "handled"; weeks: number; limit: number; offset: number }) =>
  request<AnswerQualityReply>("GET", `/admin/answer-quality${query(params)}`);
/** Mark as handled, or open it again; `seen` is the given_at shown (409 when rated again since). */
export const setAnswerHandled = (id: string, handled: boolean, seen: string) =>
  request<{ id: string; handled_at: string | null }>("PUT", `/admin/answer-quality/${id}/handled`, { handled, seen });
/** What Kaagaz learned (PRD 6 F3), and Remove on one document. */
export const listLearned = () => request<{ items: LearnedDocument[] }>("GET", "/admin/learned");
export const removeLearned = (documentId: string) => request<void>("DELETE", `/admin/learned/${documentId}`);
/** AI use from `from` to `to` (YYYY-MM-DD, both included; the server's default when left out), for one user when given. */
export type UsageParams = { from?: string; to?: string; user_id?: string };
export const getAdminUsage = (params: UsageParams = {}) =>
  request<UsageReport>("GET", `/admin/usage${query(params)}`);

// --- the platform operator (super admin) -----------------------------------------
// Its own cookie (path /api/operator), sent only to these routes.

export const operatorLogin = (email: string, password: string, code: string) =>
  request<OperatorMe>("POST", "/operator/login", { email, password, code });
export const operatorLogout = () => request<void>("POST", "/operator/logout");
export const operatorMe = () => request<OperatorMe>("GET", "/operator/me");
/** Moving two-step to a new device: the password and a code from the CURRENT device give the new key. */
export const operatorMoveStart = (password: string, code: string) =>
  request<OperatorMoveOffer>("POST", "/operator/2fa/move/start", { password, code });
/** A code from the NEW device moves it; every other browser is signed out, this one stays in. */
export const operatorMoveConfirm = (move_token: string, code: string) =>
  request<OperatorMe>("POST", "/operator/2fa/move/confirm", { move_token, code });
export const listOrgs = () => request<{ items: Organisation[] }>("GET", "/operator/orgs");
export const createOrg = (org: { name: string; admin_email: string; admin_name: string }) =>
  request<OrganisationCreated>("POST", "/operator/orgs", org);
/** Needs a fresh code from the operator's own authenticator (step-up). */
export const disableOrg = (id: string, code: string) =>
  request<unknown>("POST", `/operator/orgs/${id}/disable`, { code });
export const enableOrg = (id: string) => request<unknown>("POST", `/operator/orgs/${id}/enable`);
/** Feature 28: allow or stop auto-filing; stopping it also switches it off. */
export const setOrgAutofile = (id: string, granted: boolean) =>
  request<unknown>("PUT", `/operator/orgs/${id}/autofile`, { granted });
/** A disabled organisation, its name typed exactly (step-up); deleted after the grace period. */
export const scheduleDelete = (id: string, name: string, code: string) =>
  request<unknown>("POST", `/operator/orgs/${id}/schedule-delete`, { name, code });
export const cancelDelete = (id: string, code: string) =>
  request<unknown>("POST", `/operator/orgs/${id}/cancel-delete`, { code });
export const listAdmins = (orgId: string) => request<{ items: OrgAdmin[] }>("GET", `/operator/orgs/${orgId}/admins`);
export const createAdmin = (orgId: string, admin: { email: string; name: string; code: string }) =>
  request<AdminCreated>("POST", `/operator/orgs/${orgId}/admins`, admin);
/** Reset two-step needs a fresh code (step-up); the others none. */
export const changeAdmin = (orgId: string, userId: string, change: AdminChange, code: string) =>
  request<{ admin: OrgAdmin }>(
    "POST",
    `/operator/orgs/${orgId}/admins/${userId}/${change}`,
    change === "reset-2fa" ? { code } : undefined,
  );
/** Newest first; `before` is the reply's `next` (a keyset cursor); `action` keeps the actions starting with it. */
export const listActivity = (params: { before?: string; org?: string; action?: string }) =>
  request<{ items: PlatformEvent[]; next: string | null }>("GET", `/operator/activity${query(params)}`);
export const getHealth = () => request<PlatformHealth>("GET", "/operator/health");
/** The model catalogue, every model OpenRouter lists that a scan could use. */
export const getOperatorModels = () => request<{ items: OperatorModel[] }>("GET", "/operator/models");
/** One organisation's AI settings, the defaults it follows and its budget (features 25-27). */
export const getOrgAi = (id: string) => request<OrgAiReply>("GET", `/operator/orgs/${id}/ai`);
/** Shared OpenRouter RPM settings for one organisation and its users. */
export const getOrgModelRates = (id: string) => request<OrgModelRates>("GET", `/operator/orgs/${id}/model-rates`);
export const putOrgModelRate = (id: string, rpm: number | null, code: string) =>
  request<OrgModelRates>("PUT", `/operator/orgs/${id}/model-rates`, { rpm, code });
export const putUserModelRate = (orgId: string, userId: string, rpm: number | null, code: string) =>
  request<OrgModelRates>("PUT", `/operator/orgs/${orgId}/users/${userId}/model-rate`, { rpm, code });
/** Save them, with a fresh code (step-up). */
export const putOrgAi = (
  id: string,
  body: AiValues & { ensemble: boolean; ai_budget_usd: number | null },
  code: string,
) => request<OrgAiReply & { ensemble_off: boolean }>("PUT", `/operator/orgs/${id}/ai`, { ...body, code });
/** The platform defaults and how many organisations follow each area. */
export const getAiDefaults = () => request<AiDefaults>("GET", "/operator/ai-defaults");
/** Save them, with a fresh code: every organisation still on them follows. */
export const putAiDefaults = (body: AiValues, code: string) =>
  request<AiDefaults>("PUT", "/operator/ai-defaults", { ...body, code });
/** Enable or disable one model on the platform, with a fresh code (step-up). The id's
 * slash stays a slash: the route takes the whole author/slug. */
export const setOperatorModelEnabled = (id: string, enabled: boolean, code: string) =>
  request<{ model: { id: string; enabled: boolean; changed: boolean } }>(
    "PUT",
    `/operator/models/${id.split("/").map(encodeURIComponent).join("/")}`,
    { enabled, code },
  );
/** Tokens and dollars per organisation and model; no user or document. */
export const getOperatorUsage = (params: { from?: string; to?: string } = {}) =>
  request<{ from: string; to: string; items: OperatorUsageRow[] }>("GET", `/operator/usage${query(params)}`);
/** Operator insights (F1, F2) over the last `days` days (1 to 90) and the period before. */
export const getOperatorInsights = (days: number) =>
  request<InsightsReply>("GET", `/operator/insights${query({ days })}`);
/** Reading, answers and review (F3); outcome values below K come as null. */
export const getOperatorQuality = (days: number) => request<QualityReply>("GET", `/operator/quality${query({ days })}`);
/** #387: each organisation's numbers over the last `days` days (1 to 90). */
export const getOrgNumbers = (days: number) => request<OrgNumbersReply>("GET", `/operator/org-numbers${query({ days })}`);
/** Compare models (F4): observational, not an A/B test. */
export const getModelBoard = (days: number) => request<BoardReply>("GET", `/operator/model-board${query({ days })}`);
/** PRD 6 F3: replies per organisation and ended week (1 to 12); hidden counts come as null. */
export const getAnswerWeeks = (weeks: number) => request<AnswerWeeksReply>("GET", `/operator/answer-weeks${query({ weeks })}`);

// --- word-level review and Ensemble after the scan (routers/word_review.py, ensemble.py) ---

const doc = (id: string) => `/documents/${encodeURIComponent(id)}`;
const word = (id: string, key: string) => `${doc(id)}/flags/${encodeURIComponent(key)}`;

export const getReviewStatus = (id: string) => request<ReviewStatus>("GET", `${doc(id)}/review-status`);
export const listFlags = (id: string, params: { page?: number; state?: FlagState } = {}) =>
  request<{ items: WordFlag[]; total: number }>("GET", `${doc(id)}/flags${query(params)}`);
/** A reviewer or an admin; `expectedText` is the text they saw (409 changed_since if it moved on). */
export const confirmWord = (id: string, key: string, expectedText: string) =>
  request<WordFlag>("POST", `${word(id, key)}/confirm`, { expected_text: expectedText });
export const editWord = (id: string, key: string, text: string, expectedText: string) =>
  request<WordFlag>("POST", `${word(id, key)}/edit`, { text, expected_text: expectedText });
export const listChanges = (
  id: string,
  params: { source?: "person" | "ensemble"; limit?: number; before?: string } = {},
) => request<{ items: WordChange[]; next_before: string | null }>("GET", `${doc(id)}/changes${query(params)}`);
export const undoChange = (id: string, changeId: string) =>
  request<WordChange>("POST", `${doc(id)}/changes/${encodeURIComponent(changeId)}/undo`);

export const getEnsembleEstimate = (id: string, scope: EnsembleScope, wordKeys: string[] = []) =>
  request<EnsembleEstimate>("GET", `${doc(id)}/ensemble/estimate${query({ scope, word_keys: wordKeys })}`);
export const startEnsemble = (id: string, scope: EnsembleScope, wordKeys: string[] = []) =>
  request<EnsembleRun>("POST", `${doc(id)}/ensemble`, { scope, word_keys: wordKeys });
const run = (id: string, runId: string) => `${doc(id)}/ensemble/${encodeURIComponent(runId)}`;
export const stopEnsemble = (id: string, runId: string) => request<EnsembleRun>("POST", `${run(id, runId)}/stop`);
export const retryEnsemble = (id: string, runId: string) => request<EnsembleRun>("POST", `${run(id, runId)}/retry`);
export const keepAllEnsemble = (id: string, runId: string) =>
  request<{ kept: number; skipped: number }>("POST", `${run(id, runId)}/keep-all`);
const reading = (id: string, readingId: string) => `${doc(id)}/ensemble/readings/${encodeURIComponent(readingId)}`;
export const keepReading = (id: string, readingId: string) =>
  request<{ id: string; decision: "kept"; change_id: string }>("POST", `${reading(id, readingId)}/keep`);
export const revertReading = (id: string, readingId: string) =>
  request<{ id: string; decision: "reverted" }>("POST", `${reading(id, readingId)}/revert`);

// --- AI settings by task (spec 17 Oct 2026) ------------------------------------------

export const getAiSettings = () => request<AiSettings>("GET", "/ai-settings");
/** A member's own choices; null: use the organisation's setting. */
export const putMyAiSettings = (tasks: Partial<Record<AiTask, TaskLevel | null>>) =>
  request<AiSettings>("PUT", "/ai-settings/me", { tasks });
/** Admins only: the organisation's defaults and locks. */
export const putOrgAiSettings = (tasks: Partial<Record<AiTask, { level: TaskLevel; locked: boolean }>>) =>
  request<AiSettings>("PUT", "/ai-settings/organisation", { tasks });

/** Operator: every product feature's platform switch. */
export const getFeatureSwitches = () => request<FeatureSwitch[]>("GET", "/operator/feature-switches");
/** Flip one, with a fresh code (step-up, as every operator change that takes something away). */
export const setFeatureSwitch = (feature: string, enabled: boolean, code: string) =>
  request<FeatureSwitch[]>("PUT", `/operator/feature-switches/${encodeURIComponent(feature)}`, { enabled, code });
const orgSwitches = (orgId: string) => `/operator/orgs/${encodeURIComponent(orgId)}/feature-switches`;
/** Operator: one organisation's own switches (0039), each with the platform's. */
export const getOrgFeatureSwitches = (orgId: string) => request<OrgFeatureSwitch[]>("GET", orgSwitches(orgId));
/** Flip one for that organisation only, with a fresh code (step-up). */
export const setOrgFeatureSwitch = (orgId: string, feature: string, enabled: boolean, code: string) =>
  request<OrgFeatureSwitch[]>("PUT", `${orgSwitches(orgId)}/${encodeURIComponent(feature)}`, { enabled, code });

/** Operator: the sign-in idle timeout (0040), the platform's (no orgId) or one organisation's. */
const timeoutPath = (orgId?: string) =>
  orgId ? `/operator/orgs/${encodeURIComponent(orgId)}/session-timeout` : "/operator/session-timeout";
export const getSessionTimeout = (orgId?: string) => request<SessionTimeout>("GET", timeoutPath(orgId));
/** Set it, with a fresh code (step-up); null: the default, or follow the platform. */
export const setSessionTimeout = (hours: number | null, code: string, orgId?: string) =>
  request<SessionTimeout>("PUT", timeoutPath(orgId), { hours, code });

/** Operator: the folder upload limits (#484), the platform's or one organisation's; saving needs a fresh code. */
const folderLimitsPath = (orgId?: string) =>
  orgId ? `/operator/orgs/${encodeURIComponent(orgId)}/folder-upload-limits` : "/operator/folder-upload-limits";
export const getFolderUploadLimits = () => request<PlatformFolderLimits>("GET", folderLimitsPath());
export const setFolderUploadLimits = (limits: FolderLimits, code: string) =>
  request<PlatformFolderLimits>("PUT", folderLimitsPath(), { ...limits, code });
export const getOrgFolderUploadLimits = (orgId: string) => request<OrgFolderLimits>("GET", folderLimitsPath(orgId));
export const setOrgFolderUploadLimits = (orgId: string, limits: FolderLimits, code: string) =>
  request<OrgFolderLimits>("PUT", folderLimitsPath(orgId), { ...limits, code });

/** Operator: the speed controls (package S, 0053); saving needs a fresh code, null: the server's setting. */
export const getSpeed = () => request<Speed>("GET", "/operator/speed");
export const setSpeed = (values: Record<SpeedName, number | null>, code: string) =>
  request<Speed>("PUT", "/operator/speed", { ...values, code });

/** Operator: the platform-wide test model. */
export const getTestModel = () => request<TestModel>("GET", "/operator/test-model");
/** Start or replace it, with a fresh code (step-up). */
export const startTestModel = (body: { scan_model: string | null; chat_model: string | null; note: string }, code: string) =>
  request<TestModel>("POST", "/operator/test-model", { ...body, code });
/** Undo it: every organisation back on its own settings, with a fresh code (step-up). */
export const stopTestModel = (code: string) => request<TestModel>("DELETE", "/operator/test-model", { code });
