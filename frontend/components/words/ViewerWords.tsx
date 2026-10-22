"use client";

// My words in the viewer (council B2): the user's own spots on this page, a
// "Show my words" switch and the list of them (each item marks its spot), and
// the "Add meaning" form for a value or a scanned-text line. The markers are
// drawn by PageViewer and SheetGrid; they are only a picture (aria-hidden), the
// list is how a keyboard or screen reader reaches them. Every word, meaning and
// line is plain text from the user or the document: never HTML, never logged.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { scrollBehavior } from "@/components/chat/hooks";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { addMyWord, ApiError, getMyWordsOnPage } from "@/lib/api";
import type { MySpot, Region } from "@/lib/types";
import { browserStore, markersOf, markerText, pickableWords, readShowMyWords, spotsLoadText } from "./spots";
import { viewerBody, wordFromText, writeShowMyWords } from "./spots";
import type { Marker, ViewerWordBody } from "./spots";
import { DuplicateDialog, WordFields } from "./WordParts";
import { draftOk, failedState, retryBody } from "./words";
import type { Choice, Existing, WordDraft } from "./words";
import styles from "./Words.module.css";

/** The element id of a marker on the page, so its list item can bring it into view. */
export const markerId = (id: string) => `my-word-${id}`;

type Loaded = { key: string; spots: MySpot[] } | { key: string; error: string };

export interface ViewerWords {
  show: boolean;
  setShow(on: boolean): void;
  markers: Marker[]; // [] while hidden, loading or failed
  loading: boolean;
  error: string | null;
  activeId: string | null;
  setActiveId(id: string | null): void;
  reload(): void;
}

