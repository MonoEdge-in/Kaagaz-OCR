"use client";

// The chat screen: the conversation list (a side pane from 1024 px, a drawer
// below), the open thread and the composer. The thread is the one scroll area
// and a log. The log itself is not live (it would read the whole history aloud);
// one hidden status line says short things instead. While a question is out, it
// shows as pending and the conversation cannot be switched. Only the latest
// conversation opened may fill the thread, so a slow reply for an earlier one
// is dropped. In the composer's toolbar, the scope chip and picker (ScopePicker.tsx):
// a reopened conversation shows its last question's scope again. Beside Send, the
// Answer settings button (AnswerSettings.tsx, features 25-27) when there is a choice.
// Beside each question, the user's own story matches are asked in parallel,
// in the chip's scope (lib/storyMatches.ts), and shown apart from the answer,
// never in it. They belong to the question, not to the answer succeeding
// (storySlot.ts): below the pending card as soon as they arrive, then below the
// answer, or at the end of the log when the answer failed. Only a newer
// question, opening a conversation or starting a new one clears them. The same
// status line says how many, once: with the answer, or after it.
// "Search my stories" (ModeSwitch.tsx, lib/chatMode.ts; council 3 Oct 2026):
// a question then only searches the user's stories, with no answer and nothing
// saved, and its results show in their own section (StorySearchView.tsx)
// instead of the conversation. The mode is kept for the tab; a conversation or
// "Ask about this document" always asks the documents.
// UI audit (16 Oct 2026): each conversation can be renamed or deleted from the list
// (A1, ConversationList.tsx), what happened shows in a toast; the newest "not found"
// answer offers to search all documents (A4); the empty screen offers up to three
// example questions about the newest ready documents, each asked about its own (A5).
// A question out when Ask is left (another page of the app) is kept in askStore.ts;
// back on Ask it shows as pending again, or answered, or failed with the question
// back in the box (Tushar, 16 Oct 2026).
// #535 (Tushar, 7 Nov 2026): the answer streams (askStream, lib/api.ts). In the pending card's
// place, StreamingAnswer.tsx shows the real stages, the documents found and each verified
// sentence as it comes, the thread following it only while the person is at the bottom. When
// done's reply is in, its card waits for the reveal to finish (or Stop), then settles in the
// preview's place (AnswerView's `settle`), with no scroll.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Fragment, useCallback, useEffect, useEffectEvent, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Toast } from "@/components/admin/Toast";
import type { Notice } from "@/components/admin/Toast";
import { StoryMatches } from "@/components/stories/StoryMatches";
import { Drawer } from "@/components/ui/Drawer";
import { StillLoading } from "@/components/shell/StillLoading";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { useHydrated } from "@/components/ui/useHydrated";
import { capChatOptions, effectiveOf, taskOf, useAiSettings } from "@/lib/aiSettings";
import {
  ApiError,
  FEATURE_OFF,
  askStream,
  deleteConversation,
  getChatOptions,
  getConversation,
  listConversations,
  listDocuments,
  renameConversation,
  savedAnswer,
} from "@/lib/api";
import type { Landing } from "@/lib/askStream";
import { MODE_TEXTS, modeLockedText, readMode, saveMode, sendPlan, startMode, STORY_SEARCH_LIMIT } from "@/lib/chatMode";
import { DRAFT_KEPT, readDraft, saveDraft, saveDraftSoon } from "@/lib/drafts";
import { useSession } from "@/lib/session";
import type { ChatMode } from "@/lib/chatMode";
import {
  findStoryMatches,
  latestOnly,
  SEARCH_PENDING,
  searchStatusText,
  searchStoryMatches,
  STORY_NOTE_CHAT,
  storyStatusText,
} from "@/lib/storyMatches";
import type { ChatOptions, ChatResponse, ClarifyReply, ConversationSummary, Message } from "@/lib/types";
import {
  answerCardId,
  answerStatusText,
  ASKING_STATUS,
  LOADED_STATUS,
  questionsByAnswer,
  restoreAfterFailure,
  STOPPED_LANDING_STATUS,
  STOPPED_STATUS,
} from "./answer";
import { AnswerSettings } from "./AnswerSettings";
import { askInFlight, resumable, resumedConversation, settleAsk, startAsk, stopAsk } from "./askStore";
import type { InFlight } from "./askStore";
import { AnswerView } from "./AnswerView";
import { answerDomId, answersClarify, chosenAfter, openClarify, pickedText } from "./clarify";
import { ClarifyView } from "./ClarifyView";
import { ConversationList } from "./ConversationList";
import { scrollBehavior, useAskProgress } from "./hooks";
import {
  ASK_DRAFT,
  answerScrollTop,
  askDraft,
  askDraftText,
  askErrorText,
  chatHref,
  choiceBody,
  conversationFromSearch,
  conversationLoadText,
  defaultPick,
  documentFromSearch,
  hasChoices,
  isNearBottom,
  loadPick,
  questionFromSearch,
  savePick,
  settleWaitingDraft,
  starterQuestions,
  stoppedQuestion,
  usablePick,
  withSavedAnswer,
} from "./logic";
import type { AnswerPick, Starter } from "./logic";
import { ModeSwitch } from "./ModeSwitch";
import { QuestionBox } from "./QuestionBox";
import {
  answeredScope,
  chipScope,
  chooseScope,
  EVERY_DOCUMENT,
  keptScope,
  restoredScope,
  scopeLockedText,
  sentScope,
  startScope,
} from "./scope";
import { ScopePicker } from "./ScopePicker";
import type { ChosenScope } from "./scope";
import { storyPlace, storySlot } from "./storySlot";
import { StreamingAnswer } from "./StreamingAnswer";
import type { StorySlot, StorySlotEvent } from "./storySlot";
import { StorySearchView } from "./StorySearchView";
import type { StorySearchState } from "./StorySearchView";
import styles from "./Chat.module.css";

