"use client";

// "Read again" at the end of the viewer header's status line (PRD 2, X1), for
// a reviewer or an admin. It asks first, inside the page, never in a browser
// dialog: which reviewer fixes come back, and that the document is out of
// search meanwhile. Focus goes to Cancel, and back to Read again when it is
// cancelled or fails. Once the server has queued the document, the screen
// takes its reply (onStarted) and follows the reading, and a line says so
// until it settles; then the button is back. The steps and the words are
// components/viewer/reprocess.ts; the server checks the role again.
// When the organisation has two or more models (model choice MS4), the
// question also has "Read with", set to the model the document asked for
// (detail.read_model, else the default), and says every page is read again
// (no cost words for the organisation, Tushar 15 Oct 2026); a model is sent
// only when the user changes it, so the document never moves to the default
// unasked. The models come from GET /upload-limits, and without them there is
// no choice. Dynamic and "More than one reader…" (features 26, 27) are offered
// here as at upload; picking nothing keeps the document's own reading.

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  offersChoice,
  pickedReader,
  readAgainReading,
  readAgainWarning,
  startAgents,
} from "@/components/documents/readers";
import { ReaderSelect } from "@/components/documents/UploadArea";
import { Icon } from "@/components/ui/Icon";
import {
  canSend,
  reprocessConfirmText,
  reprocessErrorText,
  reprocessReducer,
} from "@/components/viewer/reprocess";
import { getUploadLimits, reprocessDocument } from "@/lib/api";
import type { DocumentSummary, EnsembleOffer, UploadReader } from "@/lib/types";
import styles from "./DocumentView.module.css";

export interface ReadAgainProps {
  documentId: string;
  fixes: number; // the detail's reviewer_fixes
  pages: number | null; // the detail's page_count
  readModel: string | null; // the detail's read_model: the select starts on it
  offered: boolean; // canReprocess(role, detail)
  reading: boolean; // beingRead(detail.status)
  onStarted(reply: DocumentSummary): void;
}

export function ReadAgain({ documentId, fixes, pages, readModel, offered, reading, onStarted }: ReadAgainProps) {
  const [state, dispatch] = useReducer(reprocessReducer, { step: "idle" });
  const [readers, setReaders] = useState<UploadReader[]>([]);
  const [ensemble, setEnsemble] = useState<EnsembleOffer | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [ticked, setTicked] = useState<string[] | null>(null);
  const agents = ticked ?? startAgents(ensemble);
  const model = readAgainReading(readers, ensemble, picked, agents); // sent: only what the user picked
  const shown = pickedReader(readers, picked ?? readModel); // what the select shows

  useEffect(() => {
    if (!offered) return;
    let cancelled = false;
    // Not loaded: no "Read with", and the server reads with the organisation's own model.
    getUploadLimits().then(
      (limits) => {
        if (cancelled) return;
        setReaders(limits.readers);
        setEnsemble(limits.ensemble ?? null);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [offered]);
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
    reprocessDocument(documentId, model).then(
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
        <ReaderSelect
          readers={readers}
          picked={picked ?? readModel}
          onPick={setPicked}
          ensemble={ensemble}
          agents={agents}
          onAgents={setTicked}
        />
        {offersChoice(readers, ensemble) && shown !== null && (
          <p role="status">{readAgainWarning(pages)}</p>
        )}
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
        className="btn"
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
