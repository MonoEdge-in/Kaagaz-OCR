# Running Kaagaz on a laptop

Two ways: everything in Docker, or the stand-ins in Docker and the API,
worker and web app run straight on the laptop. Every key and password in
`.env.example`, `docker-compose.yml` and `infra/` is a **laptop-only
placeholder**, never a real secret.

## What runs where

| Service | Port | What it is | On the EC2 server |
|---|---|---|---|
| `frontend` | 3000 | Next.js web app (open this) | the same image, behind Caddy |
| `api` | 8000, on 127.0.0.1 only | FastAPI, `/api/*` | the same image, behind Caddy |
| `worker` | none | `python -m kaagaz.worker_main`, one job at a time | the same image |
| `migrate` | none | one-shot: `alembic upgrade head`, then makes the storage bucket | on every deploy |
| `postgres` | 5432 | PostgreSQL 17 with pgvector | the same container (no RDS in this phase) |
| `seaweedfs` | 8333 | file storage, S3 API | the same container (no S3 in this phase) |
| `elasticmq` | 9324 | job queue `kaagaz-jobs` and its failure bin `kaagaz-jobs-failed`, SQS API; messages kept in the volume `elasticmq-data` | not run: jobs are in Amazon SQS (ap-south-1), through the instance role |
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
one search inside documents returns; `SEARCH_MAX_SCAN_TUPLES` (20000), the most
tuples one meaning index walk visits, and `SEARCH_EXACT_BELOW` (2000), how few vectors a
person can see before meaning search scans them exactly (PRD 7); `ANSWER_NEIGHBOURS` (1, 0 to 3), the
pieces on each side of an evidence piece also given to the draft; and
`CHAT_HISTORY_TURNS` (3, 0 to 10), the question and answer pairs a follow-up
is rewritten with (0: no rewrite).

