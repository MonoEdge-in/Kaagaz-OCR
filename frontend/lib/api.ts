// The one place the app calls the backend. Every call goes to /api (forwarded to
// FastAPI by next.config.ts), sends the session cookie, and turns an error reply
// into an ApiError with the backend's short code. A reply saying the session
// has ended (401 not_signed_in), or that an admin must set up two-step first (403
// mfa_enrol_required, QA 4 Nov 2026), is also told to the session provider, which
// sends the browser to sign in (lib/session.tsx), where the set-up is offered.

import type { DocumentSearchParams } from "@/components/documents/insideSearch";
import type { DocumentListParams } from "@/components/documents/libraryQuery";
import { sseParser, streamEvent } from "./askStream";
import type { Landing, StreamEvent } from "./askStream";
import type { StoryMatchBody } from "./storyMatches";
import type {
  AccuracyCheck,
  ActivityPage,
  AddedUser,
  AdminChange,
  AdminDepartment,
  DepartmentMember,
  PersonAccess,
  PlacePreview,
  HiddenMove,
  MovePreview,
  Audience,
  AudienceCheck,
  NamedList,
  KeptFromMe,
  AdminCreated,
  AdminStorage,
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
  FolderBack,
  FilePlaced,
  FolderRestored,
  FolderSummary,
  TrashedFolder,
  TrashedFolderContents,
  GoogleRulesChange,
  FolderUploadItem,
  FolderUploadPlan,
  FolderUploadPlanBody,
  FolderUploadPrevious,
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
  OrgGoogleSignIn,
  OrgFolderLimits,
  Organisation,
  OrgModelRates,
  OrgNumbersReply,
  OrganisationCreated,
  PageReadings,
  PageTurnSaved,
  PageSummary,
  DocumentWords,
  PasswordReset,
  PendingConnection,
  PlatformEvent,
  PlatformFolderLimits,
  PlatformHealth,
  LearnedDocument,
  LineSaved,
  QualityReply,
  ReasonCode,
  ReviewChoice,
  ReviewItem,
  ReviewList,
  ReviewReason,
  ReviewStatus,
  Role,
  VerifyAllResult,
  ScanCost,
  ScanCostAsk,
  ScopeBody,
  SessionTimeout,
  SheetCells,
  SignUpConfirming,
  SourcePin,
  Speed,
  SpeedName,
  Stats,
  StorageLimit,
  Tag,
  TaskLevel,
  TestModel,
  TwoStepSetup,
  WaysToSignIn,
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

let googleLinkRequired: (() => void) | null = null;

/** Who to tell when a reply says this session may only connect Google first (#507: 403
 * google_link_required); null stops telling. */
export function onGoogleLinkRequired(listener: (() => void) | null): void {
  googleLinkRequired = listener;
}

function noticeSessionEnd(error: ApiError): ApiError {
  const ended = error.status === 401 && error.code === "not_signed_in";
  if (ended || (error.status === 403 && error.code === "mfa_enrol_required")) sessionEnded?.();
  if (error.status === 403 && error.code === "google_link_required") googleLinkRequired?.();
  return error;
}

// State-changing requests carry this header; the backend refuses them without it,
// which a cross-site form cannot add (CSRF).
const CSRF_HEADER = { "X-Requested-With": "kaagaz" };

/** What every screen says where a feature switched off for the whole product was (403 feature_off). */
export const FEATURE_OFF = "This is turned off for now.";

/** A refusal because the super admin switched the feature off for everyone. */
export const isFeatureOff = (caught: unknown) => caught instanceof ApiError && caught.code === "feature_off";

/** An upload refused because it would take the organisation past its storage limit (#516). Its
 * message is the API's own words, shown as they are; it is never sent again by itself, whatever
 * its status, as more room comes only from deleting files or a higher limit. */
export const isStorageFull = (caught: unknown) => caught instanceof ApiError && caught.code === "storage_full";

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

/**
 * Issue 468: a GET whose answer has not started in this long stops, and fails as a lost
 * connection does (status 0), in these words. Only the wait for the answer to start (its
 * headers) is timed: reading the body is not, so a big answer on a slow link still arrives.
 * Only GETs: Ask's answer and every change are POST, PUT, PATCH or DELETE, and keep waiting as
 * long as they take; an upload's calls have their own, longer bounds (PART_IDLE_MS below).
 */
export const GET_TIMEOUT_MS = 30_000;
export const TOO_SLOW = "Kaagaz is taking too long to answer. Check your connection and try again.";

/** The request never got an answer: no connection (fetch's TypeError), or status 0 (TOO_SLOW). */
export const lostConnection = (caught: unknown) =>
  caught instanceof ApiError ? caught.status === 0 : caught instanceof TypeError;

/** Aborts when either does: the caller's own signal (its abort stays the caller's) or the timeout. */
export function withTimeout(timeout: AbortSignal, signal?: AbortSignal): AbortSignal {
  if (!signal) return timeout;
  const both = new AbortController();
  for (const each of [signal, timeout]) {
    if (each.aborted) both.abort(each.reason);
    else each.addEventListener("abort", () => both.abort(each.reason), { once: true });
  }
  return both.signal;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = method === "GET" ? {} : { ...CSRF_HEADER };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const timeout = method === "GET" ? new AbortController() : null;
  const timer = timeout ? setTimeout(() => timeout.abort(), GET_TIMEOUT_MS) : undefined;
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: timeout ? withTimeout(timeout.signal, signal) : signal,
    });
  } catch (caught) {
    if (timeout?.signal.aborted && !signal?.aborted) throw new ApiError(0, "timeout", TOO_SLOW);
    throw caught;
  } finally {
    clearTimeout(timer); // the answer started (or failed): its body is read untimed
  }
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
 * have it); the first code (adminEnrol) signs in as that admin. With email on (#457), the
 * email is confirmed first and the organisation waits for approval: SignUpConfirming. */
export const signUp = (body: { organisation_name: string; name: string; email: string; password: string }) =>
  request<TwoStepSetup | SignUpConfirming>("POST", "/auth/signup", body);
/** Who is signed in, 200 either way (issue 378): a signed-out visit logs no expected 401. */
export const currentSession = () =>
  request<{ signed_in: true; me: Me } | { signed_in: false; providers?: string[] }>("GET", "/auth/session");

// Ways to sign in (#457 phase 1): every route is 404 while no provider is switched on.
/** After a provider for anyone with two-step sign-in on: the code (the second-step cookie). */
export const oidcCode = (code: string) => request<Me>("POST", "/auth/oidc/code", { code });
/** "isn't connected": the provider's email, kept out of the address, read once. */
export const oidcNotice = () => request<{ email: string | null }>("GET", "/auth/oidc/notice");
export const identities = () => request<WaysToSignIn>("GET", "/auth/identities");
/** The password (and the code with two-step sign-in on), then the provider's address to open. */
export const connectProvider = (provider: string, password: string, code?: string) =>
  request<{ url: string }>("POST", `/auth/oidc/${encodeURIComponent(provider)}/connect`, {
    password,
    ...(code === undefined ? {} : { code }),
  });
/** Disconnect with the password, or cancel a waiting one with none (#457 review). The reply is
 * the new list. */
export const removeIdentity = (id: string, password?: string) =>
  request<WaysToSignIn>(
    "POST",
    `/auth/identities/${encodeURIComponent(id)}/remove`,
    password === undefined ? {} : { password },
  );

/** The first code from the set-up (sign-in or sign-up) turns two-step on and signs in. */
export const adminEnrol = (enrolToken: string, code: string) =>
  request<Me>("POST", "/auth/admin/enrol", { enrol_token: enrolToken, code });
/** Account, Two-step sign-in, Turn on (#457, any role): the password, then the set-up key; the
 * first code (adminEnrol) turns it on. */
export const startTwoStep = (password: string) => request<TwoStepSetup>("POST", "/auth/2fa/start", { password });
/** Turn off, for anyone: the password and a current code. An admin's stays off until they turn
 * it on again (Tushar, 30 Oct 2026). */
export const turnOffTwoStep = (password: string, code: string) =>
  request<Me>("POST", "/auth/2fa/off", { password, code });
/** With two-step sign-in on, the code too (#457). */
export const changePassword = (current: string, next: string, code?: string) =>
  request<Me>("POST", "/auth/password", {
    current_password: current,
    new_password: next,
    ...(code === undefined ? {} : { code }),
  });

// Email (#457 phase 0): every route is 404 while the server's email is off.
/** Resolves while email is on (the sign-in page's "Forgot password?"); rejects with 404 when off. */
export const emailOpen = () => request<{ enabled: boolean }>("GET", "/auth/email");
/** ``pending``: the confirmed person's organisation is a sign-up waiting for approval. */
export const confirmEmail = (token: string) =>
  request<{ ok: true; pending: boolean }>("POST", "/auth/email/confirm", { token });
/** ``sent`` is false when the email could not be sent. */
export const sendConfirmEmail = () => request<{ ok: true; sent: boolean }>("POST", "/auth/email/send-confirm");
/** "Send the link again" with no session (a waiting sign-up's admin): always the same reply. */
export const resendConfirmEmail = (email: string) =>
  request<{ message: string }>("POST", "/auth/email/resend-confirm", { email });
/** Always the same reply, whoever the email belongs to. */
export const forgotPassword = (email: string) => request<{ message: string }>("POST", "/auth/forgot", { email });
export const resetPassword = (token: string, newPassword: string) =>
  request<{ ok: boolean }>("POST", "/auth/reset", { token, new_password: newPassword });
export const acceptInvite = (token: string, newPassword: string) =>
  request<{ ok: boolean }>("POST", "/auth/invite/accept", { token, new_password: newPassword });
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
/** Brings a document in Trash back, with every version of it; #542: and the folder that came
 * back with it, when it went to Trash with its folder (null otherwise). */
export const restoreDocument = (id: string) =>
  request<DocumentSummary & { restored_folder?: FolderBack | null; placed?: FilePlaced | null }>(
    "POST",
    `/documents/${id}/restore`,
  );
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
/** A move (PRD 7, #601): an admin who moves a file where they can't see it gets `{id, hidden:
 * true}`, not the file; the move stands. */
export const moveDocument = (id: string, folderId: string | null) =>
  request<DocumentSummary | HiddenMove>("PATCH", `/documents/${id}`, { folder_id: folderId });
export const isHiddenMove = (reply: DocumentSummary | HiddenMove): reply is HiddenMove =>
  "hidden" in reply && reply.hidden === true;
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
/** #521 Make it a separate file: this version leaves its family under `name`; family_id is the family it left. */
export const separateVersion = (id: string, name: string) =>
  request<{ document: DocumentSummary; family_id: string }>("POST", `${doc(id)}/separate`, { name });
/** #521: Undo of Make it a separate file, back in `familyId` as its newest version, with `name` again. */
export const undoSeparate = (id: string, familyId: string, name?: string) =>
  request<DocumentSummary>("POST", `${doc(id)}/separate/undo`, { family_id: familyId, name });
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

/** Turn pages for everyone (#600): only the pages named, turn 0 puts one back; the reply is the whole set. */
export const savePageTurnsForEveryone = (
  id: string,
  changes: { page_no: number; turn: 0 | 90 | 180 | 270 }[],
  userId: string
) =>
  request<{ page_turns: PageTurnSaved[] }>("PATCH", `/documents/${encodeURIComponent(id)}/page-turns`, {
    changes,
    user_id: userId,
  });

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
  // No navigator.onLine (Node, where CI runs the tests) reads as online; a browser always has one.
  const online = typeof navigator === "undefined" || navigator.onLine !== false;
  if (online) return new Promise((resolve) => setTimeout(resolve, delay));
  return new Promise((resolve) => window.addEventListener("online", () => resolve(), { once: true }));
}

