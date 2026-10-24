# Running Kaagaz on a laptop

Two ways: everything in Docker, or the stand-ins in Docker and the API,
worker and web app run straight on the laptop. Every key and password in
`.env.example`, `docker-compose.yml` and `infra/` is a **laptop-only
placeholder**, never a real secret.

## What runs where

| Service | Port | What it is | On the EC2 server |
|---|---|---|---|
| `frontend` | 3000 | Next.js web app (open this) | the same image, behind Caddy |
| `api` | 8000 | FastAPI, `/api/*` | the same image, behind Caddy |
| `worker` | none | `python -m kaagaz.worker_main`, one job at a time | the same image |
| `migrate` | none | one-shot: `alembic upgrade head`, then makes the storage bucket | on every deploy |
| `postgres` | 5432 | PostgreSQL 17 with pgvector | the same container (no RDS in this phase) |
| `seaweedfs` | 8333 | file storage, S3 API | the same container (no S3 in this phase) |
| `elasticmq` | 9324 | job queue `kaagaz-jobs` and its failure bin `kaagaz-jobs-failed`, SQS API; messages kept in the volume `elasticmq-data` | the same container (no SQS in this phase) |
| `clamav` | 3310 | clamd virus scanner | the same container |
| `caddy` | none | HTTPS in front of `frontend` and `api` (`docker-compose.prod.yml` only) | 80 and 443, the only open ports |

## 1. Settings

```
cp .env.example .env                 # bash
Copy-Item .env.example .env          # PowerShell
```

Then set `SESSION_SECRET` and `TOTP_ENCRYPTION_KEY` in `.env`, each to at
least 32 random characters and different from each other. Both placeholders
are too short on purpose, so nothing starts until you do. Run this twice:

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`MODEL_ENABLED=false` (the default) runs everything with no AI. The Gemini
reader, chat and embeddings use the configured `OCR_MODEL`, `MODEL_CHAT` and
`MODEL_EMBED` through OpenRouter. They require Vrushit's third-party approval and an
`OPENROUTER_API_KEY` in the server's `.env`; do not use a free-tier key.
With `MODEL_ENABLED=true`, `MODEL_CHAT` and `MODEL_EMBED` must be `google/gemini-`
models, or nothing starts.
With the model on, the worker reads each document's card with `MODEL_CHAT`
(PRD 2): a reply may take up to `MODEL_CARD_MAX_TOKENS` tokens, in a window of
`MODEL_CONTEXT_TOKENS` (PRD 5 M1), and one card call times out after
`MODEL_EXTRACT_TIMEOUT_SECONDS` (twice that plus 75 must fit
`QUEUE_LEASE_SECONDS`, or nothing starts).

An older `.env` needs these added from `.env.example` (a missing setting stops
the start with its name): `TOTP_ENCRYPTION_KEY`, `LOGIN_MAX_FAILURES`,
`LOGIN_LOCK_MINUTES`, `ADMIN_SESSION_MINUTES`, `MAX_IMAGE_MB`, `MAX_SHEET_MB`,
`TRASH_DAYS`, `JOB_MAX_ATTEMPTS`, `OCR_TIMEOUT_SECONDS` and
`OCR_DPI`, the model budgets `MODEL_CARD_MAX_TOKENS`,
`MODEL_ANSWER_MAX_TOKENS`, `MODEL_EXTRACT_TIMEOUT_SECONDS` and
`CARD_MAX_CHARS`,
the invoice windows `EXTRACT_WINDOW_CHARS`, `EXTRACT_WINDOW_OVERLAP` and
`EXTRACT_MAX_WINDOWS`,
plus the new `MAX_PAGES`, `MAX_IMAGE_PIXELS` and `QUEUE_LEASE_SECONDS` values. Recreate the queue too (`docker compose up -d
--force-recreate elasticmq`): its failure-bin limit is now 10.

PRD 3 adds four required settings, each checked at start-up by name:
`SEARCH_EF` (100, 1 to 1000), how many candidates the meaning index walk keeps
(`hnsw.ef_search`); `SEARCH_MAX_DOCUMENTS` (20, 1 to 100), the most documents
one search inside documents returns; `ANSWER_NEIGHBOURS` (1, 0 to 3), the
pieces on each side of an evidence piece also given to the draft; and
`CHAT_HISTORY_TURNS` (3, 0 to 10), the question and answer pairs a follow-up
is rewritten with (0: no rewrite).

Two optional settings (council 4 Oct 2026): `ANSWER_TRANSLATE_QUESTION` (false when
unset) also searches an English or Romanised question in the script of its scans, with
one model call that times out after `MODEL_TRANSLATE_TIMEOUT_SECONDS` (20, PROVISIONAL).
One more (council 5 Oct 2026, PROVISIONAL): `OCR_DPI_RULE` (`fixed` when unset) draws each
scan page at `OCR_DPI`; `text_height` draws it at the DPI its text needs, `OCR_DPI` at most;
`text_height_no_scan_cap` is the same without the scan cap, for the accuracy test.

PRD 5 context and memory adds six optional settings (council 9 Oct 2026), each the PRD's
value when unset and checked at start-up by name: `MODEL_CONTEXT_TOKENS` (200000, at least
1), the chat model's context window, which every budget uses (the card, the invoice
windows, the answers, the rewrite and the map); `ANSWER_WHOLE_DOC_TOKENS` (120000, 0 turns
it off), the size up to which a chat's documents are given whole, which must leave room in
`MODEL_CONTEXT_TOKENS` for two answer replies and 1100 more; `TOPIC_MAX_PER_DOC` (40, at
least 1), the topics kept for a document; `TOPIC_EXPAND_MAX_PIECES` (12, 0 or more), the
pieces a topic adds per document, and a matching section at most; `QUERY_ASPECTS_MAX` (6, 0 or more), the aspects a question
is also searched as; and `CHAT_SUMMARY_EVERY` (6, at least 1), the turns between chat
summaries.

**Upgrading an install to PRD 3.** Migration 0006 is offline: it locks
`pieces` and `messages` while it rewrites the vectors and builds the indexes.

1. `docker compose stop api worker`.
2. Add `SEARCH_EF=100`, `SEARCH_MAX_DOCUMENTS=20`, `ANSWER_NEIGHBOURS=1` and
   `CHAT_HISTORY_TURNS=3` to `.env`.
3. `alembic upgrade head` (in Docker, `docker compose up --build migrate`).
4. Start the api and worker again (`docker compose up -d api worker`).
5. With the model on, run `python -m kaagaz.cli reembed` once (in Docker,
   `docker compose exec api python -m kaagaz.cli reembed`). Until it ends,
   meaning search sees only the documents it has done; words and typos find
   everything. It can be stopped and run again, and it stops before changing
   anything if the model doesn't answer.

## 2a. Everything in Docker

```
docker compose up --build
```

Open http://localhost:3000.

* The first start of `clamav` can take several minutes while it updates its
  signatures. The worker waits for it; the web app and API work meanwhile.
* The frontend image bakes in `BACKEND_URL=http://api:8000` when it is built
  (Next.js writes the `/api` rewrite into the build).
* Scans are read by the `OCR_ENGINE` in `.env`: `rapidocr` on a laptop with no
  key, or `gemini` with `OPENROUTER_API_KEY` set (section 1).
* Stop with `docker compose stop` (the worker finishes the job in hand).
  `docker compose down -v` also deletes the data volumes.

## 2b. No Docker for the app

Start only the stand-ins in Docker:

```
docker compose up postgres seaweedfs elasticmq clamav
```

No Docker at all: point `DATABASE_URL`, `STORAGE_*` and `QUEUE_*` in `.env`
at your own PostgreSQL 16+ (with the `vector` and `pg_trgm` extensions),
S3-compatible store and SQS-compatible queue. Without ClamAV, set
`SCANNER=eicar_dev`: **laptop only**, it finds the EICAR test file and
nothing else, so never use it with real files.

Then, from `backend/` with its virtual environment (`backend/.venv`), with
the variables from `.env` loaded into the shell:

```
pip install -e ".[dev,ocr]"            # once
alembic upgrade head
uvicorn kaagaz.api.main:app --port 8000
python -m kaagaz.worker_main          # in a second terminal
```

