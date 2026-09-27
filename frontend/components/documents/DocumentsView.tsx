"use client";

// The documents screen: upload, search, the list and its pages. While any
// document is still being checked or read, the list is asked for again every
// 3 seconds; once all have settled, it stops.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, listDocuments } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { DocumentList } from "@/lib/types";
import { DocumentsTable } from "./DocumentsTable";
import { PAGE_SIZE, POLL_MS, offsetAfterDelete, pageInfo, shouldPoll } from "./logic";
import { UploadArea } from "./UploadArea";
import styles from "./Documents.module.css";

interface Loaded {
  list: DocumentList;
  now: number; // when it arrived, for "5 minutes ago"
}

export function DocumentsView() {
  const { me } = useSession();
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState({ q: "", offset: 0 });
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listDocuments({ limit: PAGE_SIZE, offset: query.offset, q: query.q }).then(
      (list) => {
        if (cancelled) return;
        setLoaded({ list, now: Date.now() });
        setError(null);
      },
      (caught: unknown) => {
        if (cancelled) return;
        setError(caught instanceof ApiError ? caught.message : "Could not load documents.");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [query, reload]);

  const items = loaded?.list.items;
  useEffect(() => {
    if (!items || !shouldPoll(items)) return;
    const timer = setTimeout(() => setReload((n) => n + 1), POLL_MS);
    return () => clearTimeout(timer);
  }, [items]);

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuery({ q: draft.trim(), offset: 0 });
  }

  function clearSearch() {
    setDraft("");
    setQuery({ q: "", offset: 0 });
  }

  function afterDelete() {
    const shown = loaded?.list.items.length ?? 0;
    const offset = offsetAfterDelete(query.offset, shown, PAGE_SIZE);
    if (offset !== query.offset) setQuery({ ...query, offset });
    else setReload((n) => n + 1);
  }

  const list = loaded?.list;
  const pages = list ? pageInfo(query.offset, list.items.length, list.total) : null;

  return (
    <div className="stack">
      <h1>Documents</h1>
      <UploadArea onUploaded={() => setReload((n) => n + 1)} />

      <section className="card stack" aria-labelledby="list-heading">
        <div className={styles.listHead}>
          <h2 id="list-heading">Your documents</h2>
          <form className={styles.search} role="search" onSubmit={search}>
            <label htmlFor="documents-q" className="visually-hidden">
              Search by name
            </label>
            <input
              id="documents-q"
              className="input"
              type="search"
              placeholder="Search by name"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
            <button type="submit" className="btn">
              Search
            </button>
            {query.q && (
              <button type="button" className="btn" onClick={clearSearch}>
                Clear
              </button>
            )}
          </form>
        </div>

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}

        <div aria-live="polite">
          {!list && !error && <p className="muted">Loading…</p>}
          {list && list.total === 0 && query.q === "" && (
            <p className="muted">No documents yet. Drop a PDF, spreadsheet or photo to start.</p>
          )}
          {list && list.total === 0 && query.q !== "" && (
            <p className="muted">No document names contain “{query.q}”.</p>
          )}
        </div>

        {loaded && loaded.list.items.length > 0 && (
          <DocumentsTable
            items={loaded.list.items}
            me={me}
            now={loaded.now}
            onDeleted={afterDelete}
          />
        )}

        {pages && list && list.total > 0 && (
          <nav className={styles.pager} aria-label="Pages">
            <span className="muted" aria-live="polite">
              {pages.from}–{pages.to} of {list.total}
            </span>
            <button
              type="button"
              className="btn"
              disabled={!pages.hasPrevious}
              onClick={() => setQuery({ ...query, offset: Math.max(0, query.offset - PAGE_SIZE) })}
            >
              Previous
            </button>
            <button
              type="button"
              className="btn"
              disabled={!pages.hasNext}
              onClick={() => setQuery({ ...query, offset: query.offset + PAGE_SIZE })}
            >
              Next
            </button>
          </nav>
        )}
      </section>
    </div>
  );
}
