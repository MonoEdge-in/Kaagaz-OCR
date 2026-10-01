// Tabs of one browser share one session cookie. When a tab signs out or signs
// in, it tells the other tabs who is signed in now (null: nobody) on the
// BroadcastChannel "kaagaz-session". A tab that still shows someone else's
// screens then loads a fresh page (lib/session.tsx), so nothing of theirs
// stays in its memory. Only this site's pages can use the channel.
//
// Each tab uses one channel for both, opened on first use: a channel never
// gets its own messages, so a tab never hears itself.

export const SESSION_CHANNEL = "kaagaz-session";

let channel: BroadcastChannel | null = null;

function tabChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  channel ??= new BroadcastChannel(SESSION_CHANNEL);
  return channel;
}

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
