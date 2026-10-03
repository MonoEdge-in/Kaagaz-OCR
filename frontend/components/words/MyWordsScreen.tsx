"use client";

// /words: my own words and what they mean ("malak" means owner), only mine (the
// server sees to it). Add at the top; the list is filtered in the browser (at
// most 500); each meaning can be changed or deleted (asked first). An add or a
// change that meets another meaning asks first (DuplicateDialog). Results go to
// one polite live region; problems are alerts by the form or in the dialog.

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { addMyWord, changeMyWord, deleteMyWord, listMyWords } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { MyWord } from "@/lib/types";
import { DuplicateDialog, WordFields } from "./WordParts";
import { addBody, changeBody, draftOk, failedState, limitNote, retryBody, savedState, SOURCE_TEXT, wordGroups } from "./words";
import type { ChangeBody, Choice, Existing, NewWordBody, WordDraft } from "./words";
import styles from "./Words.module.css";

const EMPTY: WordDraft = { word: "", meaning: "" };
type RowDialog = { mode: "change" | "delete"; item: MyWord; draft: WordDraft; error: string | null };
// One save: an add (POST), or a change (PATCH of wordId). draft is what the question names.
type Job = { draft: WordDraft } & ({ body: NewWordBody; wordId?: undefined } | { body: ChangeBody; wordId: string });

