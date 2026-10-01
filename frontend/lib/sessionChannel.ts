// Tabs of one browser share one session cookie. When a tab signs out or signs
// in, it tells the other tabs who is signed in now (null: nobody) on the
// BroadcastChannel "kaagaz-session". A tab that still shows someone else's
// screens then loads a fresh page (lib/session.tsx), so nothing of theirs
// stays in its memory. Only this site's pages can use the channel.

export const SESSION_CHANNEL = "kaagaz-session";

/** Tell the other tabs who is signed in now: a user id, or null after signing out. */
export function announceSession(userId: string | null): void {
  if (typeof BroadcastChannel === "undefined") return;
  const channel = new BroadcastChannel(SESSION_CHANNEL);
  channel.postMessage({ userId });
  channel.close();
}

/** Calls onChange with who another tab says is signed in now. Returns a stop function. */
export function watchSession(onChange: (userId: string | null) => void): () => void {
  if (typeof BroadcastChannel === "undefined") return () => undefined;
  const channel = new BroadcastChannel(SESSION_CHANNEL);
  channel.onmessage = (event: MessageEvent<unknown>) => {
    const data = event.data;
    if (typeof data !== "object" || data === null || !("userId" in data)) return;
    const { userId } = data;
    if (userId === null || typeof userId === "string") onChange(userId);
  };
  return () => channel.close();
}
