"use client";

// The chat screen: the conversation list (a side pane from 1024 px, a drawer
// below), the open thread and the composer. The thread is the one scroll area
// and a log (announced politely as answers arrive). While a question is out, it
// shows as pending and the conversation cannot be switched. Only the latest
// conversation opened may fill the thread, so a slow reply for an earlier one
// is dropped. Above the composer, the scope chip and picker (ScopePicker.tsx):
// a reopened conversation shows its last question's scope again.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Drawer } from "@/components/ui/Drawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { ApiError, ask, getConversation, listConversations, listDocuments } from "@/lib/api";
import type { ConversationSummary, Message } from "@/lib/types";
import { questionsByAnswer } from "./answer";
import { AnswerView } from "./AnswerView";
import { ConversationList } from "./ConversationList";
import { scrollBehavior } from "./hooks";
import {
  answerScrollTop,
  askErrorText,
  chatHref,
  conversationFromSearch,
  documentFromSearch,
  isNearBottom,
  STILL_LOOKING_AFTER_MS,
} from "./logic";
import { QuestionBox } from "./QuestionBox";
import {
  answeredScope,
  chooseScope,
  keptScope,
  restoredScope,
  scopeLockedText,
  sentScope,
  startScope,
} from "./scope";
import { ScopePicker } from "./ScopePicker";
import styles from "./Chat.module.css";

const DRAWER_ID = "chat-conversations";
const HEADING = "Ask about your documents";

/** Focus the composer, but only with a mouse on a wide screen: on phones it would pop up the keyboard. */
function focusComposerOnDesktop(input: HTMLTextAreaElement | null) {
  if (!input) return;
  if (!window.matchMedia("(min-width: 1024px) and (pointer: fine)").matches) return;
  input.focus({ preventScroll: true });
}

function PendingAnswer() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), STILL_LOOKING_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, []);
  return (
    <div className={styles.answerCard} aria-busy="true">
      <p className={styles.pendingText}>
        <span className="spinner" aria-hidden="true" />
        {slow ? "Still looking. Long registers take a little longer." : "Looking through your documents…"}
      </p>
      <div className={styles.skeletonLines} aria-hidden="true">
        <span className={`skeleton ${styles.skeletonLine}`} />
        <span className={`skeleton ${styles.skeletonLine}`} />
        <span className={`skeleton ${styles.skeletonLine}`} />
      </div>
    </div>
  );
}

/** Keep the address bar on the open conversation, without a navigation. */
function showInUrl(conversationId: string | null): void {
  window.history.replaceState(null, "", chatHref(conversationId));
}