/** Told while an upload call waits to go again (true) and why, then once it goes (false). */
export type UploadWaiting = (waiting: boolean, why?: "connection" | "server") => void;

/** A failing server (5xx) is asked this many more times, then the row fails with Retry. */
const SERVER_RETRIES = 4;

// An upload call that never answers (a server restart mid-request) is given up on, then sent
// again as a failing server's is (noAnswer), so a row never waits for ever (production QA, 5 Nov
// 2026): after SERVER_RETRIES more it fails with Retry (review of #564).
/** A part with no progress and no answer for this long. Not the whole part: a slow line takes longer. */
const PART_IDLE_MS = 120_000;
/** The upload's JSON calls; `complete` stores and checks the whole file, so it gets longer. */
const CALL_TIMEOUT_MS = 60_000;
const COMPLETE_TIMEOUT_MS = 300_000;

/** No answer in time: a 5xx to retryWhileOffline (counted, "server"), Retry on the row, TOO_SLOW its words. */
const noAnswer = () => new ApiError(504, "timeout", TOO_SLOW);

/** request(), given up after `ms` with no answer (noAnswer), for retryWhileOffline. */
async function requestInTime<T>(ms: number, method: string, path: string, body?: unknown): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await request<T>(method, path, body, controller.signal);
  } catch (error) {
    throw controller.signal.aborted ? noAnswer() : error;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Gives up on `xhr` once `wait(ms)` has run out with no new wait or `stop()`: rejected with
 * noAnswer (so retryWhileOffline sends it again, as a failing server's), then aborted.
 */
function giveUpWhenIdle(xhr: XMLHttpRequest, reject: (error: unknown) => void) {
  let idle: ReturnType<typeof setTimeout> | undefined;
  const stop = () => clearTimeout(idle);
  const wait = (ms: number) => {
    stop();
    idle = setTimeout(() => {
      reject(noAnswer());
      xhr.abort();
    }, ms);
  };
  return { wait, stop };
}

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
      if (isStorageFull(error)) throw error; // never again by itself, even as a 5xx (#516)
      const paced = rateLimitWait(error, delay);
      if (paced !== null) {
        await waitForNetwork(paced);
        continue;
      }
      const lost = lostConnection(error);
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
    const idle = giveUpWhenIdle(xhr, reject);
    xhr.upload.onprogress = (event) => {
      idle.wait(PART_IDLE_MS);
      if (event.lengthComputable) onProgress(event.loaded);
    };
    xhr.onload = async () => {
      idle.stop();
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else if (xhr.status === 0) reject(new ApiError(0, "network", "Could not reach the server."));
      else reject(noticeSessionEnd(await errorFrom(new Response(xhr.responseText, {
        status: xhr.status,
        headers: { "Content-Type": "application/json", "Retry-After": xhr.getResponseHeader("Retry-After") ?? "" },
      }))));
    };
    xhr.onerror = () => {
      idle.stop();
      reject(new TypeError("Network error"));
    };
    xhr.onabort = () => {
      idle.stop();
      reject(new TypeError("Upload interrupted"));
    };
    xhr.send(body);
    idle.wait(PART_IDLE_MS);
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
    () => requestInTime<{ candidates: { id: string; parts: Record<string, string> }[] }>(
      CALL_TIMEOUT_MS, "POST", "/documents/upload-sessions/candidates", identified,
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
  // shortcut: a start whose answer never came is sent again and makes a second session; the
  // first is never used, and the purge removes it a week on.
  const session = await retryWhileOffline(
    () => requestInTime<{ id: string; part_size: number; part_count: number } | UploadResult>(
      CALL_TIMEOUT_MS, "POST", "/documents/upload-sessions", { ...identified, resume_session_id: resumeId },
    ),
    onWaiting,
  );
  // A folder upload's item already in: the server answers with the upload itself (#484).
  if ("document" in session) return session;
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
    () => requestInTime<UploadResult>(COMPLETE_TIMEOUT_MS, "POST", `/documents/upload-sessions/${session.id}/complete`, {}),
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
/** This person's latest upload of a folder named `sourceName` whose folder is still there, or null. */
export const getPreviousFolderUpload = (sourceName: string) =>
  request<{ previous: FolderUploadPrevious | null }>("GET", `/folder-uploads/previous${query({ source_name: sourceName })}`);
/** One page of a folder upload's items in seq order, after `after` (-1: from the start). */
export const listFolderItems = (id: string, view: FolderItemView, after: number, limit: number) =>
  request<FolderItemsPage>("GET", `${folderUpload(id)}/items${query({ view, after, limit })}`);

/**
 * One multipart POST with upload progress (XHR); the reply is JSON. Never left waiting: given up
 * on as a part is while it sends, and as a `complete` once all of it has gone (the server then
 * stores and checks the file); sent again for the same item, which answers once it is in.
 */
function postFile(path: string, form: FormData, onProgress: (fraction: number) => void): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-Requested-With", "kaagaz");
    const idle = giveUpWhenIdle(xhr, reject);
    xhr.upload.onprogress = (event) => {
      const sent = event.lengthComputable && event.loaded >= event.total;
      idle.wait(sent ? COMPLETE_TIMEOUT_MS : PART_IDLE_MS);
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    };
    xhr.onload = async () => {
      idle.stop();
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
    xhr.onerror = () => {
      idle.stop();
      reject(new TypeError("Network error"));
    };
    xhr.onabort = () => {
      idle.stop();
      reject(new TypeError("Upload interrupted"));
    };
    xhr.send(form);
    idle.wait(PART_IDLE_MS);
  });
}

/**
 * Send one file of a folder upload to its item. Up to 8 MiB it is one call to
 * POST /documents?folder_upload_item_id= (in the query, so the server's rate limit gives it the
 * folder upload's bucket) at the folder upload's own pace (`turn`); a bigger file takes the
 * resumable path at the upload pace. Neither names a folder or a batch: the server takes both
 * from the item. A lost connection, a failing server (5xx) and a 429's Retry-After are waited out
 * as for any upload (retryWhileOffline), always for the same item.
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
      if (reading?.read_mode) form.append("read_mode", reading.read_mode);
      if (reading?.read_model) form.append("read_model", reading.read_model);
      return postFile(`/documents${query({ folder_upload_item_id: itemId })}`, form, onProgress);
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
/** Rename, move or order. An explicit parent_id: null moves the folder to the top level. PRD 7
 * (#601): a move that opens what it holds to the admin making it takes their `password` (403
 * password_required without it). */
export const updateFolder = (
  id: string,
  changes: { name?: string; parent_id?: string | null; sort_order?: number | null; password?: string },
) => request<Folder>("PATCH", `/folders/${id}`, changes);
export const deleteFolder = (id: string) => request<void>("DELETE", `/folders/${id}`);
/** #537, an admin's: move what is inside to `to` (null: the top level) or send it all to Trash,
 * then delete the folder. The reply: what moved, or the files that went to Trash. PRD 7 (#601): a
 * keep that changes who can see it is 409 access_would_change until it is sent with `confirm`;
 * a confirmed one that opens it to the admin takes their `password` (403 password_required). */
export const deleteFolderWithContents = (
  id: string,
  what: { contents: "keep"; to: string | null; confirm?: boolean; password?: string } | { contents: "trash" },
) =>
  request<{ files: number; folders: number }>(
    "DELETE",
    `/folders/${id}${query({
      contents: what.contents,
      to: what.contents === "keep" ? (what.to ?? undefined) : undefined,
      confirm: what.contents === "keep" && what.confirm ? true : undefined,
    })}`,
    what.contents === "keep" && what.password !== undefined ? { password: what.password } : undefined,
  );
export const folderSummary = (id: string) => request<FolderSummary>("GET", `/folders/${id}/summary`);
/** #542: Trash's folders; one opened (its files: listDocuments with trashed_folder_id); Restore puts
 * one back where it was; delete forever has a document's rule (an admin with two-step, or a typed
 * password). Under /documents/trash, where the typed password's cookie is sent. */
export const listTrashedFolders = () => request<{ items: TrashedFolder[] }>("GET", "/documents/trash/folders");
export const getTrashedFolder = (id: string) =>
  request<TrashedFolderContents>("GET", `/documents/trash/folders/${id}`);
export const restoreTrashedFolder = (id: string) =>
  request<FolderRestored>("POST", `/documents/trash/folders/${id}/restore`);
/** Replies in a row that may leave the count where it was (another tab deleting the same folder)
 * before deleteTrashedFolderForever stops asking. */
const STUCK_REPLIES = 3;

/**
 * B18 (the backend's A12): a folder in Trash is deleted for good a part at a time. While files
 * remain the server says 202 {remaining}; this asks again, telling `onLeft` how many are left,
 * until its 204. A count that stops going down for STUCK_REPLIES replies stops it, as an error,
 * so a stuck server is never asked for ever.
 */
export async function deleteTrashedFolderForever(id: string, onLeft?: (remaining: number) => void): Promise<void> {
  let left = Number.POSITIVE_INFINITY;
  let stuck = 0;
  for (;;) {
    const reply = await request<{ remaining?: unknown } | null | undefined>("DELETE", `/documents/trash/folders/${id}/permanent`);
    if (reply === undefined) return; // 204: all gone
    const remaining = typeof reply?.remaining === "number" ? reply.remaining : left; // no count: no progress
    stuck = remaining < left ? 0 : stuck + 1;
    left = remaining;
    if (Number.isFinite(left)) onLeft?.(left);
    if (stuck >= STUCK_REPLIES) throw new ApiError(409, "no_progress", "Deleting made no progress. Try again in a minute.");
  }
}
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
) => request<ChatResponse>("POST", "/chat", chatBody(question, conversationId, scope, choice, reply), signal);

