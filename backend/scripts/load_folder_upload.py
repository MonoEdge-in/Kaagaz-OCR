"""Load test for a whole-folder upload (#484): 50,000 files in 5,100 folders, 100 levels deep.
Not in CI: it runs against a running stack and takes hours.

    cd backend
    LOADTEST_PASSWORD=... PROBE_PASSWORD=... python -m scripts.load_folder_upload \\
        --base http://localhost:8000 --email E --password-env LOADTEST_PASSWORD \\
        --probe-email P --probe-password-env PROBE_PASSWORD

Run it on the laptop stack WITHOUT OPENROUTER_API_KEY, so no paid model call is ever made: every
file is a distinct ~100-byte CSV (nothing is a duplicate, and a CSV is read without a model).
``--email`` is a member of one organisation; ``--probe-email`` a member of a second one, whose
list and one-file upload are timed every 30 s while the first uploads (one organisation's big
upload must not slow another's). Passwords come from the named environment variables, never
the command line. It signs in with ``POST /api/auth/login`` and sends ``X-Requested-With:
kaagaz``, like the browser. Files go 4 at a time, paced at 90% of the server's
``folder_upload.calls_per_minute``, and a 429 or 503 waits its Retry-After, as the browser does.

It prints the plan and registration seconds, files a minute, the file call's p50, p95 and max,
the replies by status (429 included), the probe's list p95 and upload seconds, the deepest
folder's list p95, ``GET /api/folders``' bytes and seconds, the end summary, and PASS or FAIL
for each bar. It is a command line report, so it writes to standard output.
"""

import argparse
import hashlib
import os
import statistics
import sys
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

import httpx2 as httpx

from kaagaz.db.folder_uploads import PlannedFolder

ROOT, BRANCHES, PER_BRANCH, FILES_PER_LEAF, CHAIN, CHAIN_FILES = "A", 50, 99, 10, 99, 500
AT_ONCE, PAGE = 4, 1000
CSRF = {"X-Requested-With": "kaagaz"}


def make_tree() -> tuple[list[PlannedFolder], list[tuple[int, str]]]:
    """Root A; 50 folders under it with 99 each (each of those with 10 files); and a chain of 99
    folders under A, the deepest at level 100, holding 500 files. 5,100 folders and 50,000
    files: (folder index, file name) each. O(n)."""
    folders = [PlannedFolder(ROOT, None)]
    files: list[tuple[int, str]] = []
    for branch in range(BRANCHES):
        folders.append(PlannedFolder(f"B{branch:02d}", 0))
        parent = len(folders) - 1
        for leaf in range(PER_BRANCH):
            folders.append(PlannedFolder(f"L{leaf:02d}", parent))
            files.extend((len(folders) - 1, f"f{n}.csv") for n in range(FILES_PER_LEAF))
    parent = 0
    for level in range(CHAIN):
        folders.append(PlannedFolder(f"D{level + 2}", parent))
        parent = len(folders) - 1
    files.extend((parent, f"deep{n}.csv") for n in range(CHAIN_FILES))
    return folders, files


def depth(folders: list[PlannedFolder]) -> int:
    """The deepest level in the list, the root at 1. O(n)."""
    levels = [1] * len(folders)
    for index, folder in enumerate(folders[1:], start=1):
        levels[index] = levels[folder.parent or 0] + 1
    return max(levels)


def file_bytes(seq: int) -> bytes:
    """A distinct ~100-byte CSV for file ``seq``."""
    return f"seq,value\n{seq},{'x' * 80}\n".encode()


def _paths(folders: list[PlannedFolder]) -> list[str]:
    paths: list[str] = []
    for folder in folders:
        paths.append(folder.name if folder.parent is None else f"{paths[folder.parent]}/{folder.name}")
    return paths


def _sign_in(base: str, email: str, password: str) -> tuple[httpx.Client, str]:
    client = httpx.Client(base_url=base, headers=CSRF, timeout=120)
    client.post("/api/auth/login", json={"email": email, "password": password}).raise_for_status()
    me = client.get("/api/auth/me").json()
    return client, str(me.get("me", me)["user"]["id"])


def _p(values: list[float], q: int) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    return statistics.quantiles(values, n=100)[q - 1]


def _timed(call: Callable[[], httpx.Response]) -> tuple[httpx.Response, float]:
    started = time.perf_counter()
    reply = call()
    return reply, time.perf_counter() - started


