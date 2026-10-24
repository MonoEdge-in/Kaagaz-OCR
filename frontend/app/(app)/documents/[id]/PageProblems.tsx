"use client";

// The banner over a problem page (PRD 5 U4), extending the low-resolution banner:
// what happened in one line from the issue table and the fix, for each problem, then
// three buttons. Read this page again and Keep as is (a reviewer or an admin) ask
// first, inside the page, never in a browser dialog; the question takes focus, and
// focus goes back to the button when it is cancelled or fails, or on to the note that
// says it is done when it goes through (the question and that button are gone then:
// "Being read again", the live status, or "kept as is"). Upload a better scan
// sends a new version of the document (the existing upload), which is read in full.
// The steps and the words are components/viewer/pageProblems.ts.
// UI audit DD3/DD4 (16 Oct 2026): an alert, in the Needs attention tab: its icon, a title,
// each problem as a line (no bullets), then one row of like buttons. This is the page's
// own Read again; the whole document's is in the document's menu.
// One box (Tushar, 24 Oct 2026: "both wordings are the same, make one box"): `inline`, it sits
// inside the Attention panel under its page, which already says what is wrong, so it gives
// only the fix in one line and the buttons, with no banner, icon or title of its own.

import Link from "next/link";
import { useCallback, useReducer, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { BusyLabel } from "@/components/documents/BusyLabel";
import { ACCEPT_ATTRIBUTE } from "@/components/documents/logic";
import { Icon } from "@/components/ui/Icon";
import {
  READING_AGAIN_TEXT,
  fixErrorText,
  fixQuestion,
  fixReducer,
  keptText,
  problemsOf,
} from "@/components/viewer/pageProblems";
import type { Fix } from "@/components/viewer/pageProblems";
import { acceptPage, readPageAgain, uploadVersion } from "@/lib/api";
import type { DocumentSummary, PageSummary } from "@/lib/types";
import styles from "./DocumentView.module.css";

export interface PageProblemsProps {
  documentId: string;
  page: PageSummary;
  userId: string;
  canFix: boolean; // a reviewer or an admin, out of Trash
  canUpload: boolean; // out of Trash
  onReadAgain(reply: DocumentSummary): void;
  onKept(page: PageSummary): void;
  /** Inside the Attention panel, under its page: the fix and the buttons only. */
  inline?: boolean;
}

type Upload = { step: "sending" } | { step: "sent"; id: string; words: string } | { step: "error"; message: string };

export function PageProblems({
  documentId,
  page,
  userId,
  canFix,
  canUpload,
  onReadAgain,
  onKept,
  inline = false,
}: PageProblemsProps) {
  const [state, dispatch] = useReducer(fixReducer, { step: "idle" });
  const [upload, setUpload] = useState<Upload | null>(null);
  const [askedAgain, setAskedAgain] = useState(false); // until the page is read again and the screen reloads
  const fileInput = useRef<HTMLInputElement>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const refocus = useRef(false);
  const focusOnMount = useCallback((element: HTMLElement | null) => element?.focus(), []);
  const holdOpener = (fix: Fix) => (element: HTMLButtonElement | null) => {
    if (!element || element.dataset.fix !== fix) return;
    if (!refocus.current || opener.current?.dataset.fix !== fix) return;
    refocus.current = false;
    element.focus();
  };
  const problems = problemsOf(page);
  if (problems.length === 0) return null;
  const kept = keptText(page);
  const readingAgain = page.read_again_requested_at !== null || askedAgain;
  const questionId = `page-${page.page_no}-question`;
  const titleId = `page-${page.page_no}-problems`;
  // Set when the fix has just gone through, so the note mounts (or is kept) with focus.
  const focusIfDone = (fix: Fix) => (state.step === "done" && state.fix === fix ? focusOnMount : undefined);

  function open(fix: Fix, button: HTMLButtonElement) {
    opener.current = button;
    refocus.current = false;
    dispatch({ type: "open", fix });
  }

  function cancel() {
    refocus.current = true;
    dispatch({ type: "cancel" });
  }

  function send(fix: Fix) {
    if (state.step !== "confirming") return;
    dispatch({ type: "send" });
    const done =
      fix === "read"
        ? readPageAgain(documentId, page.page_no).then((reply) => {
            setAskedAgain(true);
            onReadAgain(reply);
          })
        : acceptPage(documentId, page.page_no).then(onKept);
    done.then(
      () => dispatch({ type: "sent" }),
      (caught: unknown) => {
        refocus.current = true;
        dispatch({ type: "failed", message: fixErrorText(caught) });
      },
    );
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const [file] = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!file) return;
    setUpload({ step: "sending" });
    uploadVersion(documentId, file, userId, () => undefined).then(
      (reply) =>
        setUpload({
          step: "sent",
          id: reply.document.id,
          words: reply.duplicate
            ? `This file was uploaded before, as “${reply.document.display_name}”. Nothing new was stored.`
            : `Sent as version ${reply.document.version_no}. It is read in full.`,
        }),
      (caught: unknown) => setUpload({ step: "error", message: fixErrorText(caught) }),
    );
  }

  const confirming = state.step === "confirming" || state.step === "sending" ? state : null;
  return (
    <section className={inline ? styles.problemInline : styles.problemBanner} aria-labelledby={titleId}>
      {inline ? null : <Icon name="alert" />}
      <div className={styles.problemBody}>
        <p id={titleId} className={inline ? "visually-hidden" : styles.problemTitle}>
          {inline ? `Fix page ${page.page_no}` : `Page ${page.page_no} is flagged for review`}
        </p>
        {problems.map((problem) => (
          <p key={problem.code} className={styles.problemNote}>
            {inline ? null : (
              <>
                <span className={styles.problemSays}>{problem.says}</span>{" "}
              </>
            )}
            {problem.fix}
          </p>
        ))}
        {kept && (
          <p ref={focusIfDone("keep")} className={styles.problemNote} tabIndex={-1}>
            {kept}
          </p>
        )}
        {readingAgain && (
          <p ref={focusIfDone("read")} className={styles.problemNote} role="status" tabIndex={-1}>
            {READING_AGAIN_TEXT}
          </p>
        )}
        {confirming ? (
          <div
            ref={focusOnMount}
            tabIndex={-1}
            className={styles.readAgainConfirm}
            role="group"
            aria-labelledby={questionId}
          >
            <p id={questionId} className={styles.readAgainQuestion}>
              {fixQuestion(confirming.fix, page.page_no)}
            </p>
            <div className={styles.readAgainButtons}>
              <button type="button" className="btn" onClick={cancel} disabled={confirming.step === "sending"}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => send(confirming.fix)}
                disabled={confirming.step === "sending"}
              >
                <BusyLabel
                  busy={confirming.step === "sending"}
                  idle={confirming.fix === "read" ? "Read it again" : "Keep it as is"}
                  working={confirming.fix === "read" ? "Read it again" : "Keep it as is"}
                />
              </button>
            </div>
          </div>
        ) : (
          <div className={styles.problemActions}>
            {canFix && !readingAgain && (
              <button
                ref={holdOpener("read")}
                data-fix="read"
                type="button"
                className="btn"
                onClick={(event) => open("read", event.currentTarget)}
              >
                <Icon name="refresh" />
                Read this page again
              </button>
            )}
            {canUpload && (
              <button
                type="button"
                className="btn"
                onClick={() => fileInput.current?.click()}
                disabled={upload?.step === "sending"}
              >
                <Icon name="upload" />
                <BusyLabel busy={upload?.step === "sending"} idle="Upload a better scan" working="Sending…" />
              </button>
            )}
            {canFix && kept === null && (
              <button
                ref={holdOpener("keep")}
                data-fix="keep"
                type="button"
                className="btn"
                onClick={(event) => open("keep", event.currentTarget)}
              >
                Keep as is
              </button>
            )}
          </div>
        )}
        {state.step === "error" && (
          <p className="error-text" role="alert">
            {state.message}
          </p>
        )}
        {upload?.step === "sent" && (
          <p className={styles.problemNote} role="status">
            {upload.words} <Link href={`/documents/${upload.id}`}>Open it</Link>
          </p>
        )}
        {upload?.step === "error" && (
          <p className="error-text" role="alert">
            {upload.message}
          </p>
        )}
        {canUpload && (
          <>
            <label htmlFor={`better-scan-${page.page_no}`} className="visually-hidden">
              A better scan of this document
            </label>
            <input
              ref={fileInput}
              id={`better-scan-${page.page_no}`}
              className="visually-hidden"
              type="file"
              accept={ACCEPT_ATTRIBUTE}
              tabIndex={-1}
              onChange={chooseFile}
            />
          </>
        )}
      </div>
    </section>
  );
}