const chatBody = (
  question: string,
  conversationId?: string,
  scope?: ScopeBody,
  choice?: AnswerChoice,
  reply?: ClarifyReply,
  questionId?: string,
) => ({
  question,
  ...(conversationId ? { conversation_id: conversationId } : {}),
  ...(scope ? { scope } : {}),
  ...(choice ?? {}),
  ...(reply ? { reply } : {}),
  ...(questionId ? { question_id: questionId } : {}),
});

/** Why a stream broke before `done`, as logged (console.warn) by askStream. */
type BrokenBecause = "no_connection" | "no_route" | "server_error" | "no_body" | "stream_broken" | "idle";

/** The stream broke before `done` (`because`). With no `landing`, askStream asks POST /chat
 * instead (with the same question id, so the server never answers it twice); after `started`, the
 * server finishes and saves the answer anyway, so askStream fetches it there. */
class StreamFailed extends Error {
  constructor(
    readonly because: BrokenBecause,
    readonly landing: Landing | null = null,
  ) {
    super("stream failed");
  }
}

/** No byte for this long means the stream is gone (the server pings every 10 s): it is given up. */
export const IDLE_MS = 30_000;
/** The waits between looks for a broken stream's saved answer after `started` (the last one again
 * and again), for RECOVER_FOR_MS at most: the server may work on an answer for
 * ANSWER_STREAM_MAX_SECONDS (180 s by default), so that long and a little more. */
