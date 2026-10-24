"use client";

// The clock beside the brand (Tushar, 24 Oct 2026): for the first PEEK_FOR_MS of every
// PEEK_EVERY_MS (at :00 and :30 each minute) a small clock pops in next to "Kaagaz", its hands
// sweep to India time with the time beside it, then it fades away. It runs on the one shared
// tick (lib/tick.ts), which sleeps while the tab is hidden. Decoration only (aria-hidden).

import type { CSSProperties } from "react";
import { useTick } from "@/lib/tick";
import styles from "./ClockPeek.module.css";

export const PEEK_EVERY_MS = 30_000;
export const PEEK_FOR_MS = 4_500;

const ZONE = "Asia/Kolkata";

/** "4:37 pm", India time. */
export function clockText(now: Date): string {
  return new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: ZONE })
    .format(now)
    .toLowerCase();
}

/** Each hand's turn from 12, in degrees, India time. */
export function clockHands(now: Date): { hour: number; minute: number } {
  const parts = new Intl.DateTimeFormat("en-GB", { hour: "numeric", minute: "numeric", hourCycle: "h23", timeZone: ZONE }).formatToParts(now);
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((part) => part.type === "minute")?.value ?? 0);
  return { hour: (hour % 12) * 30 + minute * 0.5, minute: minute * 6 };
}

/** Whether the clock shows at this wall-clock time, in milliseconds since 1970. */
export function peeking(wallMs: number): boolean {
  return wallMs % PEEK_EVERY_MS < PEEK_FOR_MS;
}

export function ClockPeek() {
  const tick = useTick(); // 0 on the server and before the first tick: hidden
  const wall = tick === 0 ? null : performance.timeOrigin + tick;
  const now = wall !== null && peeking(wall) ? new Date(wall) : null;
  const hands = now ? clockHands(now) : { hour: 0, minute: 0 };
  return (
    <span className={styles.clock} aria-hidden="true" data-shown={now ? "" : undefined}>
      <span className={styles.face}>
        <span className={`${styles.hand} ${styles.hour}`} style={{ "--turn": `${hands.hour}deg` } as CSSProperties} />
        <span className={`${styles.hand} ${styles.minute}`} style={{ "--turn": `${hands.minute}deg` } as CSSProperties} />
      </span>
      <span className={styles.time}>{now ? clockText(now) : ""}</span>
    </span>
  );
}
