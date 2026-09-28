
REST API for the "Uyên Thư Các" personal comic bookshelf. The frontend is in ../BeMeoLac.
The spec is api-contract.md: paths, request and response shapes must match it exactly.

## Stack
Python 3.12, FastAPI, SQLAlchemy 2.0 (async, asyncpg) + Alembic, PostgreSQL on Neon,
Pydantic v2 + pydantic-settings, JWT (access + refresh tokens), argon2 password hashing,
pytest + httpx for tests. Package manager: uv. Config from environment variables (.env, never committed).

## Structure
app/main.py, app/core (config, security, db), app/models, app/schemas, app/api/routes,
app/services, alembic/, tests/.

## Deployment target
Vercel Functions (serverless, Python runtime) + Neon Postgres (free plan).
- Stateless: no in-memory state, no local files, no background schedulers (no APScheduler).
- Scheduled jobs are protected HTTP endpoints (CRON_SECRET) called by Vercel Cron once a day;
  they must be idempotent and process work in small batches (well under 300s).
- The app uses DATABASE_URL (Neon pooled), with a small pool suitable for serverless.
  Alembic migrations use DATABASE_URL_DIRECT (non-pooled).
- Images (covers, avatars) are stored as small WebP thumbnails in the database.

## Rules
- Every comic, shelf, tag and notification belongs to one user; always filter by the current user.
- Reading progress lives on comics, not on sources.
- Error format: { "detail": str, "code": str | None }. Pagination: items, total, page, page_size.
- Messages shown to users are in Vietnamese.
- Every new endpoint has at least one test.

## Before finishing any task
Run the tests and fix all failures. Do not change the frontend unless asked.