export const RECOVER_STEPS_MS = [2_000, 4_000, 8_000, 16_000, 30_000] as const;
export const RECOVER_FOR_MS = 190_000;
/** Said when the saved answer has not appeared in RECOVER_FOR_MS: honest, and promising nothing. */
export const STILL_SAVING = "Couldn't get the answer yet. Open this conversation again in a minute.";

/** An `error` event's code as the status /chat would have answered with, for askErrorText. An
 * unknown code is a refusal in the server's own words (shown as they are). */
const STREAM_ERROR_STATUS: Record<string, number> = {
  bad_request: 400,
  not_signed_in: 401,
  feature_off: 403,
  not_found: 404,
  conflict: 409,
  rate_limited: 429,
  answer_timeout: 400, // ANSWER_STREAM_MAX_SECONDS: its words shown as they are, never asked again
  internal_error: 500,
  backend_unavailable: 503,
};

const isChatResponse = (value: unknown): value is ChatResponse => {
  const reply = value as Partial<ChatResponse> | null;
  return typeof reply?.conversation_id === "string" && typeof reply.question?.id === "string" && typeof reply.answer?.id === "string";
};

/** Reads POST /chat/stream's events until `done` (its reply) or `error` (thrown as /chat's error). */
async function streamChat(body: unknown, signal: AbortSignal | undefined, onEvent: (event: StreamEvent) => void) {
  let response: Response;
  try {
    response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { ...CSRF_HEADER, "Content-Type": "application/json", Accept: "text/event-stream" },
      credentials: "same-origin",
      body: JSON.stringify(body),
      signal,
    });
  } catch (caught) {
    if (signal?.aborted) throw caught;
    throw new StreamFailed("no_connection");
  }
  if (!response.ok) {
    if (response.status === 404 || response.status === 405) throw new StreamFailed("no_route");
    if (response.status >= 500) throw new StreamFailed("server_error");
    throw noticeSessionEnd(await errorFrom(response)); // refused as /chat would refuse it: no second ask
  }
  if (!response.body) throw new StreamFailed("no_body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  // Set by the parser's callback, so held in an object TypeScript does not narrow to null.
  const state: {
    outcome: { reply: ChatResponse } | { error: ApiError } | { failed: true } | null;
    landing: Landing | null;
  } = { outcome: null, landing: null };
  const parser = sseParser((name, data) => {
    if (state.outcome !== null) return;
    let value: unknown;
    try {
      value = JSON.parse(data);
    } catch {
      state.outcome = { failed: true };
      return;
    }
    if (name === "done") {
      const reply = (value as { reply?: unknown } | null)?.reply;
      state.outcome = isChatResponse(reply) ? { reply } : { failed: true };
    } else if (name === "error") {
      const { code, message } = (value ?? {}) as { code?: unknown; message?: unknown };
      const known = typeof code === "string" ? code : "error";
      const words = typeof message === "string" && message.trim() ? message : "Something went wrong.";
      state.outcome = { error: noticeSessionEnd(new ApiError(STREAM_ERROR_STATUS[known] ?? 400, known, words)) };
    } else {
      const event = streamEvent(name, value);
      if (event?.type === "started") state.landing = event.landing;
      if (event) onEvent(event);
    }
  });
  try {
    while (state.outcome === null) {
      const read = await readWithin(reader, IDLE_MS);
      if (read === "idle") {
        reader.cancel().catch(() => undefined);
        throw new StreamFailed("idle", state.landing);
      }
      if (read.done) {
        parser.push(decoder.decode());
        parser.end();
        break;
      }
      parser.push(decoder.decode(read.value, { stream: true }));
    }
  } catch (caught) {
    if (signal?.aborted || caught instanceof StreamFailed) throw caught;
    throw new StreamFailed("stream_broken", state.landing);
  }
  const { outcome } = state;
  if (outcome === null || "failed" in outcome) throw new StreamFailed("stream_broken", state.landing);
  reader.cancel().catch(() => undefined); // anything after done or error is not read
  if ("error" in outcome) throw outcome.error;
  return outcome.reply;
}