With `OCR_ENGINE=rapidocr`, Marathi and Hindi scans also need RapidOCR's
Devanagari model (8 MB). The worker never downloads it (the Docker image has
it), so fetch it once into the package's models folder:

```
python -c "from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR; RapidOCR(params={'Rec.lang_type': LangRec.DEVANAGARI, 'Rec.ocr_version': OCRVersion.PPOCRV5, 'Rec.model_type': ModelType.MOBILE})"
```

Without it, those pages stay as the default model read them, and the log says
`ocr_model_missing`.

The worker makes the storage bucket on its first start if it is missing.
Ctrl+C stops it after the job in hand. At most once an hour it also deletes
for good what has been in Trash longer than `TRASH_DAYS` days (30). With the
model on, every 10 minutes it gives up to 64 pieces their missing meaning vectors.

And the web app, from `frontend/`:

```
npm ci                                 # once, Node 20 or later
npm run dev                            # http://localhost:3000
```

It forwards `/api/*` to `BACKEND_URL` (default `http://localhost:8000`).

## 2c. On the EC2 server

One t3.xlarge (4 vCPU, 16 GB, Unlimited CPU credits; pending Vrushit)
runs `docker-compose.yml` with `docker-compose.prod.yml` laid over it
(Production on EC2, below). Laptops never use the second file. Scans are read
by Gemini and the models answer through OpenRouter, so nothing on the server
needs more than its CPU; RapidOCR runs in the worker as the checker and the
fallback. The move from the earlier g4dn.xlarge, step by step:
docs/runbooks/prd5-cpu-server-switch.md.

**The host, once.**

* Docker Compose 2.24.4 or newer (`docker compose version`;
  `docker-compose.prod.yml` uses `!reset`).
* At least 20 GB free on `/` for a build: below that, `deploy.sh` prunes the
  build cache first.
* A 4 to 8 GB swapfile: the server has 16 GB of memory for everything.
  `sudo fallocate -l 8G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`,
  and a `/swapfile none swap sw 0 0` line in `/etc/fstab`.
* The security group lets in 443 and 80, nothing else: no SSH, use Session
  Manager (EC2 PRD D8). With `docker-compose.prod.yml` only Caddy publishes
  ports (80 and 443, Production on EC2 below). Without it, `docker-compose.yml`
  still publishes 3000, 8000, 5432, 8333, 9324 and 3310 on the host, and
  Docker's ports go around the host's own firewall, so the security group is
  what keeps them closed.

**Check** (the done-when list; each is NOT RUN until done on the server, with
`dc` as in Production on EC2, below):

1. `dc run --rm worker python -m kaagaz.cli verify-models`: every model and
   RapidOCR line says PASS.
2. Seconds per page, inside the worker container (it has the settings; the
   file must be in the container):

   ```
   docker compose cp ./made-up-marathi-scan.png worker:/tmp/scan.png
   docker compose exec worker python -m kaagaz.cli measure --file /tmp/scan.png
   ```

   Write the numbers into docs/architecture.md, Decisions (the t3.xlarge
   row). A real document's steps, upload to Ready and the indexing after it,
   for the last 5 documents read: `dc exec -T worker python -m kaagaz.cli scan-times --last 5`.

**RapidOCR, the fallback.** If Gemini misbehaves, set `OCR_ENGINE=rapidocr` in
`.env` and restart both: `dc up -d api worker`. Change it back the same way.

### Production on EC2

The production server (EC2 Deployment PRD, approved by Vrushit, 4 Oct 2026):
one EC2 server in Mumbai (`ap-south-1`, section 2c) runs everything above in Docker
Compose, on one domain over HTTPS. It deploys only the `production` branch.
**NOT RUN yet:** each go-live check below is NOT RUN until it has run there.

Below, `dc` is the two files, run in `/home/ubuntu/kaagaz`:

```
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml'
```

`docker-compose.prod.yml` adds `caddy` (`caddy:2` pinned by digest, Caddy
2.11.6; `infra/caddy/Caddyfile`), the only service that publishes ports: 80
and 443. Caddy gets and renews the Let's Encrypt certificate for
`KAAGAZ_DOMAIN`, redirects HTTP to HTTPS, sends `/api/*` to `api:8000` and the
rest to `frontend:3000`, refuses a body over 60 MB, and logs one JSON line per
request. Every other port is cleared, every long-running service restarts after
a crash or a reboot, and every log is rotated (5 files of 20 MB). The job queue
keeps its messages in the volume `elasticmq-data`, so a job survives a restart;
one that was being read comes back after its lease (`QUEUE_LEASE_SECONDS`,
1,200 s).

**Secrets, on the server only.** Two files, both mode 600, never in git:
`/home/ubuntu/kaagaz/.env` and `/home/ubuntu/kaagaz-secrets/s3.json` (the shape
of `infra/seaweedfs/s3.json`, with new keys). Start `.env` from `.env.example`:
every setting is required, so a missing one (`MODEL_ANSWER_MAX_TOKENS`, for
example) stops the start with its name. Then set these (names and rules only;
make random values with `openssl rand -base64 48`, or `openssl rand -hex 24`
for passwords):

| Name | Value or rule |
|---|---|
| `SESSION_SECRET` | Random. Changing it signs everyone out |
| `TOTP_ENCRYPTION_KEY` | Random. If it is lost, every admin's 2FA must be reset |
| `POSTGRES_PASSWORD`, and the same password in `DATABASE_URL` | Random hex. Postgres takes it only when its volume is first made |
| `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY` | Random. Must equal the keys in `s3.json` |
| `COOKIE_SECURE=true`, `MODEL_ENABLED=true`, `OCR_ENGINE=gemini`, `OCR_DPI=300`, `OCR_TIMEOUT_SECONDS=120`, and `OPENROUTER_API_KEY` | As shown; the key is the OpenRouter key with its own credit limit (docs/architecture.md, Decisions) |
| `KAAGAZ_DOMAIN`, `ACME_EMAIL`, `SEAWEEDFS_S3_CONFIG=/home/ubuntu/kaagaz-secrets/s3.json` | Read by `docker-compose.prod.yml`, which stops with the name of any that is missing |

Keep an offline copy of `.env` and `s3.json` in a password manager. The EBS
snapshots hold the secrets too, so never share or copy a snapshot out. The Wake
Lambda's `INSTANCE_ID` and `WAKE_TOKEN` live only in the Lambda console.

**The server, once.** Through Session Manager, as `ubuntu` (`sudo -iu ubuntu`):

1. The host checks of "The host, once" above: `docker compose version` 2.24.4
   or newer, and `swapon --show` shows the 8 GB swapfile.
2. The code, with the read-only deploy key `~/.ssh/kaagaz_deploy` (the deploy
   unit sets the same `GIT_SSH_COMMAND`):
   `GIT_SSH_COMMAND="ssh -i ~/.ssh/kaagaz_deploy -o IdentitiesOnly=yes" git clone git@github.com:MonoEdge-in/Kaagaz-OCR.git ~/kaagaz`,
   then `git checkout production` inside it.
3. The two secret files, as above.
4. The units. The first line keeps the idle stop off until WP-C (the queue
   keeps its messages) has been checked on the server: a message sent before
   `dc restart elasticmq` is received after it. Then delete the file.

   ```
   sudo mkdir -p /etc/kaagaz && sudo touch /etc/kaagaz/no-idle-stop
   sudo cp ~/kaagaz/infra/ec2/*.service ~/kaagaz/infra/ec2/*.timer /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now kaagaz-deploy.timer kaagaz-pgdump.timer kaagaz-idle.timer
   ```

5. The first deploy (several minutes):
   `sudo systemctl start --no-block kaagaz-deploy.service && journalctl -u kaagaz-deploy -f`.
6. The models, once:
   `dc run --rm worker python -m kaagaz.cli verify-models` says PASS on every line.
