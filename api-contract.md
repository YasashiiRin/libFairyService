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
- [Shelves](#shelves)
- [Sources](#sources)
- [Tags](#tags)
- [Stats](#stats)
- [Notifications](#notifications)
- [Endpoint index](#endpoint-index)
- [Open points for the backend](#open-points-for-the-backend)

---

## Conventions

### Base URL and paths

- The frontend reads the backend origin from `VITE_API_URL` (for example `https://api.example.com`).
- Every path starts with `/api`.
- Path IDs are strings (the frontend URL-encodes them).

### Authentication

- Every endpoint except `POST /api/auth/login` and `POST /api/auth/refresh` requires
  `Authorization: Bearer <access_token>`.
- A missing, expired or invalid access token → **401**. The frontend then calls
  `POST /api/auth/refresh` **once** and retries the original request. If the refresh
  fails too, it clears the session and redirects to `/login`.
- Concurrent 401s share a single refresh attempt, so the refresh endpoint can rotate
  the refresh token safely.

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

Errors: **401** `invalid_credentials` (wrong username/email or password; don't reveal which one).
This 401 does not trigger the refresh flow.

Used by: `/login`.

### `POST /api/auth/refresh`

No auth header.

Request: `{ "refresh_token": "…" }`

Response **200**: `{ "access_token": "…", "refresh_token": "…" }`. `refresh_token`
is optional: if you leave it out, the client keeps the old one. Rotating it is recommended.

Errors: **401** `invalid_refresh_token` (expired, revoked or unknown). The client then logs out.

Used by: the HTTP client, automatically, after any 401.

### `POST /api/auth/logout`

Request: `{ "refresh_token": "…" }`. Revokes this device's session only.

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
}
```

### `GET /api/users/me`

Response **200** `User`.

Used by: app start-up (restores the saved session), `/account/data` (reloads `last_backup_at` after an export).

### `PATCH /api/users/me`

Request: `{ "display_name"?: string, "bio"?: string }`

Response **200** `User`.

Errors: **422** `validation_error` (for example an empty `display_name`).

Used by: `/account/profile`.

### `PUT /api/users/me/avatar`

Request: `multipart/form-data`, with the image in the `file` field. Images only, 2 MB max.

Response **200** `User` with the new `avatar_url`.

Errors: **400** `invalid_file` (not an image, or unreadable), **413** `file_too_large`.

Used by: `/account/profile`.

### `PATCH /api/users/me/settings`

Request: `Partial<UserSettings>`. Only the fields that changed are sent.

Response **200** `User`.

Errors: **422** `validation_error`.

Used by: `/account/appearance` and the theme toggle in the header (`theme`,
`font_size`, `sparkle_enabled`), and `/account/notifications` (`notify_*`, `daily_reminder_*`).

### `POST /api/users/me/password`

Request: `{ "current_password": "…", "new_password": "…" }`

Response **204**.

Errors: **400** `wrong_password` (the UI shows it under the "current password" field),
**422** `validation_error` (the new password is too weak).

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

### `Comic`

```ts
{
  id: string; title: string; author: string; description: string; cover_url: string;
  status: 'reading' | 'completed' | 'plan_to_read' | 'on_hold' | 'dropped';
  current_chapter: number; total_chapters: number;
  rating: number;            // 0–5
  is_favorite: boolean; note: string; tags: string[];
  sources: Source[]; primary_source_id: string;
  shelf_ids: string[];
  has_new_chapter: boolean;
  last_read_at: string; created_at: string; updated_at: string;
}
Source = {
  id: string; site_name: string; url: string; favicon_url: string; chapter_url: string;
  latest_chapter: number; is_alive: boolean; last_checked_at: string;
}
```

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
  The server may keep them, or assign new IDs and remap `primary_source_id` to match.
- `shelf_ids` may include the virtual `"all"` shelf. Ignore it.
- `cover_url` may be an `https://` URL or a `data:image/…;base64,…` URL from a file the user picked.
  See [Open points](#open-points-for-the-backend).

Response **201** `Comic`.

Errors: **422** `validation_error` (empty title, `current_chapter > total_chapters`, or `rating` outside 0–5).

Used by: `/add`.

### `PATCH /api/comics/{id}`

Request `ComicUpdate`: any subset of the `ComicCreate` fields. When `sources` is
given, it **replaces** the whole list. Sets `updated_at`.

Response **200** `Comic`.

Errors: **404** `comic_not_found`, **422** `validation_error`.

Used by: `/comics/:id` (status, rating, note, tags, sources, details).

### `DELETE /api/comics/{id}`

Response **204**. Also removes the comic from every shelf order and deletes its notifications.

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

- clamps the value to `0..total_chapters`,
- sets `status` to `completed` when it reaches `total_chapters`, otherwise to `reading`,
- sets `last_read_at` and `updated_at` to now,
- logs the chapters read today for the stats (reading calendar, streak, monthly chart),
- recommended (the mock doesn't do this yet): clears `has_new_chapter` when the user catches up with the latest source chapter.

Response **200** `Comic`.

Errors: **404** `comic_not_found`, **422** `validation_error`.

Used by: `/comics/:id` (chapter stepper), `/` and `/shelves/:id` ("đọc tiếp" +1 chapter).

### `POST /api/comics/{id}/sources`

Request: `Source`. The client sends an `id` (`src_<timestamp>`), which the server may replace.

Response **201** `Comic` with the source appended.

Errors: **404** `comic_not_found`, **422** `validation_error`.

Used by: `/add` (the "add as secondary source" option when the title already exists).

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

## Endpoint index

| Method   | Path                                         | Service function                           | Used by                                   |
| -------- | -------------------------------------------- | ------------------------------------------ | ----------------------------------------- |
| POST     | `/api/auth/login`                            | `authService.login`                        | `/login`                                  |
| POST     | `/api/auth/refresh`                          | `http.ts` (automatic)                      | every page, after a 401                   |
| POST     | `/api/auth/logout`                           | `authService.logout`                       | avatar menu, `/account`                   |
| GET      | `/api/users/me`                              | `userService.getCurrentUser`               | app start-up, `/account/data`             |
| PATCH    | `/api/users/me`                              | `userService.updateProfile`                | `/account/profile`                        |
| PUT      | `/api/users/me/avatar`                       | `userService.uploadAvatar`                 | `/account/profile`                        |
| PATCH    | `/api/users/me/settings`                     | `userService.updateSettings`               | `/account/appearance`, `/account/notifications`, theme toggle |
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

1. **Cover uploads.** `/add` currently sends a picked cover file as a `data:` URL
   inside `cover_url`. Either accept it (store the image and return a hosted URL,
   with a size limit and **413** `file_too_large`), or add
   `POST /api/comics/{id}/cover` (multipart, like the avatar) and switch the page to it.
2. **CORS.** Allow the Vercel origin(s) and `http://localhost:3000`, with the headers
   `Authorization` and `Content-Type`, and expose `Content-Disposition`.
3. **Token lifetimes.** The client only needs the access token to fail with 401 once
   it expires. Suggested: 15 minutes for the access token, 30 days for the refresh token, rotated on refresh.
4. **New chapters and broken links.** Something has to set `has_new_chapter`,
   `Source.latest_chapter` and `Source.is_alive`, and create `new_chapter` and `broken_link`
   notifications. That could be a scheduled job that honours
   `settings.notify_new_chapter` and `settings.notify_broken_link`.
5. **Time zone.** Stats periods and the reading calendar use "today". Store a time zone per user,
   or assume `Asia/Ho_Chi_Minh`.