/** The reader's next chunk, or "idle" when none comes in `ms`. */
async function readWithin(reader: ReadableStreamDefaultReader<Uint8Array>, ms: number) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const idle = new Promise<"idle">((resolve) => {
    timer = setTimeout(() => resolve("idle"), ms);
  });
  try {
    return await Promise.race([reader.read(), idle]);
  } finally {
    clearTimeout(timer);
  }
}

/** Waits `ms`, or rejects as fetch does once `signal` aborts. */
function pause(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const stopped = () => {
      clearTimeout(timer);
      reject(signal?.reason ?? new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", stopped);
      resolve();
    }, ms);
    if (signal?.aborted) stopped();
    else signal?.addEventListener("abort", stopped, { once: true });
  });
}

/**
 * The answer the server saved where `started` said: GET /conversations/{id}, again after each
 * of RECOVER_STEPS_MS (the last one again and again), until the question's answer message is
 * there, for RECOVER_FOR_MS at most; then an ApiError "answer_pending" (STILL_SAVING) whose
 * details name the conversation. Not there yet (a 404 while the new conversation is unsaved, a
 * lost connection): asked again; any other refusal (the session ended) is its own error. Also
 * how Stop still shows the answer (ChatScreen).
 */
export async function savedAnswer(landing: Landing, signal?: AbortSignal): Promise<ChatResponse> {
  const ends = Date.now() + RECOVER_FOR_MS;
  for (let step = 0; ; step += 1) {
    try {
      const { messages } = await request<Conversation>("GET", `/conversations/${landing.conversationId}`, undefined, signal);
      const at = messages.findIndex((each) => each.id === landing.questionId);
      const answer = at < 0 ? undefined : messages.slice(at + 1).find((each) => each.role === "assistant");
      const question = messages[at];
      if (question && answer) return { conversation_id: landing.conversationId, question, answer };
    } catch (caught) {
      if (signal?.aborted) throw caught;
      const waiting = caught instanceof ApiError && (caught.status === 404 || caught.status === 0);
      if (!waiting && !lostConnection(caught)) throw caught;
    }
    const wait = RECOVER_STEPS_MS[Math.min(step, RECOVER_STEPS_MS.length - 1)] ?? 30_000;
    if (Date.now() + wait > ends) {
      throw new ApiError(503, "answer_pending", STILL_SAVING, { conversation_id: landing.conversationId });
    }
    await pause(wait, signal);
  }
}

