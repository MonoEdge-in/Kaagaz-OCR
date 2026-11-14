// The folder upload calls (#484): each file goes to its item through the existing POST
// /api/documents (one call up to 8 MiB) or the resumable start, naming only the item, never a
// folder or a batch; a failing server is asked again for the same item; the lists' shapes.

import { afterEach, describe, expect, it, vi } from "vitest";
import { createTurn, getPreviousFolderUpload, listFolderItems, planFolderUpload, registerFolderItems, uploadFolderItem } from "@/lib/api";
import { FakePart, fakeUploadServer } from "./fake-upload-server";

const UPLOADED = { document: { id: "d1" }, duplicate: false, in_trash: false };
const free = async () => undefined;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** XMLHttpRequest stand-in: answers each POST with the next reply in `replies`. */
function fakePosts(replies: { status: number; body: unknown }[]) {
  const sent: FakePart[] = [];
  class Post extends FakePart {
    override send(body: unknown) {
      this.body = body;
      sent.push(this);
      const reply = replies.shift() ?? { status: 201, body: UPLOADED };
      queueMicrotask(() => {
        this.status = reply.status;
        this.responseText = JSON.stringify(reply.body);
        this.onload?.();
      });
    }
  }
  vi.stubGlobal("XMLHttpRequest", Post);
  return sent;
}

describe("sending one file of a folder upload", () => {
  it("is one POST to /api/documents naming the item in the query, never a folder or a batch", async () => {
    const sent = fakePosts([]);
    const file = new File(["%PDF-1"], "bill.pdf");
    const result = await uploadFolderItem(file, "item-1", "u1", () => undefined, free, { read_mode: "dynamic" });
    expect(result.document?.id).toBe("d1");
    expect(sent).toHaveLength(1);
    expect(sent[0]?.method).toBe("POST");
    // In the query: the server's rate limit gives it the folder upload's bucket before reading the body.
    expect(sent[0]?.url).toBe("/api/documents?folder_upload_item_id=item-1");
    const form = sent[0]?.body as FormData;
    expect(form.has("folder_upload_item_id")).toBe(false);
    expect(form.get("user_id")).toBe("u1");
    expect(form.get("read_mode")).toBe("dynamic");
    expect(form.has("folder_id")).toBe(false);
    expect(form.has("batch_id")).toBe(false);
    expect(sent[0]?.headers["X-Requested-With"]).toBe("kaagaz");
  });

  it("asks again for the same item after a 503, then takes the 201", async () => {
    vi.useFakeTimers();
    const sent = fakePosts([{ status: 503, body: { error: { code: "unavailable", message: "Busy." } } }]);
    const waiting: boolean[] = [];
    const done = uploadFolderItem(new File(["x"], "a.pdf"), "item-7", "u1", () => undefined, free, undefined, (on) => waiting.push(on));
    await vi.advanceTimersByTimeAsync(1500);
    await expect(done).resolves.toMatchObject({ duplicate: false });
    expect(sent.map((post) => post.url)).toEqual([
      "/api/documents?folder_upload_item_id=item-7",
      "/api/documents?folder_upload_item_id=item-7",
    ]);
    expect(waiting).toEqual([true, false]);
  });

  it("says a refusal in the server's words and does not ask again", async () => {
    const sent = fakePosts([{ status: 409, body: { error: { code: "folder_gone", message: "The folder was deleted." } } }]);
    await expect(uploadFolderItem(new File(["x"], "a.pdf"), "i", "u1", () => undefined, free)).rejects.toMatchObject({
      status: 409,
      code: "folder_gone",
    });
    expect(sent).toHaveLength(1);
  });

  it("starts a file over 8 MiB on the resumable path, naming only the item", async () => {
    const server = fakeUploadServer();
    const big = new File([new Uint8Array(8 * 1024 * 1024 + 1)], "big.pdf");
    await uploadFolderItem(big, "item-9", "u1", () => undefined, free);
    expect(server.starts[0]).toMatchObject({ folder_upload_item_id: "item-9", file_name: "big.pdf" });
    expect(server.starts[0]).not.toHaveProperty("folder_id");
    expect(server.starts[0]).not.toHaveProperty("batch_id");
  });

  it("takes the resumable start's upload reply for an item already in, sending nothing more", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      const body = url.endsWith("/candidates") ? { candidates: [] } : { ...UPLOADED, item: { id: "item-9", state: "stored" } };
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const parts = fakePosts([]);
    const big = new File([new Uint8Array(8 * 1024 * 1024 + 1)], "big.pdf");
    await expect(uploadFolderItem(big, "item-9", "u1", () => undefined, free)).resolves.toMatchObject({ document: { id: "d1" } });
    expect(calls).toEqual(["/api/documents/upload-sessions/candidates", "/api/documents/upload-sessions"]);
    expect(parts).toHaveLength(0);
  });

  it("waits its turn: a bucket of 2 a minute lets 2 go at once and the third a half minute later", async () => {
    vi.useFakeTimers();
    const turn = createTurn(2);
    const went: number[] = [];
    for (let n = 0; n < 3; n++) void turn().then(() => went.push(Date.now()));
    await vi.advanceTimersByTimeAsync(0);
    expect(went).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(went).toHaveLength(3);
  });
});

describe("the folder upload API's shapes", () => {
  it("plans, registers and lists at the agreed paths", async () => {
    const calls: { url: string; method: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      calls.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const body = {
      id: "f1",
      user_id: "u1",
      target: "folder" as const,
      parent_folder_id: null,
      merge: true,
      folders: [{ name: "A", parent: null }],
      file_count: 1,
      total_bytes: 5,
      skipped_count: 0,
    };
    await planFolderUpload(body);
    await registerFolderItems("f1", "u1", [{ path_key: "k".repeat(64), name: "a.pdf", folder_id: "x", size: 5 }]);
    await listFolderItems("f1", "attention", -1, 500);
    expect(calls).toEqual([
      { url: "/api/folder-uploads", method: "POST", body },
      {
        url: "/api/folder-uploads/f1/items",
        method: "POST",
        body: { user_id: "u1", items: [{ path_key: "k".repeat(64), name: "a.pdf", folder_id: "x", size: 5 }] },
      },
      { url: "/api/folder-uploads/f1/items?view=attention&after=-1&limit=500", method: "GET", body: undefined },
    ]);
  });

  it("asks for this person's last upload of a folder of that name", async () => {
    const urls: string[] = [];
    const previous = { id: "f0", target: "folder", parent_folder_id: "p1", root_folder_id: "r1", created_at: "2026-11-03T10:00:00Z" };
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify({ previous }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    await expect(getPreviousFolderUpload("Bills & Co")).resolves.toEqual({ previous });
    expect(urls).toEqual(["/api/folder-uploads/previous?source_name=Bills+%26+Co"]);
  });
});
