// Production QA (5 Nov 2026): an upload call that never answers (a server restart mid-request)
// left the row waiting for ever with no word. A part gives up after 2 minutes idle and a JSON call
// of the upload after its own bound, and each is then sent again, the row saying it waits for the
// server meanwhile. A folder upload's small file (one POST to its item) is given up on and sent
// again the same way. Review of #564: a call with no answer counts as a failing server's, so after
// SERVER_RETRIES more the row fails with Retry instead of waiting for ever. And a `complete` asked
// again after its reply was lost gets the stored file back from the server (T1), never an error.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { canSendAgain } from "@/components/documents/logic";
import { ApiError, uploadDocument, uploadFolderItem } from "@/lib/api";
import { settle } from "./fake-clock";

const SESSION = { id: "s1", part_size: 8 * 1024 * 1024, part_count: 1 };
const DOCUMENT = { id: "d1", display_name: "deed.pdf", status: "received" };

/**
 * A stand-in API. `hangComplete`: how many first `complete`s never answer; `loseComplete`: the
 * first fails as with no connection. `hangPart`: the first part never answers.
 */
function stubApi(options: { hangPart?: boolean; hangComplete?: number; loseComplete?: boolean }) {
  const asked: string[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const path = url.split("?")[0] ?? url;
    asked.push(path);
    const completes = asked.filter((each) => each.endsWith("/complete")).length;
    if (path.endsWith("/complete") && completes <= (options.hangComplete ?? 0)) {
      // A server that never answers: only the browser's abort ends the request.
      return new Promise<Response>((_, reject) =>
        init?.signal?.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError"))),
      );
    }
    if (path.endsWith("/complete") && completes === 1 && options.loseComplete) throw new TypeError("Failed to fetch");
    const body = path.endsWith("/candidates")
      ? { candidates: [] }
      : path.endsWith("/complete")
        ? { document: DOCUMENT, duplicate: false, in_trash: false }
        : SESSION;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  let parts = 0;
  class FakeXhr {
    status = 204;
    responseText = "";
    upload = { onprogress: null };
    onload: (() => void) | null = null;
    onerror = null;
    onabort: (() => void) | null = null;
    withCredentials = false;
    open(method: string) {
      if (method !== "POST") return;
      this.status = 201; // a folder upload's small file: one POST, answered with the upload
      this.responseText = JSON.stringify({ document: DOCUMENT, duplicate: false, in_trash: false });
    }
    setRequestHeader() {}
    getResponseHeader() {
      return null;
    }
    abort() {
      this.onabort?.(); // as a browser: abort() ends the request with its abort event
    }
    send() {
      parts += 1;
      if (parts === 1 && options.hangPart) return; // never answers: only an abort() ends it
      queueMicrotask(() => this.onload?.());
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  return { asked, parts: () => parts };
}

type Outcome = { done: boolean; failed: unknown; said: [boolean, string | undefined][] };

async function upload(folderItem = false): Promise<Outcome> {
  const said: [boolean, string | undefined][] = [];
  let done = false;
  let failed: unknown;
  const file = new File(["%PDF-1.7"], "deed.pdf");
  const onWaiting = (waiting: boolean, why?: "connection" | "server") => said.push([waiting, why]);
  void (
    folderItem
      ? uploadFolderItem(file, "i1", "u1", () => {}, async () => {}, undefined, onWaiting)
      : uploadDocument(file, "u1", () => {}, undefined, undefined, undefined, undefined, undefined, onWaiting)
  ).then(
    () => (done = true),
    (error: unknown) => (failed = error),
  );
  await settle(() => done || failed !== undefined, 8_000, 5_000);
  return { done, failed, said };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const WAITED_ONCE = [
  [true, "server"],
  [false, undefined],
];

describe("an upload call that never answers", () => {
  it("a part that never answers is sent again, and the row shows the waiting state", async () => {
    const api = stubApi({ hangPart: true });
    const { done, said } = await upload();
    expect(done).toBe(true);
    expect(api.parts()).toBe(2);
    expect(said).toEqual(WAITED_ONCE);
  });

  it("a complete that never answers is asked again, and the row shows the waiting state", async () => {
    const api = stubApi({ hangComplete: 1 });
    const { done, said } = await upload();
    expect(done).toBe(true);
    expect(api.asked.filter((path) => path.endsWith("/complete"))).toHaveLength(2);
    expect(said).toEqual(WAITED_ONCE);
  });

  it("a folder upload's small file that never answers is sent again to its item", async () => {
    const api = stubApi({ hangPart: true });
    const { done, said } = await upload(true);
    expect(done).toBe(true);
    expect(api.parts()).toBe(2);
    expect(said).toEqual(WAITED_ONCE);
  });

  it("a complete that never answers time after time fails the row with Retry", async () => {
    const api = stubApi({ hangComplete: 99 });
    const { done, failed, said } = await upload();
    expect(done).toBe(false);
    expect(api.asked.filter((path) => path.endsWith("/complete"))).toHaveLength(5); // SERVER_RETRIES + 1
    expect(failed).toBeInstanceOf(ApiError);
    expect(canSendAgain(failed)).toBe(true);
    expect(said.filter(([waiting]) => waiting)).toEqual(Array(4).fill([true, "server"]));
  });
});

describe("a complete asked again after its reply was lost (T1)", () => {
  it("ends with the stored file, never an error", async () => {
    const api = stubApi({ loseComplete: true });
    const { done } = await upload();
    expect(done).toBe(true);
    expect(api.asked.filter((path) => path.endsWith("/complete"))).toHaveLength(2);
  });
});
