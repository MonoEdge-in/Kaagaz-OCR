"use client";

// "What was read" under a value (a native disclosure, plain React text); the
// Scanned text link is left out for a sheet, whose grid already is the reading.

import Link from "next/link";
import { pageLink } from "@/lib/regions";
import type { ExtractedValue, PageSummary } from "@/lib/types";
import { fieldText, reasonText } from "./labels";
import { CONFIDENCE_NOTE, confidenceText, notSure, routeOrigin, visibleText } from "./scanned";
import styles from "./ScannedText.module.css";

export function WhatWasRead({ value, page }: { value: ExtractedValue; page: PageSummary | undefined }) {
  const pageNo = value.region.page;
  const confidence = confidenceText(value.confidence, routeOrigin(page?.route));
  const scanned =
    page && page.unit !== "cells"
      ? pageLink(value.document_id, pageNo, value.region, { scanned: true })
      : null;
  return (
    <details className={styles.read}>
      <summary>
        What was read
        <span className="visually-hidden">{`: ${fieldText(value.field)}`}</span>
      </summary>
      <dl className={styles.readList}>
        <div>
          <dt>Read as</dt>
          <dd>
            <bdi dir="auto" className="readText">
              {visibleText(value.raw)}
            </bdi>
          </dd>
        </div>
        <div>
          <dt>Second reading</dt>
          <dd>
            {value.second_reading === null ? (
              "None"
            ) : (
              <bdi dir="auto" className="readText">
                {visibleText(value.second_reading)}
              </bdi>
            )}
          </dd>
        </div>
        {confidence && (
          <div>
            <dt>Confidence</dt>
            <dd>
              {confidence}
              {notSure(value.confidence) && <span className={styles.muted}>{CONFIDENCE_NOTE}</span>}
            </dd>
          </div>
        )}
        {value.label === "needs_review" && (
          <div>
            <dt>Why it needs review</dt>
            <dd>{reasonText(value.label_reason)}</dd>
          </div>
        )}
      </dl>
      {scanned && <Link href={scanned}>See page {pageNo}&apos;s scanned text</Link>}
    </details>
  );
}