/**
 * A random version 4 uuid. crypto.randomUUID exists only in a secure context (https or
 * localhost); elsewhere (a server reached over plain http) it is built from getRandomValues,
 * which every browser has (B15).
 */
export function randomId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8]! & 0x3f) | 0x80; // the RFC 4122 variant
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * #535: ask with the answer streamed (POST /chat/stream, the same body and auth as ask). Each
 * real stage, the sources found and each verified sentence go to `onEvent` as they come; the
 * reply is `done`'s, the same as /chat's. The question's id is chosen here (a uuid) and sent
 * with the stream and with its fallback, so the server never answers or saves one question twice.
 * On a network or stream failure before `done` (logged with why; no byte for IDLE_MS is one):
 * once `started` has come, the server finishes and saves the answer anyway, so it is fetched
 * from that conversation (savedAnswer), never asked again; before `started`, it asks POST /chat
 * once instead. No error when either works. Stop (`signal`) aborts any of them and never falls
 * back: the server still saves the answer. `questionId`: Ask's own retry (hotfix, 15 Nov 2026)
 * sends its first try's again, so a try the server saved is answered from that save.
 */
export async function askStream(
  question: string,
  conversationId: string | undefined,
  scope: ScopeBody | undefined,
  choice: AnswerChoice | undefined,
  reply: ClarifyReply | undefined,
  signal: AbortSignal | undefined,
  onEvent: (event: StreamEvent) => void,
  questionId: string = randomId(),
): Promise<ChatResponse> {
  const body = chatBody(question, conversationId, scope, choice, reply, questionId);
  try {
    return await streamChat(body, signal, onEvent);
  } catch (caught) {
    if (!(caught instanceof StreamFailed) || signal?.aborted) throw caught;
    if (caught.landing) {
      console.warn("Ask's stream broke after it started; fetching the saved answer:", caught.because);
      return savedAnswer(caught.landing, signal);
    }
    console.warn("Ask's stream broke before it started; asking POST /api/chat once instead:", caught.because);
    return request<ChatResponse>("POST", "/chat", body, signal);
  }
}
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
}) => request<AddedUser>("POST", "/admin/users", user);
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
/** What this organisation has stored, Trash included, and its storage limit (#516). */
export const getAdminStorage = () => request<AdminStorage>("GET", "/admin/storage");
/** "Approve new sign-in connections automatically" (#457 phase 1); nothing else changes. */
export const setAutoApprove = (on: boolean) =>
  request<CustomerSettings>("PATCH", "/admin/settings", { auto_approve_connections: on });
/** Connections to approve (#457 phase 1); each reply is the list left. */
export const pendingConnections = () =>
  request<{ items: PendingConnection[] }>("GET", "/admin/identities?status=pending");
