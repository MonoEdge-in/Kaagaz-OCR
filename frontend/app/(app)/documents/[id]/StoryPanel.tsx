"use client";

// "Your story about this document" (Story of this PDF, S4): the signed-in user's
// own words about the document, in any language, so search can find it later.
// Only its author ever sees it (the server sees to it). With no story, the text
// box and the hint show; with one, the story shows with Edit and Delete (asked
// first, inside the page). The upload list links here (#your-story), and then
// focus comes here. Results go to one polite live region; problems are alerts.
// The story is shown as text only and never logged. The rules and the words are
// components/viewer/story.ts; the server checks the text again and wins.

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { FormEvent, RefObject } from "react";
import {
  countText,
  STORY_ANCHOR,
  STORY_HINT,
  STORY_MAX,
  codePoints,
  storyBody,
  storyFailure,
  storyProblem,
  tidyStory,
} from "@/components/viewer/story";
import { deleteMyStory, getMyStory, saveMyStory } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { MyStory } from "@/lib/types";
import styles from "./DocumentView.module.css";

type Loaded = { attempt: number; story: MyStory | null } | { attempt: number; error: string };

export function StoryPanel({ documentId, inTrash }: { documentId: string; inTrash: boolean }) {
  const { me } = useSession();
  const id = useId();
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [tried, setTried] = useState(false); // the instant check shows once Save was pressed
  const [refused, setRefused] = useState<string | null>(null); // the server's 400, by the box
  const [alert, setAlert] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "delete" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [status, setStatus] = useState("");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const editRef = useRef<HTMLButtonElement>(null);
  const deleteRef = useRef<HTMLButtonElement>(null);
  // Where focus goes after the next render, when the focused element goes away.
  const focusNext = useRef<RefObject<HTMLElement | null> | null>(null);
  const focusOnMount = useCallback((element: HTMLElement | null) => element?.focus(), []);

  useEffect(() => {
    focusNext.current?.current?.focus();
    focusNext.current = null;
  });

  useEffect(() => {
    if (inTrash) return;
    let cancelled = false;
    getMyStory(documentId).then(
      (reply) => {
        if (cancelled) return;
        // Opened from "Add a story" after an upload: bring the section to the user.
        if (window.location.hash === `#${STORY_ANCHOR}`) focusNext.current = reply.story ? headingRef : boxRef;
        setLoaded({ attempt, story: reply.story });
      },
      (caught: unknown) => {
        if (!cancelled) setLoaded({ attempt, error: storyFailure(caught, "load").message });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, attempt, inTrash]);

  const heading = (
    <h2 id={`${id}-heading`} ref={headingRef} tabIndex={-1} className={styles.panelHeading}>
      Your story about this document
    </h2>
  );
  if (inTrash) {
    return (
      <section id={STORY_ANCHOR} className={styles.panel} aria-labelledby={`${id}-heading`}>
        {heading}
        <p className="muted">Your story is kept while this document is in Trash. Restore it to see or change your story.</p>
      </section>
    );
  }

  const current = loaded && loaded.attempt === attempt ? loaded : null;
  const story = current && "story" in current ? current.story : null;

  function finish(message: string, saved: MyStory | null) {
    setLoaded({ attempt, story: saved });
    setEditing(false);
    setTried(false);
    setDraft("");
    setStatus(message);
    focusNext.current = headingRef;
  }

  function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setTried(true);
    setRefused(null);
    setAlert(null);
    if (storyProblem(draft)) {
      boxRef.current?.focus();
      return;
    }
    setBusy("save");
    saveMyStory(documentId, storyBody(draft, me.user.id))
      .then(
        (reply) => finish("Your story is saved. Only you can see it.", reply.story),
        (caught: unknown) => {
          const failure = storyFailure(caught, "save");
          if (failure.kind === "field") setRefused(failure.message);
          else setAlert(failure.message);
          focusNext.current = boxRef;
        },
      )
      .finally(() => setBusy(null));
  }

  function remove() {
    if (busy) return;
    setBusy("delete");
    setAlert(null);
    deleteMyStory(documentId, me.user.id)
      .then(
        () => finish("Your story is deleted.", null),
        (caught: unknown) => {
          const failure = storyFailure(caught, "delete");
          if (failure.kind === "gone") finish(failure.message, null);
          else {
            setAlert(failure.message);
            focusNext.current = deleteRef;
          }
        },
      )
      .finally(() => {
        setBusy(null);
        setConfirming(false);
      });
  }

  function startEdit(text: string) {
    setDraft(text);
    setEditing(true);
    setAlert(null);
    focusNext.current = boxRef;
  }

  function cancelEdit() {
    setEditing(false);
    setTried(false);
    setRefused(null);
    focusNext.current = editRef;
  }

  const problem = refused ?? (tried ? storyProblem(draft) : null);
  const over = codePoints(tidyStory(draft)) > STORY_MAX;
  const spinner = <span className="spinner" aria-hidden="true" />;
  const target = `btn ${styles.storyTarget}`;
  const describedBy = [story ? null : `${id}-hint`, `${id}-count`, problem ? `${id}-problem` : null];

  return (
    <section id={STORY_ANCHOR} className={styles.panel} aria-labelledby={`${id}-heading`}>
      {heading}
      <p className="hint">Only you can see your story.</p>
      <p className="visually-hidden" role="status" aria-live="polite">
        {status}
      </p>
      {alert && (
        <p className="error-text" role="alert">
          {alert}
        </p>
      )}
      {!current && <p className="muted">Loading your story…</p>}
      {current && "error" in current && (
        <div role="alert" className="stack">
          <p className="error-text">{current.error}</p>
          <button type="button" className={target} onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </button>
        </div>
      )}
      {current && "story" in current && (story === null || editing) && (
        <form className="stack" onSubmit={save} noValidate>
          <div className="field">
            <label htmlFor={`${id}-box`}>{story ? "Change your story" : "Your story"}</label>
            {!story && (
              <p id={`${id}-hint`} className="hint">
                {STORY_HINT}
              </p>
            )}
            <textarea
              id={`${id}-box`}
              ref={boxRef}
              className={`input ${styles.storyBox}`}
              rows={4}
              dir="auto"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              aria-invalid={problem ? true : undefined}
              aria-describedby={describedBy.filter(Boolean).join(" ")}
            />
            <p id={`${id}-count`} className={over ? "error-text" : "hint"}>
              {countText(draft)}
            </p>
            {problem && (
              <p id={`${id}-problem`} className="error-text" role="alert">
                {problem}
              </p>
            )}
          </div>
          <div className="row">
            <button type="submit" className={`${target} btn-primary`} aria-disabled={busy !== null || undefined}>
              {busy === "save" && spinner}
              {busy === "save" ? "Saving…" : "Save"}
            </button>
            {story && (
              <button type="button" className={target} onClick={cancelEdit}>
                Cancel
              </button>
            )}
          </div>
        </form>
      )}
      {story && !editing && (
        <>
          <p className={styles.storyText} dir="auto">
            {story.story}
          </p>
          <p className="hint">Last saved {formatDateTime(story.updated_at)}</p>
          {confirming ? (
            <div role="group" aria-labelledby={`${id}-ask`} className="stack">
              <p id={`${id}-ask`}>Delete your story about this document? This can&apos;t be undone.</p>
              <div className="row">
                <button type="button" className={`${target} btn-danger`} onClick={remove} aria-disabled={busy !== null || undefined}>
                  {busy === "delete" && spinner}
                  {busy === "delete" ? "Deleting…" : "Delete"}
                </button>
                <button
                  ref={focusOnMount}
                  type="button"
                  className={target}
                  onClick={() => {
                    setConfirming(false);
                    focusNext.current = deleteRef;
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="row">
              <button ref={editRef} type="button" className={target} onClick={() => startEdit(story.story)}>
                Edit<span className="visually-hidden"> your story</span>
              </button>
              <button ref={deleteRef} type="button" className={target} onClick={() => setConfirming(true)}>
                Delete<span className="visually-hidden"> your story</span>
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
