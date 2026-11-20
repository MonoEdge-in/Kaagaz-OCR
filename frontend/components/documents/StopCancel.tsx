"use client";

// #713: Stop and Cancel on the document page while its file is waiting, checked or read: the tray's
// words and question for one file (batchTray.ts), the safe answer first. Stop is for who may read
// it again; Cancel (Move to Trash) for who may move it there (can_delete). The server checks both.

import { useState } from "react";
import { ApiError, deleteDocument, stopDocument } from "@/lib/api";
import { ActionDialog } from "./ActionDialog";
import { STOP_TEXT, trayQuestion } from "./batchTray";
import type { TrayAction } from "./batchTray";
import styles from "./Documents.module.css";
import library from "./Library.module.css";

export function StopCancel({
  documentId,
  name,
  canStop,
  canCancel,
  onDone,
}: {
  documentId: string;
  name: string;
  canStop: boolean;
  canCancel: boolean;
  onDone(message: string): void; // what was done, said by the page; it reloads the file
}) {
  const [asking, setAsking] = useState<TrayAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!canStop && !canCancel) return null;
  const question = asking ? trayQuestion(asking, [name]) : null;

  async function yes(action: TrayAction) {
    setAsking(null);
    setError(null);
    try {
      if (action === "stop") await stopDocument(documentId);
      else await deleteDocument(documentId);
      onDone(action === "stop" ? STOP_TEXT.stoppedPage : STOP_TEXT.cancelledLine);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Check your connection, then try again.");
    }
  }

  return (
    <div className={styles.rowLine}>
      <span className={styles.rowActions}>
        {canStop && (
          <button type="button" className="btn btn-quiet" aria-label={STOP_TEXT.stopOne(name)} onClick={() => setAsking("stop")}>
            {STOP_TEXT.stop}
          </button>
        )}
        {canCancel && (
          <button
            type="button"
            className={`btn btn-quiet ${styles.dangerQuiet}`}
            aria-label={STOP_TEXT.cancelOne(name)}
            onClick={() => setAsking("cancel")}
          >
            {STOP_TEXT.cancel}
          </button>
        )}
      </span>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {asking && question && (
        <ActionDialog title={question.title} onClose={() => setAsking(null)}>
          {question.note && <p className="muted">{question.note}</p>}
          <div className={library.formButtons}>
            <button
              type="button"
              className={`btn ${asking === "cancel" ? "btn-danger" : ""} ${library.apart}`}
              onClick={() => void yes(asking)}
            >
              {question.yes}
            </button>
            <button type="button" className="btn btn-primary" data-autofocus onClick={() => setAsking(null)}>
              {question.no}
            </button>
          </div>
        </ActionDialog>
      )}
    </div>
  );
}
