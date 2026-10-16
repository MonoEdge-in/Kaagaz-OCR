// Repository rules that no single component test would catch.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

const ROOT = join(__dirname, "..");
const SOURCE_DIRS = ["app", "components", "lib"];

function files(dir: string, keep: (path: string) => boolean): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...files(path, keep));
    else if (keep(path)) out.push(path);
  }
  return out;
}

const all = (keep: (path: string) => boolean) =>
  SOURCE_DIRS.flatMap((dir) => files(join(ROOT, dir), keep));

describe("guards", () => {
  it("never renders markup from strings (document text is plain text only)", () => {
    const offenders = all((p) => /\.(ts|tsx)$/.test(p)).filter((p) =>
      /dangerouslySetInnerHTML|\.innerHTML\b|outerHTML|insertAdjacentHTML/.test(readFileSync(p, "utf8")),
    );
    expect(offenders.map((p) => relative(ROOT, p))).toEqual([]);
  });

  it("uses colour tokens: no hex colours in CSS outside app/globals.css", () => {
    const offenders = all((p) => p.endsWith(".css") && !p.endsWith("globals.css")).filter((p) =>
      /#[0-9a-fA-F]{3,8}\b/.test(readFileSync(p, "utf8")),
    );
    expect(offenders.map((p) => relative(ROOT, p))).toEqual([]);
  });

  it("uses SVG icons, not emoji", () => {
    const offenders = all((p) => p.endsWith(".tsx")).filter((p) =>
      /\p{Extended_Pictographic}/u.test(readFileSync(p, "utf8")),
    );
    expect(offenders.map((p) => relative(ROOT, p))).toEqual([]);
  });

  // Critical #5: a client-side move keeps the tab's memory (cached PDFs, the
  // last screens). Signing out, a session ending and signing in are full page
  // loads instead. The one router.replace to a sign-in page left is the first
  // visit with no session, which has nothing in memory yet.
  it("never moves to or from a sign-in page with router.replace once a session existed", () => {
    const moves = all((p) => /\.(ts|tsx)$/.test(p)).flatMap((p) =>
      Array.from(readFileSync(p, "utf8").matchAll(/router\.(?:replace|push)\(([^)]*)\)/g), (m) => ({
        file: relative(ROOT, p).replaceAll("\\", "/"),
        to: m[1] ?? "",
      })),
    );
    const toSignIn = moves.filter((m) =>
      /login|signInPath|endedPath|signedOutPath|pageAfterSessionChange/.test(m.to),
    );
    expect(toSignIn).toEqual([{ file: "lib/session.tsx", to: "signInPath(pathname" }]);
    const fromSignIn = moves.filter((m) => m.file.startsWith("app/login/"));
    expect(fromSignIn).toEqual([]);
    const leaving = [
      "app/login/SignInForm.tsx",
      "app/login/EnrolStep.tsx", // one sign-in page (16 Oct 2026): the admin form went
      "app/signup/SignUpForm.tsx",
      // Changed on purpose (UI audit G6, 16 Oct 2026): signing out moved from AppShell to AccountMenu.
      "components/shell/AppShell.tsx",
      "components/shell/AccountMenu.tsx",
      "components/shell/SetPasswordScreen.tsx",
      "components/admin/AdminView.tsx",
    ];
    for (const file of leaving) expect(readFileSync(join(ROOT, file), "utf8"), file).not.toContain("useRouter");
  });

  // A8a (#35): the documents screen never reloads itself after uploads. A
  // reload wiped the error and duplicate rows and the search; the list asks
  // the server again instead (lib/poll.ts).
  it("never reloads the documents screen", () => {
    const offenders = files(join(ROOT, "components", "documents"), (p) => /\.(ts|tsx)$/.test(p))
      .filter((p) => /location\.reload\(/.test(readFileSync(p, "utf8")))
      .map((p) => relative(ROOT, p));
    expect(offenders).toEqual([]);
  });

  // A8a: the uploads list is not a live region; the screen says each batch once, politely.
  // Changed on purpose (scan states, 16 Oct 2026): the status bar's one line is said politely
  // (brief: aria-live="polite" for progress); the tray's rows, lines and counts still are not.
  it("keeps the uploads list and its counts out of the live regions", () => {
    const source = readFileSync(join(ROOT, "components", "documents", "UploadArea.tsx"), "utf8");
    expect(source.match(/aria-live/g)).toHaveLength(1);
    expect(source).toMatch(/<p className=\{styles\.barLine\} aria-live="polite"/);
  });

  // One way out (lib/sessionChannel.ts), so the first page asked for wins.
  it("leaves for a fresh page only through leavePage", () => {
    const offenders = all((p) => /\.(ts|tsx)$/.test(p))
      .filter((p) => /location\.replace\(/.test(readFileSync(p, "utf8")))
      .map((p) => relative(ROOT, p).replaceAll("\\", "/"));
    expect(offenders).toEqual(["lib/sessionChannel.ts"]);
  });

  // Changed on purpose (UI audit, 16 Oct 2026): A3, Send is a labelled button, dimmed while the box is empty too.
  it("keeps Send focusable while waiting: aria-disabled, never disabled", () => {
    const box = readFileSync(join(ROOT, "components/chat/QuestionBox.tsx"), "utf8");
    const start = box.indexOf("styles.send}`}");
    expect(start).toBeGreaterThan(-1);
    const send = box.slice(start, box.indexOf("</button>", start));
    expect(send).toContain("aria-disabled={pending || empty || undefined}");
    expect(send).not.toMatch(/\sdisabled=/);
  });

  it("reads the chat out through one status line, not the whole log", () => {
    const chat = readFileSync(join(ROOT, "components/chat/ChatScreen.tsx"), "utf8");
    expect(chat).toMatch(/role="log"\s+aria-live="off"/);
    expect(chat.match(/role="status"/g)?.length).toBe(1);
  });

  // The /api rewrite's wait (council 3 Oct 2026): Next's default 30 s cut slow
  // chat answers, and 0 means that default, not "never".
  it("lets the /api proxy wait longer than a slow chat answer", () => {
    const timeout = nextConfig.experimental?.proxyTimeout;
    expect(typeof timeout).toBe("number");
    expect(timeout).toBeGreaterThanOrEqual(150_000);
  });

  it("has one polite region on the review page", () => {
    const queue = readFileSync(join(ROOT, "components/review/ReviewQueue.tsx"), "utf8");
    expect(queue.match(/aria-live=/g)?.length).toBe(1);
  });

  it("keeps a focused element clear of the sticky top bar (WCAG 2.4.11)", () => {
    const css = readFileSync(join(ROOT, "app/globals.css"), "utf8");
    // Changed on purpose (MonoEdge design system, 16 Oct 2026): the header plus 8px, and the
    // header is now a row above <main>, the one scroller, which keeps its own 8px.
    expect(css).toMatch(/scroll-padding-top:\s*calc\(var\(--topbar-space\) \+ var\(--space-2\)\)/);
    expect(readFileSync(join(ROOT, "components/shell/Shell.module.css"), "utf8")).toMatch(
      /\.main \{[^}]*scroll-padding-top:\s*var\(--space-2\);/,
    );
  });

  // PRD 4 #13: the countdown is never announced, and every countdown shares one timer.
  it("counts down without a live region, on the one shared timer", () => {
    const leaf = readFileSync(join(ROOT, "components/documents/TimeLeft.tsx"), "utf8");
    expect(leaf).not.toMatch(/aria-live|role="(status|timer|alert)"|setInterval|setTimeout/);
    for (const file of ["lib/timeLeft.ts", "lib/tick.ts"]) {
      expect(readFileSync(join(ROOT, file), "utf8"), file).not.toMatch(/from "(@\/lib\/|\.\/)api"/);
    }
    const timers = all((p) => /\.(ts|tsx)$/.test(p))
      .filter((p) => /\bsetInterval\(/.test(readFileSync(p, "utf8")))
      .map((p) => relative(ROOT, p).replaceAll("\\", "/"));
    expect(timers.sort()).toEqual(["components/chat/hooks.ts", "lib/tick.ts"]);
  });

  // Critical #5: a module-level Map is a cache that outlives the screen, and
  // grows without end. The viewer keeps PDFs in its LruCache instead.
  it("keeps no module-level Map in components", () => {
    // documentCache.ts is deleted by the citation-page change (critical #5, part 2).
    const offenders = files(join(ROOT, "components"), (p) => /\.(ts|tsx)$/.test(p))
      .filter((p) => !p.endsWith("documentCache.ts"))
      .filter((p) => /^(export )?(const|let|var) \w+(: [^=]+)? = new Map\b/m.test(readFileSync(p, "utf8")));
    expect(offenders.map((p) => relative(ROOT, p).replaceAll("\\", "/"))).toEqual([]);
  });
});