class _Pace:
    """At most ``per_minute`` sends a minute across the threads."""

    def __init__(self, per_minute: int) -> None:
        self._gap, self._next, self._lock = 60.0 / max(1, per_minute), time.monotonic(), threading.Lock()

    def wait(self) -> None:
        with self._lock:
            slot = max(self._next, time.monotonic())
            self._next = slot + self._gap
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Folder upload load test (#484)")
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password-env", required=True)
    parser.add_argument("--probe-email", required=True)
    parser.add_argument("--probe-password-env", required=True)
    args = parser.parse_args(argv)
    client, user_id = _sign_in(args.base, args.email, os.environ[args.password_env])
    probe, probe_id = _sign_in(args.base, args.probe_email, os.environ[args.probe_password_env])
    folders, files = make_tree()
    limits = client.get("/api/upload-limits").json()["folder_upload"]

    upload_id = str(uuid.uuid4())
    body = {
        "id": upload_id,
        "user_id": user_id,
        "target": "folder",
        "folders": [{"name": f.name, "parent": f.parent} for f in folders],
        "file_count": len(files),
        "total_bytes": sum(len(file_bytes(seq)) for seq in range(len(files))),
        "skipped_count": 0,
    }
    planned, plan_seconds = _timed(partial(client.post, "/api/folder-uploads", json=body))
    planned.raise_for_status()
    folder_ids: list[str] = planned.json()["folder_ids"]

    paths = _paths(folders)
    items: list[dict[str, Any]] = []
    started = time.perf_counter()
    for start in range(0, len(files), PAGE):
        page = [
            {
                "path_key": hashlib.sha256(f"{paths[folder]}/{name}".encode()).hexdigest(),
                "name": name,
                "folder_id": folder_ids[folder],
                "size": len(file_bytes(start + offset)),
            }
            for offset, (folder, name) in enumerate(files[start : start + PAGE])
        ]
        reply = client.post(
            f"/api/folder-uploads/{upload_id}/items", json={"user_id": user_id, "items": page}
        )
        reply.raise_for_status()
        items.extend(reply.json()["items"])
    register_seconds = time.perf_counter() - started

    statuses: Counter[int] = Counter()
    latencies: list[float] = []
    pace = _Pace(int(limits["calls_per_minute"] * 0.9))
    done = threading.Event()
    probe_lists: list[float] = []
    probe_uploads: list[float] = []

    def send(one: dict[str, Any]) -> None:
        url = f"/api/documents?folder_upload_item_id={one['id']}"
        part = {"file": (one["name"], file_bytes(one["seq"]), "text/csv")}
        while True:
            pace.wait()
            reply, seconds = _timed(partial(client.post, url, data={"user_id": user_id}, files=part))
            statuses[reply.status_code] += 1
            if reply.status_code in (429, 503):
                time.sleep(float(reply.headers.get("Retry-After", "2")))
                continue
            latencies.append(seconds)
            return

    def probing() -> None:
        while not done.wait(30):
            probe_lists.append(_timed(partial(probe.get, "/api/documents"))[1])
            part = {"file": ("probe.csv", f"probe,{uuid.uuid4()}\n".encode(), "text/csv")}
            upload = partial(probe.post, "/api/documents", data={"user_id": probe_id}, files=part)
            probe_uploads.append(_timed(upload)[1])

    threading.Thread(target=probing, daemon=True).start()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=AT_ONCE) as pool:
        list(pool.map(send, [one for one in items if one["state"] == "waiting"]))
    send_minutes = (time.perf_counter() - started) / 60
    done.set()

    deepest = folder_ids[-1]
    deep = [_timed(partial(client.get, f"/api/documents?folder_id={deepest}"))[1] for _ in range(20)]
    tree, tree_seconds = _timed(partial(client.get, "/api/folders"))
    summary = client.get(f"/api/folder-uploads/{upload_id}").json()

    out = sys.stdout  # a command line report, so it writes to stdout rather than the log
    out.write(f"plan: {plan_seconds:.2f} s for {len(folders):,} folders, {len(files):,} files,")
    out.write(f" depth {depth(folders)}\n")
    out.write(f"registration: {register_seconds:.2f} s\n")
    out.write(f"files a minute: {len(latencies) / max(send_minutes, 1e-9):.0f}\n")
    out.write(f"file call: p50 {_p(latencies, 50):.3f} s, p95 {_p(latencies, 95):.3f} s,")
    out.write(f" max {max(latencies or [0]):.3f} s\n")
    out.write(f"replies: {dict(sorted(statuses.items()))}\n")
    out.write(f"probe: list p95 {_p(probe_lists, 95):.3f} s, upload max {max(probe_uploads or [0]):.3f} s\n")
    out.write(f"deepest folder list: p95 {_p(deep, 95) * 1000:.0f} ms\n")
    out.write(f"GET /api/folders: {len(tree.content):,} bytes in {tree_seconds * 1000:.0f} ms\n")
    out.write(f"summary: {summary}\n")
    accounted = summary["stored"] + summary["duplicate"] + summary["failed"]
    bars = {
        "every file accounted for, none waiting": accounted == len(files) and summary["waiting"] == 0,
        "5,100 folders": len(folder_ids) == 5_100,
        "plan under 5 s": plan_seconds < 5,
        "file call p95 under 1 s": _p(latencies, 95) < 1,
        "probe list p95 under 1 s": _p(probe_lists, 95) < 1,
        "probe upload under 5 s": max(probe_uploads or [0]) < 5,
        "deepest list p95 under 500 ms": _p(deep, 95) < 0.5,
        "GET /api/folders under 1 MB and 300 ms": len(tree.content) < 1_000_000 and tree_seconds < 0.3,
    }
    for bar, passed in bars.items():
        out.write(f"{'PASS' if passed else 'FAIL'}  {bar}\n")
    return 0 if all(bars.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
