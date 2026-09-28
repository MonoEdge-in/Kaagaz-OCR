# Running Kaagaz on a laptop

Two ways: everything in Docker, or the stand-ins in Docker and the API,
worker and web app run straight on the laptop. Every key and password in
`.env.example`, `docker-compose.yml` and `infra/` is a **laptop-only
placeholder**, never a real secret.

## What runs where

| Service | Port | What it is | In AWS |
|---|---|---|---|
| `frontend` | 3000 | Next.js web app (open this) | the same image |
| `api` | 8000 | FastAPI, `/api/*` | the same image |
| `worker` | none | `python -m kaagaz.worker_main`, one job at a time | the same image |
| `migrate` | none | one-shot: `alembic upgrade head`, then makes the storage bucket | a deploy step |
| `postgres` | 5432 | PostgreSQL 17 with pgvector | Amazon RDS |
| `seaweedfs` | 8333 | file storage, S3 API | Amazon S3 |
| `elasticmq` | 9324 | job queue `kaagaz-jobs` and its failure bin `kaagaz-jobs-failed`, SQS API | Amazon SQS |
| `clamav` | 3310 | clamd virus scanner | ClamAV (PROVISIONAL) |
| `ollama` | 11434 | local AI, optional (`--profile ollama`) | PROVISIONAL |

## 1. Settings

```
cp .env.example .env                 # bash
Copy-Item .env.example .env          # PowerShell
```

Then set `SESSION_SECRET` in `.env` to at least 32 random characters. The
placeholder is too short on purpose, so nothing starts until you do:

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`MODEL_ENABLED=false` (the default) runs everything with no AI. To use
Ollama, pull `MODEL_CHAT` and `MODEL_EMBED` first, then set it to `true`.

## 2a. Everything in Docker

```
docker compose up --build
```

Open http://localhost:3000.

* The first start of `clamav` can take several minutes while it updates its
  signatures. The worker waits for it; the web app and API work meanwhile.
* The frontend image bakes in `BACKEND_URL=http://api:8000` when it is built
  (Next.js writes the `/api` rewrite into the build).
* The containers reach Ollama on the laptop at `host.docker.internal:11434`.
  To run Ollama in Docker instead, set `DOCKER_MODEL_BASE_URL=http://ollama:11434`
  in `.env` and run `docker compose --profile ollama up --build`, then pull the
  models once: `docker compose exec ollama ollama pull <model>`.
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
alembic upgrade head
uvicorn kaagaz.api.main:app --port 8000
python -m kaagaz.worker_main          # in a second terminal
```

The worker makes the storage bucket on its first start if it is missing.
Ctrl+C stops it after the job in hand.

And the web app, from `frontend/`:

```
npm run dev                            # http://localhost:3000
```

It forwards `/api/*` to `BACKEND_URL` (default `http://localhost:8000`).

## 3. Demo customers

With the database migrated, from `backend/` (or `docker compose exec api` in
front of it):

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
.venv/Scripts/python.exe -m pytest -m isolation      # customer separation first
.venv/Scripts/python.exe -m pytest -m "not db"       # no database needed
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
