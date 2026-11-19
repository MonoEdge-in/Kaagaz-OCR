"use client";

// PRD 9 F-06 (D4): once a file the person just uploaded is read and looks like a new version, a
// pop-up asks 'This looks like a new version of "X" (82% the same text). Save it as a new version?',
// one row a pair: Yes (the banner's Accept: the next version, and Current), No (kept apart, never
// asked again) and See the differences; Yes to all when there are several. An answered row says what
// was done, with Undo after Yes. Each suggestion is asked here once, also after a reload (kept on
// this browser in localStorage, which may be off: then it asks again, which is safe); closed, the
// Upload tab's row and the document's banner still ask (the fallback).

import Link from "next/link";
import { useState } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import library from "@/components/documents/Library.module.css";
import type { VersionSuggestion } from "@/lib/types";
import { linkErrorText } from "./link";
import { chooseFile, chooseVersion, compareHref } from "./VersionChoice";
import type { Chosen } from "./VersionChoice";
import { VERSION_TEXT, bannerTitle, reasonsText } from "./words";
import styles from "./Versions.module.css";

const askedKey = (id: string) => `kaagaz.version-asked.${id}`;

function askedBefore(id: string): boolean {
  try {
    return window.localStorage.getItem(askedKey(id)) === "1";
  } catch {
    return false;
  }
}

export function VersionPopup({ suggestions }: { suggestions: readonly VersionSuggestion[] }) {
  const [rows, setRows] = useState<VersionSuggestion[]>([]); // the pop-up's, in the order they came
  const [asked, setAsked] = useState<ReadonlySet<string>>(() => new Set()); // shown here once already
  const [answers, setAnswers] = useState<ReadonlyMap<string, Chosen>>(() => new Map());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fresh = suggestions.filter((each) => each.status === "pending" && each.outcome === "suggested" && !asked.has(each.id) && !askedBefore(each.id));
  if (fresh.length > 0) {
    // A new suggestion from the poll joins the pop-up (state set while rendering, React's way).
    setAsked(new Set([...asked, ...fresh.map((each) => each.id)]));
    setRows([...rows, ...fresh]);
  }
  if (rows.length === 0) return null;

  const answered = (id: string, chosen: Chosen) => setAnswers((before) => new Map(before).set(id, chosen));
  async function answer(work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (caught) {
      setError(linkErrorText(caught)); // the server's words, else check the connection
    } finally {
      setBusy(false);
    }
  }
  const open = rows.filter((each) => !answers.has(each.id));
  const close = () => {
    try {
      for (const each of rows) window.localStorage.setItem(askedKey(each.id), "1");
    } catch {
      // not kept: asked again after a reload
    }
    setRows([]);
    setAnswers(new Map());
    setError(null);
  };

  return (
    <ActionDialog title={VERSION_TEXT.popupTitle} onClose={close} canClose={() => !busy} wide={rows.length > 1}>
      <div className={library.dialogForm}>
        <ul className={styles.versions}>
          {rows.map((each) => {
            const chosen = answers.get(each.id);
            return (
              <li key={each.id} className={`${styles.version} ${styles.choiceBlock}`}>
                <p className={styles.bannerTitle}>
                  {bannerTitle(each.candidate.name, reasonsText(each))}
                  {chosen ? "" : ` ${VERSION_TEXT.saveAsVersion}`}
                </p>
                {chosen ? (
                  <p role="status">
                    {chosen.message}{" "}
                    {chosen.undo && (
                      <button
                        type="button"
                        className="btn btn-quiet"
                        disabled={busy}
                        onClick={() =>
                          void answer(async () => {
                            await chosen.undo?.();
                            answered(each.id, { message: VERSION_TEXT.reverted });
                          })
                        }
                      >
                        {VERSION_TEXT.undo}
                      </button>
                    )}
                  </p>
                ) : (
                  <div className={styles.bannerButtons}>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={busy}
                      aria-label={`${VERSION_TEXT.yes}: ${each.document.name}`}
                      onClick={() => void answer(async () => answered(each.id, await chooseVersion(each)))}
                    >
                      {VERSION_TEXT.yes}
                    </button>
                    <button
                      type="button"
                      className="btn"
                      disabled={busy}
                      aria-label={`${VERSION_TEXT.no}: ${each.document.name}`}
                      onClick={() => void answer(async () => answered(each.id, await chooseFile(each)))}
                    >
                      {VERSION_TEXT.no}
                    </button>
                    <Link className="btn btn-quiet" href={compareHref(each.document.id, each.candidate.id)} onClick={close}>
                      {VERSION_TEXT.seeDifferences}
                    </Link>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className={library.formButtons}>
          {open.length > 1 && (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() =>
                void answer(async () => {
                  for (const each of open) answered(each.id, await chooseVersion(each));
                })
              }
            >
              {VERSION_TEXT.yesToAll}
            </button>
          )}
          <button type="button" className="btn" disabled={busy} onClick={close}>
            {VERSION_TEXT.close}
          </button>
        </div>
      </div>
    </ActionDialog>
  );
}
