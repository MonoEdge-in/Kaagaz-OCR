"use client";

// /search (desktop/Search.html; docs/architecture.md, The screens): the top bar's search lands
// here. "Inside documents" searches the documents' text (GET /api/search, the same
// results as the documents screen's own search, words marked); "Names" finds
// documents by name (GET /api/documents?q=). The question and the choice live in the
// address (?q=…&in=names), so a search can be shared, reloaded and gone back to. The
// filters, sorting and "show more" stay on the documents screen (NB-26, NB-27).

import Form from "next/form";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { InsideSearchResults } from "@/components/documents/InsideResults";
import { MAX_SEARCH_LENGTH } from "@/components/documents/insideSearch";
import type { SearchMode } from "@/components/documents/insideSearch";
import { LabelChip } from "@/components/files/LabelChip";
import { Button } from "@/components/ui/Button/Button";
import { EmptyState } from "@/components/ui/EmptyState/EmptyState";
import { Icon } from "@/components/ui/Icon/Icon";
import { RadioPill } from "@/components/ui/Segmented/Segmented";
import { ApiError, listDocuments, searchDocuments } from "@/lib/api";
import { docTypeLabel, statusChip } from "@/lib/format";
import type { DocumentList, DocumentSearch } from "@/lib/types";
import styles from "./Search.module.css";

/** How many names the Names search shows; the documents screen pages through more. */
export const NAMES_LIMIT = 25;

/** The search the address asks for: the question (trimmed, at most 200 characters) and where. */
export function searchFromAddress(params: URLSearchParams): { q: string; mode: SearchMode } {
  const q = (params.get("q") ?? "").trim().slice(0, MAX_SEARCH_LENGTH);
  return { q, mode: params.get("in") === "names" ? "names" : "inside" };
}

/** The address of a search: /search?q=rent, /search?q=rent&in=names. */
export function searchHref(q: string, mode: SearchMode): string {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (mode === "names") params.set("in", "names");
  const text = params.toString();
  return text ? `/search?${text}` : "/search";
}

/** "1 document is named like this.", "25 documents...", with "first 25 of 40" when there are more. */
export function namesCountText(shown: number, total: number): string {
  if (total > shown) return `The first ${shown} of ${total} documents named like this.`;
  return shown === 1 ? "1 document is named like this." : `${shown} documents are named like this.`;
}

const FAILED = "Could not search just now. Check your connection and try again.";
const errorText = (caught: unknown) => (caught instanceof ApiError && caught.status !== 0 ? caught.message : FAILED);

type Result =
  | { key: string; inside: DocumentSearch }
  | { key: string; names: DocumentList }
  | { key: string; error: string };

export function SearchScreen({ q, mode }: { q: string; mode: SearchMode }) {
  const router = useRouter();
  const [draft, setDraft] = useState(q);
  const [result, setResult] = useState<Result | null>(null);
  const [attempt, setAttempt] = useState(0);
  const key = `${mode}|${q}|${attempt}`;

  // A new address (the top bar, back and forward) puts its question in the box.
  const [shownQ, setShownQ] = useState(q);
  if (shownQ !== q) {
    setShownQ(q);
    setDraft(q);
  }

  useEffect(() => {
    if (q === "") return;
    let cancelled = false;
    const done = (next: Result) => {
      if (!cancelled) setResult(next);
    };
    if (mode === "inside") {
      searchDocuments({ q }).then(
        (inside) => done({ key, inside }),
        (caught: unknown) => done({ key, error: errorText(caught) }),
      );
    } else {
      listDocuments({ q, limit: NAMES_LIMIT }).then(
        (names) => done({ key, names }),
        (caught: unknown) => done({ key, error: errorText(caught) }),
      );
    }
    return () => {
      cancelled = true;
    };
  }, [q, mode, key]);

  const current = result !== null && result.key === key ? result : null;
  const error = current && "error" in current ? current.error : null;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Search</h1>
          <p>Find words inside your documents, or a document by its name.</p>
        </div>
      </header>

      <Form action="/search" role="search" className={styles.form}>
        <label htmlFor="search-q" className="visually-hidden">
          {mode === "inside" ? "Search inside documents" : "Search documents by name"}
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
            placeholder={mode === "inside" ? "e.g. rent paid in September, सातबारा 44" : "e.g. Mehta Textiles invoice"}
            onChange={(event) => setDraft(event.target.value)}
          />
        </span>
        {mode === "names" && <input type="hidden" name="in" value="names" />}
        <Button type="submit" variant="primary">
          Search
        </Button>
      </Form>

      <RadioPill
        label="Search in"
        name="search-in"
        value={mode}
        onChange={(next) => router.replace(searchHref(q, next))}
        options={[
          { value: "inside", label: "Inside documents" },
          { value: "names", label: "Names" },
        ]}
      />

      {q === "" ? (
        <EmptyState title="What are you looking for?">
          Type a word, a name, an amount or a survey number. Hindi and Marathi words work too.
        </EmptyState>
      ) : mode === "inside" ? (
        <InsideSearchResults
          found={current && "inside" in current ? current.inside : null}
          error={error}
          q={q}
          noResults="No document holds these words."
          datesIgnored={false}
          onRetry={() => setAttempt((n) => n + 1)}
          onClear={() => router.push(searchHref("", mode))}
        />
      ) : (
        <NameResults
          names={current && "names" in current ? current.names : null}
          error={error}
          q={q}
          onRetry={() => setAttempt((n) => n + 1)}
        />
      )}
    </div>
  );
}

function NameResults({
  names,
  error,
  q,
  onRetry,
}: {
  names: DocumentList | null;
  error: string | null;
  q: string;
  onRetry(): void;
}) {
  if (error !== null) {
    return (
      <div className={styles.error} role="alert">
        <p>
          <strong>Could not search the names.</strong> {error}
        </p>
        <Button size="sm" icon="read-again" onClick={onRetry}>
          Try again
        </Button>
      </div>
    );
  }
  if (names === null) {
    return (
      <p className="muted" role="status">
        Searching the names…
      </p>
    );
  }
  if (names.items.length === 0) {
    return <EmptyState title="No document is named like this.">Try fewer words, or search inside documents.</EmptyState>;
  }
  return (
    <>
      <p className="muted" role="status">
        {namesCountText(names.items.length, names.total)}
      </p>
      <ol className={styles.names} aria-label={`Documents named like “${q}”`}>
        {names.items.map((document) => {
          const chip = statusChip(document.status);
          return (
            <li key={document.id} className={styles.name}>
              <Link href={`/documents/${document.id}`} className={styles.nameLink}>
                {document.display_name}
              </Link>
              <span className={styles.facts}>
                <span>{docTypeLabel(document.doc_type)}</span>
                {document.sensitivity && <LabelChip label={document.sensitivity} />}
                <span className={chip.className} data-tone={chip.tone}>
                  {chip.text}
                </span>
              </span>
            </li>
          );
        })}
      </ol>
      {names.total > names.items.length && (
        <p>
          <Link href={`/documents?q=${encodeURIComponent(q)}`}>See them all on the documents screen</Link>
        </p>
      )}
    </>
  );
}