const DRAWER_ID = "chat-conversations";
const HEADING = "Ask about your documents";

/** PRD 6 A: focus moves to a new answer's card once it is drawn (never scrolling it). */
function focusAnswer(messageId: string) {
  requestAnimationFrame(() => window.document.getElementById(answerCardId(messageId))?.focus({ preventScroll: true }));
}

/** Focus the composer, but only with a mouse on a wide screen: on phones it would pop up the keyboard. */
function focusComposerOnDesktop(input: HTMLTextAreaElement | null) {
  if (!input) return;
  if (!window.matchMedia("(min-width: 1024px) and (pointer: fine)").matches) return;
  input.focus({ preventScroll: true });
}

/** The tab's own storage, or null where the browser refuses it. */
function tabStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** What to say when a question got no answer. */
const errorText = (error: unknown) => askErrorText(error instanceof ApiError ? error : null);

/** Keep the address bar on the open conversation, without a navigation. */
function showInUrl(conversationId: string | null): void {
  window.history.replaceState(null, "", chatHref(conversationId));
}

/** `initialSearch`: the address's query string when chat opened (app/(app)/chat/page.tsx), read once. */
export function ChatScreen({ initialSearch }: { initialSearch: string }) {
  const pathname = usePathname();
  const userId = useSession().me.user.id;
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [listFailed, setListFailed] = useState(false);
  // A question sent before Ask was left (askStore.ts), picked up once at mount.
  const [resumed] = useState(() => resumable(askInFlight(), initialSearch));
  // The conversation named in the URL, or the picked-up question's, read once at
  // mount: later changes are this screen's own replaceState.
  const [restoreId] = useState(
    () => conversationFromSearch(initialSearch) ?? (resumed ? resumedConversation(resumed) : null),
  );
  const autoQuestion =
    new URLSearchParams(initialSearch).get("send") === "1" ? questionFromSearch(initialSearch) : null;
  const [activeId, setActiveId] = useState<string | null>(restoreId);
  // The documents the next question may use (scope.ts): one document when the
  // viewer's "Ask about this document" opened chat, else restored from the conversation.
  const [scope, setScope] = useState(() =>
    restoreId ? keptScope() : startScope(documentFromSearch(initialSearch)),
  );
  const [messages, setMessages] = useState<Message[]>([]);
  const [threadError, setThreadError] = useState(""); // why, once threadState is "error"
  const [threadState, setThreadState] = useState<"idle" | "loading" | "error">(
    restoreId ? "loading" : "idle",
  );
  // Still on its way at mount: pending until it settles (the effect below).
  const [waiting] = useState(() => resumed !== null && !resumed.outcome);
  const [pending, setPending] = useState<string | null>(() => (waiting ? resumed!.question : null));
  const [askError, setAskError] = useState<string | null>(() =>
    resumed?.outcome && "error" in resumed.outcome ? errorText(resumed.outcome.error) : null,
  );
  // A picked-up question that failed goes back into the box.
  // Or a question from an ordinary ?q= link, waiting for Send.
  const [draft, setDraft] = useState<string | undefined>(() =>
    resumed?.outcome && "error" in resumed.outcome
      ? resumed.question
      : autoQuestion
        ? undefined
        : (questionFromSearch(initialSearch) ?? undefined),
  );
  const [noDocuments, setNoDocuments] = useState<boolean | null>(null);
  const [starters, setStarters] = useState<Starter[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const closeNotice = useCallback(() => setNotice(null), []);
  // The one status line for screen readers.
  const [status, setStatus] = useState("");
  // The newest question's story matches (storySlot.ts), mirrored in a ref so a
  // reply can read the slot it just changed.
  const [slot, setSlot] = useState<StorySlot | null>(null);
  const slotRef = useRef<StorySlot | null>(null);
  const [storyRequests] = useState(latestOnly);
  const storyAbort = useRef<AbortController | null>(null);
  // Ask documents or search my stories: the tab's kept mode, read once React runs
  // in the browser (the server has no sessionStorage), until one is chosen here.
  const hydrated = useHydrated();
  // Issue 468: the box's draft, kept in this tab (lib/drafts.ts, ASK_DRAFT): the words typed and
  // the question still waiting for its answer. Read once in the browser, and put back in the box
  // only when nothing else fills it (a question picked up from askStore.ts, or a ?q= link).
  const typedNow = useRef("");
  const waitingNow = useRef<string | null>(waiting ? resumed!.question : null);
  const [draftRead, setDraftRead] = useState(false);
  // The words put back, while the box still holds them unchanged: a line says they were kept (DRAFT_KEPT).
  const [keptNote, setKeptNote] = useState<string | null>(null);
  if (hydrated && !draftRead) {
    setDraftRead(true);
    const kept = askDraftText(readDraft(userId, ASK_DRAFT));
    if (kept !== null && draft === undefined && resumed === null && autoQuestion === null) {
      setDraft(kept);
      setKeptNote(kept);
    }
  }
  const keepDraft = useCallback(
    () => saveDraftSoon(userId, ASK_DRAFT, askDraft(typedNow.current, waitingNow.current)),
    [userId],
  );
  const onTyped = useCallback(
    (text: string) => {
      typedNow.current = text;
      keepDraft();
      setKeptNote((shown) => (shown !== null && shown !== text ? null : shown));
    },
    [keepDraft],
  );
  const [chosenMode, setChosenMode] = useState<ChatMode | null>(null);
  const mode: ChatMode =
    chosenMode ??
    (hydrated ? startMode(readMode(tabStorage()), documentFromSearch(initialSearch), restoreId) : "documents");
  // PRD 6 B2: the open clarifying question, which the composer answers until it is left.
  const [leftClarify, setLeftClarify] = useState<string | null>(null);
  const asking = mode === "documents" ? openClarify(messages, leftClarify) : null;
  const [search, setSearch] = useState<StorySearchState>({ kind: "idle" });
  const [searchRequests] = useState(latestOnly);
  const searchAbort = useRef<AbortController | null>(null);
  // The question on its way, for Stop; B7: a picked-up one still on its way too.
  const asking_ = useRef<InFlight | null>(waiting ? resumed : null);
  // #535: the question whose stream the pending card shows, its progress, done's reply while
  // the card finishes revealing it, and who waits for it to settle (sendQuestion).
  const [streaming, setStreaming] = useState<InFlight | null>(() => (waiting ? resumed : null));
  const progress = useAskProgress(streaming);
  const [final, setFinal] = useState<ChatResponse | null>(null);
  const settleWait = useRef<((changed: boolean) => void) | null>(null);
  // The answers that settled in a streamed card's place: they fade in rather than rise.
  const [settledIn, setSettledIn] = useState<ReadonlyMap<string, "same" | "changed">>(() => new Map());
  // A streamed answer just settled: the thread stays where it is.
  const streamedIn = useRef(false);
  // B1: the questions stopped after `started` (by the id the server saves them with), each in the
  // thread with STOPPED_LANDING_STATUS under it until its saved answer takes its place.
  const [stopped, setStopped] = useState<ReadonlySet<string>>(() => new Set());
  const unstop = (id: string) =>
    setStopped((current) => {
      const next = new Set(current);
      next.delete(id);
      return next;
    });
  // B2: the waits for those saved answers, cancelled by open, startNew and leaving the screen.
  const savedWaits = useRef(new Set<AbortController>());
  // B13: the answers this screen asked with Precision: a timeout suggests Swift for them only.
  const [preciseAnswers, setPreciseAnswers] = useState<ReadonlySet<string>>(() => new Set());
  const markPrecise = (answerId: string) => setPreciseAnswers((current) => new Set(current).add(answerId));
  // The drawer is open for the path it was opened on, so any route change closes it.
  const [listOpenOn, setListOpenOn] = useState<string | null>(null);
  const listOpen = listOpenOn !== null && listOpenOn === pathname;
  // Answer settings (features 25-27): what the organisation allows, and this tab's pick.
  // Not loaded or failed: no chip, and the organisation's defaults answer.
  const [loadedOptions, setChatOptions] = useState<ChatOptions | null>(null);
  const [picked, setAnswerPick] = useState<AnswerPick | null>(null); // null: nothing picked yet
  // The pick this browser kept; read once (loadPick reads nothing on the server), and the chip
  // shows only after the options load in the browser, so the server's render never differs.
  const [kept] = useState<unknown>(loadPick);
  const choosePick = useCallback((pick: AnswerPick) => {
    savePick(pick);
    setAnswerPick(pick);
  }, []);
  // AI settings (spec 17 Oct 2026): the levels stop at the effective Ask level, and start from it.
  const aiSettings = useAiSettings();
  const chatOptions = loadedOptions && capChatOptions(loadedOptions, effectiveOf(aiSettings, "ask"));
  const answerPick =
    chatOptions &&
    ((picked && usablePick(chatOptions, picked)) || usablePick(chatOptions, kept) || defaultPick(chatOptions));
  const askOff = taskOf(aiSettings, "ask")?.available === false;

  // Only the latest conversation opened may fill the thread.
  const openRequest = useRef(0);
  const autoQuestionSent = useRef(false);
  // False once the screen is gone: an answer then waits in askStore.ts for the next one.
  const alive = useRef(false);
  useEffect(() => {
    alive.current = true;
    const waits = savedWaits.current; // one Set for the screen's life
    return () => {
      alive.current = false;
      settleWait.current?.(false); // nothing waits for a card that is gone
      waits.forEach((wait) => wait.abort());
    };
  }, []);
  const threadRef = useRef<HTMLElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Follow a new answer only when the person is already reading the end.
  const nearBottom = useRef(true);
  // After a conversation loads, show its end once.
  const jumpToEnd = useRef(false);

  const updateSlot = (event: StorySlotEvent): StorySlot | null => {
    slotRef.current = storySlot(slotRef.current, event);
    setSlot(slotRef.current);
    return slotRef.current;
  };

  /** Retire the story request in flight and hide the cards. */
  const forgetStories = () => {
    storyAbort.current?.abort();
    storyAbort.current = null;
    storyRequests.next();
    updateSlot({ type: "clear" });
  };

  /** Switch the mode and keep it for the tab; leaving story mode stops its search. */
  const chooseMode = (next: ChatMode) => {
    setChosenMode(next);
    saveMode(tabStorage(), next);
    if (next === "stories") return;
    searchAbort.current?.abort();
    searchRequests.next();
    setSearch((current) => (current.kind === "loading" ? { kind: "idle" } : current));
  };

  const refreshList = async () => {
    try {
      const list = await listConversations();
      setConversations(list.items);
      setListFailed(false);
    } catch {
      setListFailed(true);
    }
  };

  useEffect(() => {
    let cancelled = false;
    listConversations()
      .then((list) => {
        if (!cancelled) setConversations(list.items);
      })
      .catch(() => {
        if (!cancelled) setListFailed(true);
      });
    getChatOptions()
      .then((options) => {
        if (!cancelled) setChatOptions(options);
      })
      .catch(() => undefined); // no chip: the organisation's defaults answer
    return () => {
      cancelled = true;
    };
  }, []);

  /** #535: open the conversation a streamed answer lands in (after Stop, or a stream broken
   * after `started`), with the list refreshed so a new one shows there. */
  const showLanding = (landing: Landing) => {
    if (activeId === landing.conversationId) return;
    setActiveId(landing.conversationId);
    showInUrl(landing.conversationId);
    void refreshList();
  };

  /** Stop after `started`: the conversation opens and the question stays in the thread with
   * STOPPED_LANDING_STATUS under it (B1, never the welcome screen) until its saved answer takes
   * its place, even after newer questions. Cancelled by open, startNew and leaving (B2); if it
   * never comes, the question stays and the box says why. */
  const waitForSaved = (question: string, landing: Landing, precise: boolean) => {
    const request = openRequest.current;
    const id = landing.questionId;
    showLanding(landing);
    setStatus(STOPPED_LANDING_STATUS);
    setStopped((current) => new Set(current).add(id));
    setMessages((current) => (current.some((message) => message.id === id) ? current : [...current, stoppedQuestion(id, question)]));
    const wait = new AbortController();
    savedWaits.current.add(wait);
    savedAnswer(landing, wait.signal)
      .then(
        (saved) => {
          if (!alive.current || openRequest.current !== request) return; // another conversation is open
          if (precise) markPrecise(saved.answer.id);
          unstop(id);
          setMessages((current) => withSavedAnswer(current, saved));
          setStatus(answerStatusText(saved.answer.answer)); // said: it arrives while the person may be elsewhere
        },
        (failed: unknown) => {
          if (!alive.current || openRequest.current !== request || wait.signal.aborted) return;
          unstop(id);
          setStatus(""); // the box's alert says why
          setAskError(errorText(failed));
        },
      )
      .finally(() => savedWaits.current.delete(wait));
  };
  // B7: the same for a question picked up from before Ask was left (asked with the org's choice).
  const stoppedResumed = useEffectEvent((question: string, landing: Landing) => waitForSaved(question, landing, false));

  // Reopen the conversation from the URL. One that is gone or not this person's
  // (the server says 404) quietly becomes the empty chat. A picked-up question
  // still on its way stays pending; once answered (and saved), the conversation
  // loads again with it. `loaded` runs when that load ends, whichever way.
  useEffect(() => {
    let cancelled = false;
    // `gone`: the conversation is not there (404), after the screen became the empty chat.
    const load = (id: string, loaded?: () => void, gone?: () => void) => {
      const request = ++openRequest.current;
      getConversation(id)
        .then((conversation) => {
          if (request !== openRequest.current) return;
          jumpToEnd.current = true;
          setMessages(conversation.messages);
          setScope(restoredScope(conversation.messages));
          setThreadState("idle");
          setStatus(LOADED_STATUS);
        })
        .catch((error: unknown) => {
          if (request !== openRequest.current) return;
          if (error instanceof ApiError && error.status === 404) {
            setActiveId(null);
            setScope(startScope(null));
            setThreadState("idle");
            showInUrl(null);
            gone?.();
          } else {
            setThreadError(conversationLoadText(error));
            setThreadState("error");
          }
        })
        .finally(() => {
          if (!cancelled) loaded?.();
        });
    };
    /** A conversation new to this screen: shown, and the list asked for again to have it. */
    const openNew = (id: string) => {
      setActiveId(id);
      showInUrl(id);
      setThreadState("loading");
      listConversations()
        .then((list) => {
          if (!cancelled) setConversations(list.items);
        })
        .catch(() => undefined); // the list keeps what it had; Refresh asks again
    };
    if (restoreId) load(restoreId);
    if (resumed && !waiting) settleAsk(resumed);
    else if (resumed) {
      const refill = () => {
        typedNow.current = restoreAfterFailure(typedNow.current, resumed.question);
        keepDraft();
        setDraft(resumed.question);
      };
      resumed.done.then(
        (reply) => {
          if (cancelled) return;
          settleAsk(resumed);
          if (!restoreId) openNew(reply.conversation_id); // a new conversation: its answer made it
          waitingNow.current = null;
          keepDraft();
          load(reply.conversation_id, () => {
            setPending(null);
            setStatus(answerStatusText(reply.answer.answer));
            focusAnswer(reply.answer.id);
          });
        },
        (error: unknown) => {
          if (cancelled) return;
          settleAsk(resumed);
          waitingNow.current = null;
          setPending(null);
          // B7: Stopped here, as sendQuestion's Stop: after `started` it waits for the saved answer;
          // before, the question is back in the box. No error either way.
          if (resumed.stopped) {
            if (resumed.landing) {
              keepDraft();
              stoppedResumed(resumed.question, resumed.landing);
            } else {
              setStatus(STOPPED_STATUS);
              refill();
            }
            return;
          }
          setAskError(errorText(error));
          // B8: its answer is not in its conversation yet: that conversation opens, with the words
          // (as sendQuestion does); the box is refilled only if that conversation is not there.
          if (error instanceof ApiError && error.code === "answer_pending" && resumed.landing) {
            keepDraft();
            openNew(resumed.landing.conversationId);
            load(resumed.landing.conversationId, undefined, refill);
            return;
          }
          refill();
        },
      );
    }
    return () => {
      cancelled = true;
    };
  }, [restoreId, resumed, waiting, keepDraft]); // stoppedResumed: an Effect Event

  // One small call tells the empty screen whether there is anything to ask about.
  useEffect(() => {
    let cancelled = false;
    listDocuments({ limit: 6 })
      .then((list) => {
        if (cancelled) return;
        const none = list.total === 0 && list.items.length === 0;
        setNoDocuments(none);
        setStarters(starterQuestions(list.items));
        if (!none && window.document.activeElement === window.document.body) {
          focusComposerOnDesktop(inputRef.current);
        }
      })
      .catch(() => {
        // Unknown: show the normal empty screen, which still works.
        if (!cancelled) setNoDocuments(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the thread's scroll padding equal to the composer's height, so a card
  // scrolled into view is never hidden behind it. A thread at its very end stays
  // there when the composer grows (the scope chip's folder name arrives after
  // a conversation opened, or the question wraps to a new line).
  useEffect(() => {
    const composer = composerRef.current;
    const thread = threadRef.current;
    if (!composer || !thread || typeof ResizeObserver === "undefined") return;
    let height = composer.offsetHeight;
    const observer = new ResizeObserver(() => {
      const grew = composer.offsetHeight - height;
      height = composer.offsetHeight;
      thread.style.setProperty("--composer-h", `${height}px`);
      if (grew > 0 && isNearBottom(thread, grew + 2)) thread.scrollTop = thread.scrollHeight;
    });
    observer.observe(composer);
    return () => observer.disconnect();
  }, []);

  // A new answer: scroll to its first line if the person was at the end. Focus is moved by focusAnswer, never here.
  useEffect(() => {
    const thread = threadRef.current;
    const log = logRef.current;
    if (!thread || !log) return;
    if (streamedIn.current) {
      streamedIn.current = false; // it took the streamed card's place: nothing to scroll to
      return;
    }
    if (jumpToEnd.current) {
      jumpToEnd.current = false;
      const pin = () => {
        thread.scrollTop = thread.scrollHeight;
      };
      pin();
      nearBottom.current = true;
      // Older answers are painted only near view (.answerWrap, Chat.module.css), so
      // their real heights replace the placeholder after the first paint: pin again then.
      let frame = requestAnimationFrame(() => {
        frame = requestAnimationFrame(pin);
      });
      return () => cancelAnimationFrame(frame);
    }
    // The newest answer, not the story box that may follow it.
    let last = log.lastElementChild;
    if (last?.hasAttribute("data-stories")) last = last.previousElementSibling;
    if (!nearBottom.current || !(last instanceof HTMLElement)) return;
    const top =
      thread.scrollTop + last.getBoundingClientRect().top - thread.getBoundingClientRect().top;
    thread.scrollTo({ top: answerScrollTop(thread, top), behavior: scrollBehavior() });
  }, [messages]);

  // A question just sent: show it and the pending card.
  useEffect(() => {
    const thread = threadRef.current;
    if (pending === null || !thread) return;
    thread.scrollTo({ top: thread.scrollHeight, behavior: scrollBehavior() });
    nearBottom.current = true;
  }, [pending]);

  /** #535: keep the end of a streaming answer in view, only while the person is at the bottom.
   * B10: once a frame, so the thread's height is not read (a forced layout) on every word. */
  const followFrame = useRef(0);
  const follow = () => {
    if (followFrame.current !== 0) return;
    followFrame.current = requestAnimationFrame(() => {
      followFrame.current = 0;
      const thread = threadRef.current;
      if (!nearBottom.current || !thread) return;
      thread.scrollTop = thread.scrollHeight;
    });
  };
  useEffect(() => () => cancelAnimationFrame(followFrame.current), []);

  /** #535: hand done's reply to the streamed card; resolves once it has finished revealing it and
   * settles ("changed": the reply differs from the preview), or at once on Stop (`stop`). */
  const settled = (reply: ChatResponse, stop: AbortSignal) =>
    new Promise<"same" | "changed">((resolve) => {
      const finish = (changed: boolean) => {
        settleWait.current = null;
        stop.removeEventListener("abort", skip);
        resolve(changed ? "changed" : "same");
      };
      const skip = () => finish(false);
      settleWait.current = finish;
      stop.addEventListener("abort", skip);
      setFinal(reply);
    });

  /** B1, B2: forget a stopped question and stop waiting for its saved answer. */
  const forgetStopped = () => {
    savedWaits.current.forEach((wait) => wait.abort());
    savedWaits.current.clear();
    setStopped(new Set());
  };

  const open = async (id: string) => {
    const request = ++openRequest.current;
    forgetStories();
    forgetStopped();
    chooseMode("documents");
    setListOpenOn(null);
    setActiveId(id);
    showInUrl(id);
    setMessages([]);
    // Until it loads (or if it fails), the server keeps this conversation's own
    // scope; nothing of the last conversation's shows or is sent.
    setScope(keptScope());
    setAskError(null);
    setStatus("");
    setThreadState("loading");
    try {
      const conversation = await getConversation(id);
      if (request !== openRequest.current) return;
      jumpToEnd.current = true;
      setMessages(conversation.messages);
      setScope(restoredScope(conversation.messages));
      setThreadState("idle");
      setStatus(LOADED_STATUS);
    } catch (error) {
      if (request !== openRequest.current) return;
      setThreadError(conversationLoadText(error));
      setThreadState("error");
    }
  };

  const startNew = () => {
    openRequest.current += 1;
    forgetStories();
    forgetStopped();
    setListOpenOn(null);
    setActiveId(null);
    showInUrl(null);
    setMessages([]);
    setScope(startScope(null));
    setAskError(null);
    setStatus("");
    setThreadState("idle");
    if (noDocuments !== true) focusComposerOnDesktop(inputRef.current);
  };

  /** "Search my stories": no answer, nothing saved. False when it failed, so the box gets the question back. */
  const searchStories = async (question: string): Promise<boolean> => {
    searchAbort.current?.abort();
    const controller = new AbortController();
    searchAbort.current = controller;
    const token = searchRequests.next();
    setSearch({ kind: "loading", question });
    setStatus(SEARCH_PENDING);
    try {
      const found = await searchStoryMatches(
        question,
        chipScope(scope),
        STORY_SEARCH_LIMIT,
        "stories",
        controller.signal,
      );
      if (searchRequests.isLatest(token)) {
        setSearch({ kind: "done", question, found });
        setStatus(searchStatusText(found));
      }
      return true;
    } catch {
      if (!searchRequests.isLatest(token)) return true;
      setSearch({ kind: "error", question });
      setStatus("");
      return false;
    }
  };

  /** `only`: ask about these documents this once (A4 "Search all documents", A5's examples).
   * `chose`: the clarify choices tapped; typed words answer the open clarify too (PRD 6 B2). */
  const sendQuestion = async (question: string, only?: ChosenScope, chose?: ClarifyReply): Promise<boolean> => {
    const plan = sendPlan(mode);
    if (!plan.chat) return searchStories(question);
    const answering = chose ?? (asking && answersClarify(question) ? { message_id: asking.id, choice_ids: [] } : undefined);
    const asked = only ? chooseScope(scope, only) : scope;
    if (only) setScope(asked);
    setPending(question);
    setAskError(null);
    setStatus(ASKING_STATUS);
    // In parallel with the answer, in the chip's scope; kept by this question's token.
    storyAbort.current?.abort();
    const token = storyRequests.next();
    const controller = new AbortController();
    storyAbort.current = controller;
    updateSlot({ type: "ask", token, question });
    if (plan.sideCards) {
      void findStoryMatches(question, chipScope(asked), controller.signal).then((matches) => {
        const now = updateSlot({ type: "matches", token, matches });
        if (now?.token === token && now.answer !== "pending" && matches.length > 0) {
          setStatus(storyStatusText(matches.length));
        }
      });
    }
    // Kept outside this screen (askStore.ts), so leaving Ask never drops it.
    const choice = choiceBody(chatOptions, answerPick);
    const precise = choice?.thinking === "high"; // B13: Precision
    const entry = startAsk(question, activeId, (signal, onEvent) =>
      askStream(
        question,
        activeId ?? undefined,
        sentScope(asked),
        choice,
        answering,
        signal,
        onEvent,
      ),
    );
    asking_.current = entry;
    setStreaming(entry);
    try {
      const reply = await entry.done;
      if (!alive.current) return true; // the next chat screen shows it
      const how = await settled(reply, entry.controller.signal);
      if (!alive.current) return true; // left while it revealed: the next chat screen shows it
      settleAsk(entry);
      streamedIn.current = true;
      if (precise) markPrecise(reply.answer.id);
      setSettledIn((current) => new Map(current).set(reply.answer.id, how));
      setMessages((current) => [...current, reply.question, reply.answer]);
      // Only after a tapped choice does focus move, to the reply; typing keeps it in the box.
      if (chose) requestAnimationFrame(() => document.getElementById(answerDomId(reply.answer.id))?.focus());
      setScope((current) => answeredScope(current, reply.question));
      const answerStatus = answerStatusText(reply.answer.answer);
      const now = updateSlot({ type: "answered", token, answerId: reply.answer.id });
      const count = now?.token === token ? (now.matches?.length ?? 0) : 0;
      setStatus(count > 0 ? `${answerStatus} ${storyStatusText(count)}` : answerStatus);
      focusAnswer(reply.answer.id);
      if (activeId === null) {
        setActiveId(reply.conversation_id);
        showInUrl(reply.conversation_id);
        void refreshList();
      }
      return true;
    } catch (error) {
      if (!alive.current) return false; // the next chat screen shows it
      settleAsk(entry);
      // The error is its own alert; the story cards stay, at the end of the log.
      updateSlot({ type: "failed", token });
      if (entry.stopped) {
        if (entry.landing) {
          // #535: the server finishes and saves it (Tushar, 16 Oct 2026): its conversation opens,
          // the box is cleared (Tushar's call), and the answer shows there once it is saved.
          waitForSaved(question, entry.landing, precise);
          return true;
        }
        setStatus(STOPPED_STATUS); // no error: the question is back in the box to change or send
        return false;
      }
      setStatus("");
      // #535: its answer is not in its conversation yet: that conversation opens, with the words.
      if (error instanceof ApiError && error.code === "answer_pending" && entry.landing) showLanding(entry.landing);
      setAskError(errorText(error));
      return false;
    } finally {
      setPending(null);
      setFinal(null);
      if (asking_.current === entry) asking_.current = null;
    }
  };

  /** Send a question, kept as the draft's waiting question until it settles (issue 468). Not
   * answered, it stays as typing: the box gets it back, and so does a page a session's end left. */
  const onAsk = async (question: string, only?: ChosenScope, chose?: ClarifyReply): Promise<boolean> => {
    setKeptNote(null);
    waitingNow.current = question;
    saveDraft(userId, ASK_DRAFT, askDraft(typedNow.current, question));
    const sent = await sendQuestion(question, only, chose);
    if (!alive.current) {
      settleWaitingDraft(userId, question, sent); // this screen is gone; the next one has its own
      return sent;
    }
    waitingNow.current = null;
    if (!sent) typedNow.current = restoreAfterFailure(typedNow.current, question);
    keepDraft();
    return sent;
  };

  // Home submitted this question before navigating here. Consume the marker before
  // sending, so a refresh cannot submit the same question a second time.
  const askOnce = useEffectEvent((question: string) => onAsk(question));
  useEffect(() => {
    if (!autoQuestion || !hydrated || askOff || resumed || pending !== null || autoQuestionSent.current) return;
    autoQuestionSent.current = true;
    showInUrl(null);
    void askOnce(autoQuestion).then((sent) => {
      if (!sent) setDraft(autoQuestion);
    });
  }, [autoQuestion, hydrated, askOff, resumed, pending]);

  const rename = async (id: string, title: string): Promise<boolean> => {
    try {
      const renamed = await renameConversation(id, title);
      setConversations((list) => list?.map((item) => (item.id === id ? renamed : item)) ?? list);
      setNotice({ text: "Conversation renamed." });
      return true;
    } catch (error) {
      setNotice({ problem: true, text: error instanceof ApiError ? error.message : "Not renamed. Check your connection, then try again." });
      return false;
    }
  };

  const remove = async (id: string): Promise<boolean> => {
    try {
      await deleteConversation(id);
      setConversations((list) => list?.filter((item) => item.id !== id) ?? list);
      if (id === activeId) startNew();
      setNotice({ text: "Conversation deleted." });
      return true;
    } catch (error) {
      setNotice({ problem: true, text: error instanceof ApiError ? error.message : "Not deleted. Check your connection, then try again." });
      return false;
    }
  };

  const questions = questionsByAnswer(messages);
  const chosen = chosenAfter(messages);
  const busy = pending !== null;
  const choose = (message: Message, ids: string[]) =>
    void onAsk(pickedText(message, ids), undefined, { message_id: message.id, choice_ids: ids });
  const place = storyPlace(slot);
  const storyCards = (question?: string) =>
    slot?.matches ? (
      <StoryMatches matches={slot.matches} note={STORY_NOTE_CHAT} headingLevel={2} question={question} />
    ) : null;
  const empty = messages.length === 0 && threadState === "idle" && !busy;
  // A4: the newest answer, when it found nothing in fewer than every document, offers them all.
  const last = messages.at(-1);
  const askAllFor =
    last?.answer?.status === "not_found" && scope.scope.kind !== "all" && scope.scope.kind !== "kept" ? last : null;
  const askAllQuestion = askAllFor ? (questions.get(askAllFor.id) ?? null) : null;
  const askAll = () => {
    if (askAllQuestion && !busy) void onAsk(askAllQuestion, EVERY_DOCUMENT);
  };
  const activeTitle = conversations?.find((item) => item.id === activeId)?.title ?? null;

  const list = (
    <ConversationList
      items={conversations}
      failed={listFailed}
      activeId={activeId}
      disabled={busy}
      onOpen={(id) => void open(id)}
      onNew={startNew}
      onRetry={() => {
        setListFailed(false);
        void refreshList();
      }}
      onRename={rename}
      onDelete={remove}
    />
  );

  let heading: ReactNode;
  if (!empty) {
    heading = <h1 className="visually-hidden">{activeTitle ?? HEADING}</h1>;
  } else if (noDocuments === null) {
    heading = <h1 className="visually-hidden">{HEADING}</h1>;
  } else if (noDocuments) {
    heading = (
      <div className={styles.empty}>
        <EmptyState
          icon="upload"
          title={HEADING}
          headingLevel={1}
          action={
            <Link href="/documents" className="btn btn-primary">
              Go to Documents
            </Link>
          }
        >
          Upload a document first. Answers come only from your own documents.
        </EmptyState>
      </div>
    );
  } else {
    heading = (
      <div className={styles.empty}>
        <EmptyState icon="chat" title={HEADING} headingLevel={1}>
          Get clear answers from your documents, with every response linked to its source page.
        </EmptyState>
        {starters.length > 0 ? (
          <section className={styles.starters} aria-labelledby="chat-starters">
            <h2 id="chat-starters" className={styles.subheading}>
              Try one of these
            </h2>
            <ul className={styles.starterList}>
              {starters.map((starter) => (
                <li key={starter.documentId}>
                  <button
                    type="button"
                    className={styles.starter}
                    disabled={busy}
                    onClick={() => void onAsk(starter.question, { kind: "documents", ids: [starter.documentId] })}
                  >
                    <span>{starter.question}</span>
                    <span className={styles.starterDoc}>{starter.documentName}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    );
  }

  return (
    <div className={styles.screen}>
      <div className={styles.pane}>{list}</div>

      <div className={styles.subbar}>
        <IconButton
          icon="history"
          label="Conversations"
          showLabel
          aria-haspopup="dialog"
          aria-expanded={listOpen}
          aria-controls={DRAWER_ID}
          onClick={() => setListOpenOn(pathname)}
        />
        <button type="button" className="btn" disabled={busy} onClick={startNew}>
          <Icon name="plus" />
          New
          <span className="visually-hidden"> conversation</span>
        </button>
      </div>

      <section
        ref={threadRef}
        className={styles.thread}
        aria-label="Questions and answers"
        data-scroll-root=""
        onScroll={(event) => {
          nearBottom.current = isNearBottom(event.currentTarget);
        }}
      >
        <div className={styles.threadInner}>
          <div className={styles.threadBody}>
            {mode === "documents" ? heading : null}
            <p className="visually-hidden" role="status">
              {status}
            </p>
            {mode === "stories" ? (
              <StorySearchView
                state={search}
                onRetry={() => {
                  if (search.kind === "error") void searchStories(search.question);
                }}
              />
            ) : null}
            {mode === "documents" && threadState === "loading" ? (
              <>
                <p className={`${styles.threadStatus} delayed`}>
                  <span className="spinner" aria-hidden="true" />
                  Loading the conversation…
                </p>
                {activeId !== null ? <StillLoading onRetry={() => void open(activeId)} /> : null}
              </>
            ) : null}
            {mode === "documents" && threadState === "error" ? (
              <div className={styles.threadError}>
                <p className="error-text" role="alert">
                  {threadError}
                </p>
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    if (activeId !== null) void open(activeId);
                  }}
                >
                  <Icon name="refresh" size={16} />
                  Try again
                </button>
              </div>
            ) : null}
            <div
              ref={logRef}
              className={styles.log}
              role="log"
              aria-live="off"
              aria-label="Conversation"
              hidden={mode === "stories"}
            >
              {messages.map((message) =>
                message.role === "user" ? (
                  <Fragment key={message.id}>
                    <div className={styles.question}>
                      <span className="visually-hidden">You asked: </span>
                      <p className={styles.plain}>{message.text}</p>
                    </div>
                    {stopped.has(message.id) ? <p className={styles.threadStatus}>{STOPPED_LANDING_STATUS}</p> : null}
                  </Fragment>
                ) : (
                  <Fragment key={message.id}>
                    <div className={styles.answerWrap} id={answerDomId(message.id)} tabIndex={-1}>
                      {message.answer?.status === "clarify" ? (
                        <ClarifyView
                          message={message}
                          open={message === asking}
                          chosen={chosen.get(message.id)}
                          busy={busy}
                          onChoose={choose}
                          onOther={() => inputRef.current?.focus()}
                        />
                      ) : (
                        <AnswerView
                          message={message}
                          question={questions.get(message.id) ?? null}
                          onAskAll={message === askAllFor ? askAll : undefined}
                          onAskQuestion={busy ? undefined : (text) => void onAsk(text)}
                          settle={settledIn.get(message.id)}
                          precise={preciseAnswers.has(message.id)}
                        />
                      )}
                    </div>
                    {place.at === "answer" && place.answerId === message.id ? storyCards() : null}
                  </Fragment>
                ),
              )}
              {pending !== null ? (
                <>
                  <div className={styles.question}>
                    <span className="visually-hidden">You asked: </span>
                    <p className={styles.plain}>{pending}</p>
                  </div>
                  <StreamingAnswer
                    progress={progress}
                    final={final}
                    onSettle={(changed) => settleWait.current?.(changed)}
                    onGrow={follow}
                  />
                  {place.at === "pending" ? storyCards() : null}
                </>
              ) : null}
              {place.at === "end" ? storyCards(place.question) : null}
            </div>
          </div>
          <div ref={composerRef} className={styles.composer}>
            {/* Ask switched off for the whole product: a calm note where the composer was. */}
            {askOff || askError === FEATURE_OFF ? (
              <p className={styles.composerNote}>{FEATURE_OFF}</p>
            ) : (
            <>
            {keptNote !== null ? <p className={styles.composerNote}>{DRAFT_KEPT}</p> : null}
            <QuestionBox
              pending={busy || search.kind === "loading"}
              onStop={busy ? () => asking_.current && stopAsk(asking_.current) : undefined}
              error={mode === "documents" ? askError : null}
              onAsk={onAsk}
              draft={draft}
              onTyped={onTyped}
              inputRef={inputRef}
              texts={MODE_TEXTS[mode]}
              answering={
                asking?.answer?.clarify
                  ? { question: asking.answer.clarify.question, onLeave: () => setLeftClarify(asking.id) }
                  : null
              }
              tools={
                <>
                  <ModeSwitch mode={mode} locked={modeLockedText(busy)} onChange={chooseMode} />
                  <ScopePicker
                    state={scope}
                    locked={scopeLockedText(busy, threadState === "loading")}
                    onChoose={(next) => setScope((current) => chooseScope(current, next))}
                  />
                </>
              }
              settings={
                mode === "documents" && hasChoices(chatOptions) && answerPick ? (
                  <AnswerSettings options={chatOptions} pick={answerPick} locked={busy} onPick={choosePick} />
                ) : null
              }
            />
            </>
            )}
          </div>
        </div>
      </section>

      <Drawer open={listOpen} onClose={() => setListOpenOn(null)} title="Conversations" id={DRAWER_ID}>
        {list}
      </Drawer>
      <Toast notice={notice} onClose={closeNotice} />
    </div>
  );
}