export const approveConnection = (id: string) =>
  request<{ items: PendingConnection[] }>("POST", `/admin/identities/${encodeURIComponent(id)}/approve`);
export const declineConnection = (id: string) =>
  request<{ items: PendingConnection[] }>("POST", `/admin/identities/${encodeURIComponent(id)}/decline`);
/** Every connected account of this person goes, and they are signed out; the password stays. */
export const removeConnections = (id: string) =>
  request<AdminUser>("POST", `/admin/users/${encodeURIComponent(id)}/remove-connections`);

// --- access (PRD 7, #601) --------------------------------------------------------------------
// Every write that can give the admin themselves access takes their `password` (403
// password_required without it; components/access/passwordStep.ts).

/** A path part: an id typed into the address bar may hold "/" or "?". */
const segment = (part: string) => encodeURIComponent(part);
export const getPersonAccess = (userId: string) => request<PersonAccess>("GET", `/admin/users/${segment(userId)}/access`);
export const setManagement = (userId: string, on: boolean, password?: string) =>
  request<AdminUser>("PUT", `/admin/users/${segment(userId)}/management`, { on, password });
/** Adds the person to the department, or sets their Head tick; `head` left out keeps the tick. */
export const putMembership = (userId: string, departmentId: string, change: { head?: boolean; password?: string } = {}) =>
  request<{ head: boolean; changed: boolean }>("PUT", `/admin/users/${segment(userId)}/departments/${segment(departmentId)}`, change);
export const removeMembership = (userId: string, departmentId: string) =>
  request<void>("DELETE", `/admin/users/${segment(userId)}/departments/${segment(departmentId)}`);
export const listAdminDepartments = () =>
  request<{ items: AdminDepartment[]; keeps_files_to_some_people: boolean }>("GET", "/admin/departments");
export const listDepartmentMembers = (departmentId: string) =>
  request<{ items: DepartmentMember[] }>("GET", `/admin/departments/${segment(departmentId)}/members`);
export const departmentOnlyPreview = (departmentId: string, on: boolean) =>
  request<PlacePreview>("GET", `/admin/departments/${segment(departmentId)}/department-only/preview${query({ on })}`);
export const setDepartmentOnly = (departmentId: string, on: boolean, password?: string) =>
  request<{ department_only: boolean; members: number; warnings: string[] }>(
    "PUT",
    `/admin/departments/${segment(departmentId)}/department-only`,
    { on, password },
  );
export const addNamed = (folderId: string, userId: string, password?: string) =>
  request<{ changed: boolean }>("PUT", `/folders/${segment(folderId)}/named/${segment(userId)}`, { password });
export const removeNamed = (folderId: string, userId: string) =>
  request<void>("DELETE", `/folders/${segment(folderId)}/named/${segment(userId)}`);
export const giveFile = (documentId: string, userId: string, password?: string) =>
  request<{ versions: number; changed: boolean }>("PUT", `/documents/${segment(documentId)}/shares/${segment(userId)}`, { password });
export const takeFile = (documentId: string, userId: string) =>
  request<void>("DELETE", `/documents/${segment(documentId)}/shares/${segment(userId)}`);

// PR 11b: the library's access.
/** Give files to people at once (at most 100 files and 20 people): how many pairs were new. */
export const giveFiles = (documentIds: readonly string[], userIds: readonly string[], password?: string) =>
  request<{ given: number }>("POST", "/documents/shares", { document_ids: documentIds, user_ids: userIds, password });
export type AudienceKind = "documents" | "folders";
export const getAudience = (kind: AudienceKind, id: string) => request<Audience>("GET", `/${kind}/${segment(id)}/audience`);
export const checkAudience = (kind: AudienceKind, id: string, userId: string) =>
  request<AudienceCheck>("GET", `/${kind}/${segment(id)}/audience${query({ user_id: userId })}`);
/** An admin session's alone: the files here they can't open, names only (anyone else: 403/404). */
export const keptFromMe = (folderId: string) => request<KeptFromMe>("GET", `/folders/${segment(folderId)}/kept-from-me`);
export const listNamed = (folderId: string) => request<NamedList>("GET", `/folders/${segment(folderId)}/named`);
/** Who would lose and gain the folder's files were it private (`on`), with `named` named now. */
export const privatePreview = (folderId: string, on: boolean, named: readonly string[] = []) =>
  request<PlacePreview>("GET", `/folders/${segment(folderId)}/private/preview${query({ on, named })}`);
/** Make it private, naming people on it (and, PR 10c, `dropNamed`: people named before taken off in
 * the same transaction; sent only when there are some), or open again (`named` only with `on`). */
export const setPrivate = (folderId: string, on: boolean, named: readonly string[], password?: string, dropNamed: readonly string[] = []) =>
  request<{ private: boolean; named: Person[]; you_lose_access: boolean }>("PUT", `/folders/${segment(folderId)}/private`, {
    on,
    named,
    ...(dropNamed.length ? { drop_named: dropNamed } : {}),
    password,
  });
/** PRD 7 PR 10c (#601): who would lose and gain a file, or what a folder holds, were it moved into
 * `into` (null: the top level). The person a file is held for gets counts only (`losing_count`,
 * `gaining_count`, empty lists); someone who may not make such a move gets 403. */
export const documentMovePreview = (documentId: string, into: string | null) =>
  request<MovePreview>("GET", `/documents/${segment(documentId)}/move-preview${query({ folder_id: into ?? undefined })}`);
