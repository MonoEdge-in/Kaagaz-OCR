"use client";

// The chat screen: the conversation list, the open thread and the question box.
// The thread is a log (announced politely as answers arrive). While a question
// is out, it shows as pending and the conversation cannot be switched.

import { useEffect, useRef, useState } from "react";
import { ApiError, ask, getConversation, listConversations } from "@/lib/api";
import type { ConversationSummary, Message } from "@/lib/types";
import { questionsByAnswer } from "./answer";
import { AnswerView } from "./AnswerView";
import { ConversationList } from "./ConversationList";
import { QuestionBox } from "./QuestionBox";
import styles from "./Chat.module.css";

const messageOf = (error: unknown, fallback: string) =>
  error instanceof ApiError ? error.message : fallback;

export function ChatScreen() {
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [threadState, setThreadState] = useState<"idle" | "loading" | "error">("idle");
  const [pending, setPending] = useState<string | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  // Only the latest conversation opened may fill the thread.
  const openRequest = useRef(0);

  const refreshList = async () => {
    try {
      const list = await listConversations();
      setConversations(list.items);
      setListError(null);
    } catch (error) {
      setListError(messageOf(error, "Could not load your conversations."));
    }
  };

  useEffect(() => {
    let cancelled = false;
    listConversations()
      .then((list) => {
        if (!cancelled) setConversations(list.items);
      })
      .catch((error: unknown) => {
        if (!cancelled) setListError(messageOf(error, "Could not load your conversations."));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const open = async (id: string) => {
    const request = ++openRequest.current;
    setActiveId(id);
    setMessages([]);
    setAskError(null);
    setThreadState("loading");
    try {
      const conversation = await getConversation(id);
      if (request !== openRequest.current) return;
      setMessages(conversation.messages);
      setThreadState("idle");
    } catch {
      if (request === openRequest.current) setThreadState("error");
    }
  };

  const startNew = () => {
    openRequest.current += 1;
    setActiveId(null);
    setMessages([]);
    setAskError(null);
    setThreadState("idle");
  };

  const onAsk = async (question: string): Promise<boolean> => {
    setPending(question);
    setAskError(null);
    try {
      const reply = await ask(question, activeId ?? undefined);
      setMessages((current) => [...current, reply.question, reply.answer]);
      if (activeId === null) {
        setActiveId(reply.conversation_id);
        void refreshList();
      }
      return true;
    } catch (error) {
      setAskError(messageOf(error, "Could not send your question."));
      return false;
    } finally {
      setPending(null);
    }
  };

  const questions = questionsByAnswer(messages);

  return (
    <div className={styles.layout}>
      <ConversationList
        items={conversations}
        error={listError}
        activeId={activeId}
        disabled={pending !== null}
        onOpen={(id) => void open(id)}
        onNew={startNew}
      />
      <section className={`stack ${styles.thread}`} aria-labelledby="thread-heading">
        <h1 id="thread-heading">Ask</h1>
        {threadState === "loading" ? <p className="muted">Loading the conversation…</p> : null}
        {threadState === "error" ? (
          <p className="error-text">Could not load this conversation.</p>
        ) : null}
        {messages.length === 0 && threadState === "idle" && pending === null ? (
          <p className="muted">
            Ask a question in English or Hinglish. Every answer shows the page it came from.
          </p>
        ) : null}
        <div className="stack" role="log" aria-live="polite" aria-label="Conversation">
          {messages.map((message) =>
            message.role === "user" ? (
              <div key={message.id} className={styles.question}>
                <span className="visually-hidden">You asked: </span>
                <p className={styles.plain}>{message.text}</p>
              </div>
            ) : (
              <div key={message.id} className={`card ${styles.answer}`}>
                <span className="visually-hidden">Answer: </span>
                <AnswerView message={message} question={questions.get(message.id) ?? null} />
              </div>
            ),
          )}
          {pending !== null ? (
            <>
              <div className={styles.question}>
                <span className="visually-hidden">You asked: </span>
                <p className={styles.plain}>{pending}</p>
              </div>
              <p className="muted">Looking through your documents…</p>
            </>
          ) : null}
        </div>
        {askError ? (
          <p className="error-text" role="alert">
            {askError}
          </p>
        ) : null}
        <QuestionBox pending={pending !== null} onAsk={onAsk} />
      </section>
    </div>
  );
}
