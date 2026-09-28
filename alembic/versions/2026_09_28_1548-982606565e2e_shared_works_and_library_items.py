"""shared works and library items

Comics become shared works (deduplicated by source link) plus one library item per owner.
Library item ids are the old comic ids and work source ids are the old source ids, so API ids,
primary sources, shelves, reading logs and notifications keep pointing at the same things.

Revision ID: 982606565e2e
Revises: 921fe4ffa68e
Create Date: 2026-09-28 15:48:39.031193

"""
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Sequence, Union
from urllib.parse import urlsplit, urlunsplit

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '982606565e2e'
down_revision: Union[str, Sequence[str], None] = '921fe4ffa68e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



WORK_LINK_SQL = "url ~ '^[^:]+://[^/]+/.'"


def identifies_work(url: str) -> bool:
    """Frozen copy of app/services/urls.identifies_work: a page link, not a bare site."""
    return bool(urlsplit(normalize_url(url)).path.strip("/"))


def normalize_url(url: str) -> str:
    """Frozen copy of app/services/urls.normalize_url (migrations must not import app code)."""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    if parts.username:
        host = f"{parts.username}@{host}"
    path = parts.path.rstrip("/") if parts.path not in ("", "/") else ""
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


# lightweight table handles for bulk inserts
works_t = sa.table(
    "works",
    *(sa.column(c) for c in ("id", "title", "description", "publication_status", "latest_chapter", "cover_url", "cover",
                             "cover_updated_at", "owner_user_id", "created_at", "updated_at")),
    sa.column("alt_titles", postgresql.ARRAY(sa.Text())),
    sa.column("authors", postgresql.ARRAY(sa.Text())),
    sa.column("genres", postgresql.ARRAY(sa.Text())),
    sa.column("external_ids", postgresql.JSONB()),
)
work_sources_t = sa.table(
    "work_sources",
    *(sa.column(c) for c in ("id", "work_id", "private_owner_id", "position", "site_name", "url", "favicon_url", "chapter_url",
                             "latest_chapter", "is_alive", "last_checked_at", "added_by_user_id", "created_at")),
)
library_items_t = sa.table(
    "library_items",
    *(sa.column(c) for c in ("id", "user_id", "work_id", "status", "current_chapter", "rating", "is_favorite", "note",
                             "has_new_chapter", "primary_source_id", "last_chapter_url", "last_read_at", "created_at", "updated_at")),
)
library_item_tags_t = sa.table("library_item_tags", sa.column("library_item_id"), sa.column("tag_id"), sa.column("position"))
shelf_items_t = sa.table("shelf_items", sa.column("shelf_id"), sa.column("library_item_id"), sa.column("position"), sa.column("added_at"))
comics_t = sa.table(
    "comics",
    *(sa.column(c) for c in ("id", "user_id", "title", "author", "description", "cover_url", "cover", "cover_updated_at", "status",
                             "current_chapter", "total_chapters", "rating", "is_favorite", "note", "primary_source_id",
                             "has_new_chapter", "last_read_at", "created_at", "updated_at")),
)
sources_t = sa.table(
    "sources",
    *(sa.column(c) for c in ("id", "comic_id", "position", "site_name", "url", "favicon_url", "chapter_url", "latest_chapter",
                             "is_alive", "last_checked_at", "created_at")),
)
comic_tags_t = sa.table("comic_tags", sa.column("comic_id"), sa.column("tag_id"), sa.column("position"))
shelf_comics_t = sa.table("shelf_comics", sa.column("shelf_id"), sa.column("comic_id"), sa.column("position"), sa.column("added_at"))
tags_t = sa.table("tags", sa.column("id"), sa.column("user_id"), sa.column("name"), sa.column("created_at"))

_EPOCH = datetime(1970, 1, 1).astimezone()


def _insert(table, rows: list[dict]) -> None:
    if rows:
        op.bulk_insert(table, rows)


