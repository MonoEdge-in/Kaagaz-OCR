"use client";

// Sources by file (#718, design board 1) and the files searched (#719, board 2). Each file is one
// compact row: an arrow button that shows or hides its pages, the file's name, its folder path in
// grey ("Shared with you" in a folder this person can't see), how many pages, and "Open file".
// Open, it lists up to 5 pages: the label, the text with the question's words marked, the
// citation numbers ("[1]") or "Also matches", and "Open", which goes to the exact spot. "Searched
// N files" under the sources opens the closest files the search found, best first, the cited ones
// tagged "Used": the first 7, then "Show N more". All text is plain text.

import Link from "next/link";
import { useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { useBackFrom, withFrom } from "@/lib/backLink";
import type { FilePage, SearchedFile } from "@/lib/types";
import {
  SEARCHED_SHOWN,
  documentLink,
  filePageLink,
  folderPathText,
  markParts,
  pageCiteText,
  pageCountText,
  snippetAround,
} from "./answer";
import styles from "./Chat.module.css";

interface FileRowProps {
  id: string;
  name: string;
  path: readonly string[] | null | undefined;
  pages: readonly FilePage[];
  terms: readonly string[];
  rank?: number; // the searched list's place, shown before the name
  used?: boolean; // the answer cites it (the searched list)
  startOpen?: boolean;
}

function FileRow({ id, name, path, pages, terms, rank, used = false, startOpen = false }: FileRowProps) {
  const from = useBackFrom();
  const [open, setOpen] = useState(startOpen);
  const place = folderPathText(path);
  return (
    <li className={styles.fileItem}>
      <div className={styles.fileRow}>
        <button
          type="button"
          className={`btn btn-quiet ${styles.fileToggle}`}
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} pages of ${name}`}
          onClick={() => setOpen(!open)}
        >
          <Icon name="chevron-right" size={16} className={styles.fileChevron} />
        </button>
        {rank !== undefined ? <span className={styles.fileRank}>{rank}</span> : <Icon name="file" size={16} />}
        <div className={styles.fileName}>
          <span className={styles.sourceName} title={name}>
            {name}
          </span>
          {used ? <span className={styles.usedTag}>Used</span> : null}
          {place ? <span className={styles.filePath}>{place}</span> : null}
        </div>
        <span className={styles.sourceMeta}>{pageCountText(pages.length)}</span>
        <Link className={styles.openLink} href={withFrom(documentLink(id, terms), from)}>
          Open file<span className="visually-hidden">{` ${name}`}</span>
        </Link>
      </div>
      {open ? (
        <ol className={styles.filePages} aria-label={`Pages of ${name}`}>
          {pages.map((page) => (
            <li key={page.page_no} className={styles.filePage}>
              <span className={styles.sourceMeta}>Page {page.page_no}</span>
              <span className={styles.filePageText}>
                {markParts(snippetAround(page.snippet, terms, 160), terms).map((part, index) =>
                  part.mark ? <mark key={index}>{part.text}</mark> : part.text,
                )}
              </span>
              <span className={styles.sourceMeta}>{pageCiteText(page)}</span>
              <Link className={styles.openLink} href={withFrom(filePageLink(id, page, terms), from)}>
                Open<span className="visually-hidden">{` page ${page.page_no} of ${name}`}</span>
              </Link>
            </li>
          ))}
        </ol>
      ) : null}
    </li>
  );
}

/** #718: the files a new answer cites, each with its pages; the first open. */
export function FileSources({
  files,
  terms,
}: {
  files: readonly { document_id: string; title: string; path?: string[] | null; pages: FilePage[] }[];
  terms: readonly string[];
}) {
  return (
    <div>
      <section className={styles.fileBox} aria-label="Sources">
        <h3 className={styles.fileHeading}>Sources · {files.length === 1 ? "1 file" : `${files.length} files`}</h3>
        <ul className={styles.fileList}>
          {files.map((file, index) => (
            <FileRow
              key={file.document_id}
              id={file.document_id}
              name={file.title}
              path={file.path}
              pages={file.pages}
              terms={terms}
              startOpen={index === 0}
            />
          ))}
        </ul>
      </section>
      <p className={styles.sourceMeta}>
        Each file lists up to 5 pages: cited pages first ([n]), then pages that also matched. Open goes to the exact spot.
      </p>
    </div>
  );
}

/** #719: "Searched N files", which opens the closest files the search found. */
export function SearchedFiles({ files, terms }: { files: readonly SearchedFile[]; terms: readonly string[] }) {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const shown = all ? files : files.slice(0, SEARCHED_SHOWN);
  const more = files.length - shown.length;
  return (
    <div>
      <button type="button" className="btn btn-quiet" aria-expanded={open} onClick={() => setOpen(!open)}>
        Searched {files.length === 1 ? "1 file" : `${files.length} files`}
      </button>
      {open ? (
        <section className={styles.fileBox} aria-label="Files searched for this answer">
          <h3 className={styles.fileHeading}>Files searched for this answer</h3>
          <p className={styles.sourceMeta}>The {files.length} closest files, best first.</p>
          <ul className={styles.fileList}>
            {shown.map((file, index) => (
              <FileRow
                key={file.document_id}
                id={file.document_id}
                name={file.name}
                path={file.path}
                pages={file.pages}
                terms={terms}
                rank={index + 1}
                used={file.used}
              />
            ))}
          </ul>
          {more > 0 ? (
            <button type="button" className="btn" onClick={() => setAll(true)}>
              Show {more} more
            </button>
          ) : null}
          <p className={styles.sourceMeta}>Only files you can open are listed. Open the arrow to see the best pages in a file.</p>
        </section>
      ) : null}
    </div>
  );
}