export function MyWordsScreen() {
  const { me } = useSession();
  const userId = me.user.id;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const deleted = useRef(false); // then focus goes to the heading, as the row is gone
  const [attempt, setAttempt] = useState(0);
  const [items, setItems] = useState<MyWord[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [filter, setFilter] = useState("");
  const [draft, setDraft] = useState(EMPTY);
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState<(Job & { existing: Existing[] }) | null>(null);
  const [row, setRow] = useState<RowDialog | null>(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    let cancelled = false;
    listMyWords().then(
      (result) => !cancelled && setItems(result.items),
      () => !cancelled && setLoadError(true),
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  /** Busy while it runs; a refusal goes to onRefused in the server's words, the 409 question to the dialog. */
  async function run(work: () => Promise<void>, onRefused: (message: string) => void, job?: Job) {
    setBusy(true);
    try {
      await work();
    } catch (caught) {
      const state = failedState(caught);
      if (state.kind === "ask" && job) setAsking({ ...job, existing: state.existing });
      else onRefused(state.kind === "refused" ? state.message : "Not saved.");
    } finally {
      setBusy(false);
    }
  }

  const save = (job: Job, onRefused: (message: string) => void) =>
    run(
      async () => {
        const reply = job.wordId === undefined ? await addMyWord(job.body) : await changeMyWord(job.wordId, job.body);
        const { id } = reply.word;
        setItems((now) => [...(now ?? []).filter((it) => it.id !== id && it.id !== job.body.replace_id), reply.word]);
        if (job.body.replace_id) setAttempt((n) => n + 1); // and ask the server which rows a replace left
        setStatus(savedState(reply).message);
        if (job.wordId === undefined) {
          setDraft(EMPTY);
          setTried(false);
        }
      },
      onRefused,
      job,
    );

  function submitAdd(event: FormEvent) {
    event.preventDefault();
    setTried(true);
    setProblem(null);
    const body = addBody(draft, userId);
    if (!busy && draftOk(draft)) void save({ body, draft: body }, setProblem);
  }

  function choose(choice: Choice) {
    if (!asking) return;
    setAsking(null);
    const body = retryBody(asking.body, choice);
    if (body) void save({ ...asking, body } as Job, setProblem);
    else setStatus("Not saved.");
  }

  function submitRow(event: FormEvent) {
    event.preventDefault();
    if (!row || busy) return;
    const { item } = row;
    const fail = (error: string) => setRow({ ...row, error });
    if (row.mode === "delete") {
      void run(async () => {
        await deleteMyWord(item.id, userId);
        setItems((now) => (now ?? []).filter((it) => it.id !== item.id));
        setStatus(`Deleted '${item.word}' as '${item.meaning}'.`);
        deleted.current = true;
        setRow(null);
      }, fail);
      return;
    }
    if (!draftOk(row.draft)) return;
    const body = changeBody(item, row.draft, userId);
    setRow(null); // a question, if one comes, takes its place; a refusal opens it again
    if (body) void save({ body, wordId: item.id, draft: { word: body.word ?? item.word, meaning: body.meaning ?? item.meaning } }, fail);
  }

  const groups = items ? wordGroups(items, filter) : [];
  const note = items ? limitNote(items.length) : null;
  const spinner = busy && <span className="spinner" aria-hidden="true" />;
  const alert = (text: string | null) => text && <p className="error-text" role="alert">{text}</p>;

  return (
    <div className="page">
      <header className="page-header"><div>
        <h1 ref={headingRef} tabIndex={-1}>My words</h1>
        <p>Words the way you write them, and what they mean. Chat uses them for your questions. Only you see them.</p>
      </div></header>
      <p className="visually-hidden" role="status" aria-live="polite">{status}</p>
      {alert(problem)}

      <form className={`card stack ${styles.block}`} onSubmit={submitAdd} noValidate aria-labelledby="add-word">
        <h2 id="add-word" className={styles.subheading}>Add a word</h2>
        <div className={styles.fields}>
          <WordFields idPrefix="add" draft={draft} onChange={setDraft} checked={tried} />
          <button type="submit" className={`btn btn-primary ${styles.target}`} aria-disabled={busy || undefined}>
            {spinner}Add
          </button>
        </div>
        {note && <p className="hint">{note}</p>}
      </form>

      {items === null && !loadError && <p className="muted">Loading your words…</p>}
      {loadError && (
        <div role="alert" className={styles.fields}>
          <p>Your words could not be loaded. Check your internet connection, then try again.</p>
          <button type="button" className={`btn ${styles.target}`} onClick={() => { setLoadError(false); setAttempt((n) => n + 1); }}>Try again</button>
        </div>
      )}
      {items?.length === 0 && (
        <EmptyState icon="words" title="No words yet">
          <p>
            Add a word the way you type it, and its meaning in English. For example: <b>malak</b> → <b>owner</b>.
            Then &quot;malak kon ahe?&quot; also finds the owner.
          </p>
        </EmptyState>
      )}

      {items !== null && items.length > 0 && (
        <section aria-labelledby="words-heading" className={`stack ${styles.block}`}>
          <div className={styles.fields}>
            <h2 id="words-heading" className={styles.subheading}>Your words ({items.length})</h2>
            <div className={`field ${styles.field}`}>
              <label htmlFor="words-filter">Search your words</label>
              <input id="words-filter" type="search" className="input" value={filter} autoComplete="off" onChange={(event) => setFilter(event.target.value)} />
            </div>
          </div>
          {groups.length === 0 && <p className="muted">No words match this search.</p>}
          <ul className={styles.list}>
            {groups.map((group) => (
              <li key={group.key} className={styles.group}>
                <span className={styles.word}>{group.word}</span>
                <ul className={styles.list}>
                  {group.meanings.map((item) => (
                    <li key={item.id} className="row">
                      <span className={styles.text}>
                        <span aria-hidden="true">→ </span><span className="visually-hidden">means </span>{item.meaning}
                      </span>
                      <span className="muted">{SOURCE_TEXT[item.source]}</span>
                      {item.built_in && <span className="badge badge-review">Chat also knows this word</span>}
                      {(["Change", "Delete"] as const).map((verb) => (
                        <button key={verb} type="button" className={`btn btn-quiet ${styles.target}`} aria-label={`${verb} '${item.word}' as '${item.meaning}'`}
                          onClick={() => setRow({ mode: verb === "Change" ? "change" : "delete", item, draft: item, error: null })}>{verb}</button>
                      ))}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </section>
      )}

      {asking && <DuplicateDialog draft={asking.draft} existing={asking.existing} onChoose={choose} />}
      {row && (
        <ActionDialog
          title={row.mode === "change" ? `Change '${row.item.word}'` : "Delete this word?"}
          onClose={() => setRow(null)}
          onClosed={() => {
            if (deleted.current) headingRef.current?.focus();
            deleted.current = false;
          }}
        >
          <form onSubmit={submitRow} noValidate className="stack">
            {row.mode === "change" ? (
              <WordFields idPrefix="edit" draft={row.draft} onChange={(next) => setRow({ ...row, draft: next })} checked />
            ) : (
              <p>Delete &apos;{row.item.word}&apos; as &apos;{row.item.meaning}&apos;? Chat stops using it.</p>
            )}
            {alert(row.error)}
            <div className="row">
              <button type="submit" className={`btn ${row.mode === "change" ? "btn-primary" : "btn-danger"} ${styles.target}`} aria-disabled={busy || undefined}>
                {spinner}{row.mode === "change" ? "Save" : "Delete"}
              </button>
              <button type="button" className={`btn ${styles.target}`} data-autofocus onClick={() => setRow(null)}>Cancel</button>
            </div>
          </form>
        </ActionDialog>
      )}
    </div>
  );
}
