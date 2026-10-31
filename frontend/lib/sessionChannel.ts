// Tabs of one browser share one session cookie. When a tab signs out or signs
// in, it tells the other tabs who is signed in now (null: nobody) on the
// BroadcastChannel "kaagaz-session". A tab that still shows someone else's
// screens then loads a fresh page (lib/session.tsx), so nothing of theirs
// stays in its memory. Only this site's pages can use the channel.
//
// Each tab uses one channel for both, opened on first use: a channel never
// gets its own messages, so a tab never hears itself.
//
// leavePage is the one way a tab leaves for a fresh page (signing in or
// out, a session ending, another tab's change). The first page asked for
// wins, so a late reply saying the session has ended cannot send a tab that
// is signing out somewhere else.

export const SESSION_CHANNEL = "kaagaz-session";

let channel: BroadcastChannel | null = null;
let leaving = false;

function tabChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  channel ??= new BroadcastChannel(SESSION_CHANNEL);
  return channel;
}

/** Load a fresh page, so nothing of whoever was signed in stays in memory. Only once. */
export function leavePage(to: string): void {
  if (leaving) return;
  leaving = true;
  window.location.replace(to);
}

/** The tab is leaving on purpose (leavePage): a "leave this page?" prompt must not stop it (issue 468). */
export const leavingPage = (): boolean => leaving;

/** Tell the other tabs who is signed in now: a user id, or null after signing out. */
export function announceSession(userId: string | null): void {
  tabChannel()?.postMessage({ userId });
}

/** Calls onChange with who another tab says is signed in now. Returns a stop function. */
export function watchSession(onChange: (userId: string | null) => void): () => void {
  const tab = tabChannel();
  if (!tab) return () => undefined;
  const listener = (event: MessageEvent<unknown>) => {
    const data = event.data;
    if (typeof data !== "object" || data === null || !("userId" in data)) return;
    const { userId } = data;
    if (userId === null || typeof userId === "string") onChange(userId);
  };
  tab.addEventListener("message", listener);
  return () => tab.removeEventListener("message", listener);
}
