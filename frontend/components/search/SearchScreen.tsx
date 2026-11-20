"use client";

// /search (desktop/Search.html; docs/architecture.md, The screens): the top bar's search lands
// here. #730 (Tushar, 20 Nov 2026): one box, "Find a file by name or path". A file's name, a path
// pasted in any common form (pathSearch.ts: Kaagaz's "A › B", a Windows or Mac path, a file://
// address) or a Kaagaz file link. A link opens its file; a path or a name lists the matching files
// first (each with its place in grey), then the matching folders. Only what this person can open
// (GET /folders, GET /documents and the file's own page, all as them); nothing found, or nothing
// they may open, is one neutral line that never names anything. Searching inside documents is the
// Documents screen's own search now; an old /search?in=inside (or in=names) link searches its words
// here. The question lives in the address (?q=), so a search can be shared, reloaded and gone back to.
// Issue 468: a search still waiting after about ten seconds says so, with Try again (StillLoading).
// A session that ended here kept the search's words as this person's draft, not in the sign-in's
// ?next= (lib/session.tsx, SEC1): back on /search with no words, the same person gets them back.

import Form from "next/form";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { filePlace } from "@/components/chat/scope";
import { buildTree } from "@/components/documents/folderTree";
import type { FolderTree } from "@/components/documents/folderTree";
import { MAX_SEARCH_LENGTH } from "@/components/documents/insideSearch";
import { SEARCH_DRAFT } from "@/components/shell/gate";
import { StillLoading } from "@/components/shell/StillLoading";
import { Button } from "@/components/ui/Button/Button";
import { EmptyState } from "@/components/ui/EmptyState/EmptyState";
import { Icon } from "@/components/ui/Icon/Icon";
import { ApiError, getDocumentSummary, listDocuments, listFolders } from "@/lib/api";
import { useBackFrom, withFrom } from "@/lib/backLink";
import { clearDraft, readDraft } from "@/lib/drafts";
import { statusChip } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { DocumentSummary } from "@/lib/types";
import {
  FILES_FETCHED,
  FILES_SHOWN,
  FOLDERS_SHOWN,
  NO_MATCH_TEXT,
  documentFromLink,
  fileQuery,
  findByPath,
  pathParts,
} from "./pathSearch";
import type { FoundPlace } from "./pathSearch";
import styles from "./Search.module.css";

export const SEARCH_LABEL = "Find a file by name or path";

/** The search the address asks for: the words (trimmed, at most 200 characters). An old `in=`
 * (inside, names) is left out: every search is by name or path now (#730). */
export function searchFromAddress(params: URLSearchParams): { q: string } {
  return { q: (params.get("q") ?? "").trim().slice(0, MAX_SEARCH_LENGTH) };
}

/** The address of a search: /search?q=rent. */
export function searchHref(q: string): string {
  return q ? `/search?${new URLSearchParams({ q }).toString()}` : "/search";
}

const FAILED = "Could not search just now. Check your connection and try again.";
const errorText = (caught: unknown) => (caught instanceof ApiError && caught.status !== 0 ? caught.message : FAILED);

export interface Found {
  tree: FolderTree;
  files: DocumentSummary[];
  more: number; // matching files past FILES_SHOWN
  folders: FoundPlace[];
  closestIn: FoundPlace | null; // no file matched; these are this folder's files
}

const NOTHING: Found = { tree: buildTree([]), files: [], more: 0, folders: [], closestIn: null };

/** What a search finds (a link's file is opened instead: "open"); a hidden or unknown file's link
 * finds nothing, as a random id does (the API answers both alike). */
export async function runSearch(q: string): Promise<Found | { open: string }> {
  const linked = documentFromLink(q);
  if (linked !== null) {
    try {
      await getDocumentSummary(linked);
      return { open: `/documents/${linked}` };
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) return NOTHING;
      throw caught;
    }
  }
  const parts = pathParts(q);
  if (parts.length === 0) return NOTHING; // only separators or quotes
  const [folders, named] = await Promise.all([listFolders(), listDocuments({ q: fileQuery(parts), limit: FILES_FETCHED })]);
  const tree = buildTree(folders.items);
  const found = findByPath(tree, parts, named.items);
  const files = found.closestIn
    ? (await listDocuments({ folder_id: found.closestIn.id, limit: FILES_SHOWN })).items
    : found.files;
  return {
    tree,
    files: files.slice(0, FILES_SHOWN),
    more: Math.max(0, files.length - FILES_SHOWN),
    folders: found.folders.slice(0, FOLDERS_SHOWN),
    closestIn: found.closestIn,
  };
}

type Result = { key: string; found: Found } | { key: string; error: string };