/** `initialSearch`: the address's query string when chat opened (app/(app)/chat/page.tsx), read once. */
export function ChatScreen({ initialSearch }: { initialSearch: string }) {
  const pathname = usePathname();
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [listFailed, setListFailed] = useState(false);
  // The conversation named in the URL, read once at mount: later changes are
  // this screen's own replaceState.
  const [restoreId] = useState(() => conversationFromSearch(initialSearch));
  const [activeId, setActiveId] = useState<string | null>(restoreId);
  // The documents the next question may use (scope.ts): one document when the
  // viewer's "Ask about this document" opened chat, else restored from the conversation.
  const [scope, setScope] = useState(() =>
    restoreId ? keptScope() : startScope(documentFromSearch(initialSearch)),
  );
  const [messages, setMessages] = useState<Message[]>([]);
  const [threadState, setThreadState] = useState<"idle" | "loading" | "error">(
    restoreId ? "loading" : "idle",
  );
  const [pending, setPending] = useState<string | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  const [noDocuments, setNoDocuments] = useState<boolean | null>(null);
  // The drawer is open for the path it was opened on, so any route change closes it.
  const [listOpenOn, setListOpenOn] = useState<string | null>(null);
  const listOpen = listOpenOn !== null && listOpenOn === pathname;

  // Only the latest conversation opened may fill the thread.
  const openRequest = useRef(0);
  const threadRef = useRef<HTMLElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Follow a new answer only when the person is already reading the end.
  const nearBottom = useRef(true);
  // After a conversation loads, show its end once.
  const jumpToEnd = useRef(false);

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
    return () => {
      cancelled = true;
    };
  }, []);

  // Reopen the conversation from the URL. One that is gone or not this person's
  // (the server says 404) quietly becomes the empty chat.
  useEffect(() => {
    if (!restoreId) return;
    const request = ++openRequest.current;
    getConversation(restoreId)
      .then((conversation) => {
        if (request !== openRequest.current) return;
        jumpToEnd.current = true;
        setMessages(conversation.messages);
        setScope(restoredScope(conversation.messages));
        setThreadState("idle");
      })
      .catch((error: unknown) => {
        if (request !== openRequest.current) return;
        if (error instanceof ApiError && error.status === 404) {
          setActiveId(null);
          setScope(startScope(null));
          setThreadState("idle");
          showInUrl(null);
        } else {
          setThreadState("error");
        }
      });
  }, [restoreId]);

  // One small call tells the empty screen whether there is anything to ask about.
  useEffect(() => {
    let cancelled = false;
    listDocuments({ limit: 1 })
      .then((list) => {
        if (cancelled) return;
        const none = list.total === 0 && list.items.length === 0;
        setNoDocuments(none);
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
  // scrolled into view is never hidden behind it.
  useEffect(() => {
    const composer = composerRef.current;
    const thread = threadRef.current;
    if (!composer || !thread || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      thread.style.setProperty("--composer-h", `${composer.offsetHeight}px`);
    });
    observer.observe(composer);
    return () => observer.disconnect();
  }, []);

  // A new answer: scroll to its first line if the person was at the end. Never moves focus.
  useEffect(() => {
    const thread = threadRef.current;
    const log = logRef.current;
    if (!thread || !log) return;
    if (jumpToEnd.current) {
      jumpToEnd.current = false;
      thread.scrollTop = thread.scrollHeight;
      nearBottom.current = true;
      return;
    }
    const last = log.lastElementChild;
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

  const open = async (id: string) => {
    const request = ++openRequest.current;
    setListOpenOn(null);
    setActiveId(id);
    showInUrl(id);
    setMessages([]);
    // Until it loads (or if it fails), the server keeps this conversation's own
    // scope; nothing of the last conversation's shows or is sent.
    setScope(keptScope());
    setAskError(null);
    setThreadState("loading");
    try {
      const conversation = await getConversation(id);
      if (request !== openRequest.current) return;
      jumpToEnd.current = true;
      setMessages(conversation.messages);
      setScope(restoredScope(conversation.messages));
      setThreadState("idle");
    } catch {
      if (request === openRequest.current) setThreadState("error");
    }
  };

  const startNew = () => {
    openRequest.current += 1;
    setListOpenOn(null);
    setActiveId(null);
    showInUrl(null);
    setMessages([]);
    setScope(startScope(null));
    setAskError(null);
    setThreadState("idle");
    if (noDocuments !== true) focusComposerOnDesktop(inputRef.current);
  };

  const onAsk = async (question: string): Promise<boolean> => {
    setPending(question);
    setAskError(null);
    try {
      const reply = await ask(question, activeId ?? undefined, sentScope(scope));
      setMessages((current) => [...current, reply.question, reply.answer]);
      setScope((current) => answeredScope(current, reply.question));
      if (activeId === null) {
        setActiveId(reply.conversation_id);
        showInUrl(reply.conversation_id);
        void refreshList();
      }
      return true;
    } catch (error) {
      setAskError(askErrorText(error instanceof ApiError ? error : null));
      return false;
    } finally {
      setPending(null);
    }
  };

  const questions = questionsByAnswer(messages);
  const busy = pending !== null;
  const empty = messages.length === 0 && threadState === "idle" && !busy;
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
            {heading}
            {threadState === "loading" ? (
              <p className={`${styles.threadStatus} delayed`}>
                <span className="spinner" aria-hidden="true" />
                Loading the conversation…
              </p>
            ) : null}
            {threadState === "error" ? (
              <div className={styles.threadError}>
                <p className="error-text" role="alert">
                  Could not load this conversation.
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
              aria-live="polite"
              aria-label="Conversation"
            >
              {messages.map((message) =>
                message.role === "user" ? (
                  <div key={message.id} className={styles.question}>
                    <span className="visually-hidden">You asked: </span>
                    <p className={styles.plain}>{message.text}</p>
                  </div>
                ) : (
                  <AnswerView
                    key={message.id}
                    message={message}
                    question={questions.get(message.id) ?? null}
                  />
                ),
              )}
              {pending !== null ? (
                <>
                  <div className={styles.question}>
                    <span className="visually-hidden">You asked: </span>
                    <p className={styles.plain}>{pending}</p>
                  </div>
                  <PendingAnswer />
                </>
              ) : null}
            </div>
          </div>
          <div ref={composerRef} className={styles.composer}>
            <ScopePicker
              state={scope}
              locked={scopeLockedText(busy, threadState === "loading")}
              onChoose={(next) => setScope((current) => chooseScope(current, next))}
            />
            <QuestionBox pending={busy} error={askError} onAsk={onAsk} inputRef={inputRef} />
          </div>
        </div>
      </section>

      <Drawer open={listOpen} onClose={() => setListOpenOn(null)} title="Conversations" id={DRAWER_ID}>
        {list}
      </Drawer>
    </div>
  );
}