Two optional settings (council 4 Oct 2026): `ANSWER_TRANSLATE_QUESTION` (false when
unset) also searches an English or Romanised question in the script of its scans, with
one model call that times out after `MODEL_TRANSLATE_TIMEOUT_SECONDS` (20, PROVISIONAL).
`ANSWER_PLAN_TIMEOUT_SECONDS` (4 when unset, 1 to 60): a first question's rewrite and plan
call ends by then, and the question is answered without its plan (code `plan_timeout`).
`ANSWER_STREAM_MAX_SECONDS` (180 when unset, 5 to 600; also the streamed draft's own limit, MODEL_TIMEOUT_SECONDS being its idle one): `POST /api/chat/stream` (#535) gives
up after this long. It says "This answer is taking too long. Try a narrower question or Swift."
and saves nothing.
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
* The API's port 8000 is published on 127.0.0.1 only, never to the network:
  the image trusts the `X-Forwarded-For` it is sent (`backend/Dockerfile`), so
  another machine could choose its own address.
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
npm ci                                 # once, Node 24 (as frontend/Dockerfile)
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
   for the last 5 documents read: `dc run --rm migrate python -m kaagaz.cli scan-times --last 5` (PRD 7, #606: the CLI runs as `kaagaz_worker`, so it runs in migrate, `dc run --rm migrate python -m kaagaz.cli <command>`, until JOBS_DATABASE_URL is set, then in worker; in api it is refused).
   Pages read before PRD 9 that say uncertain only for unpinned lines: `dc run --rm migrate python -m kaagaz.cli regrade-pages` lists them by organisation, `--apply` grades them good (no model call; on a server only with Vrushit's approval).

**RapidOCR, the fallback.** If Gemini misbehaves, set `OCR_ENGINE=rapidocr` in
`.env` and restart both: `dc up -d --no-deps api worker`. Change it back the same way.

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
rest to `frontend:3000`, refuses a body over 110 MB, and logs one JSON line per
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

**Rotating the keys, every 90 days (#481)**, and at once if one may have leaked
(a key once sat in git: `open_router_key_replacement.txt`, removed on 3 Nov 2026, so
rotate the OpenRouter key now). Write each date in the password manager's entry.
A changed `.env` value reaches the containers with `dc up -d --no-deps api worker`
(compose recreates them); then update the offline copy.

| Secret | How | Who notices |
|---|---|---|
| `OPENROUTER_API_KEY` | A new key on openrouter.ai with the same credit limit and data settings, into `.env`, restart; `dc run --rm worker python -m kaagaz.cli verify-models` says PASS; then delete the old key | Nobody |
| `MISTRAL_API_KEY` | The same on console.mistral.ai; check zero data retention still covers the account before `MISTRAL_ZDR_APPROVED` stays true | Nobody |
| `SESSION_SECRET` | `openssl rand -base64 48`, at a quiet time, restart | Everyone, operators too, is signed out, and unexpired emailed links signed with it (confirm your email) stop working: say so first |
| `TOTP_ENCRYPTION_KEY` | Not the same as `SESSION_SECRET`. Nothing re-encrypts the stored two-step secrets yet, so a new key leaves every one unreadable: after it, run `reset-admin-2fa --email` for each admin and `reset-operator-2fa --email` for each operator (at a terminal), and each enrols again. Until a re-seal command exists (proposed in #481: open each secret with the old key, seal it with the new, one transaction per organisation), rotate this one only on suspicion | Every admin and operator enrols again |
| `POSTGRES_PASSWORD` and `DATABASE_URL` | Postgres reads `POSTGRES_PASSWORD` only when its volume is new, so set it in the database first, through stdin (never a flag; `read -rs NEW` takes it unseen): `printf "ALTER ROLE kaagaz PASSWORD '%s';\n" "$NEW" \| dc exec -T postgres psql -q -U kaagaz -d kaagaz`, then both lines of `.env`, restart. After #479, the same for each login | Nobody (a few seconds) |
| `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY`, and the `STORAGE_ADMIN_` pair | Add a second entry to the identity's `credentials` in `s3.json`, `dc up -d --no-deps --force-recreate seaweedfs`, the new pair into `.env`, restart; then remove the old entry and recreate seaweedfs again | Nobody (seconds of storage restart) |
| `GOOGLE_CLIENT_SECRET` | Google Cloud console, the OAuth client: add a secret (two may be active), into `.env`, restart, sign in with Google once, then disable the old one | Nobody |
| SES (`EMAIL_FROM`) | No key: the server's role sends, and AWS rotates its credentials itself. Check its policy allows `ses:SendEmail` from `EMAIL_FROM` only, and that no SMTP user exists | Nobody |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Laptop-only stand-ins for ElasticMQ; never set on the server (its role is used) | Nobody |

Outside `.env`: `WAKE_TOKEN` (`openssl rand -hex 24` in the Lambda console; the old
link then gives 404, so send people the new one), and the backup key pair (section 6,
"Backups off the server"): a new pair only if the private key may have leaked, keeping
the old key 35 days to read the backups made before.

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
   sudo systemctl daemon-reload && sudo systemctl enable --now kaagaz-deploy.timer kaagaz-pgdump.timer kaagaz-idle.timer kaagaz-disk.timer kaagaz-containers.timer
   ```

5. The first deploy (several minutes):
   `sudo systemctl start --no-block kaagaz-deploy.service && journalctl -u kaagaz-deploy -f`.
6. The models, once:
   `dc run --rm worker python -m kaagaz.cli verify-models` says PASS on every line.
7. The first customer and admin:
   `dc run --rm migrate python -m kaagaz.cli create-customer --name "<Company>" --admin-email <email> --admin-name "<Name>"`
   (it asks for the admin's password at a hidden prompt). The admin signs in at
   `https://<DOMAIN>/login` and sets up two-step sign-in there (every admin must have it).

**What runs by itself.** `kaagaz-deploy.timer` runs `infra/ec2/deploy.sh` 2
minutes after boot and every 5 minutes: when `origin/production` has a new
commit it deploys it (Deploys and rollback, below; 1 to 2 minutes of downtime
while the app restarts), otherwise it starts anything that is down and, when
`infra/caddy/Caddyfile` differs from the copy in `~/.kaagaz-caddyfile`, hands it
to Caddy with a graceful `caddy reload` (#246 D8; a file Caddy refuses leaves the
old config running and logs `caddy reload failed`). Each run
logs one line, the commit and `deployed`, `unchanged`, `held`, `failed` or
`rolled back to <commit>` (`journalctl -u kaagaz-deploy -n 50`); a failed build
or migration is tried again on the next run. After a good deploy it removes build cache unused for 7 days
(`docker builder prune --filter until=168h`, never volumes), and before a build
with under 20 GB free on `/` it logs `low disk: ...` and does the same first;
`docker system df` shows what is left. `kaagaz-pgdump.timer` writes `~/backups/kaagaz-YYYYMMDD.dump` (mode
600, the last 7 kept) at 20:00 UTC (01:30 IST), or at the next boot if the
server was stopped then; with `BACKUP_BUCKET` and `BACKUP_CERT` set it also
copies an encrypted backup off the server (section 6, "Backups off the
server"). Data Lifecycle Manager takes a daily EBS snapshot at
20:30 UTC (02:00 IST), keeping 7. Restore from a snapshot (the whole disk) or
a dump (section 6). Logs: `dc logs --since 1h <service>`.

**Deploys and rollback** (#246, ADR 0052). A new commit goes live in four steps,
and the site keeps serving the old release until step 3:

1. Build `kaagaz-backend:<commit>` and `kaagaz-frontend:<commit>`. A failed
   build changes nothing.
2. `alembic upgrade head` in a throwaway container from the new image
   (`KAAGAZ_TAG=<commit> dc run --rm -T migrate`). A failed migration changes
   no container and logs `migrate failed; site still on <commit>`.
3. Point `kaagaz-backend:local` and `kaagaz-frontend:local` (what `dc` runs) at
   the new images, start every service but `migrate` with `--no-deps`, and wait
   up to 3 minutes for api and frontend to be healthy (frontend since #477;
   its check fetches `/login` with node, as the slim image has no curl).
4. If it is not, point `:local` back at the previous images and start those
   (`<commit> rolled back to <previous>`). That commit is held: the timer logs
   `<commit> held` and never tries it again; the next commit on `production`
   deploys as usual.

`~/.kaagaz-images` holds the live commit, then the one to roll back to, and is
what `:local` must point at: every timer run puts `:local` back on the live
images (undoing a hand `dc build`) before starting anything that is down.
`~/.kaagaz-deployed` holds the live commit too, written only once api and frontend are
healthy, and `~/.kaagaz-rolled-back` the held commit. The live and previous
images are kept; older commit images are removed after a good deploy. On the
first deploy with this flow, if api is healthy, the running `:local` images are
named after the commit in `~/.kaagaz-deployed`; otherwise the log says there is
no previous release to roll back to yet.

To roll back by hand, as `ubuntu`: `~/kaagaz/infra/ec2/deploy.sh --rollback`.
It starts the previous images, waits for api and frontend to be healthy, holds the commit
the checkout is on, and prints `<commit> rolled back to <previous>`; run it
again to go forward. While a deploy or the nightly backup holds the lock it
refuses and says so; `journalctl -u kaagaz-pgdump -n 5` shows whether the backup
is still running. It never runs a downgrade migration, so the older code runs
on the newer schema: this is safe only while migrations only add columns and
tables (ADR 0052, M1). A rollback also uses the checkout's compose files and
Caddyfile, those of the newest commit, so every release must keep them working
for the release before it.

**Never start the app with a plain `dc up -d`** on the server: it runs
`migrate` with `:local`. Images from before #246 M2 fail on a database ahead of
them (after a rollback over a migration) and leave api, worker and frontend
stopped; since M2, `alembic upgrade head` on such a database changes nothing,
logs one `database_ahead` line and exits 0. Use
`dc up -d --no-deps <service>`, and never `dc up --build` or `dc build`.

If `migrate` fails: the site is still on the old release, and every 5 minutes
the timer tries again. Read the error with `journalctl -u kaagaz-deploy -n 50`.
Fix it in a PR, never by hand on the server. Before any `alembic stamp` (Vrushit's
decision), take a backup (`~/kaagaz/infra/ec2/pgdump.sh`) and check
`alembic_version` against the real schema: on 24 Oct 2026 the row said 0042
while 0043 to 0045 were already applied, so `upgrade` failed with
`DuplicateTable`. For each migration after the stamped one, check that the
tables it creates exist (`SELECT to_regclass('public.<table>')`), then stamp
the last one that is fully there.

**Production that ran production-with-folder-upload (0061c)** (#484). Its database is at
`0061c`, a branch off 0061b that Testing-Env also carries, byte for byte. Releasing
Testing-Env just migrates: `alembic upgrade head` runs 0061 to 0070 with the folder tables
already there, then 0071, which only merges the two lines, then 0072, which adds what
Testing-Env has more (`folder_uploads.items_counted`, set to `items_registered` for uploads
already made, and three column comments). The uploads and limits stay. Nothing is done by hand:
no `alembic stamp`. The test server is at 0071 as #509 merged it (the same folder tables, run
after 0070), so it gets 0072 and what follows, and the same schema; a fresh database gets 0061c,
0071, 0072. After 0072 come 0073 (Ultra low cost up to 55 paise a page, #488) and 0074 (Google
sign-in by organisation and role, #507), the same on every server.
A downgrade across 0071 names its target (`alembic downgrade 0070`, which keeps 0061c and its
tables): alembic refuses `downgrade -1` from a merge as an ambiguous walk.

**Limited database logins** (#479, migration 0067, Vrushit's approval). 0067 makes
`kaagaz_api` (api and worker) and `kaagaz_ops` (api's super admin part) with no password,
so nothing changes on release day: the app keeps the owner's login until `.env` names the
new ones. Test server first for a few days, then a full restore drill (section 6), then
production at a quiet time:

1. Each password, random (`openssl rand -hex 24`), typed at the prompt, never on a command
   line: `dc exec postgres psql -U <POSTGRES_USER> -d <POSTGRES_DB>`, then
   `\password kaagaz_api` and `\password kaagaz_ops`.
2. In `.env`: `APP_DATABASE_URL=postgresql://kaagaz_api:<its password>@postgres:5432/<POSTGRES_DB>`
   and `OPS_DATABASE_URL=postgresql://kaagaz_ops:<its password>@postgres:5432/<POSTGRES_DB>`.
   `docker-compose.yml` gives api and worker the first as `DATABASE_URL`, only api the
   second, only migrate the owner's, and none of them `POSTGRES_PASSWORD`. Set both together:
   with the first alone, api stops at start-up with one `operator_login_failed` line.
3. `dc up -d --no-deps api worker`. Check: `dc exec api printenv POSTGRES_PASSWORD` prints an
   empty line; `dc exec postgres psql -U <POSTGRES_USER> -d <POSTGRES_DB> -c "SELECT usename,
   count(*) FROM pg_stat_activity WHERE datname = current_database() GROUP BY 1"` shows
   `kaagaz_api` (and `kaagaz_ops` once the console is used); sign in, open a document, ask a
   question, upload one, and open the operator console.
4. From then on the owner-only commands run in migrate, the one container with the owner's
   login: `dc run --rm migrate python -m kaagaz.cli <command>` for `create-customer`,
   `seed-demo`, `disable-customer`, `enable-customer`, `purge-customer`, `create-operator`,
   `reset-operator-2fa`, `unlock-operator` and `verify-models --record`. In api, PostgreSQL
   refuses them.
5. `kaagaz_api` may hold 80 connections: api, 3 workers and one CLI run, 15 each. Before
   `WORKER_REPLICAS` goes above 3, `ALTER ROLE kaagaz_api CONNECTION LIMIT <15 x (replicas + 2)>`.

**The undo:** delete the two lines from `.env` and `dc up -d --no-deps api worker`; the app
logs in as the owner again. The logins stay and do nothing.

**The workers' own login** (#587, migration 0083, Vrushit's approval). 0083 makes the role
`kaagaz_worker`, with exactly `kaagaz_app`'s rights, and the login `kaagaz_jobs`, which may
only switch into it, with no password: the workers keep their login and role until `.env`
names `kaagaz_jobs`. Test server first, then production:

1. Its password, as in step 1 above: `\password kaagaz_jobs` in psql as the owner (on Amazon
   RDS, its master user), typed at the prompt.
2. In `.env`: `JOBS_DATABASE_URL=postgresql://kaagaz_jobs:<its password>@<host:port of
   APP_DATABASE_URL>/<POSTGRES_DB>`. `docker-compose.yml` gives it only to worker, as its
   `DATABASE_URL`, and with it `DB_WORKER_ROLE=kaagaz_worker`; api and migrate get neither.
   Never set `DB_WORKER_ROLE` in `.env`: the login and the role change together.
3. `dc up -d --no-deps worker`. Check: the `pg_stat_activity` query of step 3 above shows
   `kaagaz_jobs`; `dc logs --tail 50 worker` shows no `permission denied`; an upload reaches Ready.
4. From then on the CLI's work commands run in worker, as the workers do:
   `dc run --rm worker python -m kaagaz.cli <command>`. Until this step, `dc run --rm migrate python -m
   kaagaz.cli <command>`; in api they are refused (PRD 7, #606: the CLI runs as `kaagaz_worker`,
   which `kaagaz_api` can't switch into).
5. The connections, PROVISIONAL: `kaagaz_jobs` may hold 65 (3 workers and one CLI run, 15 each,
   and 5 to spare). Before `WORKER_REPLICAS` goes above 3,
   `ALTER ROLE kaagaz_jobs CONNECTION LIMIT <15 x (replicas + 1) + 5>`. Only once EVERY server
   that shares this database has moved its workers, `ALTER ROLE kaagaz_api CONNECTION LIMIT 35`
   (api and one CLI run). These numbers assume one app server per database server. The test
   server and production share one RDS database today, so do not lower it until #577 is done.

**The undo:** first `ALTER ROLE kaagaz_api CONNECTION LIMIT 80` if step 5 lowered it, then delete
the `JOBS_DATABASE_URL` line from `.env` and `dc up -d --no-deps worker`; the workers are back on
`kaagaz_api`. Do this BEFORE any `alembic downgrade 0082`: the downgrade takes every right from
`kaagaz_worker`, and workers still on it would fail.

**The per-person database rule** (PRD 7 PR 9a, #606, migration 0091, Vrushit's approval). 0091
ships it off: nothing changes until it is turned on, one organisation at a time. Never before all
of: #577 (the test server has its own database), both 0091 and 0092 (PR 9b) deployed, and the
steps above done (the API on `kaagaz_api`, the workers on `kaagaz_jobs`). Test server first:

1. In `.env`: `DB_LOGIN_CHECK=refuse`, then `dc up -d --no-deps api worker`. The API and each
   worker now refuse to start on a login the rule can't hold (the owner, a superuser, or one that
   can switch into `kaagaz_worker` or `kaagaz_operator`); `dc logs --tail 50 api worker` says which.
2. `dc run --rm migrate python -m kaagaz.cli person-rule status` (the owner's login: only it may
   touch the switch). It says `off`.
3. `... person-rule on --customer <the organisation's id>`. It refuses while any worker report of
   the last 10 minutes came from another role than `kaagaz_worker`, or `kaagaz_api` can switch
   into it. It logs and sends an ops alert. Then the red team (PRD 7 section 11).
4. Every organisation: `... person-rule on --all`. Production only after phase 8, the same way.

**The way back, at once:** `... person-rule off --all` (or `--customer <id>`): no deploy, no
migration, never refused; the views still keep each person to what they may see. Turn it off
before rolling the code back past PR 9a: older code reads hidden rows as gone.

**The closed lines and the storage keys (#477), by hand at a quiet time.** The
release that adds the `data`, `scan` and `edge` networks moves postgres and
seaweedfs to `data` in deploy step 2 (`run migrate` recreates them), while the
old api and worker are still on the old network: the site is down until step 3,
a minute or two. The same release also brings the website health check and
Node 24, so a failure in step 6 may be any of the three: follow these steps for
it, on the test server first, then on production. As `ubuntu`:

1. Pause the deploys: `sudo systemctl stop kaagaz-deploy.timer`.
2. Keep the server on: `sudo touch /etc/kaagaz/no-idle-stop`.
3. The keys, before the code is pulled. In `~/kaagaz-secrets/s3.json` keep the app
   identity's keys and set its `actions` to `["Read:<STORAGE_BUCKET>",
   "Write:<STORAGE_BUCKET>", "List:<STORAGE_BUCKET>"]`; add a second identity
   with new random keys and `["Admin"]`, and put those keys in `.env` as
   `STORAGE_ADMIN_ACCESS_KEY` and `STORAGE_ADMIN_SECRET_KEY` (secrets table
   above). Then `dc up -d --no-deps --force-recreate seaweedfs` and check the
   site still uploads and opens a document: the running release uses the app key
   only. Without the admin pair, the deploy's migrate stops with `set
   STORAGE_ADMIN_ACCESS_KEY and STORAGE_ADMIN_SECRET_KEY in .env` and changes nothing.
4. A backup: `~/kaagaz/infra/ec2/pgdump.sh`.
5. Once `sudo ~/kaagaz/infra/ec2/idle-stop.sh --dry-run` shows both queues
   empty, stop the workers: `dc stop worker`.
6. With the release on the branch the server deploys, deploy:
   `sudo systemctl start kaagaz-deploy.service`;
   `journalctl -u kaagaz-deploy -n 20` ends `<commit> deployed`. Check: `dc ps`
   shows every service healthy, `docker network ls` lists `kaagaz-ocr_data`,
   `kaagaz-ocr_scan` and `kaagaz-ocr_edge`, and an upload reaches Ready and a
   question is answered. If it ends `failed` or `rolled back to <commit>`
   instead, some services may still be on the old network: put every one on the
   new lines with `dc up -d --no-deps api worker frontend caddy`, then read
   `journalctl -u kaagaz-deploy -n 50` and `dc logs --tail 50 api frontend`.
7. `docker network rm kaagaz-ocr_default`, `sudo rm /etc/kaagaz/no-idle-stop`,
   and `sudo systemctl start kaagaz-deploy.timer`.

The undo is a revert commit of that release on `production` (by pull request),
deployed with steps 1, 2, 5 and 6: `deploy.sh --rollback` only swaps images and
never moves a container back to the old network. The narrowed `s3.json` stays:
the release before it works with it (its migrate only finds the bucket).

**By hand, on a server that runs another branch** (the test server on Testing-Env, whose deploy timer stays off, 4 Nov 2026). Steps 1 to 5 as above, then, with `dc` as before:

1. **Get the code.** `git fetch origin <branch>` and `git checkout -B <branch> origin/<branch>`.
2. **Build.** `dc build migrate frontend`. Run it with `nohup … &` if the session may time out.
3. **Move the data services onto the new networks first.** `dc up -d --no-deps postgres seaweedfs elasticmq clamav`.
4. **Migrate.** `dc run --rm migrate`.
5. **Start the app.** `dc up -d --no-deps --force-recreate api worker frontend caddy`.

Starting the app before step 3 leaves api on the new `data` network and postgres on the old one. Api then stops at start-up with `database_unreachable`.

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
(`kaagaz-jobs` and `kaagaz-jobs-express`, asked from Amazon SQS) have nothing waiting, being read or waiting to retry, no deploy holds `/tmp/kaagaz-deploy.lock`,
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

**Alerts (#480).** Three pieces, all email:

* The monitor: the Lambda `kaagaz-monitor` (Python 3.12, `infra/aws/monitor/handler.py`),
  every 5 minutes. Once the server has run 10 minutes it checks `/api/health` and `/` over
  HTTPS and that the certificate has 14 days or more left; once it has run an hour, that the
  newest backup run's `done` is under 26 hours old; on weekdays from 09:45 to 10:00 IST, that the
  schedule started it. A check failing twice in a row sends one email through SNS, naming
  the check and why (an HTTP status or an error name); a pass sends one more. The counts are
  one SSM parameter, `/kaagaz/monitor/failures` (free; a CloudWatch alarm per check would
  cost money and could not say why). Set up once, by Vrushit, in ap-south-1:
  ```
  aws sns create-topic --name kaagaz-alerts --query TopicArn --output text
  aws sns subscribe --topic-arn <TOPIC_ARN> --protocol email --notification-endpoint <address>   # then confirm the email's link
  aws iam create-role --role-name kaagaz-monitor --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  aws iam attach-role-policy --role-name kaagaz-monitor --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  aws iam put-role-policy --role-name kaagaz-monitor --policy-name kaagaz-monitor --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"ec2:DescribeInstances","Resource":"*"},{"Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::<BUCKET>"},{"Effect":"Allow","Action":["ssm:GetParameter","ssm:PutParameter"],"Resource":"arn:aws:ssm:ap-south-1:<ACCOUNT>:parameter/kaagaz/monitor/failures"},{"Effect":"Allow","Action":"sns:Publish","Resource":"<TOPIC_ARN>"}]}'
  (cd infra/aws/monitor && python -m zipfile -c /tmp/monitor.zip handler.py)
  aws lambda create-function --function-name kaagaz-monitor --runtime python3.12 --handler handler.handler --timeout 60 --role arn:aws:iam::<ACCOUNT>:role/kaagaz-monitor --zip-file fileb:///tmp/monitor.zip --environment 'Variables={INSTANCE_ID=<INSTANCE_ID>,SITE_DOMAIN=<DOMAIN>,BACKUP_BUCKET=<BUCKET>,TOPIC_ARN=<TOPIC_ARN>}'
  aws lambda put-function-concurrency --function-name kaagaz-monitor --reserved-concurrent-executions 1
  aws lambda put-function-event-invoke-config --function-name kaagaz-monitor --maximum-retry-attempts 0 --destination-config 'OnFailure={Destination=<TOPIC_ARN>}'
  aws events put-rule --name kaagaz-monitor --schedule-expression 'rate(5 minutes)' --query RuleArn --output text
  aws lambda add-permission --function-name kaagaz-monitor --statement-id kaagaz-monitor-schedule --action lambda:InvokeFunction --principal events.amazonaws.com --source-arn <RULE_ARN>
  aws events put-targets --rule kaagaz-monitor --targets Id=monitor,Arn=<FUNCTION_ARN>
  ```
  The failure destination emails when the monitor itself fails to run. Check:
  `aws lambda invoke --function-name kaagaz-monitor out.json` writes `{"alerted": false}`;
  on the test server, `dc stop frontend` for 10 minutes brings one email, and
  `dc up -d --no-deps frontend` a second. A new version: zip again and
  `aws lambda update-function-code --function-name kaagaz-monitor --zip-file fileb:///tmp/monitor.zip`.
* The disk: `kaagaz-disk.timer` runs `infra/ec2/disk-alert.sh` hourly; over 80% used on `/`
  it emails `OPS_ALERT_EMAIL` (set it in `.env`) from `EMAIL_FROM` through SES, at most once a
  day (`~/.kaagaz-disk-alert` holds the day). No CloudWatch agent. It runs the backend image
  on the host network with `.env` (`docker run --env-file`, which keeps quotes, so write
  `OPS_ALERT_EMAIL` and `EMAIL_FROM` unquoted), so it reaches the server's role with the
  instance metadata hop limit left at 1. When the email cannot go, the journal says
  `DISK <n>% USED and the alert email was NOT sent` under the reason, and
  `systemctl --failed` lists `kaagaz-disk.service`.
  While SES is in its sandbox, the address must be verified too. Install:
  `sudo cp ~/kaagaz/infra/ec2/kaagaz-disk.* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now kaagaz-disk.timer`.
  Check: `docker run --rm --network host --env-file .env kaagaz-backend:local python -m kaagaz.cli disk-alert --used 81`
  prints `disk alert sent` and the email arrives; on a test box, `fallocate -l <GB>G ~/fill` past 80%
  then `sudo systemctl start kaagaz-disk.service` sends it, and `rm ~/fill`.
* The reading pause (#478): the worker emails `OPS_ALERT_EMAIL` through SES with the server's
  role (no AWS key on the server), from Compose's bridge network, so the role is reached only
  with the instance metadata hop limit at 2 (the api's people emails too). Check:
  `dc exec worker python -c "import boto3;print(boto3.Session().get_credentials())"` prints a
  credentials object, not `None`. If `None`, from a laptop signed in as an admin:
  `aws ec2 modify-instance-metadata-options --instance-id <INSTANCE_ID> --http-put-response-hop-limit 2 --http-endpoint enabled`,
  then `dc restart api worker`. A send that fails logs one `ops_alert_failed` line with SES's
  code or the error's type (`NoCredentialsError`: the role was not reached).

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
* **10.7** A made-up 95 MB PDF uploads, and a 110 MB one is refused with the
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

**When CI's `npm audit` fails (#481).** `npm audit --omit=dev --audit-level=high` fails a pull
request on a high or critical advisory in the website's own libraries. First `npm audit fix`, or
a newer version of the library that brings it in; if only a nested library has the fix, pin that
version under `overrides` in `frontend/package.json`. When no fixed version exists yet, and the
advisory's code is not reachable from the website, a pull request may lower that CI step to
`--audit-level=critical`, with a comment above it naming the advisory (its GHSA id), why it does
not reach the app, the date, and a recheck date at most 14 days on; Vrushit reviews it. While it
is lowered, run `npm audit --omit=dev` by hand on every frontend change, as other high findings
no longer fail CI. On the recheck date, or as soon as a fix ships, the step goes back to `high`.
If the code is reachable, the library is removed or replaced instead.

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

**4a. Ways to sign in with Google (#457 phase 1)**
- Set up: in Google Cloud, a project with the External consent screen (scopes `openid email profile`, test users named while it is in Testing) and an OAuth client of type Web application whose redirect URI is `https://<site>/api/auth/oidc/google/callback` (http only on localhost). Then in `.env`: `GOOGLE_SIGNIN_ENABLED=true`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_REDIRECT_URI` (all three required while on; the secret is never logged).
- Check (screen): Account, Ways to sign in, Connect, type the password (and the code with two-step on), pick an account at Google. Sign out, then Continue with Google on /login. As admin, Admin, Users: Connections to approve, Approve or Decline, and a row's Remove connected accounts; Admin, Settings: the auto-approval switch.
- Expect: a member's Google with another email says "Sent to your admin for approval." and cannot sign in until approved; the same email (verified by Google) connects at once. An unknown Google account says it isn't connected. Anyone with codes on types the code after Google.
- Tests (a made-up Google, `backend/tests/oidc_fake.py`, no network): `test_oidc.py`, `test_api_oidc_signin.py`, `test_api_oidc_connect.py`, `test_api_admin_identities.py`, `test_isolation_*identities.py`; `frontend/tests/provider-sign-in.test.ts`, `ways-to-sign-in.test.ts`, `admin-connections.test.ts`.

**4b. Google Analytics (#457 phase 4)**
- Set up: `NEXT_PUBLIC_GA_ID` (a GA4 `G-` id) in `.env` before the frontend is built. Tracked: /login (not with `?step=code`), /signup and /forgot-password, after Accept analytics.
- Release: in the GA4 property, switch off Enhanced measurement's "Page changes based on browser history events" and "Form interactions", switch off Google signals and the ads features, and set data retention to 2 months.
- Tests: `frontend/tests/analytics.test.ts`.

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

**14b. Upload a whole folder (#484)**
- Check (API): `POST $API/folder-uploads` with the folder list, then `POST $API/folder-uploads/<id>/items` (pages of 1,000), then each file to `POST $API/documents?folder_upload_item_id=<item>`; `GET $API/folder-uploads/<id>` for progress.
- Expect: the same folders, at any depth up to 100; each file in its own folder, never auto-filed; a retry or a second send of an item stores nothing new; a deleted folder gives `folder_gone`, never the top level; an unsafe file name is `bad_name`; another user's upload while one still sends (active in the last 30 minutes) is 409 `upload_in_progress`. Limits: `GET/PUT /api/operator/folder-upload-limits` (a fresh code; at most 20 GB). Sends at once (`FOLDER_UPLOAD_FILES_AT_ONCE_PER_ORG` 4, `FOLDER_UPLOAD_FILES_AT_ONCE` 8) are counted per API process, not per server. In an organisation whose reading is paused (#478), the upload's progress counts its held files as `held` ("waiting" in the tray, with the pause's words), never reading; files not sent are "not sent yet".
- Tests: `test_api_folder_uploads.py`, `test_api_folder_upload_files.py`, `test_folder_uploads_plan_db.py`, `test_folder_upload_reading_pause_db.py`, `test_isolation_*folder_uploads.py`.
- Load test (not in CI, hours). Laptop stack only, without `OPENROUTER_API_KEY`; never run it on the test server. Make two organisations for it (`python -m kaagaz.cli create-customer`, then `create-user` for a member of each): the second one's member is the probe. From `backend/` (`.venv/Scripts/python.exe` on Windows, `.venv/bin/python` elsewhere): `LOADTEST_PASSWORD=... PROBE_PASSWORD=... .venv/Scripts/python.exe -m scripts.load_folder_upload --email <member> --password-env LOADTEST_PASSWORD --probe-email <member of the second organisation> --probe-password-env PROBE_PASSWORD`. It prints PASS or FAIL for each bar. Clean-up: delete both organisations in the operator console, then `python -m kaagaz.cli purge-customer --customer-id <id> --now` for each.

**14c. Storage limit per organisation (#516)**
- Check (API): as the operator, `PUT $API/operator/orgs/<id>/storage-limit` with `{"storage_gb": 1, "code": "<a fresh code>"}`; as that organisation's admin, `GET $API/admin/storage`; then upload files until it is full.
- Expect: `{used_bytes, limit_bytes, limit_gb}` for the admin; the first file past 1 GB is 413 `storage_full` with its words and `details`, at the upload (a resumable one at its complete) or the folder upload plan; the same file again (a duplicate) is still answered; emptying the Trash (delete forever) makes room. `{"storage_gb": null}` follows the platform (`/api/operator/storage-limit`, null: 50 GB).
- Tests: `test_api_storage_limit.py`, `test_upload_storage_limit.py`, `test_operator_storage_limit_db.py`, `test_migration_0061d_db.py`, `test_isolation_storage_limits.py`.

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
- Expect: the first gives `route:"search"` with `kind:"piece"` citations (exact words, typo-tolerant and, with a model, meaning search are merged). The second gives `status:"not_found"`, the not-found line ("No document searched has the words “weather” or “Delhi”." when no document has them, else "No answer to the whole question was found. These documents match some of your words.") and any closest documents. With a model on, any sentence citing nothing, or with a number that is not in its evidence, is dropped, and links and markup are stripped.
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
- Check: `curl -b <admin jar> "$API/admin/audit?limit=10"`, `$API/admin/stats` and `"$API/admin/members?period=30d"` (`month`, `30d` or `all`); as the operator, `"$API/operator/org-numbers?days=30"`.
- Expect: the audit log newest first, with names and actions such as `auth.login`, `document.upload`, `chat.ask`, `review.resolve`, `user.create`; the stats count documents by status, questions, answers found and not found, feedback and open review items, for this customer only; members lists this customer's people with files uploaded, pages read, questions asked, values verified and last active, no money; org-numbers gives each organisation's files, pages read by route, failed files by reason, active people, questions and AI use.
- Tests: `test_api_admin.py`, `test_analytics_recount_db.py`, `test_isolation_member_numbers.py`, `test_isolation_org_numbers.py`.
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
- Measure (PRD 2 X4): from `backend/` with `.env` loaded, `python -m kaagaz.cli measure --file <a local file> [--truth <folder of 1.txt, 2.txt, ...>]`. It prints each page's route and why, the route check's numbers and the seconds, then the totals; no text and no file name. With `--truth`, each page adds CER, WER and the value error rate (and field matches from an optional `n.fields.json`), and a `truth` line adds them up; building a truth set and comparing two settings: docs/architecture.md, "Measure true accuracy with a truth set". It uses no database, storage or queue, and it skips the virus scan, so use it only on files you trust. Tests: `test_cli_measure.py`.

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

**Restore**, the reverse, into a fresh stack: a new laptop with the same `.env`, or this one under another project name, which gets its own empty volumes (`docker compose stop` the main stack first to free its ports). The containers are created but not started, so the files go in before SeaweedFS first runs; the copy is owned by root, so the `chown` hands it back to the image's `seaweed` user. `pg_dump` leaves out roles, so the app, operator and worker roles are made first, as migrations 0001, 0015 and 0083 make them, and the logins of 0067 and 0083 and 0067's refusal of temporary tables after (#479, #587; set their passwords as in "Limited database logins" if the drill's `.env` names them):

```
docker compose -p kaagaz-drill create --build
docker compose -p kaagaz-drill cp ../kaagaz-backup/backup-files/. seaweedfs:/data
docker compose -p kaagaz-drill run --rm --no-deps --entrypoint chown seaweedfs -R seaweed:seaweed /data
docker compose -p kaagaz-drill up -d postgres
docker compose -p kaagaz-drill exec -T postgres psql -U kaagaz -d kaagaz -c "CREATE ROLE kaagaz_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE" -c "GRANT kaagaz_app TO kaagaz" -c "CREATE ROLE kaagaz_operator NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE" -c "GRANT kaagaz_operator TO kaagaz" -c "CREATE ROLE kaagaz_worker NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE" -c "GRANT kaagaz_worker TO kaagaz"
docker compose -p kaagaz-drill cp ../kaagaz-backup/kaagaz.dump postgres:/tmp/kaagaz.dump
docker compose -p kaagaz-drill exec -T postgres pg_restore -U kaagaz -d kaagaz --exit-on-error /tmp/kaagaz.dump
docker compose -p kaagaz-drill exec -T postgres psql -U kaagaz -d kaagaz -c "CREATE ROLE kaagaz_api LOGIN NOINHERIT CONNECTION LIMIT 80" -c "CREATE ROLE kaagaz_ops LOGIN NOINHERIT CONNECTION LIMIT 15" -c "CREATE ROLE kaagaz_jobs LOGIN NOINHERIT CONNECTION LIMIT 65" -c "GRANT kaagaz_app TO kaagaz_api WITH INHERIT FALSE, SET TRUE" -c "GRANT kaagaz_operator TO kaagaz_ops WITH INHERIT FALSE, SET TRUE" -c "GRANT kaagaz_worker TO kaagaz_jobs WITH INHERIT FALSE, SET TRUE" -c "REVOKE TEMPORARY ON DATABASE kaagaz FROM PUBLIC"
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

### Backups off the server (#476)

Each night, after its local dump, `infra/ec2/pgdump.sh` streams three parts into an S3 bucket in ap-south-1, under a folder of its own for each run, `<run>/` (`YYYYMMDDTHHMMSSZ`, so a second run never writes over the first): `kaagaz.dump` (that dump), `roles.sql` (the logins, `pg_dumpall --roles-only --no-role-passwords`) and `files.tar` (every stored file, read through the S3 API by `kaagaz.cli backup-files`, so SeaweedFS keeps running; a read of no files fails the run). Each part is encrypted on the server as it streams, with `openssl enc -aes-256-ctr` and its own random 256-bit key, and goes up as `<part>.enc`; only that key is sealed with `openssl cms` to a certificate whose private key never touches the server, as `<part>.key.cms`. (`openssl cms -decrypt` holds its whole input in memory: 400 MB took 1.07 GB, so a large part sealed with it could not be restored.) Nothing encrypted is written to the disk. `<run>/done` goes last, only when every part is in: one line per part (its object, size and sha256), then `files <count>`. The upload runs `kaagaz.cli backup-upload` in the backend image on the host network, with the server's own role (no AWS key on the server), and never over an object already there. Without `BACKUP_BUCKET` and `BACKUP_CERT` in `.env` it stops after the local dump. The run holds the deploy lock, so no deploy or idle stop cuts it. `journalctl -u kaagaz-pgdump -n 20` ends `copied backup <run> off the server (<count> files)`; the monitor (section 2c, "Alerts") emails when no `done` is under 26 hours old.

**Once, by Vrushit** (AWS account admin; `<BUCKET>`, `<ACCOUNT>` and `<INSTANCE_ROLE>`, the server's role, are yours):

1. The key pair, on an offline machine. It asks for a passphrase; keep `kaagaz-backup-key.pem` and the passphrase in the password manager and one offline copy, never on the server: without it no backup can be read.
   `openssl req -x509 -newkey rsa:4096 -days 3650 -subj "/CN=kaagaz-backup" -keyout kaagaz-backup-key.pem -out kaagaz-backup-cert.pem`.
   Copy only `kaagaz-backup-cert.pem` to the server as `/home/ubuntu/kaagaz-secrets/backup-cert.pem`.
2. The KMS key and the bucket: Object Lock in compliance mode, 30 days (nobody, root included, deletes a backup early), SSE-KMS, no public access, and old backups gone after 35 days:
   ```
   aws kms create-key --region ap-south-1 --description "Kaagaz backups" --query KeyMetadata.Arn --output text
   aws s3api create-bucket --region ap-south-1 --bucket <BUCKET> --create-bucket-configuration LocationConstraint=ap-south-1 --object-lock-enabled-for-bucket
   aws s3api put-object-lock-configuration --bucket <BUCKET> --object-lock-configuration '{"ObjectLockEnabled":"Enabled","Rule":{"DefaultRetention":{"Mode":"COMPLIANCE","Days":30}}}'
   aws s3api put-bucket-encryption --bucket <BUCKET> --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms","KMSMasterKeyID":"<KEY_ARN>"},"BucketKeyEnabled":true}]}'
   aws s3api put-public-access-block --bucket <BUCKET> --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
   aws s3api put-bucket-lifecycle-configuration --bucket <BUCKET> --lifecycle-configuration '{"Rules":[{"ID":"kaagaz-35-days","Status":"Enabled","Filter":{},"Expiration":{"Days":35},"NoncurrentVersionExpiration":{"NoncurrentDays":1},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":2}}]}'
   ```
3. Write-only for the server: this inline policy on `<INSTANCE_ROLE>` (`aws iam put-role-policy --role-name <INSTANCE_ROLE> --policy-name kaagaz-backup-write --policy-document file://backup-write.json`). It may add backups and nothing else: no read, no list, no delete. `kms:Decrypt` is what S3 needs to finish a multipart upload with this key; without `s3:GetObject` it reads nothing.
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {"Sid": "AddBackupsOnly", "Effect": "Allow", "Action": ["s3:PutObject", "s3:AbortMultipartUpload"], "Resource": "arn:aws:s3:::<BUCKET>/*"},
       {"Sid": "EncryptWithTheBackupKey", "Effect": "Allow", "Action": ["kms:GenerateDataKey", "kms:Decrypt"], "Resource": "<KEY_ARN>"}
     ]
   }
   ```
   And write-once, on the bucket (`aws s3api put-bucket-policy --bucket <BUCKET> --policy file://backup-write-once.json`): a new object only, never one over an existing key, whoever writes. `kaagaz.cli backup-upload` sends `If-None-Match: *` on every object it makes; `s3:ObjectCreationOperation` leaves the parts of a multipart upload alone, as only its last step creates the object.
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {"Sid": "WriteOnce", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject", "Resource": "arn:aws:s3:::<BUCKET>/*",
        "Condition": {"StringNotEquals": {"s3:if-none-match": "*"}, "Bool": {"s3:ObjectCreationOperation": "true"}}}
     ]
   }
   ```
   The restore drill and a real restore need `s3:GetObject` and `s3:ListBucket` on the bucket and `kms:Decrypt` on the key: an admin's own sign-in, never the server's role, which can neither read nor list the bucket.
4. In the server's `.env`: `BACKUP_BUCKET=<BUCKET>` and `BACKUP_CERT=/home/ubuntu/kaagaz-secrets/backup-cert.pem`. Run `~/kaagaz/infra/ec2/pgdump.sh` once; then, signed in as an admin on a laptop (the server's role cannot list), `aws s3 ls s3://<BUCKET>/` shows the run's folder and `aws s3 ls s3://<BUCKET>/<run>/` its seven objects. A new server with no uploads yet fails here, as a read of no files is taken for a broken store: upload one document first.
5. The second region: the daily snapshot copied to Hyderabad (ap-south-2, an opt-in region) and kept 7 days there, and a Recycle Bin rule in each region, so a deleted snapshot can be brought back for 7 days (the rule itself locked for 7):
   ```
   aws account enable-region --region-name ap-south-2
   aws dlm get-lifecycle-policies --region ap-south-1 --query 'Policies[].[PolicyId,Description]'
   aws dlm get-lifecycle-policy --region ap-south-1 --policy-id <POLICY_ID> --query Policy.PolicyDetails > dlm-details.json
   ```
   In `dlm-details.json`, add to `Schedules[0]`: `"CrossRegionCopyRules": [{"Target": "ap-south-2", "Encrypted": true, "CopyTags": true, "RetainRule": {"Interval": 7, "IntervalUnit": "DAYS"}}]` (encrypted with ap-south-2's default EBS key). Then:
   ```
   aws dlm update-lifecycle-policy --region ap-south-1 --policy-id <POLICY_ID> --policy-details file://dlm-details.json
   aws rbin create-rule --region ap-south-1 --resource-type EBS_SNAPSHOT --description "Kaagaz: deleted snapshots kept 7 days" --retention-period RetentionPeriodValue=7,RetentionPeriodUnit=DAYS --lock-configuration 'UnlockDelay={UnlockDelayValue=7,UnlockDelayUnit=DAYS}'
   aws rbin create-rule --region ap-south-2 --resource-type EBS_SNAPSHOT --description "Kaagaz: deleted snapshots kept 7 days" --retention-period RetentionPeriodValue=7,RetentionPeriodUnit=DAYS --lock-configuration 'UnlockDelay={UnlockDelayValue=7,UnlockDelayUnit=DAYS}'
   ```
   The next day, `aws ec2 describe-snapshots --region ap-south-2 --owner-ids self` shows the copy.

**The restore drill, monthly and after any change to the database logins.** On a Linux or macOS laptop or a throwaway machine (not Git Bash: it rewrites the paths), never the production server, with Docker, the aws CLI signed in as above, and the private key: from a checkout, `docker compose build migrate` (it makes `kaagaz-backend:local`), then

```
aws s3 ls s3://<BUCKET>/                      # the runs; take last night's, YYYYMMDDTHHMMSSZ
infra/ec2/restore-drill.sh <BUCKET> <run> /path/to/kaagaz-backup-key.pem
```

It reads `<run>/done`, opens each part's sealed key (openssl asks for the passphrase, once per part), streams the part down and decrypts it as it reads, so memory stays small at any size, and checks its size and sha256 against `done`; the plain copies go into a private temporary folder. It then restores the logins (any SQL error stops it), the database and the files into a throwaway `pgvector/pgvector:0.8.7-pg17` and `chrislusf/seaweedfs:4.48` on their own network, then checks one document through the customer filter (as `kaagaz_app`, its customer set): its row, its original and its first page image. It ends `DRILL PASS <run>` and removes everything it made; anything else ends `DRILL FAIL: <why>`. Post its output (ids and counts only) with the date as a comment on #476; a failure is fixed before anything else.
