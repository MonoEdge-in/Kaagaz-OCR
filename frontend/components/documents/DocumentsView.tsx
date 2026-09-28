"use client";

// The documents screen: the page header with "Upload files", the dropzone,
// uploads in progress, search, the list and its pages. While any document is
// still being checked or read, the list is asked for again every 3 seconds;
// once all have settled, it stops.

import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { ApiError, listDocuments } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { DocumentList } from "@/lib/types";
import { DocumentsTable } from "./DocumentsTable";
import {
  ACCEPT_ATTRIBUTE,
  PAGE_SIZE,
  POLL_MS,
  deletedMessage,
  listView,
  offsetAfterDelete,
  pageInfo,
  shouldPoll,
} from "./logic";
import { Dropzone, UploadList, useUploads } from "./UploadArea";
import styles from "./Documents.module.css";

interface Loaded {
  list: DocumentList;
  now: number; // when it arrived, for "5 minutes ago"
}

const SKELETON_ROWS = [0, 1, 2, 3, 4];

export function DocumentsView() {
  const { me } = useSession();
  const inputRef = useRef<HTMLInputElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState({ q: "", offset: 0 });
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const uploads = useUploads(() => setReload((n) => n + 1));

  useEffect(() => {
    const hasActiveUpload = uploads.entries.some(
      (entry) => entry.state.kind === "waiting" || entry.state.kind === "uploading",
    );
    const hasCompletedUpload = uploads.entries.some(
      (entry) => entry.state.kind === "done" || entry.state.kind === "duplicate",
    );

    if (!hasActiveUpload && uploads.entries.length > 0 && hasCompletedUpload) {
      const timer = window.setTimeout(() => {
        window.location.reload();
      }, 250);
      return () => window.clearTimeout(timer);
    }
  }, [uploads.entries]);

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
        setError(
          caught instanceof ApiError
            ? caught.message
            : "Check your internet connection, then try again.",
        );
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

  const openPicker = () => inputRef.current?.click();

  function onChoose(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = ""; // so choosing the same file again still fires
    if (files.length > 0) void uploads.addFiles(files);
  }

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuery({ q: draft.trim(), offset: 0 });
  }

  function clearSearch() {
    setDraft("");
    setQuery({ q: "", offset: 0 });
  }

  function afterDelete(name: string) {
    setAnnouncement(deletedMessage(name));
    headingRef.current?.focus();
    const shown = loaded?.list.items.length ?? 0;
    const offset = offsetAfterDelete(query.offset, shown, PAGE_SIZE);
    if (offset !== query.offset) setQuery({ ...query, offset });
    else setReload((n) => n + 1);
  }

  const list = loaded?.list;
  const view = listView(list ? list.total : null, error !== null, query.q);
  const pages = list ? pageInfo(query.offset, list.items.length, list.total) : null;
  const hasDocuments = view === "list" || view === "no-match";

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Documents</h1>
          <p>Upload documents, then ask about them.</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={openPicker}>
          <Icon name="upload" />
          Upload files
        </button>
      </header>

      <label htmlFor="upload-input" className="visually-hidden">
        Files to upload
      </label>
      <input
        ref={inputRef}
        id="upload-input"
        className="visually-hidden"
        type="file"
        multiple
        accept={ACCEPT_ATTRIBUTE}
        tabIndex={-1}
        onChange={onChoose}
      />

      {view === "empty" ? (
        <Dropzone variant="full" onFiles={(files) => void uploads.addFiles(files)} onChoose={openPicker}>
          <EmptyState
            icon="file"
            title="No documents yet"
            action={
              <button type="button" className="btn btn-primary" onClick={openPicker}>
                <Icon name="upload" />
                Upload files
              </button>
            }
          >
            Upload invoices, registers or bank statements, as PDF, photo or spreadsheet.
          </EmptyState>
        </Dropzone>
      ) : (
        <Dropzone variant="slim" onFiles={(files) => void uploads.addFiles(files)} onChoose={openPicker} />
      )}

      <UploadList entries={uploads.entries} onClearFinished={uploads.clearFinished} />

      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>

      {view !== "empty" && (
        <section className={styles.list} aria-labelledby="list-heading">
          <div className={styles.listHead}>
            <h2 id="list-heading" ref={headingRef} tabIndex={-1} className={styles.listHeading}>
              Your documents
            </h2>
            {hasDocuments && (
              <form className={styles.search} role="search" onSubmit={search}>
                <label htmlFor="documents-q" className="visually-hidden">
                  Search documents by name
                </label>
                <div className={styles.searchField}>
                  <Icon name="search" className={styles.searchIcon} />
                  <input
                    id="documents-q"
                    className={`input ${styles.searchInput}`}
                    type="search"
                    enterKeyHint="search"
                    autoComplete="off"
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                  />
                </div>
                <button type="submit" className="btn">
                  Search
                </button>
                {query.q !== "" && (
                  <button type="button" className="btn btn-quiet" onClick={clearSearch}>
                    Clear search
                  </button>
                )}
              </form>
            )}
          </div>

          {error && (
            <div className={styles.errorBanner} role="alert">
              <Icon name="alert" className={styles.errorIcon} />
              <div className={styles.errorBody}>
                <p className={styles.errorTitle}>Could not load your documents.</p>
                <p>{error}</p>
              </div>
              <button type="button" className="btn" onClick={() => setReload((n) => n + 1)}>
                <Icon name="refresh" />
                Try again
              </button>
            </div>
          )}

          {view === "loading" && (
            <>
              <p className="visually-hidden" role="status">
                Loading your documents…
              </p>
              <div className={`delayed ${styles.skeletons}`} aria-hidden="true">
                {SKELETON_ROWS.map((row) => (
                  <span key={row} className={`skeleton ${styles.skeletonRow}`} />
                ))}
              </div>
            </>
          )}

          {view === "no-match" && (
            <EmptyState
              icon="search-off"
              title={`No document names contain “${query.q}”.`}
              headingLevel={3}
              action={
                <button type="button" className="btn" onClick={clearSearch}>
                  Clear search
                </button>
              }
            />
          )}

          {loaded && view === "list" && loaded.list.items.length > 0 && (
            <DocumentsTable
              items={loaded.list.items}
              me={me}
              now={loaded.now}
              onDeleted={afterDelete}
            />
          )}

          {pages && list && view === "list" && (
            <nav className={styles.pager} aria-label="Document pages">
              <span className="muted tabular" aria-live="polite">
                {pages.from}–{pages.to} of {list.total}
              </span>
              {list.total > PAGE_SIZE && (
                <>
                  <button
                    type="button"
                    className="btn"
                    disabled={!pages.hasPrevious}
                    onClick={() => setQuery({ ...query, offset: Math.max(0, query.offset - PAGE_SIZE) })}
                  >
                    <Icon name="chevron-left" />
                    Previous
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={!pages.hasNext}
                    onClick={() => setQuery({ ...query, offset: query.offset + PAGE_SIZE })}
                  >
                    Next
                    <Icon name="chevron-right" />
                  </button>
                </>
              )}
            </nav>
          )}
        </section>
      )}
    </div>
  );
}
