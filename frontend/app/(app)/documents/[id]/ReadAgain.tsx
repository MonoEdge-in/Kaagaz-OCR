"use client";

// "Read again" for the whole document (PRD 2, X1), for a reviewer or an admin, picked
// from the document's menu (UI audit DD2/DD3, 16 Oct 2026; the screen mounts this once
// it is picked). It asks first, inside the page, never in a browser
// dialog: which reviewer fixes come back, and that the document is out of
// search meanwhile. Focus goes to Cancel, and back to the menu (onClosed) when it is
// cancelled or fails. Once the server has queued the document, the screen
// takes its reply (onStarted) and follows the reading, and a line says so
// until it settles. The steps and the words are
// components/viewer/reprocess.ts; the server checks the role again.
// When the organisation has two or more models (model choice MS4), the
// question also has "Read with", set to the model the document asked for
// (detail.read_model, else the default), and says every page is read again
// (no cost words for the organisation, Tushar 15 Oct 2026); a model is sent
// only when the user changes it, so the document never moves to the default
// unasked. The models come from GET /upload-limits, and without them there is
// no choice. Dynamic (feature 26) is offered here as at upload; picking nothing
// keeps the document's own reading. Ensemble is offered only after the scan (16 Oct 2026).

import { useCallback, useEffect, useReducer, useState } from "react";
import { offersChoice, pickedReader, readAgainReading, readAgainWarning } from "@/components/documents/readers";
import { BusyLabel } from "@/components/documents/BusyLabel";
import { ReaderSelect } from "@/components/documents/UploadArea";
import {
  canSend,
  reprocessConfirmText,
  reprocessErrorText,
  reprocessReducer,
} from "@/components/viewer/reprocess";
import { getUploadLimits, reprocessDocument } from "@/lib/api";
import type { DocumentSummary, UploadReader } from "@/lib/types";
import styles from "./DocumentView.module.css";

export interface ReadAgainProps {
  documentId: string;
  fixes: number; // the detail's reviewer_fixes
  pages: number | null; // the detail's page_count
  readModel: string | null; // the detail's read_model: the select starts on it
  reading: boolean; // beingRead(detail.status)
  onStarted(reply: DocumentSummary): void;
  onClosed(): void; // cancelled or refused: focus goes back to the menu
}

export function ReadAgain({ documentId, fixes, pages, readModel, reading, onStarted, onClosed }: ReadAgainProps) {
  const [state, dispatch] = useReducer(reprocessReducer, { step: "confirming" });
  const [readers, setReaders] = useState<UploadReader[]>([]);
  const [picked, setPicked] = useState<string | null>(null);
  const model = readAgainReading(readers, picked); // sent: only what the user picked
  const shown = pickedReader(readers, picked ?? readModel); // what the select shows

  useEffect(() => {
    let cancelled = false;
    // Not loaded: no "Read with", and the server reads with the organisation's own model.
    getUploadLimits().then(
      (limits) => {
        if (cancelled) return;
        setReaders(limits.readers);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);
  if (state.step === "done" && !reading) dispatch({ type: "settled" });

  // A stable callback ref, so it runs once when its element appears.
  const focusOnMount = useCallback((element: HTMLElement | null) => {
    element?.focus();
  }, []);

  function send() {
    if (!canSend(state)) return; // already sent once
    dispatch({ type: "send" });
    reprocessDocument(documentId, model).then(
      (reply) => {
        dispatch({ type: "sent" });
        onStarted(reply);
      },
      (caught: unknown) => {
        dispatch({ type: "failed", message: reprocessErrorText(caught) });
        onClosed();
      },
    );
  }

  function cancel() {
    dispatch({ type: "cancel" });
    onClosed();
  }

  if (state.step === "done") {
    return (
      <p ref={focusOnMount} tabIndex={-1} role="status" className={styles.readAgainLine}>
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
        <ReaderSelect readers={readers} picked={picked ?? readModel} onPick={setPicked} />
        {offersChoice(readers) && shown !== null && (
          <p role="status">{readAgainWarning(pages)}</p>
        )}
        <div className={styles.readAgainButtons}>
          <button ref={focusOnMount} type="button" className="btn" onClick={cancel} disabled={sending}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={send} disabled={sending}>
            <BusyLabel busy={sending} idle="Read again" working="Starting…" />
          </button>
        </div>
      </div>
    );
  }

  return state.step === "error" ? (
    <p className={`error-text ${styles.readAgainLine}`} role="alert">
      {state.message}
    </p>
  ) : null;
}
