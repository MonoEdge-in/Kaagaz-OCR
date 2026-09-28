# Kaagaz frontend

The screens people use: sign in, upload and list documents, ask questions with cited answers, review values, and admin. Next.js (App Router) with React; PDF pages are drawn with PDF.js. There is no CSS framework: shared classes live in `app/globals.css` and each component has a CSS module next to it.

The browser only ever talks to this app. `next.config.ts` forwards every `/api/*` request to the FastAPI backend (`BACKEND_URL`, default `http://localhost:8000`), so the session cookie stays first-party and no CORS is needed.

## Run it

Needs Node 20 or later. From `frontend/`:

```sh
npm ci          # once, installs exactly what package-lock.json lists
npm run dev     # http://localhost:3000
```

The backend must be running on port 8000 first (`uvicorn kaagaz.api.main:app --port 8000` from `backend/`; see `docs/running-locally.md` for the database, storage, queue and demo users). To point at a backend somewhere else:

```sh
BACKEND_URL=http://localhost:9000 npm run dev
```

## Checks

These are the same checks CI runs, all from `frontend/`:

| What | Command |
|---|---|
| Lint | `npm run lint` |
| Typecheck | `npm run typecheck` |
| Tests | `npm test` |
| Production build | `npm run build`, then `npm start` to serve it |

Tests are vitest in node (`tests/**/*.test.ts`). There is no browser test library, so the logic behind each screen (upload checks, when to poll, who may delete, formatting, region maths) lives in plain `.ts` files and is tested there; components stay thin.

## Where things are

| Path | What |
|---|---|
| `lib/api.ts`, `lib/types.ts` | Every backend call and the API types. The one place the app talks to the backend. |
| `lib/session.tsx` | `SessionProvider` and `useSession()`: who is signed in, for every screen inside the app. |
| `lib/format.ts` | Sizes, India-time dates, relative times, status chips. |
| `lib/theme.ts` | The Light / Dark / System choice, kept in a cookie the server reads, so the first paint is already in the right theme. |
| `app/login/` | The sign-in page: the brand panel, the form, and its pure rules (`signIn.ts`). |
| `app/(app)/layout.tsx` | The sign-in gate and the app shell for every signed-in screen. |
| `components/shell/` | The app shell: a sidebar from 1024 px, a top bar and menu drawer below that, both fed by the one `navLinks(role)` list. |
| `components/ui/` | Shared pieces with behaviour: `Icon`, `IconButton`, `PasswordField`, `Drawer`, `ThemeToggle`, `EmptyState`. Their pure rules sit beside them (`*Logic.ts`, `icons.ts`) and are tested. |
| `app/(app)/documents/` | Upload and the documents list; `[id]` is one document with its pages and values. |
| `app/(app)/chat/` | Ask a question; answers with sources. |
| `app/(app)/review/` | Values that need review. |
| `app/(app)/admin/` | Users, settings, activity and numbers (admins only). |
| `components/` | The other pieces each screen is built from, grouped by screen. |

## Rules that shape this code

- Document text, file names and every message from the server are shown as plain text. Never `dangerouslySetInnerHTML` or `innerHTML`.
- Hiding a button (Delete, Review, Admin) is only a hint. The server checks the role and the customer on every request.
- Colour is never the only signal: every label and status also says it in words ("Verified", "Needs review", "Failed").
- Colours come only from the tokens in `app/globals.css`, so dark mode works everywhere. `tests/guards.test.ts` fails on a hex colour anywhere else, on markup built from strings, and on emoji (use `Icon`).
- Sign-in fields have no `name` attribute and the form posts, so a password can never end up in an address bar; the password field turns off spellcheck and autocorrect and hides the password again on submit and when the tab is left.