export function SearchScreen({ q }: { q: string }) {
  const router = useRouter();
  const userId = useSession().me.user.id;
  const [draft, setDraft] = useState(q);
  const [result, setResult] = useState<Result | null>(null);
  const [attempt, setAttempt] = useState(0);
  const key = `${q}|${attempt}`;

  // A new address (the top bar, back and forward) puts its words in the box.
  const [shownQ, setShownQ] = useState(q);
  if (shownQ !== q) {
    setShownQ(q);
    setDraft(q);
  }

  // The search a session's end kept for this person, read once and forgotten.
  useEffect(() => {
    const kept = readDraft(userId, SEARCH_DRAFT)?.q;
    clearDraft(userId, SEARCH_DRAFT);
    if (q === "" && typeof kept === "string" && kept.trim()) router.replace(searchHref(kept));
  }, [userId, q, router]);

  useEffect(() => {
    if (q === "") return;
    let cancelled = false;
    runSearch(q).then(
      (done) => {
        if (cancelled) return;
        if ("open" in done) router.replace(done.open);
        else setResult({ key, found: done });
      },
      (caught: unknown) => {
        if (!cancelled) setResult({ key, error: errorText(caught) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [q, key, router]);

  const current = result !== null && result.key === key ? result : null;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Search</h1>
          <p>Find a file or folder by its name or path. You can paste a path from your computer, or a file&apos;s link.</p>
        </div>
      </header>

      <Form action="/search" role="search" className={styles.form}>
        <label htmlFor="search-q" className="visually-hidden">
          {SEARCH_LABEL}
        </label>
        <span className={styles.box}>
          <Icon name="search" className={styles.boxIcon} />
          <input
            id="search-q"
            name="q"
            type="search"
            className={`input ${styles.input}`}
            value={draft}
            maxLength={MAX_SEARCH_LENGTH}
            enterKeyHint="search"
            autoComplete="off"
            placeholder={SEARCH_LABEL}
            onChange={(event) => setDraft(event.target.value)}
          />
        </span>
        <Button type="submit" variant="primary">
          Search
        </Button>
      </Form>

      {q === "" ? (
        <EmptyState title="What are you looking for?">
          Type a file&apos;s name, or paste its path, for example Land &amp; Legal › Land records › 7-12 Extract.pdf.
        </EmptyState>
      ) : current && "error" in current ? (
        <div className={styles.error} role="alert">
          <p>
            <strong>Could not search.</strong> {current.error}
          </p>
          <Button size="sm" icon="read-again" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </Button>
        </div>
      ) : current ? (
        <SearchResults found={current.found} />
      ) : (
        <>
          <p className="muted" role="status">
            Searching…
          </p>
          <StillLoading key={q} onRetry={() => setAttempt((n) => n + 1)} />
        </>
      )}
    </div>
  );
}

/** The files first, each with its place in grey, then the folders. Exported for the render test. */
export function SearchResults({ found }: { found: Found }) {
  const from = useBackFrom(); // this search, for the documents' back link (#436)
  const { tree, files, folders, closestIn, more } = found;
  if (files.length === 0 && folders.length === 0) {
    return (
      <p className="muted" role="status">
        {NO_MATCH_TEXT}
      </p>
    );
  }
  return (
    <>
      {closestIn ? (
        <p className="muted" role="status">
          No file matches that name in {closestIn.path}. These are the files in that folder.
        </p>
      ) : null}
      {files.length > 0 ? (
        <section aria-labelledby="found-files">
          <h2 id="found-files" className={styles.groupHeading}>
            Files
          </h2>
          <ol className={styles.names}>
            {files.map((document) => {
              const chip = statusChip(document.status, document.detected_type, document.failure_code);
              return (
                <li key={document.id} className={styles.name}>
                  <Link href={withFrom(`/documents/${document.id}`, from)} className={styles.nameLink}>
                    {document.display_name}
                  </Link>
                  <span className={styles.facts}>
                    <span className={styles.place}>{filePlace(tree, document)}</span>
                    <span className={chip.className} data-tone={chip.tone}>
                      {chip.text}
                    </span>
                  </span>
                </li>
              );
            })}
          </ol>
          {more > 0 ? <p className="muted">And {more} more. Paste more of the path to narrow it.</p> : null}
        </section>
      ) : null}
      {folders.length > 0 ? (
        <section aria-labelledby="found-folders">
          <h2 id="found-folders" className={styles.groupHeading}>
            Folders
          </h2>
          <ol className={styles.names}>
            {folders.map((folder) => (
              <li key={folder.id} className={styles.name}>
                <Link href={`/documents?folder=${encodeURIComponent(folder.id)}`} className={styles.nameLink}>
                  {folder.path}
                </Link>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </>
  );
}