/** This user's words on one page; asked for again when the page changes or a word is added. */
export function useViewerWords(documentId: string, pageNo: number | null): ViewerWords {
  const [show, setShowState] = useState(() => readShowMyWords(browserStore()));
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const key = `${documentId}:${pageNo ?? ""}:${attempt}`;

  useEffect(() => {
    if (pageNo === null || !show) return;
    let cancelled = false;
    getMyWordsOnPage(documentId, pageNo).then(
      (reply) => !cancelled && setLoaded({ key, spots: reply.items }),
      (caught: unknown) => {
        const status = caught instanceof ApiError && caught.status !== 0 ? caught.status : null;
        if (!cancelled) setLoaded({ key, error: spotsLoadText(status) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, pageNo, show, key]);

  const current = loaded && loaded.key === key ? loaded : null;
  return {
    show,
    setShow: (on) => {
      setShowState(on);
      writeShowMyWords(browserStore(), on);
    },
    markers: show && pageNo !== null && current && "spots" in current ? markersOf(current.spots, pageNo) : [],
    loading: show && current === null,
    error: show && current && "error" in current ? current.error : null,
    activeId,
    setActiveId,
    reload: () => setAttempt((n) => n + 1),
  };
}

export function PageWordsList({ words }: { words: ViewerWords }) {
  const [said, setSaid] = useState("");
  const { show, markers, error } = words;
  const mark = (marker: Marker) => {
    words.setActiveId(marker.id);
    setSaid(`Marked ${markerText(marker)} on the page, number ${marker.n}.`);
    window.document.getElementById(markerId(marker.id))?.scrollIntoView({ block: "nearest", behavior: scrollBehavior() });
  };
  return (
    <section aria-labelledby="page-words-heading" className={`stack ${styles.pageWords}`}>
      <div className={styles.fields}>
        <h2 id="page-words-heading" className={styles.subheading}>
          My words on this page{show && markers.length > 0 ? ` (${markers.length})` : ""}
        </h2>
        <label className={styles.toggle}>
          <input type="checkbox" checked={show} onChange={(event) => words.setShow(event.target.checked)} />
          Show my words
        </label>
      </div>
      <p className="visually-hidden" role="status">{said}</p>
      {show && words.loading && <p className="muted">Loading your words…</p>}
      {error && (
        <div className={styles.fields}>
          <p>{error}</p>
          <button type="button" className={`btn ${styles.target}`} onClick={words.reload}>Try again</button>
        </div>
      )}
      {show && !words.loading && !error && markers.length === 0 && (
        <p className="muted">None yet. Choose &quot;Add meaning&quot; by a value or a scanned line. Only you see your words.</p>
      )}
      {markers.length > 0 && (
        <ol className={styles.list}>
          {markers.map((marker) => (
            <li key={marker.id}>
              <button type="button" className={`btn btn-quiet ${styles.target} ${styles.markerItem}`}
                aria-pressed={words.activeId === marker.id} onClick={() => mark(marker)}>
                <span className={styles.markerNumber} aria-hidden="true">{marker.n}</span>
                <span className="visually-hidden">Mark {marker.n}: </span>
                <bdi dir="auto" className={styles.text}>{marker.word}</bdi>
                <span aria-hidden="true">→</span><span className="visually-hidden">means</span>
                <bdi dir="auto" className={styles.text}>{marker.meaning}</bdi>
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export interface AddMeaningProps {
  documentId: string;
  userId: string;
  text: string; // the value or line as read
  region: Region; // where it is: the spot saved with the word
  onClose(): void;
  onClosed(): void;
  onSaved(message: string): void;
}

/** A small form, the word prefilled as read; a word with another meaning asks first (DuplicateDialog). */
export function AddMeaningDialog({ documentId, userId, text, region, onClose, onClosed, onSaved }: AddMeaningProps) {
  const picks = pickableWords(text);
  const [draft, setDraft] = useState<WordDraft>(() => ({ word: wordFromText(text), meaning: "" }));
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState<{ body: ViewerWordBody; existing: Existing[] } | null>(null);

  async function send(body: ViewerWordBody) {
    setBusy(true);
    setProblem(null);
    try {
      await addMyWord(body);
      onSaved("Saved to My words and sent to your organization for approval. Once approved, it applies to this document.");
    } catch (caught) {
      const state = failedState(caught);
      if (state.kind === "ask") setAsking({ body, existing: state.existing });
      else setProblem(state.kind === "refused" ? state.message : "Not saved.");
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setTried(true);
    if (busy || !draftOk(draft)) return;
    const body = viewerBody(draft, userId, documentId, region);
    if (body) void send(body);
    else setProblem("This spot can't hold a word. Choose another value or line.");
  }

  function choose(choice: Choice) {
    if (!asking) return;
    const body = retryBody(asking.body, choice);
    setAsking(null);
    if (body) void send(body);
    else setProblem("Not saved. Change the meaning, or close.");
  }

  return (
    <>
      <ActionDialog title="Add meaning" onClose={onClose} onClosed={onClosed}>
        <form onSubmit={submit} noValidate className="stack">
          <p className="muted">
            Saved to My words for you, marked on this spot, and sent for organization approval. Once approved, it applies to this document.
          </p>
          {picks.length > 1 && (
            <fieldset className={styles.picks}>
              <legend>Pick the word</legend>
              {picks.map((word) => (
                <button key={word} type="button" className={`btn ${styles.target}`} aria-pressed={draft.word === word}
                  onClick={() => setDraft({ ...draft, word })}>
                  <bdi dir="auto">{word}</bdi>
                </button>
              ))}
            </fieldset>
          )}
          <WordFields idPrefix="viewer-word" draft={draft} onChange={setDraft} checked={tried} />
          {problem && <p className="error-text" role="alert">{problem}</p>}
          <div className="row">
            <button type="submit" className={`btn btn-primary ${styles.target}`} aria-disabled={busy || undefined}>
              {busy && <span className="spinner" aria-hidden="true" />}Save
            </button>
            <button type="button" className={`btn ${styles.target}`} onClick={onClose}>Cancel</button>
          </div>
        </form>
      </ActionDialog>
      {asking && <DuplicateDialog draft={asking.body} existing={asking.existing} onChoose={choose} />}
    </>
  );
}
