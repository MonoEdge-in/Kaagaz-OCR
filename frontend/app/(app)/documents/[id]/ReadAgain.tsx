"use client";

// "Read again" at the end of the viewer header's status line (PRD 2, X1), for
// a reviewer or an admin. It asks first, inside the page, never in a browser
// dialog: which reviewer fixes come back, and that the document is out of
// search meanwhile. Focus goes to Cancel, and back to Read again when it is
// cancelled or fails. Once the server has queued the document, the screen
// takes its reply (onStarted) and follows the reading, and a line says so
// until it settles; then the button is back. The steps and the words are
// components/viewer/reprocess.ts; the server checks the role again.

import { useCallback, useReducer, useRef } from "react";
import { Icon } from "@/components/ui/Icon";
import {
  canSend,
  reprocessConfirmText,
  reprocessErrorText,
  reprocessReducer,
} from "@/components/viewer/reprocess";
import { reprocessDocument } from "@/lib/api";
import type { DocumentSummary } from "@/lib/types";
import styles from "./DocumentView.module.css";

export interface ReadAgainProps {
  documentId: string;
  fixes: number; // the detail's reviewer_fixes
  offered: boolean; // canReprocess(role, detail)
  reading: boolean; // beingRead(detail.status)
  onStarted(reply: DocumentSummary): void;
}

export function ReadAgain({ documentId, fixes, offered, reading, onStarted }: ReadAgainProps) {
  const [state, dispatch] = useReducer(reprocessReducer, { step: "idle" });
  const refocusButton = useRef(false);
  const lineShown = useRef(false);
  if (state.step === "done" && !reading) dispatch({ type: "settled" });

  // Stable callback refs, so each runs once when its element appears.
  const focusOnMount = useCallback((element: HTMLElement | null) => {
    element?.focus();
  }, []);
  const showLine = useCallback((element: HTMLElement | null) => {
    if (!element) return;
    lineShown.current = true;
    element.focus();
  }, []);
  // Back from the line once reading settles: focus follows, unless it moved on.
  const holdButton = useCallback((element: HTMLButtonElement | null) => {
    if (!element) return;
    const settled = lineShown.current && document.activeElement === document.body;
    if (refocusButton.current || settled) element.focus();
    refocusButton.current = false;
    lineShown.current = false;
  }, []);

  function send() {
    if (!canSend(state)) return; // already sent once
    dispatch({ type: "send" });
    reprocessDocument(documentId).then(
      (reply) => {
        dispatch({ type: "sent" });
        onStarted(reply);
      },
      (caught: unknown) => {
        refocusButton.current = true;
        dispatch({ type: "failed", message: reprocessErrorText(caught) });
      },
    );
  }

  function cancel() {
    refocusButton.current = true;
    dispatch({ type: "cancel" });
  }

  if (state.step === "done") {
    return (
      <p ref={showLine} tabIndex={-1} role="status" className={styles.readAgainLine}>
        Reading it again. This page updates as it goes.
      </p>
    );
  }

  if (state.step === "confirming" || state.step === "sending") {
    const sending = state.step === "sending";
    return (
      <div
        className={styles.readAgainConfirm}
        role="group"
        aria-labelledby="read-again-question"
        aria-describedby="read-again-text"
      >
        <p id="read-again-question" className={styles.readAgainQuestion}>
          Read this document again?
        </p>
        <p id="read-again-text">{reprocessConfirmText(fixes)}</p>
        <div className={styles.readAgainButtons}>
          <button type="button" className="btn btn-primary" onClick={send} disabled={sending}>
            {sending ? (
              <>
                <span className="spinner" aria-hidden="true" />
                Starting…
              </>
            ) : (
              "Read again"
            )}
          </button>
          <button ref={focusOnMount} type="button" className="btn" onClick={cancel} disabled={sending}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (!offered) return null;
  return (
    <>
      <button
        ref={holdButton}
        type="button"
        className={`btn ${styles.readAgain}`}
        onClick={() => dispatch({ type: "open" })}
      >
        <Icon name="refresh" />
        Read again
      </button>
      {state.step === "error" && (
        <p className={`error-text ${styles.readAgainLine}`} role="alert">
          {state.message}
        </p>
      )}
    </>
  );
}
