# API contract — Tủ Truyện Nhỏ (Uyên Thư Các)

This is the spec for the FastAPI backend. The frontend already calls exactly these
endpoints when `VITE_USE_MOCK=false`; every call lives in `src/services/`, and the
mock implementation in `src/mocks/api/` shows the expected behaviour.

TypeScript types referenced below are defined in [`src/types/index.ts`](../src/types/index.ts).
Field names are snake_case on the wire, exactly as in those types.

## Contents

- [Conventions](#conventions)
- [Auth](#auth)
- [Users (`/api/users/me`)](#users)
- [Comics](#comics)
- [Discover (search across the web)](#discover)
- [Library (saving)](#library)
- [Home ("Khu vườn hôm nay")](#home)
- [Shelves](#shelves)
- [Sources](#sources)
- [Tags](#tags)
- [Stats](#stats)
- [Notifications](#notifications)
- [Scheduled jobs](#scheduled-jobs)
- [Endpoint index](#endpoint-index)
- [Open points for the backend](#open-points-for-the-backend)

---

## Conventions

### Base URL and paths

- The frontend reads the backend origin from `VITE_API_URL` (for example `https://api.example.com`).
- Every path starts with `/api`.
- Path IDs are strings (the frontend URL-encodes them).

### Authentication

- Every endpoint except `POST /api/auth/register`, `/login`, `/refresh`, `/forgot-password`
  and `/reset-password` requires `Authorization: Bearer <access_token>`.
- A missing, expired or invalid access token → **401**. The frontend then calls
  `POST /api/auth/refresh` **once** and retries the original request. If the refresh
  fails too, it clears the session and redirects to `/login`.
- Concurrent 401s share a single refresh attempt within one tab. Several tabs share the
  same refresh token, so the server does **not** rotate it (rotating would sign the other
  tabs out). Each device has one session. Revoking the session signs that device out at
  once, because every request checks it.

### Request and response bodies

- JSON (`Content-Type: application/json`) unless the endpoint says `multipart/form-data`.
- Timestamps are ISO 8601 strings in UTC (`2026-09-28T08:00:00Z`).
- Calendar days are `YYYY-MM-DD`, months are `YYYY-MM`.
- `204 No Content` responses have an empty body.

### Query parameters

- Empty parameters are left out (the client never sends `?q=`).
- **Array parameters are repeated keys**: `?status=reading&status=completed`
  (FastAPI: `status: list[ComicStatus] = Query(default=[])`).
- Booleans are sent as `true` (the client leaves out `false` filters).

### Pagination

Paginated list endpoints take `page` (1-based, default 1) and `page_size`
(default 20, max 100) and return `Paginated<T>`:

```json
{ "items": [ … ], "total": 42, "page": 1, "page_size": 10 }
```

`total` counts every match across all pages. The frontend computes the page count as
`ceil(total / page_size)`. Small collections (shelves, tags, sessions) return plain
arrays and are not paginated.

### Errors

Every non-2xx response has this body:

```json
{ "detail": "Không tìm thấy truyện", "code": "comic_not_found" }
```

| Field    | Type     | Notes                                                                 |
| -------- | -------- | --------------------------------------------------------------------- |
| `detail` | `string` | A human-readable message, in Vietnamese. The UI may show it as-is.    |
| `code`   | `string?`| A stable, machine-readable key. The UI branches on it.                |

FastAPI's default 422 body (`{ "detail": [ { "msg": …, "loc": … } ] }`) is also
accepted: the client joins the `msg` values and uses `code = "validation_error"`.
A custom exception handler that returns `{ detail, code: "validation_error" }` is
still preferred.

Common error codes, used on every endpoint where they apply:

| Status | `code`             | When                                                       |
| ------ | ------------------ | ---------------------------------------------------------- |
| 401    | `not_authenticated`| Missing or invalid access token (triggers the refresh flow). |
| 401    | `token_expired`    | Access token expired (triggers the refresh flow).          |
| 401    | `session_revoked`  | This device was signed out (logout, "sign out other devices", password change or reset). The refresh then fails too, so the client logs out. |
| 403    | `account_locked`   | The account is locked (`is_active = false`). The client does not log out, but it can't use the API. |
| 404    | `comic_not_found`  | Unknown comic ID (or one that belongs to another user).    |
| 404    | `shelf_not_found`  | Unknown shelf ID.                                          |
| 404    | `not_found`        | Any other unknown resource.                                |
| 422    | `validation_error` | Invalid body or query.                                     |
| 500    | `internal_error`   | Unexpected server error.                                   |

Network failures appear in the client as `code = "network_error"` (status 0). The
server never sends that code.

### Data ownership

Every resource belongs to the signed-in user. Another user's comic, shelf or
notification must answer **404**, never 403, so IDs don't leak.

### Which page uses what

"Used by" names the page (route) or shared component that calls the endpoint. The
layout components (header bell, sidebar) appear on every signed-in page.

---

## Auth

Service: [`src/services/authService.ts`](../src/services/authService.ts),
refresh logic in [`src/services/http.ts`](../src/services/http.ts).

### `POST /api/auth/login`

No auth header.

Request:

```json
{ "username": "lacbeo", "password": "••••••••" }
```

`username` may also be the user's email (case-insensitive).

Response **200** `LoginResult`:

```json
{
  "access_token": "…",
  "refresh_token": "…",
  "token_type": "bearer",
  "user": { …User }
}
```

Errors:

- **401** `invalid_credentials`: wrong username/email or password (the response doesn't reveal which).
  This 401 does not trigger the refresh flow.
- **403** `account_locked`: the password is right but the account is locked. `detail`:
  "Tài khoản của nàng đang tạm bị khóa. Nàng liên hệ quản trị viên để được mở lại nhé."
  A wrong password on a locked account still gets `invalid_credentials`.

Used by: `/login`.

### `POST /api/auth/register`

No auth header. Creates an account and signs it in.

Request:

```json
{ "username": "tiennu", "email": "tiennu@example.com", "password": "••••••••", "display_name": "Tiên Nữ" }
```

- `username`: 3–32 characters from `a-z 0-9 _ .`. It is stored in lowercase.
- `email` is stored in lowercase.
- `password`: 8–128 characters.
- `display_name` is optional, 40 characters max. Default: the username.

Response **201** `LoginResult`, as for login.

Errors: **409** `username_taken`, **409** `email_taken`, **422** `validation_error`.

Used by: not used by the frontend yet (there is no sign-up page).

### `POST /api/auth/forgot-password`

No auth header.

Request: `{ "email": "…" }`

Response **202** `{ "detail": "Nếu email này có tài khoản, …" }`. The response is the same
whether or not the email has an account, so it can't be used to find accounts.

- For an active account, it creates a single-use reset token valid for
  `PASSWORD_RESET_TTL_MINUTES` (default 30). Older unused links stop working.
- The link is `{FRONTEND_URL}/reset-password?token=…`. **For now it is printed to the server
  log**; email comes later.

Errors: **422** `validation_error` (not an email).

Used by: not used by the frontend yet (there is no reset page).

### `POST /api/auth/reset-password`

No auth header.

Request: `{ "token": "…", "new_password": "…" }` (8–128 characters).

Response **204**. Sets the new password and signs out **every** device.

Errors: **400** `invalid_reset_token` (unknown, used or expired), **403** `account_locked`,
**422** `validation_error`.

Used by: not used by the frontend yet.

### `POST /api/auth/refresh`

No auth header.

Request: `{ "refresh_token": "…" }`

Response **200**: `{ "access_token": "…", "token_type": "bearer" }`. The refresh token is not rotated:
the client keeps the one it has (`SessionTokens.refresh_token` is optional). Each refresh moves
the session's expiry to `REFRESH_TOKEN_TTL_DAYS` (default 30) from now, so the expiry slides
while the device is in use.

Errors: **401** `invalid_refresh_token` (expired, revoked or unknown), **403** `account_locked`.
The client logs out on either.

Used by: the HTTP client, automatically, after any 401.

### `POST /api/auth/logout`

Request: `{ "refresh_token": "…" }`. Revokes this device's session only. The body is optional:
without it, the session of the access token is revoked. A refresh token that belongs to
another user is ignored.

Response **204**.

Errors: none that the UI shows. The client clears its session whatever the result.

Used by: the avatar menu and `/account` ("Đăng xuất").

---

## Users

Service: [`src/services/userService.ts`](../src/services/userService.ts).

### `User`

```ts
{
  id: string; username: string; email: string;
  display_name: string; bio: string; avatar_url: string;
  role: 'user' | 'admin';
  created_at: string;
  last_backup_at: string | null;   // last successful export
  settings: UserSettings;
}
UserSettings = {
  theme: 'day' | 'night'; font_size: number; sparkle_enabled: boolean;
  notify_new_chapter: boolean; notify_broken_link: boolean;
  daily_reminder_enabled: boolean; daily_reminder_time: string; // "HH:MM"
  personalization_enabled: boolean; // default true; false: the home feed isn't about her taste
  starter_tastes: string[];         // moods picked while the library is small; set with PUT …/starter-tastes
  priority_tastes: string[];        // moods above every other taste: they lead the home feed (default [])
}
```

### `GET /api/users/me`

Response **200** `User`.

Used by: app start-up (restores the saved session), `/account/data` (reloads `last_backup_at` after an export).

### `PATCH /api/users/me`

Request: `{ "display_name"?: string, "bio"?: string, "avatar"?: string | null, "settings"?: Partial<UserSettings> }`

- `display_name`: 1–40 characters, trimmed. `bio`: at most 160 characters.
- `avatar`: a `data:image/…;base64,…` URL (stored like an upload), or `null` to remove the picture.
- `settings`: merged as in `PATCH /api/users/me/settings`.
- **Nothing else can be changed here.** The username never changes.

Response **200** `User`.

Errors:

- **400** `username_not_editable`: the body contains `username`. `detail`: "Tên đăng nhập không thể thay đổi. …"
- **400** `field_not_editable`: any other field (`email`, `role`, `password`, …).
- **400** `invalid_file` (bad `avatar`), **422** `validation_error` (for example an empty `display_name`).

Used by: `/account/profile`.

### `PUT /api/users/me/avatar`

Request: `multipart/form-data`, with the image in the `file` field. Images only, 4 MB max
(Vercel accepts request bodies up to 4.5 MB).

Stored in the database as a 256×256 WebP (centre-cropped). Response **200** `User` with the new
`avatar_url`, a signed URL like the cover's (see [Images](#images)).

Errors: **400** `invalid_file` (not an image, or unreadable), **413** `file_too_large`.

Used by: `/account/profile`.

### `PATCH /api/users/me/settings`

Request: `Partial<UserSettings>`. Only the fields that changed are sent.

Response **200** `User`.

Errors: **422** `validation_error`.

Used by: `/account/appearance` and the theme toggle in the header (`theme`,
`font_size`, `sparkle_enabled`), and `/account/notifications` (`notify_*`, `daily_reminder_*`).

`starter_tastes` can't be set here (use `PUT /api/users/me/starter-tastes`).

`priority_tastes`: at most 4 moods from `GET /api/home` `starter_options` (for example
`["Bách hợp (GL)", "Đam mỹ (BL)"]`); duplicates are dropped, `[]` clears them. An unknown mood or more
than 4 → **422** `validation_error`. See [Priority tastes](#get-apihome).

Changing `personalization_enabled` or `priority_tastes` rebuilds today's home feed on the next `GET /api/home`.

### `PUT /api/users/me/starter-tastes`

Request: `{ "starter_tastes": string[] }`: moods from `GET /api/home` `starter_options`
(`"Chữa lành"`, `"Cổ tích"`, `"Lãng mạn"`, `"Phiêu lưu"`, `"Hài hước"`, `"Học đường"`, `"Kỳ ảo"`,
`"Đời thường"`, `"Bách hợp (GL)"`, `"Đam mỹ (BL)"`), at most one of each; duplicates are dropped, `[]` clears them. They shape the home feed while the library
has fewer than 5 comics. Today's feed is built again on the next `GET /api/home`.

Response **200** `User`.

Errors: **422** `validation_error` (an unknown mood, more than there are, or another field).

Used by: `/` (the "Nàng thích đọc gì?" picker, when `needs_starter_tastes`).

### `POST /api/users/me/password`

Request: `{ "current_password": "…", "new_password": "…" }`

Response **204**. Every **other** device is signed out; this one stays signed in.

Errors: **400** `wrong_password` (the UI shows it under the "current password" field),
**422** `validation_error` (the new password is shorter than 8 characters, or the same as the current one).

Used by: `/account/security`.

### `GET /api/users/me/sessions`

Response **200** `DeviceSession[]`, with the current device first:

```ts
{ id: string; device_name: string; device_type: 'desktop' | 'phone' | 'tablet';
  browser: string; location: string; last_active_at: string; is_current: boolean }
```

Used by: `/account/security`.

### `DELETE /api/users/me/sessions`

Signs out every **other** device. This device stays signed in.

Response **200** `{ "revoked": number }`

Used by: `/account/security`.

### `GET /api/users/me/export?format=json|csv`

Response **200**: a file download.

- `Content-Disposition: attachment; filename="uyen-thu-cac-YYYY-MM-DD.json"` (or `.csv`).
  The client reads the filename from this header, so it must be exposed through CORS
  (`Access-Control-Expose-Headers: Content-Disposition`).
- JSON: `{ app: "uyen-thu-cac", version: 1, exported_at, profile: { display_name, bio, settings }, shelves: Shelf[], comics: Comic[] }`.
- CSV: UTF-8 with a BOM. Columns: `id,title,author,status,current_chapter,total_chapters,rating,is_favorite,tags,note,last_read_at,created_at`.
  Tags are joined with `; `.
- Sets `User.last_backup_at` to now.

Errors: **422** `validation_error` (unknown format).

Used by: `/account/data`, and the "Sao lưu" button on `/`.

### `POST /api/users/me/import`

Request: `multipart/form-data`, with a JSON file from the export in the `file` field.

Response **200** `ImportResult`: `{ "added": number, "skipped": number }`. Comics whose `id`
already exists are skipped.

Errors: **400** `invalid_file` (not JSON, or no `comics` array of objects with a string `id` and `title`).

Used by: `/account/data`.

### `DELETE /api/users/me`

Deletes the account and all its data.

Response **204**. The client then logs out.

Used by: `/account`.

---

## Comics

Service: [`src/services/comicService.ts`](../src/services/comicService.ts).

**How a Comic is stored.** The database keeps each comic once, as a shared **work** (title, authors,
genres, cover, total chapters, where to read), and one **library item** per user who saved it
(status, progress, rating, note, favourite, shelves, own tags). A `Comic` joins the two for one
user; `Comic.id` is the library item's id. Nothing about the web is stored until someone saves it.

- A work found on the web ([Discover](#discover)) is **shared**. A work entered by hand is
  **private** to the person who entered it. Nobody else can join it.
- **Editing a work's info** (title, author, description, total, cover, sources) changes the work
  itself when it is the user's private work or nobody else saved it. When others saved it too, the
  change is kept as **that user's own version**, and everyone else keeps seeing the shared info.
  Adding a source always adds it to the shared work, because it only adds information.

### `Comic`

```ts
{
  id: string; title: string; author: string; description: string; cover_url: string;
  status: 'reading' | 'completed' | 'plan_to_read' | 'on_hold' | 'dropped';
  current_chapter: number; total_chapters: number;
  rating: number;            // 0–5
  is_favorite: boolean; note: string; tags: string[];
  sources: Source[]; primary_source_id: string;
  shelf_ids: string[];       // always starts with "all" (the virtual shelf), then real shelf ids
  has_new_chapter: boolean;
  last_read_at: string | null;   // null = never read
  created_at: string; updated_at: string;
}
Source = {
  id: string; site_name: string; url: string; favicon_url: string; chapter_url: string;
  latest_chapter: number; is_alive: boolean;
  last_checked_at: string | null;   // set only by the server's link check
}
```

- `cover_url` is an external `https://` URL, a signed `/api/comics/{id}/cover?v=…&sig=…` URL
  for a stored cover (see [Images](#images)), or `""`.
- `primary_source_id` is `""` when the comic has no source.
- `tags`: the user's own tags in the order they entered them, then the work's genres.
- `total_chapters` is the last chapter known (the user's own number, if they set one on a shared
  work). **`current_chapter` may be higher**, because sites list new chapters before providers do.
  Clients should cap progress bars at 100 %.

> **Route order:** declare `/api/comics/facets`, `/api/comics/summary` and
> `/api/comics/lookup` **before** `/api/comics/{id}` in FastAPI.

### `GET /api/comics`

The library list, and the advanced search. All parameters are optional.

| Param             | Type                         | Notes                                                                                              |
| ----------------- | ---------------------------- | -------------------------------------------------------------------------------------------------- |
| `q`               | string                       | Case- and accent-insensitive match on title, author, tags and note.                               |
| `status`          | `ComicStatus`, repeatable    | Any of these statuses.                                                                             |
| `tag`             | string, repeatable           | Has any of these tags.                                                                             |
| `source`          | string, repeatable           | Has a source with any of these `site_name` values.                                                |
| `shelf`           | string, repeatable           | On any of these shelves. The client never sends `all`.                                            |
| `min_rating`      | 1–5                          | `rating >= min_rating`.                                                                            |
| `progress_min`    | 0–100                        | Reading progress in percent, `current_chapter / total_chapters * 100`.                             |
| `progress_max`    | 0–100                        |                                                                                                    |
| `has_new_chapter` | `true`                       | Only comics with an unread new chapter.                                                           |
| `has_broken_link` | `true`                       | Only comics with at least one source where `is_alive = false`.                                    |
| `is_favorite`     | `true`                       | Only favourites.                                                                                   |
| `sort`            | see below                    | Default: `relevance` when `q` is set, otherwise `updated_at`.                                     |
| `page`            | int ≥ 1                      |                                                                                                    |
| `page_size`       | 1–100                        | The client sends 10 (`/`), 12 (`/search`) or 100 (`/shelves/:id`).                                 |

`sort` values:

| Value        | Order                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------- |
| `updated_at` | Newest first.                                                                               |
| `title`      | A→Z, Vietnamese collation.                                                                  |
| `rating`     | Highest first.                                                                              |
| `progress`   | Highest percentage first.                                                                   |
| `relevance`  | Best `q` match first, then `updated_at`. Weights used by the mock: title prefix 6, title 4, author 3, tag 2, note 1. |
| `position`   | The shelf's saved order (`PUT /api/shelves/{id}/order`). Needs exactly one `shelf`. Comics that aren't in the saved order come last. Without a saved order, falls back to `updated_at`. |

Response **200** `Paginated<Comic>`.

Errors: **422** `validation_error`.

Used by: `/` (list with filters), `/search` (advanced search), `/shelves/:id` (the shelf's
comics with `sort=position`, and the "add comics" picker).

### `GET /api/comics/facets`

The filter options, with counts over the whole library (not the current search).

Response **200** `SearchFacets`:

```ts
{
  total: number;
  statuses: FacetOption[];   // all 5 statuses, even when the count is 0; label in Vietnamese
  genres:   FacetOption[];   // every tag, most used first
  sources:  FacetOption[];   // distinct site_name; icon = favicon_url; most used first
  shelves:  FacetOption[];   // user shelves (not "all"); icon = shelf icon
}
FacetOption = { value: string; label: string; icon?: string; count: number }
```

Status labels: `reading` Đang đọc, `completed` Đã đọc xong, `plan_to_read` Muốn đọc,
`on_hold` Tạm dừng, `dropped` Bỏ dở.

Used by: `/search`.

### `GET /api/comics/summary`

Response **200** `ComicSummary`:

```ts
{ total: number; by_status: Record<ComicStatus, number>; new_chapters: number }
```

`new_chapters` is the number of **comics** where `has_new_chapter = true`. Every status key is present.

Used by: `/` (the sidebar and the "Giờ Trà" widget), `/account` (profile header).

### `GET /api/comics/lookup?title=`

Exact title match (trimmed, case-insensitive), used to warn about duplicates.

Response **200** `{ "comic": Comic | null }`. It is never a 404.

Used by: `/add`.

### `GET /api/comics/{id}`

Response **200** `Comic`.

Errors: **404** `comic_not_found`. The page then shows "Không tìm thấy cuốn truyện này".

Used by: `/comics/:id`.

### `POST /api/comics`

Request `ComicCreate`: every `Comic` field except `id`, `created_at` and `updated_at`.

- `sources[].id` and `primary_source_id` are generated by the client (`src_<timestamp>`).
  The server assigns new IDs and remaps `primary_source_id` to match. Without a primary, the
  first source becomes primary. An empty `chapter_url` defaults to `url`. `last_checked_at`
  from the client is ignored.
- `shelf_ids`: entries that aren't shelf IDs (the virtual `"all"`, or leftover mock IDs) are ignored.
  A shelf ID that isn't one of the user's shelves → **404** `shelf_not_found`.
- `cover_url`: an `http(s)://` URL, `""`, or a `data:image/…;base64,…` URL from a file the user
  picked. A `data:` URL is stored like an uploaded cover (4 MB max).
- `tags`: trimmed, repeated spaces collapsed, duplicates dropped regardless of case (the first
  spelling wins); 30 tags max, 60 characters each. **Tags that are new for this user are created**,
  and existing ones are reused regardless of case.
- Unknown fields are rejected (422).
- A **page link** (with a path, like `https://site/manga/abc`) that a saved work already has
  attaches the comic to that work. A bare site (`https://cuutruyen.net`) never does.
  Otherwise a new **private** work is created.
- `current_chapter` may be above `total_chapters`. It is kept.

Response **201** `Comic`, or **200** with the user's existing `Comic` if they already saved that work.
[`POST /api/library`](#post-apilibrary) `{ manual }` does the same.

Errors: **404** `shelf_not_found`; **409** `source_url_taken` (the links belong to two different works);
**400** `invalid_file` / **413** `file_too_large` (bad `data:` cover); **422** `validation_error`
(empty title, `rating` outside 0–5, a source URL that isn't `http(s)://`, too many tags).

Used by: `/add`.

### `PATCH /api/comics/{id}`

Request `ComicUpdate`: any subset of the `ComicCreate` fields. Only the fields sent change. Sets `updated_at`.

- `sources` **replaces** the whole list. A source whose `id` is an existing source of this comic
  keeps that id; any other entry is a new source. `primary_source_id` may name an existing source
  or the client id of a new one in the same request. If the primary is removed (or
  `primary_source_id` is `""`), the first remaining source becomes primary. This is what
  `/comics/:id` does to add, edit and remove sources.
- `primary_source_id` without `sources`: must be one of the comic's sources (**404** `source_not_found`).
- `tags` and `shelf_ids` replace the whole list. Tags that no comic uses any more are deleted.
  Genres of the work can't be removed; sending them back again is harmless.
- `current_chapter` may be above `total_chapters` (kept). Explicitly lowering `total_chapters`
  below the current chapter pulls `current_chapter` down to it.
- `cover_url`: a `data:` URL stores a new cover; sending back the comic's own signed cover URL keeps
  it; any other URL (or `""`) replaces it.
- **On a shared work** that others saved too, `title`, `author`, `description`, `total_chapters` and
  `cover_url` become the user's own version. Setting the shared value again removes it. For
  `sources` on a shared work: a removed source is hidden for this user only; a changed site name or
  chapter link is kept for this user only; a changed link adds the new link to the work and hides
  the old one for this user; a new source is added to the work.

Response **200** `Comic`. Never 409 for a shared work.

Errors: **404** `comic_not_found` | `source_not_found` | `shelf_not_found`, **409** `source_url_taken`
(a link that belongs to another work), **422** `validation_error`.

Used by: `/comics/:id` (status, rating, note, tags, sources, details).

### `DELETE /api/comics/{id}`

Response **204**. Removes the comic from the user's library, with its shelf memberships, reading logs,
notifications, and tags no other comic uses. The shared work is deleted too once nobody has it
(its sources and cover with it).

Errors: **404** `comic_not_found`.

Used by: `/comics/:id`.

### `PUT /api/comics/{id}/favorite` · `DELETE /api/comics/{id}/favorite`

Marks (`PUT`) or unmarks (`DELETE`) a favourite. Both are idempotent and have no body.

Response **200** `Comic`.

Errors: **404** `comic_not_found`.

Used by: `/`, `/comics/:id`, `/shelves/:id`, `/stats` (the heart on comic cards).

### `PUT /api/comics/{id}/progress`

Request: `{ "current_chapter": number }`

The server:

- keeps any value ≥ 0, **including one above `total_chapters`**. When the value is above the work's
  last known chapter, the server notes it on the work (`latest_chapter_hint`) so the daily check can refresh it,
- sets `status` to `completed` when it reaches `total_chapters` exactly (unless the work is known to be
  ongoing), otherwise to `reading`. Past the total means more chapters exist, so it stays `reading`,
- sets `last_read_at` and `updated_at` to now,
- when the new chapter is higher, adds the difference to today's `reading_logs` row for this comic
  (one row per comic per day, in `APP_TIMEZONE`, default `Asia/Ho_Chi_Minh`). Going back logs nothing.
  These rows feed the stats (reading calendar, streak, monthly chart).
- clears `has_new_chapter` once the user reaches the highest `latest_chapter` among the sources.

Response **200** `Comic`.

Errors: **404** `comic_not_found`, **422** `validation_error`.

Used by: `/comics/:id` (chapter stepper), `/` and `/shelves/:id` ("đọc tiếp" +1 chapter).

### `POST /api/comics/{id}/sources`

Request: `Source`. The client sends an `id` (`src_<timestamp>`); the server replaces it.

Response **201** `Comic` with the source appended. The first source of a comic becomes primary.

Errors: **404** `comic_not_found`, **422** `validation_error`.

Used by: `/add` (the "add as secondary source" option when the title already exists).

### `PATCH /api/comics/{id}/sources/{source_id}`

Request: any of `site_name`, `url`, `favicon_url`, `chapter_url`, `latest_chapter`, `is_alive`.

On a shared work: `site_name`, `chapter_url` and `favicon_url` become this user's own; a new `url`
adds that link and hides the old one for this user; a higher `latest_chapter` is noted as a hint;
`is_alive` is left to the link check.

Response **200** `Comic`.

Errors: **404** `comic_not_found` | `source_not_found`, **422** `validation_error`.

Used by: not used by the frontend yet (`/comics/:id` edits sources through `PATCH /api/comics/{id}`).

### `DELETE /api/comics/{id}/sources/{source_id}`

Response **200** `Comic`. If it was the primary source, the first remaining source becomes primary.
On a shared work the source is only hidden for this user.

Errors: **404** `comic_not_found` | `source_not_found`.

Used by: not used by the frontend yet.

### `PUT /api/comics/{id}/primary-source`

Request: `{ "source_id": string }`

Response **200** `Comic`.

Errors: **404** `comic_not_found` | `source_not_found`.

Used by: not used by the frontend yet (`/comics/:id` sends `PATCH { primary_source_id }`).

### `PUT /api/comics/{id}/cover` · `DELETE /api/comics/{id}/cover`

`PUT`: `multipart/form-data` with the image in `file` (4 MB max). The image is stored in the
database as a WebP thumbnail that fits 480×720 (never upscaled, EXIF rotation applied, metadata
dropped). `DELETE` removes the cover. On a shared work it is the user's own cover.

Response **200** `Comic` with the new `cover_url`.

Errors: **404** `comic_not_found`, **400** `invalid_file`, **413** `file_too_large`.

Used by: not used by the frontend yet (`/add` sends the cover as a `data:` URL in `cover_url`).

### Images

`GET /api/comics/{id}/cover?v=&sig=` and `GET /api/users/{id}/avatar?v=&sig=` serve the stored
WebP files. They need **no** `Authorization` header, because `<img>` tags can't send one. Instead,
the URL is signed: API responses give it to the owner, and it can't be guessed or built for
another comic.

- Response **200** `image/webp`, `Cache-Control: public, max-age=31536000, immutable`, and an `ETag`.
  **304** when `If-None-Match` matches.
- `v` changes whenever the image changes, so each URL can be cached forever.
- An unknown id, a wrong signature or an outdated `v` → **404** `not_found`.
- The URL is relative (`/api/…`). It works as-is when the frontend reaches the API on its own
  origin (the Vite proxy in development, a Vercel rewrite in production).

---

## Discover

Search across the web for comics to save. Service: `src/services/discoverService.ts` (frontend, Part C).

Providers are asked in parallel. Currently MangaDex (its official public API) and AniList (GraphQL,
used for metadata). Providers give **metadata and links only**: no chapter content is ever
fetched. Each provider is asked with an identifying `User-Agent`, within its rate limits, and without
adult content. Their answers are cached for 6 hours (`search_cache`) and never become a catalog:
a work exists in our database only once someone saves it.

### `GET /api/discover/search?q=&page=`

`q`: 2–100 characters. `page`: 1–10 (default 1; each provider returns up to 20 per page).

Every provider gets about 4 s (`PROVIDER_TIMEOUT_SECONDS`). A slow, failing or rate-limited
provider is **skipped and listed in `providers_failed`**. The search still answers, with the other
providers' results.

Response **200**:

```ts
DiscoverPage = {
  query: string; page: number;
  results: DiscoverResult[];
  providers: string[];          // asked: ["mangadex", "anilist"]
  provider_names: Record<string, string>; // display names: { mangadex: "MangaDex", anilist: "AniList" }
  providers_failed: string[];   // skipped this time (show their provider_names in the note)
}
DiscoverResult = {
  provider: string; external_id: string;       // what POST /api/library takes
  provider_name: string;                       // the lead provider's display name: "MangaDex"
  external_ids: Record<string, string>;        // every id known: { mangadex, anilist, mal }
  providers: string[];                         // every provider that returned it
  attribution: ProviderCredit[];               // the same providers, lead first: the attribution badges
  title: string; alt_titles: string[]; description: string;
  cover_url: string;                           // the provider's image
  authors: string[]; genres: string[];
  status: 'ongoing' | 'completed' | 'hiatus' | 'cancelled' | 'unknown';
  latest_chapter: number | null;               // null = the provider doesn't say
  links: { site_name: string; url: string }[]; // where to read, one per site
  in_library: boolean;
  library_item_id: string | null;              // the user's Comic id when in_library
}
ProviderCredit = {
  provider: string;   // "mangadex"
  name: string;       // "MangaDex"
  url: string;        // the work's page there, e.g. https://mangadex.org/title/…
}
```

Show a small badge per `attribution` entry ("MangaDex", "AniList") on each result, linking to its
`url`: the data (and the cover image) comes from those providers, and MangaDex asks for credit.

Duplicates across providers are merged into one result: first by a shared external ID (for example,
MangaDex knows the AniList ID), then by the same normalised title plus a shared author (name order
doesn't matter). A merged result is led by MangaDex when it has the work (a reading site first),
and keeps the rank of its best member. Genres, alternative titles and links are combined.

**Ranking.** Results whose title matches the query come first, whatever the providers' own order:

1. **exact**: the title or any alternative title is the query (ignoring case, accents and punctuation);
2. **near-exact**: the same up to spacing ("onepiece"), a leading article ("promised neverland" →
   "The Promised Neverland"), a subtitle ("frieren" → "Frieren: Beyond Journey's End") or a small
   typo (one letter from 5 letters, two from 10; a swap counts as one: "one peice");
3. everything else.

Within each group the providers' order is kept (best first from each provider, interleaved).
Ranking applies within a page (`page` asks each provider for its next 20).

Errors: **422** `validation_error` (`q` or `page` out of range).

Used by: `/search` ("Khám phá" tab), `/add` ("Tìm theo tên").

---

## Library

### `POST /api/library`

Save a comic. Exactly one of:

| Body | Meaning |
| --- | --- |
| `{ "provider": "mangadex", "external_id": "…" }` | a search result |
| `{ "url": "https://mangadex.org/title/…" }` | a link: a work already saved with that page link, or a provider's own page (MangaDex title, AniList manga) |
| `{ "manual": ComicCreate }` | entered by hand, like `POST /api/comics` (a private work) |

Optional for the new library item: `status` (default `reading` if `current_chapter` > 0, else
`plan_to_read`), `current_chapter`, `shelf_ids`.

The server finds the work (by external ID, then by source link) or creates it. When it creates one, it
fetches the details and cover once (the cover is stored as a WebP thumbnail; if it can't be fetched,
the provider's link is kept), enriches the details from the other providers that know the work, and
attaches the work's links as sources. A work others saved only gains what it lacks: IDs, genres,
alternative titles, a higher chapter. **Idempotent**: saving a work the user already has returns
their existing item.

Response **201** `Comic`, or **200** with the user's existing `Comic`.

Errors: **404** `work_not_found` (the provider doesn't know that ID) | `shelf_not_found`;
**422** `unknown_provider` | `link_not_supported` (a link we can't read: search by name or enter it
by hand) | `validation_error` (none or several of the three ways); **503** `provider_unavailable`
(the provider is down; nothing was saved).

Used by: `/search` ("Thêm vào tủ"), `/add`.

---

## Home

"Khu vườn hôm nay": the home page shows her comics to continue, then what is new each day for her
taste (from MangaDex and AniList, safe content only: MangaDex content ratings `safe` and `suggestive`,
never erotica or pornographic; AniList `isAdult: false`, no Hentai or Ecchi). Service: `src/services/homeService.ts`
(frontend, not built yet).

### `GET /api/home`

Response **200**:

```ts
HomeOut = {
  date: string;                     // "YYYY-MM-DD" (Vietnam time): the feed's day
  personalization_enabled: boolean;
  needs_starter_tastes: boolean;    // fewer than 5 comics, no starter or priority tastes: show the mood picker
  starter_tastes: string[];
  priority_tastes: string[];        // settings.priority_tastes: they lead for_you, new_releases, trending
  starter_options: { value: string; label: string; icon: string }[];
  feed_status: 'ready' | 'building' | 'off';
  sections: HomeSection[];          // always in this order (see below)
}
HomeSection = {
  key: 'continue_reading' | 'new_chapters' | 'for_you' | 'new_releases' | 'trending';
  title: string;                    // "Đọc tiếp nhé", "Có chương mới", "Dành cho nàng", "Mới ra mắt hợp gu", "Đang được yêu thích"
  kind: 'library' | 'feed';
  comics: Comic[];                  // kind "library": her own comics (up to 10)
  items: FeedItem[];                // kind "feed": suggestions
}
FeedItem = {
  provider: string; provider_name: string; external_id: string; // save with POST /api/library { provider, external_id }
  external_ids: Record<string, string>;
  title: string; cover_url: string; authors: string[]; genres: string[];
  latest_chapter: number | null;
  status: 'ongoing' | 'completed' | 'hiatus' | 'cancelled' | 'unknown';
  attribution: ProviderCredit[];    // as in Discover: badges linking back to the providers
  reason: string;                   // "Vì nàng thích Chữa lành", "Giống Frieren mà nàng chấm 5 tim", "Bách hợp mới ra mắt", …
  score: number;
}
```

Sections, in order:

| key | kind | What |
| --- | --- | --- |
| `continue_reading` | library | "Đang đọc" comics she has started, most recently read first |
| `new_chapters` | library | comics with an unread new chapter (not repeated in `continue_reading`) |
| `for_you` | feed | AniList recommendations for works she loved, works by her favourite authors, works in her top genres (up to 16) |
| `new_releases` | feed | series started in the last year, in her genres (up to 12) |
| `trending` | feed | what readers love right now, in her genres (up to 12) |

With `personalization_enabled: false`, `for_you` is left out and `new_releases` / `trending` are
everyone's (reasons "Mới ra mắt gần đây", "Đang được nhiều người yêu thích"); `feed_status` is `"off"`.
A new user with no comics and no starter tastes also gets everyone's lists, and `needs_starter_tastes`.

**Building.** The feed is built once a day per user: by the daily cron for active users, or here on
the first request of the day. That first request waits at most `FEED_BUILD_SECONDS` (5 s) for the
providers. If some didn't answer in time, it returns what is ready with `feed_status: "building"`;
the next request (waiting at most 2 s) or the cron adds the rest. Answers already fetched are cached,
so nothing is asked twice. After 3 tries the feed is left as it is. Once built, the request only reads.

**Taste** comes from her own data only: the genres, tags and authors of her comics, weighted by status
(completed and reading count more, dropped counts against), rating, favourite, and how recently she
read them. Suggestions she dismissed count against their genres and authors. Her own Vietnamese tags
match the providers' genres ("Đời thường" = "Slice of Life"). Works already in her library (by any id,
or by title) and dismissed works are never suggested.

**Ranking.** Score = taste match + provider popularity + freshness, weighted per section. A work
appears in one section only. Within a section, no more than 3 in a row share an author or a main genre,
and a genre is named in at most 3 reasons before a work's next matching genre is named instead.

**Priority tastes** (`settings.priority_tastes`, set with `PATCH /api/users/me/settings`) come above
every other taste signal. The feed asks the providers for them directly (new, trending and best-loved
works in each), so it doesn't depend on what is already in her library. `for_you`, `new_releases`
and `trending` each start with works in a priority taste (their score is raised by 2, more than
anything else can add), then are filled with her other good matches. Among the priority works only the
author rule applies (no more than 3 in a row by one author). Their reasons name the taste:
"Bách hợp mới ra mắt" (`new_releases`), "Đam mỹ đang được yêu thích" (`trending`), "Vì nàng mê Bách hợp"
or "Bách hợp, giống Frieren mà nàng chấm 5 tim" (`for_you`). They apply only with personalization on.

| Mood | MangaDex tag | AniList |
| --- | --- | --- |
| `Bách hợp (GL)` | `Girls' Love` | tag `Yuri` (rank ≥ 60) |
| `Đam mỹ (BL)` | `Boys' Love` | tag `Boys' Love` (rank ≥ 60) |

AniList has no genre for these, only tags: a work where one is central (rank ≥ 60) lists it in `genres`
under MangaDex's name (`"Girls' Love"`, `"Boys' Love"`).

Used by: `/` (not built yet).

### `POST /api/home/dismiss`

Request: `{ "provider": string, "external_id": string }` ("Không hứng thú").

The work leaves the feed and is never suggested again (under any of its ids); its genres and authors
count a little against her taste. Idempotent.

Response **204**.

Errors: **422** `validation_error`.

Saving a suggestion uses `POST /api/library` `{ provider, external_id }`; it then leaves the feed.

---

## Shelves

Service: [`src/services/shelfService.ts`](../src/services/shelfService.ts). Membership
calls live in `comicService.ts`.

### `Shelf`

```ts
{ id: string; name: string; description: string; icon: string; color: string;
  position: number; comic_count: number; cover_urls: string[] /* up to 4 */ }
```

- `color` is one of the palette identifiers in `SHELF_COLORS`
  ([`src/components/ShelfFormModal.tsx`](../src/components/ShelfFormModal.tsx)):
  `#FEB2C0`, `#A8C49A`, `#D9C8F0`, `#FFDF97`, `#CFE8D5` or `#FFE3D2`. It is stored as data.
  The UI renders it with themed colours, not the raw hex.
- The virtual shelf **`all`** ("Tất cả truyện") is included in `GET /api/shelves` as the first shelf (`position` 1). User shelves follow.
  Its `comic_count` is the size of the library. It cannot be edited, deleted or reordered.

### `GET /api/shelves`

Response **200** `Shelf[]`, ordered by `position`, including `all`.

Used by: `/` (sidebar and shelf tabs), `/add` (shelf picker), `/comics/:id` (the "add to shelf" menu), `/shelves/:id`.

### `GET /api/shelves/{id}`

Response **200** `Shelf`.

Errors: **404** `shelf_not_found`. The page then shows "Kệ sách không tồn tại".

Used by: `/shelves/:id`.

### `POST /api/shelves`

Request `ShelfInput`: `{ name: string; description?: string; icon?: string; color?: string }`.
Defaults: icon `🌸`, color `#FEB2C0`, `position` last.

Response **201** `Shelf`.

Errors: **422** `validation_error` (empty name).

Used by: the shelf form (on `/` and `/shelves/:id`).

### `PATCH /api/shelves/{id}`

Request: `Partial<ShelfInput>`.

Response **200** `Shelf`.

Errors: **404** `shelf_not_found`, **422** `validation_error`.

Used by: the shelf form (on `/shelves/:id`).

### `DELETE /api/shelves/{id}`

Response **204**. The comics stay in the library; only their membership is removed.

Errors: **404** `shelf_not_found`.

Used by: `/shelves/:id` and the shelf form.

### `PUT /api/shelves/{id}/order`

Request: `{ "comic_ids": string[] }`. This is the full order after a drag-and-drop.

Response **204**. Read it back with `GET /api/comics?shelf={id}&sort=position`.

Errors: **404** `shelf_not_found`, **422** `validation_error`.

Used by: `/shelves/:id`.

### `POST /api/shelves/{shelf_id}/comics`

Request: `{ "comic_id": string }`. Idempotent: adding a comic that is already on the shelf is fine.

Response **200** `Comic` with the updated `shelf_ids`. Updates the shelf's `comic_count` and `cover_urls`.

Errors: **404** `shelf_not_found` / `comic_not_found`.

Used by: `/comics/:id`, `/shelves/:id` (the picker).

### `DELETE /api/shelves/{shelf_id}/comics/{comic_id}`

Response **200** `Comic` with the updated `shelf_ids`.

Errors: **404** `shelf_not_found` / `comic_not_found`.

Used by: `/comics/:id`.

---

## Sources

Service: [`src/services/sourcesService.ts`](../src/services/sourcesService.ts).

### `POST /api/sources/preview`

Fetches a comic page and reads its metadata.

Request: `{ "url": "https://…" }`

Response **200** `ComicPreview`:

```ts
{ title: string; author: string; cover_url: string; total_chapters: number;
  tags: string[]; site_name: string; favicon_url: string }
```

Errors:

- **400** `invalid_url`: not an `http(s)://` URL.
- **422** `preview_failed`: the page could not be fetched or parsed.

The UI shows `detail` for both.

Used by: `/add` ("Lấy thông tin" on the link tab).

### `POST /api/sources/check`

Re-checks every source link in the library. Updates `Source.is_alive` and
`last_checked_at`, and creates `broken_link` notifications for sources that newly broke.
It is synchronous and may take several seconds.

Response **200** `SourceCheckResult`:

```ts
{ checked: number;   // sources checked
  comics: number;    // comics covered
  broken: { comic_id: string; comic_title: string; site_name: string }[];
  checked_at: string }
```

Used by: `/account/data`.

---

## Tags

Service: [`src/services/tagsService.ts`](../src/services/tagsService.ts).

### `GET /api/tags`

Response **200** `TagCount[]`: `{ name: string; count: number }[]`, one entry for every
tag used in the library. Sorted by `count` (highest first), then by name (Vietnamese collation).

Tags are never created directly. A comic that uses a new tag name creates it (matching is
case-insensitive), and a tag that no comic uses any more is deleted.

Used by: `/` (genre filter).

---

## Stats

Service: [`src/services/statsService.ts`](../src/services/statsService.ts).

### `GET /api/stats?period=week|month|year|all`

A period always ends today, in the user's time zone. `week` starts on Monday, `month`
on the 1st, `year` on 1 January, and `all` at the first activity.

Response **200** `Stats`:

| Field                 | Meaning                                                                                              |
| --------------------- | ---------------------------------------------------------------------------------------------------- |
| `period`              | Echo of the query.                                                                                   |
| `total_comics`        | Whole library, whatever the period.                                                                  |
| `new_comics`          | Comics added during the period.                                                                      |
| `comics_read`         | Distinct comics with reading activity during the period.                                            |
| `completed_count`     | Comics finished during the period (`status = completed` and `last_read_at` in the period).           |
| `chapters_read`       | Chapters read during the period.                                                                     |
| `streak_days`         | Current run of consecutive reading days, whatever the period. Today counts even before any reading today. |
| `chapters_by_month`   | `{ month: "YYYY-MM", count }[]`, oldest first. The months of the period; for `week` and `month`, the last 6 months. |
| `genres`              | `{ name, count }[]`: comics read during the period, per tag, highest first.                          |
| `reading_calendar`    | `{ date: "YYYY-MM-DD", count }[]`: one entry per day of the period (the last 365 days for `all`), days with 0 included. |
| `top_sources`         | Up to 5 `{ site_name, favicon_url, count }`: comics read, by primary source.                         |
| `goal`                | `ReadingGoal` for the **current year**: `{ year, target, completed }`. The default `target` is 12.   |
| `recently_completed`  | Up to 10 `Comic`, most recently finished first.                                                       |

Errors: **422** `validation_error` (unknown period).

Used by: `/stats`.

### `PUT /api/stats/goals/{year}`

Request: `{ "target": number }`. An integer ≥ 1; the server rounds it.

Response **200** `ReadingGoal`.

Errors: **422** `validation_error`.

Used by: `/stats`.

---

## Notifications

Service: [`src/services/notificationService.ts`](../src/services/notificationService.ts).

### `Notification`

```ts
{ id: string; comic_id: string; comic_title: string; comic_cover_url: string;
  type: 'new_chapter' | 'broken_link' | 'achievement';
  message: string; is_read: boolean; created_at: string }
```

Clicking a notification opens `/comics/{comic_id}`. The frontend builds that link itself.

### `GET /api/notifications?type=&page=&page_size=`

`type` is `new_chapter` or `broken_link`. Leave it out for all types. The client sends `page_size=50`.

Response **200** `Paginated<Notification>`, newest first.

Used by: `/notifications`, and the bell dropdown in the header.

### `GET /api/notifications/unread-count?type=`

Response **200** `{ "count": number }`

Used by: the bell badge (header on desktop and mobile), and the filter chips (called once
per type: all, `new_chapter`, `broken_link`).

### `POST /api/notifications/{id}/read`

Response **204**. Idempotent.

Errors: **404** `not_found`.

Used by: `/notifications`, the bell dropdown.

### `POST /api/notifications/read-all`

Response **204**.

Used by: `/notifications` ("Đọc hết"), the bell dropdown.

### `DELETE /api/notifications/{id}`

Response **204**.

Errors: **404** `not_found`.

Used by: `/notifications` (swipe or delete button).

---

## Scheduled jobs

### `GET /api/cron/daily`

Called by Vercel Cron once a day at 20:00 UTC (03:00 in Vietnam; see `vercel.json`) with
`Authorization: Bearer <CRON_SECRET>`. Idempotent and quick. It:

- deletes expired `search_cache` rows, works nobody has in their library any more (after an account
  is deleted), and home feed items older than 3 days;
- checks the latest chapter of a small batch (15) of saved works on MangaDex (else AniList): works a
  reader got further in (`latest_chapter_hint`) first, then the ones checked longest ago. A higher
  chapter sets `has_new_chapter` for readers who had caught up, and sends them a `new_chapter`
  notification ("“Tên truyện” đã có chương 125 🌸") if `notify_new_chapter` is on;
- builds today's home feed for active users (signed in within 30 days), one at a time, until its time
  budget (`CRON_BUDGET_SECONDS`, 40 s; `maxDuration` is 60 s) runs low. Users left over get theirs
  on their first home visit.

Coming next: checking source links.

Response **200**:

```ts
{
  search_cache_deleted: number; orphan_works_deleted: number; feed_items_deleted: number;
  chapters_checked: number; chapters_updated: number; new_chapter_notifications: number;
  feeds_built: number;
}
```

Errors: **401** `not_authenticated` (wrong secret), **503** `cron_not_configured` (no `CRON_SECRET`).

---

## Endpoint index

| Method   | Path                                         | Service function                           | Used by                                   |
| -------- | -------------------------------------------- | ------------------------------------------ | ----------------------------------------- |
| POST     | `/api/auth/login`                            | `authService.login`                        | `/login`                                  |
| POST     | `/api/auth/refresh`                          | `http.ts` (automatic)                      | every page, after a 401                   |
| POST     | `/api/auth/logout`                           | `authService.logout`                       | avatar menu, `/account`                   |
| POST     | `/api/auth/register`                         | —                                          | not used yet                              |
| POST     | `/api/auth/forgot-password`                  | —                                          | not used yet                              |
| POST     | `/api/auth/reset-password`                   | —                                          | not used yet                              |
| GET      | `/api/users/me`                              | `userService.getCurrentUser`               | app start-up, `/account/data`             |
| PATCH    | `/api/users/me`                              | `userService.updateProfile`                | `/account/profile`                        |
| PUT      | `/api/users/me/avatar`                       | `userService.uploadAvatar`                 | `/account/profile`                        |
| PATCH    | `/api/users/me/settings`                     | `userService.updateSettings`               | `/account/appearance`, `/account/notifications`, theme toggle |
| PUT      | `/api/users/me/starter-tastes`               | — (`homeService`, not built yet)           | `/`                                       |
| POST     | `/api/users/me/password`                     | `userService.changePassword`               | `/account/security`                       |
| GET      | `/api/users/me/sessions`                     | `userService.getSessions`                  | `/account/security`                       |
| DELETE   | `/api/users/me/sessions`                     | `userService.logoutAll`                    | `/account/security`                       |
| GET      | `/api/users/me/export`                       | `userService.exportData`                   | `/account/data`, `/`                      |
| POST     | `/api/users/me/import`                       | `userService.importData`                   | `/account/data`                           |
| DELETE   | `/api/users/me`                              | `userService.deleteAccount`                | `/account`                                |
| GET      | `/api/comics`                                | `comicsService.list` / `.search`           | `/`, `/search`, `/shelves/:id`            |
| GET      | `/api/comics/facets`                         | `comicsService.getFacets`                  | `/search`                                 |
| GET      | `/api/comics/summary`                        | `comicsService.getSummary`                 | `/`, `/account`                           |
| GET      | `/api/comics/lookup`                         | `comicsService.findExistingByTitle`        | `/add`                                    |
| GET      | `/api/comics/{id}`                           | `comicsService.getComicById`               | `/comics/:id`                             |
| POST     | `/api/comics`                                | `comicsService.create`                     | `/add`                                    |
| PATCH    | `/api/comics/{id}`                           | `comicsService.update`                     | `/comics/:id`                             |
| DELETE   | `/api/comics/{id}`                           | `comicsService.delete`                     | `/comics/:id`                             |
| PUT      | `/api/comics/{id}/favorite`                  | `comicsService.setFavorite(id, true)`      | `/`, `/comics/:id`, `/shelves/:id`, `/stats` |
| DELETE   | `/api/comics/{id}/favorite`                  | `comicsService.setFavorite(id, false)`     | `/`, `/comics/:id`, `/shelves/:id`, `/stats` |
| PUT      | `/api/comics/{id}/progress`                  | `comicsService.updateProgress`             | `/`, `/comics/:id`, `/shelves/:id`        |
| POST     | `/api/comics/{id}/sources`                   | `comicsService.addSourceToComic`           | `/add`                                    |
| PATCH    | `/api/comics/{id}/sources/{source_id}`       | —                                          | not used yet                              |
| DELETE   | `/api/comics/{id}/sources/{source_id}`       | —                                          | not used yet                              |
| PUT      | `/api/comics/{id}/primary-source`            | —                                          | not used yet                              |
| PUT      | `/api/comics/{id}/cover`                     | —                                          | not used yet                              |
| DELETE   | `/api/comics/{id}/cover`                     | —                                          | not used yet                              |
| GET      | `/api/comics/{id}/cover?v=&sig=`             | (`<img src>` of `cover_url`)               | every page with covers                    |
| GET      | `/api/users/{id}/avatar?v=&sig=`             | (`<img src>` of `avatar_url`)              | header, `/account`                        |
| GET      | `/api/discover/search`                       | `discoverService.search`                   | `/search` (Khám phá), `/add`              |
| POST     | `/api/library`                               | `discoverService.addToLibrary`             | `/search` (Khám phá), `/add`, `/`         |
| GET      | `/api/home`                                  | — (`homeService`, not built yet)           | `/`                                       |
| POST     | `/api/home/dismiss`                          | — (`homeService`, not built yet)           | `/`                                       |
| GET      | `/api/cron/daily`                            | — (Vercel Cron)                            | —                                         |
| GET      | `/api/shelves`                               | `shelvesService.list`                      | `/`, `/add`, `/comics/:id`, `/shelves/:id`|
| GET      | `/api/shelves/{id}`                          | `shelvesService.getShelfById`              | `/shelves/:id`                            |
| POST     | `/api/shelves`                               | `shelvesService.create`                    | shelf form (`/`, `/shelves/:id`)          |
| PATCH    | `/api/shelves/{id}`                          | `shelvesService.update`                    | shelf form (`/shelves/:id`)               |
| DELETE   | `/api/shelves/{id}`                          | `shelvesService.delete`                    | `/shelves/:id`                            |
| PUT      | `/api/shelves/{id}/order`                    | `shelvesService.reorder`                   | `/shelves/:id`                            |
| POST     | `/api/shelves/{shelf_id}/comics`             | `comicsService.addComicToShelf`            | `/comics/:id`, `/shelves/:id`             |
| DELETE   | `/api/shelves/{shelf_id}/comics/{comic_id}`  | `comicsService.removeComicFromShelf`       | `/comics/:id`                             |
| POST     | `/api/sources/preview`                       | `sourcesService.preview`                   | `/add`                                    |
| POST     | `/api/sources/check`                         | `sourcesService.checkAll`                  | `/account/data`                           |
| GET      | `/api/tags`                                  | `tagsService.list`                         | `/`                                       |
| GET      | `/api/stats`                                 | `statsService.get`                         | `/stats`                                  |
| PUT      | `/api/stats/goals/{year}`                    | `statsService.updateGoal`                  | `/stats`                                  |
| GET      | `/api/notifications`                         | `notificationsService.list`                | `/notifications`, bell dropdown           |
| GET      | `/api/notifications/unread-count`            | `notificationsService.unreadCount`         | bell badge, filter chips                  |
| POST     | `/api/notifications/{id}/read`               | `notificationsService.markRead`            | `/notifications`, bell dropdown           |
| POST     | `/api/notifications/read-all`                | `notificationsService.markAllRead`         | `/notifications`, bell dropdown           |
| DELETE   | `/api/notifications/{id}`                    | `notificationsService.delete`              | `/notifications`                          |

---

## Open points for the backend

These are decisions the frontend doesn't make. Settle them when you build the API.

1. **Cover uploads.** *Decided:* both. A `data:` URL in `cover_url` is stored, and
   `PUT /api/comics/{id}/cover` accepts a multipart upload. Both are limited to 4 MB, and a
   `data:` URL is about a third bigger than the file, so a picked photo over ~3 MB fails on Vercel.
2. **CORS.** Allow the Vercel origin(s) and `http://localhost:3000`, with the headers
   `Authorization` and `Content-Type`, and expose `Content-Disposition`.
3. **Token lifetimes.** *Decided:* 15 minutes for the access token, and 30 days (sliding) for the
   refresh token, which is not rotated (see [Authentication](#authentication)).
4. **New chapters and broken links.** Something has to set `has_new_chapter`,
   `Source.latest_chapter` and `Source.is_alive`, and create `new_chapter` and `broken_link`
   notifications. That could be a scheduled job that honours
   `settings.notify_new_chapter` and `settings.notify_broken_link`.
5. **Time zone.** Stats periods and the reading calendar use "today". Store a time zone per user,
   or assume `Asia/Ho_Chi_Minh`.