def upgrade() -> None:
    """Upgrade schema: create works / library items, convert every comic, drop the old tables."""
    op.create_table('works',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('alt_titles', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('authors', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('genres', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('publication_status', sa.String(length=16), server_default='unknown', nullable=False),
    sa.Column('latest_chapter', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cover_url', sa.Text(), server_default='', nullable=False),
    sa.Column('cover', sa.LargeBinary(), nullable=True),
    sa.Column('cover_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('external_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('owner_user_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("publication_status IN ('ongoing', 'completed', 'hiatus', 'cancelled', 'unknown')", name=op.f('ck_works_publication_status')),
    sa.CheckConstraint('latest_chapter >= 0', name=op.f('ck_works_latest_chapter_non_negative')),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_works_owner_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_works'))
    )
    op.create_index('ix_works_owner_user_id', 'works', ['owner_user_id'], unique=False)
    op.create_index('uq_works_external_id_anilist', 'works', [sa.literal_column("(external_ids ->> 'anilist')")], unique=True, postgresql_where=sa.text("external_ids ? 'anilist'"))
    op.create_index('uq_works_external_id_mangadex', 'works', [sa.literal_column("(external_ids ->> 'mangadex')")], unique=True, postgresql_where=sa.text("external_ids ? 'mangadex'"))
    op.create_table('work_sources',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('work_id', sa.Uuid(), nullable=False),
    sa.Column('private_owner_id', sa.Uuid(), nullable=True),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('site_name', sa.String(length=100), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('favicon_url', sa.Text(), server_default='', nullable=False),
    sa.Column('chapter_url', sa.Text(), server_default='', nullable=False),
    sa.Column('latest_chapter', sa.Integer(), server_default='0', nullable=False),
    sa.Column('is_alive', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('added_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['added_by_user_id'], ['users.id'], name=op.f('fk_work_sources_added_by_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['private_owner_id'], ['users.id'], name=op.f('fk_work_sources_private_owner_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['work_id'], ['works.id'], name=op.f('fk_work_sources_work_id_works'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_work_sources'))
    )
    op.create_index('ix_work_sources_work_id_position', 'work_sources', ['work_id', 'position'], unique=False)
    # only page links are unique: a bare site ("https://cuutruyen.net") may appear on many works
    op.create_index('uq_work_sources_url_private', 'work_sources', ['private_owner_id', 'url'], unique=True, postgresql_where=sa.text(f"private_owner_id IS NOT NULL AND {WORK_LINK_SQL}"))
    op.create_index('uq_work_sources_url_shared', 'work_sources', ['url'], unique=True, postgresql_where=sa.text(f"private_owner_id IS NULL AND {WORK_LINK_SQL}"))
    op.create_table('library_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('work_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='plan_to_read', nullable=False),
    sa.Column('current_chapter', sa.Integer(), server_default='0', nullable=False),
    sa.Column('rating', sa.Float(), server_default='0', nullable=False),
    sa.Column('is_favorite', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('has_new_chapter', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('primary_source_id', sa.Uuid(), nullable=True),
    sa.Column('last_chapter_url', sa.Text(), server_default='', nullable=False),
    sa.Column('last_read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('reading', 'completed', 'plan_to_read', 'on_hold', 'dropped')", name=op.f('ck_library_items_status')),
    sa.CheckConstraint('current_chapter >= 0', name=op.f('ck_library_items_current_chapter_non_negative')),
    sa.CheckConstraint('rating >= 0 AND rating <= 5', name=op.f('ck_library_items_rating_range')),
    sa.ForeignKeyConstraint(['primary_source_id'], ['work_sources.id'], name=op.f('fk_library_items_primary_source_id_work_sources'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_library_items_user_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['work_id'], ['works.id'], name=op.f('fk_library_items_work_id_works'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_library_items')),
    sa.UniqueConstraint('user_id', 'work_id', name='uq_library_items_user_id_work_id')
    )
    op.create_index('ix_library_items_user_id_updated_at', 'library_items', ['user_id', sa.literal_column('updated_at DESC')], unique=False)
    op.create_index('ix_library_items_work_id', 'library_items', ['work_id'], unique=False)
    op.create_table('library_item_tags',
    sa.Column('library_item_id', sa.Uuid(), nullable=False),
    sa.Column('tag_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_items.id'], name=op.f('fk_library_item_tags_library_item_id_library_items'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], name=op.f('fk_library_item_tags_tag_id_tags'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('library_item_id', 'tag_id', name=op.f('pk_library_item_tags'))
    )
    op.create_index('ix_library_item_tags_tag_id', 'library_item_tags', ['tag_id'], unique=False)
    op.create_table('shelf_items',
    sa.Column('shelf_id', sa.Uuid(), nullable=False),
    sa.Column('library_item_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=True),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['library_item_id'], ['library_items.id'], name=op.f('fk_shelf_items_library_item_id_library_items'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['shelf_id'], ['shelves.id'], name=op.f('fk_shelf_items_shelf_id_shelves'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('shelf_id', 'library_item_id', name=op.f('pk_shelf_items'))
    )
    op.create_index('ix_shelf_items_library_item_id', 'shelf_items', ['library_item_id'], unique=False)

    conn = op.get_bind()
    comics = conn.execute(sa.text(
        "SELECT id, user_id, title, author, description, cover_url, cover, cover_updated_at, status, current_chapter,"
        " total_chapters, rating, is_favorite, note, primary_source_id, has_new_chapter, last_read_at, created_at, updated_at"
        " FROM comics ORDER BY created_at, id"
    )).mappings().all()
    sources = conn.execute(sa.text(
        "SELECT id, comic_id, position, site_name, url, favicon_url, chapter_url, latest_chapter, is_alive,"
        " last_checked_at, created_at FROM sources ORDER BY comic_id, position, created_at, id"
    )).mappings().all()
    sources_of = defaultdict(list)
    for src in sources:
        sources_of[src["comic_id"]].append(src)

    # 1. Group comics that share a source link (union-find). There are no external ids yet,
    #    so links are the only dedupe key.
    parent = {c["id"]: c["id"] for c in comics}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first_with_url = {}
    for src in sources:
        if not identifies_work(src["url"]):
            continue  # a bare site ("https://cuutruyen.net") says nothing about which work it is
        key = normalize_url(src["url"])
        if key in first_with_url:
            parent[find(src["comic_id"])] = find(first_with_url[key])
        else:
            first_with_url[key] = src["comic_id"]
    groups = defaultdict(list)
    for c in comics:  # created_at order, so each group starts with its oldest comic
        groups[find(c["id"])].append(c)

    works, work_sources, items = [], [], []
    item_of = {}  # comic id → library item id
    source_of = {}  # old source id → work source id
    for group in groups.values():
        first = group[0]
        work_id = uuid.uuid4()
        # a work with a real page link is shared; with none (or only a bare site) it was entered by hand,
        # so it stays private to its owner
        shared = any(identifies_work(src["url"]) for c in group for src in sources_of[c["id"]])
        owner = None if shared else first["user_id"]
        stored_cover = next((c for c in group if c["cover"] is not None), None)
        linked_cover = next((c for c in group if c["cover_url"]), None)
        works.append({
            "id": work_id,
            "title": first["title"],
            "alt_titles": sorted({c["title"] for c in group} - {first["title"]}),
            "description": next((c["description"] for c in group if c["description"]), ""),
            "authors": [first["author"]] if first["author"] else [],
            "genres": [],  # comic tags were the users' own: they become library item tags
            "publication_status": "unknown",
            "latest_chapter": max(c["total_chapters"] for c in group),
            "cover_url": "" if stored_cover else (linked_cover["cover_url"] if linked_cover else ""),
            "cover": stored_cover["cover"] if stored_cover else None,
            "cover_updated_at": stored_cover["cover_updated_at"] if stored_cover else None,
            "external_ids": {},
            "owner_user_id": owner,
            "created_at": min(c["created_at"] for c in group),
            "updated_at": max(c["updated_at"] for c in group),
        })

        kept = {}  # normalized url → work source row
        for c in group:
            for src in sources_of[c["id"]]:
                key = normalize_url(src["url"])
                if key in kept:
                    kept[key]["latest_chapter"] = max(kept[key]["latest_chapter"], src["latest_chapter"])
                    source_of[src["id"]] = kept[key]["id"]
                    continue
                kept[key] = {
                    "id": src["id"],
                    "work_id": work_id,
                    "private_owner_id": owner,
                    "position": len(kept),
                    "site_name": src["site_name"],
                    "url": key,
                    "favicon_url": src["favicon_url"],
                    "chapter_url": src["chapter_url"],
                    "latest_chapter": src["latest_chapter"],
                    "is_alive": src["is_alive"],
                    "last_checked_at": src["last_checked_at"],
                    "added_by_user_id": c["user_id"],
                    "created_at": src["created_at"],
                }
                source_of[src["id"]] = src["id"]
        work_sources.extend(kept.values())

        # 2. One library item per user (a user can save a work only once). If one user had several
        #    comics in the group, they merge: the oldest comic's id stays (so its links keep working),
        #    the progress comes from the copy read furthest.
        by_user = defaultdict(list)
        for c in group:
            by_user[c["user_id"]].append(c)
        for user_id, owned in by_user.items():
            oldest = owned[0]  # the group is in created_at order
            keep = max(owned, key=lambda c: (c["current_chapter"], c["last_read_at"] or _EPOCH, c["updated_at"]))
            notes = list(dict.fromkeys(c["note"] for c in [keep, *owned] if c["note"]))
            items.append({
                "id": oldest["id"],
                "user_id": user_id,
                "work_id": work_id,
                "status": keep["status"],
                "current_chapter": keep["current_chapter"],
                "rating": keep["rating"],
                "is_favorite": any(c["is_favorite"] for c in owned),
                "note": "\n\n".join(notes),
                "has_new_chapter": any(c["has_new_chapter"] for c in owned),
                "primary_source_id": source_of.get(keep["primary_source_id"]),
                "last_chapter_url": "",
                "last_read_at": keep["last_read_at"],
                "created_at": min(c["created_at"] for c in owned),
                "updated_at": max(c["updated_at"] for c in owned),
            })
            for c in owned:
                item_of[c["id"]] = oldest["id"]

    _insert(works_t, works)
    _insert(work_sources_t, work_sources)
    _insert(library_items_t, items)

    # 3. Tags and shelves follow their comic to its library item.
    tag_rows = {}
    for row in conn.execute(sa.text("SELECT comic_id, tag_id, position FROM comic_tags ORDER BY comic_id, position")).mappings():
        key = (item_of[row["comic_id"]], row["tag_id"])
        tag_rows.setdefault(key, row["position"])
    by_item = defaultdict(list)
    for (item_id, tag_id), _position in sorted(tag_rows.items(), key=lambda kv: kv[1]):
        by_item[item_id].append(tag_id)
    _insert(library_item_tags_t, [
        {"library_item_id": item_id, "tag_id": tag_id, "position": i}
        for item_id, tag_ids in by_item.items() for i, tag_id in enumerate(tag_ids)
    ])
    shelf_rows = {}
    for row in conn.execute(sa.text("SELECT shelf_id, comic_id, position, added_at FROM shelf_comics")).mappings():
        shelf_rows.setdefault((row["shelf_id"], item_of[row["comic_id"]]), row)
    _insert(shelf_items_t, [
        {"shelf_id": shelf_id, "library_item_id": item_id, "position": row["position"], "added_at": row["added_at"]}
        for (shelf_id, item_id), row in shelf_rows.items()
    ])

    # 4. Reading logs and notifications: comic_id → library_item_id.
    mapping = [{"comic": comic_id, "item": item_id} for comic_id, item_id in item_of.items()]
    op.add_column("reading_logs", sa.Column("library_item_id", sa.Uuid(), nullable=True))
    op.add_column("notifications", sa.Column("library_item_id", sa.Uuid(), nullable=True))
    if mapping:
        conn.execute(sa.text("UPDATE reading_logs SET library_item_id = :item WHERE comic_id = :comic"), mapping)
        conn.execute(sa.text("UPDATE notifications SET library_item_id = :item WHERE comic_id = :comic"), mapping)
    # merged comics may now log the same day twice: add them up into one row
    op.execute("""
        WITH dup AS (
            SELECT library_item_id, day, (array_agg(id ORDER BY created_at, id))[1] AS keep, sum(chapters) AS total
            FROM reading_logs GROUP BY library_item_id, day HAVING count(*) > 1
        ), summed AS (
            UPDATE reading_logs r SET chapters = dup.total FROM dup WHERE r.id = dup.keep RETURNING r.id
        )
        DELETE FROM reading_logs r USING dup
        WHERE r.library_item_id = dup.library_item_id AND r.day = dup.day AND r.id <> dup.keep
    """)
    op.drop_constraint(op.f('uq_reading_logs_comic_id_day'), 'reading_logs', type_='unique')
    op.drop_constraint(op.f('fk_reading_logs_comic_id_comics'), 'reading_logs', type_='foreignkey')
    op.drop_column('reading_logs', 'comic_id')
    op.alter_column('reading_logs', 'library_item_id', nullable=False)
    op.create_unique_constraint('uq_reading_logs_library_item_id_day', 'reading_logs', ['library_item_id', 'day'])
    op.create_foreign_key(op.f('fk_reading_logs_library_item_id_library_items'), 'reading_logs', 'library_items', ['library_item_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint(op.f('fk_notifications_comic_id_comics'), 'notifications', type_='foreignkey')
    op.drop_column('notifications', 'comic_id')
    op.create_foreign_key(op.f('fk_notifications_library_item_id_library_items'), 'notifications', 'library_items', ['library_item_id'], ['id'], ondelete='CASCADE')

    # 5. The old per-user tables.
    op.drop_constraint(op.f('fk_comics_primary_source_id_sources'), 'comics', type_='foreignkey')
    op.drop_index(op.f('ix_comic_tags_tag_id'), table_name='comic_tags')
    op.drop_table('comic_tags')
    op.drop_index(op.f('ix_shelf_comics_comic_id'), table_name='shelf_comics')
    op.drop_table('shelf_comics')
    op.drop_index(op.f('ix_sources_comic_id_position'), table_name='sources')
    op.drop_table('sources')
    op.drop_index(op.f('ix_comics_user_id_title_lower'), table_name='comics')
    op.drop_index(op.f('ix_comics_user_id_updated_at'), table_name='comics')
    op.drop_table('comics')


def downgrade() -> None:
    """Rebuild per-user comics from works + library items. Genres become the user's tags."""
    op.create_table('comics',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('author', sa.String(length=200), server_default='', nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('cover_url', sa.Text(), server_default='', nullable=False),
    sa.Column('cover', sa.LargeBinary(), nullable=True),
    sa.Column('cover_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='plan_to_read', nullable=False),
    sa.Column('current_chapter', sa.Integer(), server_default='0', nullable=False),
    sa.Column('total_chapters', sa.Integer(), server_default='0', nullable=False),
    sa.Column('rating', sa.Float(), server_default='0', nullable=False),
    sa.Column('is_favorite', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('primary_source_id', sa.Uuid(), nullable=True),
    sa.Column('has_new_chapter', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('last_read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('reading', 'completed', 'plan_to_read', 'on_hold', 'dropped')", name=op.f('ck_comics_status')),
    sa.CheckConstraint('current_chapter <= total_chapters', name=op.f('ck_comics_progress_within_total')),
    sa.CheckConstraint('current_chapter >= 0 AND total_chapters >= 0', name=op.f('ck_comics_chapters_non_negative')),
    sa.CheckConstraint('rating >= 0 AND rating <= 5', name=op.f('ck_comics_rating_range')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_comics_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_comics'))
    )
    op.create_index('ix_comics_user_id_title_lower', 'comics', ['user_id', sa.literal_column('lower(title)')], unique=False)
    op.create_index('ix_comics_user_id_updated_at', 'comics', ['user_id', sa.literal_column('updated_at DESC')], unique=False)
    op.create_table('sources',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('comic_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('site_name', sa.String(length=100), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('favicon_url', sa.Text(), server_default='', nullable=False),
    sa.Column('chapter_url', sa.Text(), server_default='', nullable=False),
    sa.Column('latest_chapter', sa.Integer(), server_default='0', nullable=False),
    sa.Column('is_alive', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['comic_id'], ['comics.id'], name=op.f('fk_sources_comic_id_comics'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sources'))
    )
    op.create_index('ix_sources_comic_id_position', 'sources', ['comic_id', 'position'], unique=False)
    op.create_table('comic_tags',
    sa.Column('comic_id', sa.Uuid(), nullable=False),
    sa.Column('tag_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.ForeignKeyConstraint(['comic_id'], ['comics.id'], name=op.f('fk_comic_tags_comic_id_comics'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], name=op.f('fk_comic_tags_tag_id_tags'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('comic_id', 'tag_id', name=op.f('pk_comic_tags'))
    )
    op.create_index('ix_comic_tags_tag_id', 'comic_tags', ['tag_id'], unique=False)
    op.create_table('shelf_comics',
    sa.Column('shelf_id', sa.Uuid(), nullable=False),
    sa.Column('comic_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=True),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['comic_id'], ['comics.id'], name=op.f('fk_shelf_comics_comic_id_comics'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['shelf_id'], ['shelves.id'], name=op.f('fk_shelf_comics_shelf_id_shelves'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('shelf_id', 'comic_id', name=op.f('pk_shelf_comics'))
    )
    op.create_index('ix_shelf_comics_comic_id', 'shelf_comics', ['comic_id'], unique=False)

    conn = op.get_bind()
    items = conn.execute(sa.text(
        "SELECT li.id, li.user_id, li.work_id, li.status, li.current_chapter, li.rating, li.is_favorite, li.note,"
        " li.primary_source_id, li.has_new_chapter, li.last_read_at, li.created_at, li.updated_at,"
        " w.title, w.authors, w.description, w.genres, w.latest_chapter, w.cover_url, w.cover, w.cover_updated_at"
        " FROM library_items li JOIN works w ON w.id = li.work_id ORDER BY li.created_at, li.id"
    )).mappings().all()
    work_sources = conn.execute(sa.text(
        "SELECT id, work_id, position, site_name, url, favicon_url, chapter_url, latest_chapter, is_alive,"
        " last_checked_at, created_at FROM work_sources ORDER BY work_id, position, id"
    )).mappings().all()
    sources_of = defaultdict(list)
    for src in work_sources:
        sources_of[src["work_id"]].append(src)

    comics, sources, primaries = [], [], []
    copied_once = set()
    for item in items:
        # the first holder of a work keeps the original source ids; later holders get copies
        keep_ids = item["work_id"] not in copied_once
        copied_once.add(item["work_id"])
        new_id = {}
        for src in sources_of[item["work_id"]]:
            sid = src["id"] if keep_ids else uuid.uuid4()
            new_id[src["id"]] = sid
            sources.append({
                "id": sid, "comic_id": item["id"], "position": src["position"], "site_name": src["site_name"],
                "url": src["url"], "favicon_url": src["favicon_url"], "chapter_url": src["chapter_url"],
                "latest_chapter": src["latest_chapter"], "is_alive": src["is_alive"],
                "last_checked_at": src["last_checked_at"], "created_at": src["created_at"],
            })
        primary = new_id.get(item["primary_source_id"]) or (next(iter(new_id.values()), None))
        total = item["latest_chapter"]
        comics.append({
            "id": item["id"], "user_id": item["user_id"], "title": item["title"],
            "author": ", ".join(item["authors"])[:200], "description": item["description"],
            "cover_url": item["cover_url"], "cover": item["cover"], "cover_updated_at": item["cover_updated_at"],
            "status": item["status"], "current_chapter": min(item["current_chapter"], total), "total_chapters": total,
            "rating": item["rating"], "is_favorite": item["is_favorite"], "note": item["note"],
            "primary_source_id": None, "has_new_chapter": item["has_new_chapter"], "last_read_at": item["last_read_at"],
            "created_at": item["created_at"], "updated_at": item["updated_at"],
        })
        if primary:
            primaries.append({"comic": item["id"], "source": primary})
    _insert(comics_t, comics)
    _insert(sources_t, sources)
    if primaries:
        conn.execute(sa.text("UPDATE comics SET primary_source_id = :source WHERE id = :comic"), primaries)
    op.create_foreign_key(
        op.f('fk_comics_primary_source_id_sources'), 'comics', 'sources', ['primary_source_id'], ['id'], ondelete='SET NULL'
    )

    # tags: the user's own first, then the work's genres (created as tags when the user has none by that name)
    tags_by_user = defaultdict(dict)
    for row in conn.execute(sa.text("SELECT id, user_id, name FROM tags")).mappings():
        tags_by_user[row["user_id"]][row["name"].lower()] = row["id"]
    own = defaultdict(list)
    for row in conn.execute(sa.text("SELECT library_item_id, tag_id FROM library_item_tags ORDER BY library_item_id, position")).mappings():
        own[row["library_item_id"]].append(row["tag_id"])
    new_tags, comic_tags = [], []
    for item in items:
        tag_ids = list(own[item["id"]])
        for genre in item["genres"]:
            known = tags_by_user[item["user_id"]]
            if genre.lower() not in known:
                known[genre.lower()] = uuid.uuid4()
                new_tags.append({"id": known[genre.lower()], "user_id": item["user_id"], "name": genre[:60], "created_at": item["created_at"]})
            if known[genre.lower()] not in tag_ids:
                tag_ids.append(known[genre.lower()])
        comic_tags.extend({"comic_id": item["id"], "tag_id": t, "position": i} for i, t in enumerate(tag_ids))
    _insert(tags_t, new_tags)
    _insert(comic_tags_t, comic_tags)
    op.execute("INSERT INTO shelf_comics (shelf_id, comic_id, position, added_at) SELECT shelf_id, library_item_id, position, added_at FROM shelf_items")

    # library item ids are the comic ids
    op.add_column('reading_logs', sa.Column('comic_id', sa.Uuid(), nullable=True))
    op.add_column('notifications', sa.Column('comic_id', sa.Uuid(), nullable=True))
    op.execute("UPDATE reading_logs SET comic_id = library_item_id")
    op.execute("UPDATE notifications SET comic_id = library_item_id")
    op.drop_constraint('uq_reading_logs_library_item_id_day', 'reading_logs', type_='unique')
    op.drop_constraint(op.f('fk_reading_logs_library_item_id_library_items'), 'reading_logs', type_='foreignkey')
    op.drop_column('reading_logs', 'library_item_id')
    op.alter_column('reading_logs', 'comic_id', nullable=False)
    op.create_unique_constraint(op.f('uq_reading_logs_comic_id_day'), 'reading_logs', ['comic_id', 'day'])
    op.create_foreign_key(op.f('fk_reading_logs_comic_id_comics'), 'reading_logs', 'comics', ['comic_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint(op.f('fk_notifications_library_item_id_library_items'), 'notifications', type_='foreignkey')
    op.drop_column('notifications', 'library_item_id')
    op.create_foreign_key(op.f('fk_notifications_comic_id_comics'), 'notifications', 'comics', ['comic_id'], ['id'], ondelete='CASCADE')

    op.drop_index('ix_shelf_items_library_item_id', table_name='shelf_items')
    op.drop_table('shelf_items')
    op.drop_index('ix_library_item_tags_tag_id', table_name='library_item_tags')
    op.drop_table('library_item_tags')
    op.drop_index('ix_library_items_work_id', table_name='library_items')
    op.drop_index('ix_library_items_user_id_updated_at', table_name='library_items')
    op.drop_table('library_items')
    op.drop_index('uq_work_sources_url_shared', table_name='work_sources')
    op.drop_index('uq_work_sources_url_private', table_name='work_sources')
    op.drop_index('ix_work_sources_work_id_position', table_name='work_sources')
    op.drop_table('work_sources')
    op.drop_index('uq_works_external_id_mangadex', table_name='works', postgresql_where=sa.text("external_ids ? 'mangadex'"))
    op.drop_index('uq_works_external_id_anilist', table_name='works', postgresql_where=sa.text("external_ids ? 'anilist'"))
    op.drop_index('ix_works_owner_user_id', table_name='works')
    op.drop_table('works')