7. The first customer and admin:
   `dc exec api python -m kaagaz.cli create-customer --name "<Company>" --admin-email <email> --admin-name "<Name>"`
   (it asks for the admin's password at a hidden prompt). The admin signs in at
   `https://<DOMAIN>/login` and sets up two-step sign-in there (every admin must have it).

**What runs by itself.** `kaagaz-deploy.timer` runs `infra/ec2/deploy.sh` 2
minutes after boot and every 5 minutes: when `origin/production` has a new
commit it rebuilds and starts everything (migrations included; 1 to 2 minutes
of downtime), otherwise it starts anything that is down. Each run logs one line,
the commit and `deployed`, `unchanged` or `failed`
(`journalctl -u kaagaz-deploy -n 50`); a failed build is tried again on the
next run. After a good deploy it removes build cache unused for 7 days
(`docker builder prune --filter until=168h`, never volumes), and before a build
with under 20 GB free on `/` it logs `low disk: ...` and does the same first;
`docker system df` shows what is left. `kaagaz-pgdump.timer` writes `~/backups/kaagaz-YYYYMMDD.dump` (mode
600, the last 7 kept) at 20:00 UTC (01:30 IST), or at the next boot if the
server was stopped then. Data Lifecycle Manager takes a daily EBS snapshot at
20:30 UTC (02:00 IST), keeping 7. Restore from a snapshot (the whole disk) or
a dump (section 6). Logs: `dc logs --since 1h <service>`.

**Releases** (EC2 PRD section 6).

* `production` started at `7c6b72d` (#97). It changes only through pull
  requests whose CI (`ci.yml`, `pr.yml`) passes; no force-push, and only
  Vrushit may bypass.
* Deployment PRs branch from `production` and are squash-merged into it, with
  the usual title (`infra(s2): ...`).
* After each merge into `production`, open a PR from `production` into `main`
  and merge it as a merge commit, not a squash, so `main` always contains
  `production`.
* Releasing app work: once a `main` commit has fully green CI, fast-forward
  `production` to it with `git push origin <sha>:production`. Never release a
  commit with failing CI. The server deploys it within 5 minutes.

**Stop when idle, Wake and the weekday schedule.** `kaagaz-idle.timer` runs
`infra/ec2/idle-stop.sh` as root every minute, from 20 minutes after boot. It
powers off (the instance's shutdown behaviour is Stop) only when the server has
been up 20 minutes, Caddy logged no signed-in request to `/api/` (one with a
Cookie header, other than `/api/health`) in the last 15 minutes, both job queues
(`kaagaz-jobs` and `kaagaz-jobs-express`) have nothing waiting or being read, no deploy holds `/tmp/kaagaz-deploy.lock`,
and `/etc/kaagaz/no-idle-stop` does not exist. A check that fails to run keeps
it on. Each run logs why (`journalctl -u kaagaz-idle -n 5`), and
`sudo ~/kaagaz/infra/ec2/idle-stop.sh --dry-run` prints the decision without
stopping. For maintenance, `sudo touch /etc/kaagaz/no-idle-stop`, and delete it
afterwards. Two things start the server:

* The weekday schedule: EventBridge Scheduler, `cron(30 9 ? * MON-FRI *)`, time
  zone `Asia/Kolkata`, target EC2 StartInstances with this instance id.
* The Wake link: the Lambda `kaagaz-wake` (Python 3.12, the code of
  `infra/aws/wake/handler.py` on `production`) behind a Function URL with auth
  type NONE; its token protects it. Users bookmark
  `https://<function-url>/?key=<WAKE_TOKEN>`. A wrong or missing key gets a
  404 and no AWS call. Its role allows only `ec2:StartInstances` on this
  instance, `ec2:DescribeInstances` and CloudWatch Logs. After a start the site
  opens in about 3 minutes.

**Go-live checks** (EC2 PRD section 10). Report each one with its command and
its output; until then it is NOT RUN.

* **10.1** `https://<DOMAIN>` has a valid certificate, and `http://` redirects
  to it.
* **10.2** From outside AWS, only ports 80 and 443 answer. Ports 3000, 8000,
  5432, 8333, 9324 and 3310 all refuse.
* **10.3** `dc ps -a`: every service is running or healthy, and `migrate` shows
  exited (0).
* **10.4** `dc run --rm worker python -m kaagaz.cli verify-models`: every line
  says PASS.
* **10.5** While the exit batch of made-up documents is read, CloudWatch shows
  the instance's CPUUtilization and CPUSurplusCreditsCharged. Measure seconds per
  page with `cli measure` (2c, Check, step 2) and record all three in
  docs/architecture.md, Decisions (the t3.xlarge row).
* **10.6** A made-up Marathi scan reaches Ready, its pages show the route `ocr`
  and "Read by Gemini", its text is Devanagari, and searching its Latin spelling
  finds it.
* **10.7** A made-up 45 MB PDF uploads, and a 60 MB one is refused with the
  size message. A chat answer cites the page with the spot highlighted, and an
  unanswerable question says not found.
* **10.8** After `sudo reboot`, the site is back within about 3 minutes and
  documents are read again. A document queued before the reboot still
  finishes (WP-C).
* **10.9** With nobody signed in and the queue empty, the instance is Stopped
  about 15 minutes later. While a document is being read, it stays on. The Wake
  link starts it, and the site works afterwards with all data intact. The
  schedule starts it on a weekday at 09:30 IST.
* **10.10** The next day, one snapshot exists and `~/backups` has a dump. Once
  in week 1: make a volume from that snapshot, attach it, mount it read-only,
  run `pg_restore --list` on its newest dump, then detach and delete the
  volume.

## 3. Demo customers

With the database migrated, in Docker:

```
docker compose exec -e KAAGAZ_DEMO_PASSWORD='<a password of 12+ characters>' api python -m kaagaz.cli seed-demo
```

Or from `backend/` with the variables from `.env` loaded:

```
KAAGAZ_DEMO_PASSWORD='<a password of 12+ characters>' python -m kaagaz.cli seed-demo
```

It makes two customers, "Sharma Traders" and "Mehta Textiles", each with an
admin, a reviewer and a member, and prints their e-mails. The password is
never a command-line flag.

## 4. Tests

Backend, from `backend/` (`.venv/Scripts/` on Windows, `.venv/bin/` elsewhere):

```
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/mypy.exe .
.venv/Scripts/python.exe -m pytest -m "not db"       # no database needed
.venv/Scripts/python.exe -m pytest -m isolation      # customer separation; most need DATABASE_URL
```

Tests that need PostgreSQL carry the `db` marker and read `DATABASE_URL`. Point
it at a separate, throwaway database (for example `kaagaz_test`), never the
one with your data, then run everything:

```
.venv/Scripts/python.exe -m pytest
```

The adapter tests (`tests/test_adapter_*.py`) need no services: S3 and SQS
are stubbed with `botocore.stub.Stubber`, and clamd is a fake server on
127.0.0.1.

Frontend, from `frontend/`:

```
npx tsc --noEmit
npx eslint .
npx vitest run
```

## 5. Features, in order, and how to validate each

Every feature the product has, in the order a document and a user meet them. Each one says what to do, what you should see, and which automated tests cover it. Run the stack first (step 2a).

**Samples.** Use made-up documents only, and keep them in `samples/`, which git ignores. The checks below make most of what they need themselves.

**Shortcuts** for a bash shell (Git Bash or WSL), run once from the repository root:

```bash
docker compose exec -T -e KAAGAZ_DEMO_PASSWORD=demo-pass-2026 api python -m kaagaz.cli seed-demo
API=http://localhost:8000/api; H='X-Requested-With: kaagaz'; J='Content-Type: application/json'
login() { curl -s -c "$1.jar" -H "$H" -H "$J" -d "{\"email\":\"$1\",\"password\":\"demo-pass-2026\"${2:+,\"code\":\"$2\"}}" $API/auth/login; echo; }
up() { curl -s -b admin@sharma-traders.example.jar -H "$H" -F "file=@$1" -w ' HTTP %{http_code}\n' $API/documents; }
SQL() { docker compose exec -T postgres psql -U kaagaz -d kaagaz -c "$1"; }
printf 'Party Name,Profit,Date\nVerma Traders,50000,15/08/2026\nGupta Traders,72000,20/08/2026\nVerma Traders,30000,25/08/2026\n' > p.csv
requeue() { docker compose exec -T worker python -c 'import sys; from kaagaz.settings import Settings; from kaagaz.adapters.queue import SqsQueue; from kaagaz.ingestion.ports import Job; SqsQueue(Settings.from_env()).enqueue(Job(sys.argv[1], sys.argv[2]))' "$1" "$2"; }
login admin@sharma-traders.example
```

The demo users are `admin@`, `reviewer@` and `member@` on `sharma-traders.example`, and the same three on `mehta-textiles.example`, all with the password above. Each `login` saves a cookie jar named after the e-mail. Admins must have two-step sign-in (0031): sign each demo admin in once at `http://localhost:3000/login` to set it up with an authenticator app, then `login admin@sharma-traders.example <6-digit code>`. Every POST, PATCH and DELETE needs the `X-Requested-With: kaagaz` header, or the API answers `403 csrf`. `SQL` runs as the table owner, so it sees every customer's rows; the app only ever sees one customer's.

### Signing in and staying safe

**1. Health check**
- Check: `curl -i $API/health`, then `docker compose stop elasticmq` and run it again.
- Expect: `200 {"status":"ok","database":"ok","storage":"ok","queue":"ok"}`; with the queue stopped, `503` with `"status":"degraded","queue":"error"`.
- Tests: `test_api_health.py`.

**2. Sign-in, who am I, sign-out**
- Check (API): `login admin@sharma-traders.example`, then `curl -b admin@sharma-traders.example.jar $API/auth/me`, then the same `login` with a wrong password.
- Expect: `200` with `{"user":{id,email,display_name,role},"customer":{id,name}}` and a `kaagaz_session` cookie (HttpOnly, 12 hours). A wrong password gives `401 bad_credentials`, the same reply for every kind of bad sign-in. Sign-out (`POST $API/auth/logout`) gives `204`; after it, `/auth/me` gives `401 not_signed_in`.
- Check (screen): open http://localhost:3000; it goes to /login. Submit empty, then type `admin` without a domain, then a wrong password.
- Expect: "Enter your e-mail address.", then "Enter an e-mail address with an @...", then the banner "The e-mail or password is not right." with your typing kept. **Show** / **Hide** reveals the password; it is hidden again on submit and when you leave the tab. Success opens /documents.
- Tests: `test_api_auth.py`; `frontend/tests/login-sign-in.test.ts`, `ui-password-field.test.ts`.

**3. Sessions, roles, CSRF and rate limit**
- Check: as admin, disable the member (feature 23), then use the member's jar on `/auth/me`. As the member, open `$API/review` and `$API/admin/users`. Send `POST $API/auth/logout` without the `X-Requested-With` header. Send 130 quick requests: `for i in $(seq 130); do curl -s -o /dev/null -w '%{http_code}\n' $API/health; done | sort | uniq -c`.
- Expect: the disabled member gets `401` at once. Members get `403 forbidden` on review and admin. No header gives `403 csrf`. About 120 replies of `200`, then `429 rate_limited` with `Retry-After: 60`. Every reply has `X-Request-ID`, `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`.
- Tests: `test_api_auth.py` (`test_roles_and_the_admin_session_gate_the_routes`, `test_post_without_csrf_header_is_refused`, `test_rate_limit_gives_429_with_the_error_shape`, `test_disabling_a_user_ends_their_session_on_the_next_request`).

**4. App shell, navigation by role, theme**
- Check (screen): sign in as each role; narrow the window below 1024 px; pick Dark in the Theme menu and reload.
- Expect: a member sees Documents and Ask; a reviewer adds Review; an admin adds Review and Admin. Below 1024 px a menu drawer opens and closes with Esc or a link. Dark stays after a reload with no flash (cookie `kaagaz_theme=dark`). A member typing /admin sees "Admin is for admins".
- Tests: `frontend/tests/shell-nav.test.ts`, `ui-drawer.test.ts`, `theme.test.ts`.

### From upload to stored text

**5. Upload**
- Check: `up p.csv`, then `SQL "SELECT id,status,detected_type,size_bytes,display_name FROM documents ORDER BY created_at DESC LIMIT 1"`.
- Expect: `HTTP 201` with `"duplicate": false`; the row has type `text`, the file's size and name `p.csv`, and moves from `received` to `clean` to `ready` within a minute. With `docker compose stop elasticmq`, an upload fails and adds no row (the upload is undone).
- Check (screen): drag p.csv onto Documents.
- Expect: a progress bar, then "Uploaded" with an Open link.
- Tests: `test_upload_service.py`, `test_api_documents.py` (`test_upload_stores_queues_and_returns_the_summary`); `frontend/tests/shell-documents.test.ts`.

**6. File types and upload limits**
- Check: `: > e.csv; up e.csv`; `printf 'MZ\x90\x00' > a.exe; up a.exe`; any Word file `up letter.docx`; `head -c 52500000 /dev/zero > big.bin; up big.bin`.
- Expect: empty file `400 empty_file`; unknown type `400 unsupported_type`; a .docx is a zip, so it is accepted (`201`) and ends `needs_reader`; the big file `413 too_large`. The type always comes from the first bytes, never the name. An admin's lower limit (feature 24) also applies. Each type has its own limit (`MAX_UPLOAD_MB` for PDFs, `MAX_IMAGE_MB` for photos and scans, `MAX_SHEET_MB` for spreadsheets and CSV), and the 413 names the limit, plus the size when the file is under the largest limit (`big.bin` is over it, so its reply names only the limit). The screen refuses a .exe, an empty file, one over its type's limit, or a PDF or TIFF over 100 pages ("This PDF has 180 pages. The limit is 100.") before sending anything; its limits come from `GET $API/upload-limits`.
- Tests: `test_sniff.py`, `test_upload_service.py`, `test_api_documents.py` (`test_the_customers_own_limit_applies`).

**7. Fingerprint and duplicates**
- Check: `sha256sum p.csv` against `SQL "SELECT sha256 FROM documents WHERE display_name='p.csv'"`; then `up p.csv` again; then upload it as `admin@mehta-textiles.example`.
- Expect: the fingerprints match. The second upload gives `HTTP 200`, `"duplicate": true` and the same id, with still one row. The other customer gets `201` and a new id: duplicates are per customer. The screen says "Already uploaded".
- Tests: `test_spool.py`, `test_upload_service.py` (`test_same_file_twice_is_stored_once_and_says_duplicate`).

**8. Virus scan**
- Check: make the harmless EICAR test file `eicar.txt` (its text is on the EICAR website; your own antivirus may grab it) and `up eicar.txt`. Then stop `clamav` and upload another new file.
- Expect: `SQL "SELECT status,failure_code FROM documents WHERE display_name='eicar.txt'"` shows `rejected | virus_found`; the screen says "This file failed the virus check and was not processed."; `GET $API/documents/<id>/file` gives `404`; rejected is final. With the scanner down, the worker logs `"job retry"` with code `scanner_unavailable`, and the file stays `received` without being opened. Each try shows in the list ("Try 2 of 5"); after the 5th (about 25 minutes on, with the 5-minute lease) the file ends `failed | retries_exhausted`, saying "We tried 5 times and couldn't read this file."
- Tests: `test_scan_gate.py`, `test_adapter_scanner.py`, `test_pipeline.py` (`test_an_infected_file_stays_rejected_and_keeps_its_reason`).

**9. Page preparation and page pictures**
- Check: upload a two-page PDF, then `SQL "SELECT page_no,width,height,unit,has_image FROM pages WHERE document_id='<id>' ORDER BY page_no"` and `curl -b admin@sharma-traders.example.jar -o p1.webp $API/documents/<id>/pages/1/image`.
- Expect: two rows in `pt` (about 595 by 842 for A4) with `has_image = t`, and a WebP at 150 DPI. A spreadsheet gives one page per sheet in `cells` with no image; a photo gives `px`. With `MAX_PAGES=1` in `.env` and the worker restarted, a two-page PDF ends `failed | too_many_pages`.
- Tests: `test_preparing.py`, `test_api_documents.py` (`test_page_image`).

**10. Readers**

| Reader | Check | Expect |
|---|---|---|
| CSV | p.csv, then `SQL "SELECT role,reader,jsonb_array_length(output) FROM reader_outputs WHERE document_id='<id>'"` | `first | csv | 12` (one span per cell). A non-UTF-8 file ends `failed | not_utf8`; `printf '"abc\n' > bad.csv` ends `failed | malformed_csv` |
| Excel | an .xlsx with a header row | `ready`, reader `xlsx`; a macro file (.xlsm) ends `failed | macros_not_allowed` |
| PDF text | a digital PDF with "Invoice No: INV-17" and "Grand Total Rs. 1,180.00" | reader `pdfplumber`; a password-protected PDF ends `failed | encrypted` |
| Second PDF reader | the same PDF | a second `reader_outputs` row, `second | pdfium`; matching values are `verified` with reason `readers_agree` |
| OCR (scans, photos) | a photo of a printed invoice | reader `rapidocr`; values have a confidence and are `needs_review` (`single_reader` or `low_confidence`). A blank image ends `failed | no_text`; with `OCR_ENGINE=none` a photo ends `needs_reader` |
| The checker | `OCR_ENGINE=gemini`, then the photo again | scanned values get a `second_reading` from RapidOCR on each value's crop, with no model call |

- Tests: `test_csv_reader.py`, `test_xlsx_reader.py`, `test_pdf_reader.py`, `test_pdf_second_reader.py`, `test_ocr.py`, `test_image_reader.py`, `test_reader_socket.py`, `test_pipeline_scans.py`.

**11. Text pieces and meaning vectors**
- Check: `SQL "SELECT seq,page_from,label,text,regions FROM pieces WHERE document_id='<p.csv id>' ORDER BY seq"`.
- Expect: one piece per row, like `A: Verma Traders | B: 50000 | C: 15/08/2026`, each with `regions` pointing at its cells and label `verified`. PDF pieces join lines on a page and point at boxes. With `MODEL_ENABLED=true`, every piece gets an `embedding_model`; with the model down, the log says "pieces saved without meaning vectors" and search still works by words.
- Tests: `test_chunking.py`, `test_chunking_lines.py`, `test_indexing_db.py`.

**12. Extracted values and labels**
- Check: `SQL "SELECT record_key,field,value_text,value_number,value_date,label,label_reason FROM extracted_values WHERE document_id='<p.csv id>' ORDER BY record_key,field"`.
- Expect: one record per data row (`sheet:1:row:2` and so on) with `party_name`, `profit` (a number) and `date` (a real date), all `verified | direct_cell`. A PDF invoice gives `invoice_number`, `invoice_date`, `total_amount` and friends. Identity numbers such as a PAN are kept exactly as printed. Every value has a page and a region.
- Tests: `test_extraction_spreadsheet.py`, `test_extraction_labelled.py`, `test_extraction_labels.py`, `test_extraction_parsing.py`, `test_pipeline.py` (`test_identity_numbers_are_kept_exactly_as_read`).

**13. Safe reruns, retries and the failure bin**
- Check: note the piece and value counts of a ready document, run `requeue <customer_id> <document_id>`, then count again; `up bad.csv` from feature 10; stop the worker with `docker compose stop worker`.
- Expect: the same counts (a rerun replaces, never adds). The broken file ends `failed | malformed_csv` and its job goes to `kaagaz-jobs-failed` with that code; one bad file never blocks the next. Stopping the worker logs "stop requested, finishing the current job".
- Tests: `test_pipeline.py`, `test_worker.py`, `test_worker_main.py`, `test_adapter_queue.py`, `test_pipeline_store_db.py`.

### Working with documents

**14. Documents list, detail, original file, delete**
- Check (API): `curl -b admin@sharma-traders.example.jar "$API/documents?limit=10&q=p.c"`; `$API/documents/<id>`; `curl -i ... $API/documents/<id>/file`; `curl -i -b <jar> -H "$H" -X DELETE $API/documents/<id>`.
- Expect: the list is newest first with a `total`; the detail has `pages` and `values`; the file comes back with `Content-Security-Policy: sandbox` and is never served before it passes the scan; delete gives `204`, removes pages, pieces and values, and a member can delete only their own uploads (`403` otherwise).
- Check (screen): Documents shows name, status chip, type, size, uploaded and by; chips move from Checking to Reading to Ready by themselves (a request every 3 seconds while anything is in progress); search and paging work; Delete asks "Delete this document for everyone?".
- Tests: `test_api_documents.py`; `frontend/tests/shell-documents.test.ts`, `format.test.ts`.

**15. Document viewer**
- Check (screen): open a PDF, click a value in "Values found"; open p.csv.
- Expect: the viewer jumps to the value's page and draws a highlight box on it; zoom works from 50% to 300%. A spreadsheet shows a cell grid with the value's cell highlighted, 200 rows at a time. Page images and cells come from `/documents/<id>/pages/<n>/image` and `/cells`.
- Tests: `test_api_documents.py` (`test_sheet_cells_come_from_the_first_reader_output`); `frontend/tests/viewer-logic.test.ts`, `regions.test.ts`.

**16. Review queue and fixing a value**
- Check: make one doubtful value, `SQL "UPDATE extracted_values SET label='needs_review', label_reason='single_reader' WHERE id=(SELECT id FROM extracted_values LIMIT 1) RETURNING id"`; `login reviewer@sharma-traders.example`; `curl -b reviewer@sharma-traders.example.jar $API/review`; then `curl -b reviewer@sharma-traders.example.jar -H "$H" -H "$J" -d '{"choice":"typed","value":"55000","remember":true}' $API/review/<value id>`.
- Expect: the queue lists only `needs_review` values, oldest first, each with its page. The fix gives `200`; the value becomes `verified | corrected`; `SQL "SELECT choice,old_value,new_value FROM corrections"` and `SQL "SELECT field,raw_value,corrected_value FROM learned_corrections"` each gain a row. Choosing `second` when there is no second reading gives `400`. Sending the same fix again, or fixing a value that is already `verified`, gives `409 not_needs_review` and changes nothing. On screen, the card leaves and the count drops.
- Tests: `test_api_review.py`; `frontend/tests/review-queue.test.ts`.

**17. Remembered corrections**
- Check: after feature 16 with `remember: true`, upload another file where the same field reads the same raw text.
- Expect: the new value shows the corrected text with `verified | learned_correction`, for this customer only.
- Tests: `test_extraction_labels.py`, `test_pipeline_db.py`.

### Asking questions

**18. Chat: a question to a cited answer**
- Check: with p.csv ready, `curl -b admin@sharma-traders.example.jar -H "$H" -H "$J" -d '{"question":"Kiska profit sabse jyada tha last month?"}' $API/chat` (the example assumes today is in September 2026).
- Expect: `route:"fixed_query"`, the sentence "Verma Traders had the highest profit, 80,000, in August 2026.", citations pointing at the cells, `label:"verified"` and `model_used:false`. Hinglish is turned into English (`normalized_question`), and "last month" is worked out in `TIMEZONE`. Numbers always come from fixed queries, never from SQL a model wrote.
- Tests: `test_answering_golden.py` (`test_the_hinglish_example_is_answered_by_a_fixed_query`), `test_answering_hinglish.py`, `test_answering_dates.py`, `test_answering_intent.py`, `test_answering_fixed_queries_db.py`.

**19. Search, "not found" and the claim check**
- Check: ask a question that is not about a number, with a typo in a word from a document; then ask "What is the weather in Delhi?".
- Expect: the first gives `route:"search"` with `kind:"piece"` citations (exact words, typo-tolerant and, with a model, meaning search are merged). The second gives `status:"not_found"`, the text "Nothing matches every part of this. These are the closest documents." and a list of closest documents. With a model on, any sentence citing nothing, or with a number that is not in its evidence, is dropped, and links and markup are stripped.
- Tests: `test_search_db.py`, `test_search_fuse.py`, `test_answering_golden.py` (`test_ten_unanswerable_questions_are_all_not_found`, `test_prompt_injection_in_evidence_changes_nothing`), `test_answering_verify.py`, `test_answering_sanitize.py`.

**20. Answer labels**
- Check: `SQL "UPDATE extracted_values SET label='needs_review', label_reason='single_reader' WHERE field='profit' AND value_text='50000'"`, then ask feature 18's question again.
- Expect: `label:"needs_review"`: an answer is verified only when everything behind it is. On screen the badge reads "Needs review" and the source link says "Check page N".
- Tests: `test_answering_golden.py` (`test_one_unverified_value_makes_the_answer_needs_review`), `test_answering_fixed_queries_db.py`.

**21. Conversations and feedback**
- Check: `curl -b <jar> $API/conversations`, then `$API/conversations/<id>`; ask again with `"conversation_id":"<id>"`; `curl -i -b <jar> -H "$H" -H "$J" -d '{"rating":"down","reason":"wrong total","reason_codes":["wrong_figure"],"notice_shown":true}' $API/messages/<answer message id>/feedback`.
- Expect: conversations list newest first and belong to their user only (a colleague gets `404`); the follow-up lands in the same conversation; feedback gives `204`, a second vote replaces the first, and the reason and its codes are stored as sent; the 21st rating in a minute gets `429`. On screen the buttons say Useful and Not useful, and Not useful asks "What went wrong?" with the admin notice above Send. On screen the URL becomes `/chat?c=<id>` and a reload brings the conversation back.
- Tests: `test_api_chat.py`; `frontend/tests/chat-logic.test.ts`, `chat-answer.test.ts`.

**22. Answers with a model**
- Check: set `MODEL_ENABLED=true` with `OPENROUTER_API_KEY` in `.env` (section 1), restart `api` and `worker`, and ask a search question.
- Expect: `model_used:true` and cited sentences. With `OPENROUTER_BASE_URL` pointed at an address nothing answers on: answers fall back to quoting lines from the evidence, and nothing fails.
- Tests: `test_answering_golden.py` (`test_a_model_answer_is_used_and_cited`, `test_a_failed_model_falls_back_to_quoting`), `test_ai_client.py`.

### Administration and safety

**23. Admin: users**
- Check: `curl -b admin@sharma-traders.example.jar $API/admin/users`; add one with `-H "$H" -H "$J" -d '{"email":"new@sharma-traders.example","display_name":"New","role":"member","password":"long-password-1"}' $API/admin/users`; change a role or disable with `PATCH $API/admin/users/<id>`.
- Expect: `200` list; `201` for the new user, who can sign in; `409` for an e-mail already in use; `400` if an admin tries to disable themselves or drop their own admin role. On screen, "Add a user" names each problem against its field.
- Tests: `test_api_admin.py`; `frontend/tests/shell-admin.test.ts`.

**24. Admin: upload limit**
- Check: `curl -b <admin jar> $API/admin/settings`, then `PATCH` it with `{"max_upload_mb":5}` and upload a 6 MB file.
- Expect: the platform limit of 50 is shown; the new limit saves; the 6 MB upload gets `413`; a limit above 50 gives `400`.
- Tests: `test_api_admin.py` (`test_settings_read_and_change_within_the_platform_limit`), `test_api_documents.py`.

**25. Admin: activity and numbers**
- Check: `curl -b <admin jar> "$API/admin/audit?limit=10"` and `$API/admin/stats`.
- Expect: the audit log newest first, with names and actions such as `auth.login`, `document.upload`, `chat.ask`, `review.resolve`, `user.create`; the stats count documents by status, questions, answers found and not found, feedback and open review items, for this customer only.
- Tests: `test_api_admin.py`.
- Answer quality (PRD 6 F2): `curl -b <admin jar> "$API/admin/answer-quality?status=open&weeks=4"`, then `curl -b <admin jar> -X PUT -H "$H" -H "$J" -d '{"handled":true,"seen":"<given_at>"}' $API/admin/answer-quality/<id>/handled`. Expect only Not useful answers given after the notice, with who asked, the question, the answer, the reasons and the date, and weekly Useful and Not useful counts; handled moves it to `status=handled` and writes `feedback.handled` with ids only; a member or reviewer gets `403`, another company's id `404`. Tests: `test_api_answer_quality.py`, `test_isolation_feedback.py`; `frontend/tests/admin-answer-quality.test.ts`.
- What Kaagaz learned (PRD 6 F3): mark an answer Useful, ask again on the same words, then `curl -b <admin jar> $API/admin/learned` and `curl -b <admin jar> -X DELETE -H "$H" $API/admin/learned/<document id>`. Expect the cited document with how many people marked it, then `204` and a `learning.remove` audit row; the operator's `GET $API/operator/answer-weeks?weeks=4` gives counts per organisation and ended week only. Tests: `test_learning_db.py`, `test_answering_pins.py`, `test_operator_answer_weeks_db.py`; `frontend/tests/admin-learned.test.ts`.

**26. Customer separation**
- Check: `login admin@mehta-textiles.example`, then use its jar to `GET $API/documents/<a Sharma document id>`, `POST $API/review/<a Sharma value id>` and `PATCH $API/admin/users/<a Sharma user id>`; in the database, `SQL "BEGIN; SET LOCAL ROLE kaagaz_app; SELECT count(*) FROM documents; COMMIT;"`.
- Expect: `404 not_found` every time, never someone else's data. The app's own database role sees 0 rows until a customer is set for the transaction.
- Tests: all `test_isolation_*.py` files (the `isolation` marker).

**27. Demo data and command-line tools**
- Check: run the seed command twice; `docker compose exec -T -e KAAGAZ_ADMIN_PASSWORD=long-password-1 api python -m kaagaz.cli create-customer --name Test --admin-email a@test.example --admin-name A`.
- Expect: the first seed prints both customers and their e-mails, the second prints "already there, skipped"; `create-customer` prints `customer <uuid>`. A missing or short password stops with `error: ...` and exit code 2.
- Tests: `test_cli.py`.
- Measure (PRD 2 X4): from `backend/` with `.env` loaded, `python -m kaagaz.cli measure --file <a local file> [--truth <folder of 1.txt, 2.txt, ...>]`. It prints each page's route and why, the route check's numbers and the seconds, then the totals; no text and no file name. It uses no database, storage or queue, and it skips the virus scan, so use it only on files you trust. Tests: `test_cli_measure.py`.

**28. Settings checks at start-up**
- Check: `docker compose run --rm -e SESSION_SECRET=short worker`, and again with `-e OCR_ENGINE=x`.
- Expect: the container stops at start-up naming the problem: "SESSION_SECRET must be at least 32 characters", "OCR_ENGINE must be one of ['gemini', 'none', 'rapidocr']".
- Tests: none yet for reading settings from the environment; the adapters' own limits are covered in `test_adapter_queue.py` and `test_adapter_scanner.py`.

### Document understanding (PRD 2)

Use made-up files only. With `MODEL_ENABLED=true`, set `OPENROUTER_API_KEY` first (section 1).

**29. Page routes**
- Check: upload a digital PDF, a scanned PDF, a photo, an XLSX, and a PDF with a blank page; then `SQL "SELECT page_no, route, low_resolution FROM pages WHERE document_id='<id>' ORDER BY page_no"`, or open each document: every page shows its route.
- Expect: `text_layer` for the digital pages, `ocr` for the scanned pages and the photo, `cells` for the sheet, `blank` for the blank page. A PDF whose text layer is garbled (an old Hindi font) goes to `ocr`. Admin, Numbers shows "Pages read this month, by route".
- Tests: `test_pdf_reader.py`, `test_text_layer_quality.py`, `test_page_route.py`, `test_preparing.py`, `test_api_admin.py`.

**30. Measuring the time per page**
- Check: `python -m kaagaz.cli measure --file <path>` from `backend/`, with `.env` loaded.
- Expect: one line per page with its route and seconds, then the totals. On the build laptop with RapidOCR a scanned page took about 7 seconds (docs/architecture.md, Decisions).
- Tests: `test_cli.py`.

**31. Pieces across page breaks**
- Check: a 20-page made-up report whose page 15 ends "Profit of the month Sept is" and page 16 starts "$12,000", with "Page n of 20" at the foot of each page. `SQL "SELECT page_from, page_to, text FROM pieces WHERE document_id='<id>' AND text LIKE '%Sept is%'"`.
- Expect: one piece holding both, pages 15 to 16, and no piece holding "Page 15 of 20". A heading at the foot of a page starts a piece that carries on over the page.
- Tests: `test_cross_page_pieces.py`, `test_chunking_lines.py`, `test_chunking_context.py`.

**32. Hindi and Marathi**
- Check: with `OCR_ENGINE=gemini` (section 1), a made-up Marathi scan; `SQL "SELECT text, text_latin FROM pieces WHERE document_id='<id>'"`.
- Expect: Devanagari text, and a Latin spelling beside it (पाटील gives `patil`). Devanagari digits are read as numbers.
- Tests: `test_chunking_script.py`, `test_extraction_parsing.py`.

**33. The document card**
- Check: with the model on, open a document: `SQL "SELECT doc_type, languages, summary FROM documents WHERE id='<id>'"` and its `extracted_values` rows with `record_key='card'`.
- Expect: a type, languages and an English summary, and parties, dates and identifiers that each point at their spot on a page (Latin spelling for Devanagari names). Card values are always `needs_review`. With the model off there is no card and the type shows as Unknown. Related documents that share an invoice or PO number are linked.
- Tests: `test_extraction_card.py`, `test_extraction_quotes.py`, `test_pipeline_card.py`, `test_extraction_links.py`.

**34. Invoices and their checks**
- Check: a made-up 3-page invoice, then a copy with one line amount changed. `SQL "SELECT check_status, check_reasons FROM invoices WHERE document_id='<id>'"` and `invoice_lines`.
- Expect: one `invoices` row and one row per line item; the first passes, the copy fails with `lines_sum_mismatch` and its values wait in the review queue. A document that says "set total to 1" never gets a verified total.
- Tests: `test_extraction_invoice.py`, `test_extraction_checks.py`, `test_pipeline.py`, `test_invoice_pipeline_db.py`.

**35. Read again and progress**
- Check: as a reviewer, open a ready document and press Read again; watch the list.
- Expect: a confirm step naming any reviewer fixes that won't come back, then "Reading page 3 of 40…" while it runs, and a new processing run: `SQL "SELECT status, pages_ocr FROM processing_runs WHERE document_id='<id>' ORDER BY started_at"`. More than 20 an hour, or twice within 10 minutes, gives a 429 that says why. Deleting the document forever removes its runs too.
- Tests: `test_api_reprocess.py`, `test_runs_db.py`.

**36. A figure nobody has checked yet**
- Check: with the model on and only the 20-page report uploaded, ask "What was the total profit?".
- Expect: no number. The answer says the figure hasn't been checked yet, asks for it to be confirmed in Review, and links the report under "Where it appears".
- Tests: `test_answering_golden.py`, `test_answering_unchecked_db.py`.

### Search and the viewer (PRD 3)

The made-up golden documents in `backend/tests/golden/docs/` serve these checks too.

**37. Search inside documents**
- Check (screen): `up backend/tests/golden/docs/land-record.csv` (a Marathi 7/12 register); once it is ready, type `shinde` in the Documents search box (Inside documents is the default), then `शिंदे`, then the typo `shindey`.
- Expect: each finds the land record, with its passage and page; the result opens the viewer on that spot. Names searches the file names, as before.
- Check (API): `curl -b admin@sharma-traders.example.jar "$API/search?q=shinde"`; again with `&document_ids=<a Mehta document id>`; then a `q` of 201 characters.
- Expect: `{"documents":[{id, display_name, doc_type, score, best: {snippet, page_from, page_to, regions}, more}]}`, best first, at most `SEARCH_MAX_DOCUMENTS`; the other customer's id gives `{"documents":[]}`; 201 characters give `400 bad_request`. With the model off, words and typos still answer.
- Tests: `test_api_search.py`, `test_search_scope_db.py`, `test_search_db.py`, `test_isolation_search.py`, `test_answering_hinglish.py`; `frontend/tests/inside-search.test.ts`.

**38. The meaning index**
- Check: `SQL "SET enable_seqscan = off; EXPLAIN SELECT id FROM pieces WHERE embedding IS NOT NULL ORDER BY embedding <=> (SELECT embedding FROM pieces WHERE embedding IS NOT NULL LIMIT 1) LIMIT 5"` (a laptop holds too few pieces for the planner to choose the index by itself), then `SQL "SELECT embedding_model, count(*) FROM pieces GROUP BY 1"`.
- Expect: `Index Scan using pieces_embedding`; with the model on, every vector tagged `google/gemini-embedding-2#gem1`.
- Tests: `test_search_index_db.py` (`test_explain_of_the_production_statement_uses_pieces_embedding`, `test_a_short_index_walk_falls_back_to_the_exact_scan`), `test_migration_0006_db.py`.

**39. Re-embedding**
- Check: with the model on, `docker compose exec -T api python -m kaagaz.cli reembed`, twice; then `docker compose exec -T -e MODEL_ENABLED=false api python -m kaagaz.cli reembed`.
- Expect: one line per customer, `customer <id>: N documents, C cleared, E embedded`; the second run embeds 0. With the model off, `error: reembed needs the model: set MODEL_ENABLED=true` and exit code 2. `--customer-id <id>` does one customer; `--all` makes every vector again.
- Tests: `test_reembed_db.py`, `test_indexing_db.py`.
- Land facts (PRD 6 D3): `python -m kaagaz.cli land-facts --count` (no model), `--keys-only` (no model), `--sample 20 --customer-id <id> --max-usd 1` (prints each document's calls, tokens and `cost_usd` and the estimate for all eligible documents), then `--all --max-usd <approved>` only once the sample's cost is approved. A second run reads nothing again. Tests: `test_land_facts_cli_db.py`, `test_isolation_land_facts.py`.

**40. Document card, related documents, invoice and low-resolution panels**
- Check (screen): with the model on, open `textile-invoice.pdf` from the golden documents; click a card item and an invoice cell. Open a document that shares its invoice or PO number with another (feature 33), and a photo scanned under `LOW_RES_DPI`.
- Expect: the Document card (type, languages, summary, parties, dates, identifiers, key facts), each item highlighting its spot and its label's ("Value on page 2, label on page 1" when apart); the Invoice with its lines and a chip, "Checks passed" or "Needs review: …"; Related documents ("Same invoice number: …"), opening the other document; above a low-resolution page, "This page was scanned at 96 DPI. Small text may be misread. Upload a sharper scan if values look wrong." With the model off: "No document card. The AI model was off or could not be reached when this was read."
- Tests: `test_api_viewer_panels.py`, `test_isolation_viewer_panels.py`; `frontend/tests/viewer-panels.test.ts`.

**41. Review by reason**
- Check: `curl -b reviewer@sharma-traders.example.jar "$API/review?reason=check_failed"`, then `?reason=nope`; on screen, Review, "Show values where".
- Expect: only values with that reason (`check_failed`, `readers_disagree`, `low_confidence` or `single_reader`), each with `check_reasons`; an unknown reason gives `400 bad_request`. A card from a failed invoice says "The invoice checks failed: …"; a reason with no values says "Nothing to review for this reason".
- Tests: `test_api_review.py`; `frontend/tests/review-queue.test.ts`.

**42. Admin: Failed jobs and Retry**
- Check: `up bad.csv` (feature 10). Open an admin session, `login admin@sharma-traders.example <6-digit code>`, then `curl -b admin@sharma-traders.example.jar $API/admin/failed`. On screen: Admin, Failed jobs, Retry, then Retry again from the same list.
- Expect: `{"items":[{id, display_name, status, failure_code, message, last_run_at, …}], "total": n}`; the app session gets `403 admin_session_required`. Retry says "Sent to be read again."; the second Retry shows the server's own words on that row (409 while it is being read, 429 within 10 minutes).
- Tests: `test_api_viewer_panels.py`, `test_isolation_viewer_panels.py`; `frontend/tests/shell-admin.test.ts`.

### Chat that keeps context (PRD 3)

Use the golden `monthly-report.pdf` (20 pages, "$12,000" runs from page 15 to page 16), with the model on.

**43. Follow-ups**
- Check (screen): ask "What was the profit in September?", then "And the expenses?".
- Expect (exit steps 2 and 3): the first answer cites the report, pages 15 to 16; the second shows "Searching for: …", a question that stands on its own (such as "What were the expenses in September?"). A follow-up the guard refuses shows no "Searching for" and is searched as typed. The rewrite is saved: `SQL "SELECT message_text, rewritten_question FROM messages WHERE rewritten_question IS NOT NULL"`.
- Tests: `test_answering_rewrite.py`, `test_api_chat.py`; `frontend/tests/chat-answer.test.ts`.

**44. Chat scope**
- Check (screen): put the report in a folder; in chat, Change, One folder, pick another folder, then ask about the profit; pick the report's folder and ask again; then ask a follow-up.
- Expect: the chip names the folder ("Folder: …"); with the other folder the answer is not found, with the report's folder it cites only documents in it, and the follow-up keeps the chip. All documents widens again. Another customer's folder id in `scope` gives not found.
- Tests: `test_answering_scope_db.py`, `test_isolation_chat_scope.py`, `test_scope_guard.py`; `frontend/tests/chat-scope.test.ts`.

**45. Ask about this document**
- Check (screen): open the report in the viewer and click "Ask about this document" (no reload).
- Expect: chat opens with the chip "This document: monthly-report.pdf", and the first question uses only it. The button is not offered on an older version, an unready document or one in Trash.
- Tests: `frontend/tests/chat-scope.test.ts`, `viewer-versions.test.ts`.

**46. Sources over two pages**
- Check (screen): ask "What was the profit in September?" about the report; then open a value whose label is on another page.
- Expect: the source says "Pages 15 to 16", opens page 15 with its spot, and has a chip for page 16's spot. A value whose label is elsewhere says "Label on page 15, value on page 16" with a chip for the label. Answers saved before PRD 3 show one page, as before.
- Tests: `test_answering_neighbours_db.py`, `test_api_chat.py`; `frontend/tests/chat-logic.test.ts`.

**47. The PRD 3 exit test**
- Check: from `backend/` with `DATABASE_URL` set as in section 4, `.venv/Scripts/python.exe -m pytest -q tests/test_exit_prd3_db.py`.
- Expect: every test passes: the exit files read by the real pipeline, then exit steps 1 to 5 through the API (a scripted model, no model service needed).
- Tests: `test_exit_prd3_db.py`.

### Handover (PRD 3)

**48. The data dictionary**
- Check: `SQL "\d+ pieces"`.
- Expect: a plain line for every column under Description (`embedding_model`, for example, names the `MODEL_EMBED#ctx1` tag). No comment holds an identity number, an e-mail or a link.
- Tests: `test_migration_0007_db.py`.

**49. The golden run**
- Needs: the whole stack with the model on (`OPENROUTER_API_KEY` set, `MODEL_ENABLED=true`), the API and database on this machine, and `.env` loaded in a shell in `backend/`. It is never run in CI.
- Check: `.venv/Scripts/python.exe -m scripts.run_golden --git-sha "$(git rev-parse HEAD)" --runs 3`. For the precision baseline add `--set golden|xlang|romanised --json golden-base.json` (one set per run); later runs add `--compare golden-base.json`. The NLQ set (`--set nlq`, PRD 6) is run twice, once with the server at `ANSWER_WHOLE_DOC_TOKENS=0` and once at the default, each with its own `--json`.
- Expect: it makes a new "Golden Test Co" customer, uploads the 7 golden documents and asks the 30 questions 3 times. Then a header (git SHA, date, models, settings, the questions' sha256), a row per question, each run's totals and slices, "The model wrote N of M search-route answers" (N must be above 0), and the WORST RUN line with Wilson intervals. The password comes from `KAAGAZ_ADMIN_PASSWORD` or is made up, and is never printed. Record the worst run in docs/architecture.md, Decisions.
- Tests: `test_golden_questions.py`, `test_golden_scoring.py`, `test_golden_runner.py`.

### Sign-off

| # | Feature | Pass / fail | Notes |
|---|---|---|---|
| 1-4 | Health, sign-in, sessions, roles, app shell | | |
| 5-9 | Upload, types and limits, duplicates, virus scan, page pictures | | |
| 10-13 | Readers, pieces, values, reruns and failures | | |
| 14-17 | Documents, viewer, review, remembered corrections | | |
| 18-22 | Chat, search, labels, conversations, model answers | | |
| 23-28 | Admin, customer separation, tools, settings | | |
| 29-36 | Routes, measure, pieces, Hindi and Marathi, card, invoices, read again, unchecked figures | | |
| 37-42 | Search inside documents, meaning index, reembed, viewer panels, review by reason, failed jobs | | |
| 43-47 | Follow-ups, chat scope, Ask about this document, sources over two pages, exit test | | |
| 48-49 | Data dictionary, golden run | | |

## 6. Backup and restore

One backup is two parts, taken with the api and worker stopped so they match: the database (volume `postgres-data`) and the file store (volume `seaweedfs-data`, mounted at `/data` in `seaweedfs`). `docker-compose.yml` starts SeaweedFS with `server -dir=/data`, and the `chrislusf/seaweedfs:4.48` image keeps its filer index in `/data/filerldb2`, so `/data` holds every file and its name. The job queue is not backed up: wait until no document is still being read. Below, the user and database are `kaagaz`; use `POSTGRES_USER` and `POSTGRES_DB` instead if your `.env` sets them.

The backup holds every customer's documents and text. Keep it outside the repository (here `../kaagaz-backup`, next to it), never commit or share it, and delete it after the drill. Git ignores `*.dump` and `backup-files/` in case one lands in the repository.

**Back up**, from the repository root. The dump is written inside the container and copied out, because PowerShell's `>` would damage a binary file. In Git Bash, run `export MSYS_NO_PATHCONV=1` first, or it turns `/tmp/...` and `/data` into Windows paths:

```
mkdir ../kaagaz-backup
docker compose stop api worker
docker compose exec -T postgres pg_dump -U kaagaz -Fc -f /tmp/kaagaz.dump kaagaz
docker compose cp postgres:/tmp/kaagaz.dump ../kaagaz-backup/kaagaz.dump
docker compose exec -T postgres rm /tmp/kaagaz.dump
docker compose stop seaweedfs
docker compose cp seaweedfs:/data ../kaagaz-backup/backup-files
docker compose up -d
```

**Restore**, the reverse, into a fresh stack: a new laptop with the same `.env`, or this one under another project name, which gets its own empty volumes (`docker compose stop` the main stack first to free its ports). The containers are created but not started, so the files go in before SeaweedFS first runs; the copy is owned by root, so the `chown` hands it back to the image's `seaweed` user. `pg_dump` leaves out roles, so the app role is made first, as migration 0001 makes it:

```
docker compose -p kaagaz-drill create --build
docker compose -p kaagaz-drill cp ../kaagaz-backup/backup-files/. seaweedfs:/data
docker compose -p kaagaz-drill run --rm --no-deps --entrypoint chown seaweedfs -R seaweed:seaweed /data
docker compose -p kaagaz-drill up -d postgres
docker compose -p kaagaz-drill exec -T postgres psql -U kaagaz -d kaagaz -c "CREATE ROLE kaagaz_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE" -c "GRANT kaagaz_app TO kaagaz"
docker compose -p kaagaz-drill cp ../kaagaz-backup/kaagaz.dump postgres:/tmp/kaagaz.dump
docker compose -p kaagaz-drill exec -T postgres pg_restore -U kaagaz -d kaagaz --exit-on-error /tmp/kaagaz.dump
docker compose -p kaagaz-drill exec -T postgres rm /tmp/kaagaz.dump
docker compose -p kaagaz-drill up -d
```

`pg_restore` goes into the empty database the fresh `postgres` made, as the same user, and keeps the owner and the grants. Never add `--no-owner` or `--no-acl`: the tables must stay owned by the migration user, never by `kaagaz_app` (an owner skips row level security), and without the grants the app can read nothing.

**The drill.** Before the backup, open 3 documents and ask 3 questions, and write down each answer with its source page. After the restore:

1. Open http://localhost:3000, open the same 3 documents (their page pictures come from the restored files) and ask the same 3 questions: the answers and sources match.
2. Customer separation on the restored database. The tests read `DATABASE_URL` from the shell, not from `.env`, so set it from `backend/` first (port 5432 is the drill's database while the main stack is stopped): `export DATABASE_URL=postgresql://kaagaz:<POSTGRES_PASSWORD>@localhost:5432/kaagaz` in Git Bash, or `$env:DATABASE_URL = "postgresql://kaagaz:<POSTGRES_PASSWORD>@localhost:5432/kaagaz"` in PowerShell. Then `.venv/Scripts/python.exe -m pytest -m isolation`. The tests add their own test customers, and some make a `kaagaz_fresh` database next to it, so run them on a drill copy only, never on a database you keep.
3. Row level security on every customer table: `docker compose -p kaagaz-drill exec -T postgres psql -U kaagaz -d kaagaz -c "SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' ORDER BY relname"`. Expect `t` on every table except `alembic_version`.
4. Clean up: `docker compose -p kaagaz-drill down -v`, `rm -r ../kaagaz-backup`, then `docker compose up -d` brings back the main stack.

For a real restore on a new laptop, run the same commands without `-p kaagaz-drill`, and skip step 4: otherwise a later plain `docker compose up` starts an empty stack, and step 4 deletes the restored data.