export const folderMovePreview = (folderId: string, into: string | null) =>
  request<MovePreview>("GET", `/folders/${segment(folderId)}/move-preview${query({ parent_id: into ?? undefined })}`);
/** ``autofileOn`` left out: the switch is left as it is (feature 28). ``google`` (#507), when
 * given, replaces the three Google sign-in rules; left out, they stay. */
export const updateSettings = (maxUploadMb: number | null, autofileOn?: boolean, google?: GoogleRulesChange) =>
  request<CustomerSettings>("PATCH", "/admin/settings", {
    max_upload_mb: maxUploadMb,
    ...(autofileOn === undefined ? {} : { autofile_on: autofileOn }),
    ...(google === undefined ? {} : { google }),
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
/** #457: a pending sign-up whose admin confirmed their email becomes active. */
export const approveOrg = (id: string) => request<unknown>("POST", `/operator/orgs/${id}/approve`);
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
/** The approximate scan cost a page at these settings (#463): read only, no code. */
export const postScanCost = (body: ScanCostAsk) => request<ScanCost>("POST", "/operator/scan-cost", body);
/** Check an organisation's accuracy against max settings at these scan settings (with the
 * most output tokens a page and `dynamic`), on 20, 50 or 100 recent pages, with a fresh code (step-up), as
 * the AI settings save. 409 check_running, 429, 400 no_pages. */
export const postAccuracyCheck = (orgId: string, body: Omit<ScanCostAsk, "customer_id"> & { pages: 20 | 50 | 100 }, code: string) =>
  request<{ id: string }>("POST", `/operator/orgs/${orgId}/accuracy-check`, { ...body, code });
/** The organisation's latest accuracy check, at any settings; null when it has none (404). */
export const getLatestAccuracyCheck = (orgId: string) =>
  request<AccuracyCheck>("GET", `/operator/orgs/${orgId}/accuracy-check/latest`).catch((caught: unknown) => {
    if (caught instanceof ApiError && caught.status === 404) return null;
    throw caught;
  });
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
/** #527: a reviewer or an admin corrects a whole line of the clean copy; `expectedText` as editWord's. */
export const editLine = (id: string, key: string, text: string, expectedText: string) =>
  request<LineSaved>("POST", `${doc(id)}/lines/${encodeURIComponent(key)}/edit`, { text, expected_text: expectedText });
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

/** Operator: whether one organisation's admins must have two-step sign-in (#481, 0068); off by default. */
const adminTwoStepPath = (orgId: string) => `/operator/orgs/${encodeURIComponent(orgId)}/admin-two-step`;
export const getAdminTwoStep = (orgId: string) => request<{ required: boolean }>("GET", adminTwoStepPath(orgId));
/** Require it or stop requiring it, with a fresh code (step-up). */
export const setAdminTwoStep = (orgId: string, required: boolean, code: string) =>
  request<{ required: boolean }>("PUT", adminTwoStepPath(orgId), { required, code });
/** Operator: Google sign-in (#507), one organisation's (null: follow the platform) and the
 * platform's default; each change needs a fresh code (step-up). */
const orgGooglePath = (orgId: string) => `/operator/orgs/${encodeURIComponent(orgId)}/google-signin`;
export const getOrgGoogleSignIn = (orgId: string) => request<OrgGoogleSignIn>("GET", orgGooglePath(orgId));
export const setOrgGoogleSignIn = (orgId: string, on: boolean | null, code: string) =>
  request<OrgGoogleSignIn>("PUT", orgGooglePath(orgId), { on, code });
export const getGoogleSignIn = () => request<{ on: boolean }>("GET", "/operator/google-signin");
export const setGoogleSignIn = (on: boolean, code: string) =>
  request<{ on: boolean }>("PUT", "/operator/google-signin", { on, code });
/** Operator: the folder upload limits (#484), the platform's or one organisation's; saving needs a fresh code. */
const folderLimitsPath = (orgId?: string) =>
  orgId ? `/operator/orgs/${encodeURIComponent(orgId)}/folder-upload-limits` : "/operator/folder-upload-limits";
export const getFolderUploadLimits = () => request<PlatformFolderLimits>("GET", folderLimitsPath());
export const setFolderUploadLimits = (limits: FolderLimits, code: string) =>
  request<PlatformFolderLimits>("PUT", folderLimitsPath(), { ...limits, code });
export const getOrgFolderUploadLimits = (orgId: string) => request<OrgFolderLimits>("GET", folderLimitsPath(orgId));
export const setOrgFolderUploadLimits = (orgId: string, limits: FolderLimits, code: string) =>
  request<OrgFolderLimits>("PUT", folderLimitsPath(orgId), { ...limits, code });

/** Operator: the storage limit (#516), the platform's default (no orgId) or one organisation's. */
const storageLimitPath = (orgId?: string | null) =>
  orgId ? `/operator/orgs/${encodeURIComponent(orgId)}/storage-limit` : "/operator/storage-limit";
export const getStorageLimit = (orgId?: string) => request<StorageLimit>("GET", storageLimitPath(orgId));
/** Save it with a fresh code (step-up). null orgId: the platform's default; null storageGb: an
 * organisation follows the platform, the platform goes back to the default (50 GB). */
export const setStorageLimit = (orgId: string | null, storageGb: number | null, code: string) =>
  request<StorageLimit>("PUT", storageLimitPath(orgId), { storage_gb: storageGb, code });

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
