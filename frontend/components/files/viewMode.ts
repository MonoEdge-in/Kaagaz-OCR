// List or Grid (File management F5), remembered per person in this browser. Storage can be
// blocked (a private window), so every read and write is in try/catch and List is the default.

export type ViewMode = "list" | "grid";

const key = (userId: string) => `kaagaz.files-view:${userId}`;

export function readViewMode(userId: string): ViewMode {
  try {
    return localStorage.getItem(key(userId)) === "grid" ? "grid" : "list";
  } catch {
    return "list";
  }
}

export function saveViewMode(userId: string, mode: ViewMode): void {
  try {
    localStorage.setItem(key(userId), mode);
  } catch {
    // Storage blocked: the choice lasts for this visit only.
  }
}
