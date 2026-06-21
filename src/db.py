"""Read layer for the public CMS API.

All queries filter status='published'. D1 is async and accepts positional
``?`` params only (no ``:name``). Results come back as JsProxy objects, so we
convert them to native Python via ``to_py()``.

URLs are built in api.py, not here — this module returns raw ``file_path`` keys.
"""


# --- JsProxy conversion helpers ------------------------------------------------

def _to_dict(r):
    if r is None:
        return None
    return r.to_py() if hasattr(r, "to_py") else dict(r)


def _to_list(res):
    rows = res.results
    if hasattr(rows, "to_py"):
        return rows.to_py()
    return [_to_dict(x) for x in rows]


# --- SQL constants -------------------------------------------------------------

_ENTRY_COLUMNS = """
    e.id, e.type, e.title, e.description, e.body, e.slug,
    e.file_path, e.file_size, e.width, e.height,
    e.taken_at, e.is_analog, e.camera_model, e.film_stock, e.duration,
    e.album_id, e.status, e.created_at, e.published_at
"""

_SQL_DETAIL = f"""
SELECT {_ENTRY_COLUMNS}, a.name AS album_name
FROM entry e
LEFT JOIN album a ON a.id = e.album_id
WHERE e.id = ? AND e.status = 'published'
"""

_SQL_DETAIL_TAGS = """
SELECT t.name
FROM entry_tag et
JOIN tag t ON t.id = et.tag_id
WHERE et.entry_id = ?
ORDER BY t.name
"""

_SQL_ALBUMS = """
SELECT
  a.id,
  a.name,
  (SELECT COUNT(*) FROM entry e WHERE e.album_id = a.id
     AND e.type = 'photo' AND e.status = 'published') AS count,
  (SELECT file_path FROM entry e2 WHERE e2.album_id = a.id
     AND e2.type = 'photo' AND e2.status = 'published'
     ORDER BY e2.id LIMIT 1) AS cover
FROM album a
ORDER BY a.name
"""

# Virtual album totals + covers.
_SQL_ALL_PHOTOS = """
SELECT COUNT(*) AS count,
  (SELECT file_path FROM entry e2 WHERE e2.type = 'photo'
     AND e2.status = 'published' ORDER BY e2.id LIMIT 1) AS cover
FROM entry e WHERE e.type = 'photo' AND e.status = 'published'
"""

_SQL_NO_ALBUM_PHOTOS = """
SELECT COUNT(*) AS count,
  (SELECT file_path FROM entry e2 WHERE e2.type = 'photo'
     AND e2.status = 'published' AND e2.album_id IS NULL
     ORDER BY e2.id LIMIT 1) AS cover
FROM entry e WHERE e.type = 'photo' AND e.status = 'published'
  AND e.album_id IS NULL
"""

_SQL_POSTS = """
SELECT e.id, e.slug, e.title, e.published_at, e.body
FROM entry e
WHERE e.type = 'post' AND e.status = 'published'
ORDER BY e.published_at DESC
"""


# --- detail --------------------------------------------------------------------

async def get_published_detail(env, id):
    row = await env.cms.prepare(_SQL_DETAIL).bind(id).first()
    entry = _to_dict(row)
    if entry is None:
        return None
    res = await env.cms.prepare(_SQL_DETAIL_TAGS).bind(id).all()
    entry["tags"] = [r["name"] for r in _to_list(res)]
    return entry


# --- albums --------------------------------------------------------------------

async def get_albums_overview(env):
    res = await env.cms.prepare(_SQL_ALBUMS).all()
    albums = _to_list(res)

    all_row = _to_dict(await env.cms.prepare(_SQL_ALL_PHOTOS).first())
    none_row = _to_dict(await env.cms.prepare(_SQL_NO_ALBUM_PHOTOS).first())

    return {
        "albums": albums,
        "all": all_row,
        "none": none_row,
    }


# --- media (photos / videos) ---------------------------------------------------

def _build_media_where(media_type, *, album, tags, date_from, date_to,
                       is_analog, camera_model, film_stock):
    """Return (where_sql, params) for a media query. Positional params only."""
    where = ["e.type = ?", "e.status = 'published'"]
    params = [media_type]

    # Album dimension applies to photos only.
    if media_type == "photo" and album is not None:
        if album == "all":
            pass
        elif album == "none":
            where.append("e.album_id IS NULL")
        else:
            where.append("e.album_id = ?")
            params.append(album)

    if tags:
        placeholders = ",".join("?" for _ in tags)
        where.append(
            "e.id IN (SELECT entry_id FROM entry_tag et "
            "JOIN tag t ON t.id = et.tag_id "
            f"WHERE t.name IN ({placeholders}) "
            "GROUP BY entry_id HAVING COUNT(DISTINCT t.name) = ?)"
        )
        params.extend(tags)
        params.append(len(tags))

    if date_from is not None:
        where.append("e.taken_at >= ?")
        params.append(date_from)
    if date_to is not None:
        where.append("e.taken_at <= ?")
        params.append(date_to)
    if is_analog is not None:
        where.append("e.is_analog = ?")
        params.append(is_analog)
    if camera_model is not None:
        where.append("e.camera_model = ?")
        params.append(camera_model)
    if film_stock is not None:
        where.append("e.film_stock = ?")
        params.append(film_stock)

    return " AND ".join(where), params


async def get_media(env, media_type, *, album=None, tags=None,
                    date_from=None, date_to=None, is_analog=None,
                    camera_model=None, film_stock=None, limit=24, offset=0):
    where_sql, params = _build_media_where(
        media_type, album=album, tags=tags, date_from=date_from,
        date_to=date_to, is_analog=is_analog, camera_model=camera_model,
        film_stock=film_stock,
    )

    sql = f"""
SELECT
  e.id, e.title, e.description, e.taken_at, e.is_analog,
  e.camera_model, e.film_stock, e.width, e.height, e.file_path,
  a.name AS album_name,
  (SELECT group_concat(t.name) FROM entry_tag et
     JOIN tag t ON t.id = et.tag_id WHERE et.entry_id = e.id) AS tags
FROM entry e
LEFT JOIN album a ON a.id = e.album_id
WHERE {where_sql}
ORDER BY e.id DESC
LIMIT ? OFFSET ?
"""
    params = params + [limit, offset]
    res = await env.cms.prepare(sql).bind(*params).all()
    rows = _to_list(res)
    for r in rows:
        raw = r.get("tags")
        r["tags"] = raw.split(",") if raw else []
    return rows


# --- facets --------------------------------------------------------------------

async def get_media_facets(env, media_type, album=None):
    where_sql, params = _build_media_where(
        media_type, album=album, tags=None, date_from=None, date_to=None,
        is_analog=None, camera_model=None, film_stock=None,
    )

    tags_sql = f"""
SELECT t.name AS name, COUNT(*) AS count
FROM entry e
JOIN entry_tag et ON et.entry_id = e.id
JOIN tag t ON t.id = et.tag_id
WHERE {where_sql}
GROUP BY t.name
ORDER BY count DESC, t.name
"""
    tags = _to_list(await env.cms.prepare(tags_sql).bind(*params).all())

    cameras_sql = f"""
SELECT DISTINCT e.camera_model AS name FROM entry e
WHERE {where_sql} AND e.camera_model IS NOT NULL AND e.camera_model != ''
ORDER BY e.camera_model
"""
    cameras = [r["name"] for r in
               _to_list(await env.cms.prepare(cameras_sql).bind(*params).all())]

    films_sql = f"""
SELECT DISTINCT e.film_stock AS name FROM entry e
WHERE {where_sql} AND e.film_stock IS NOT NULL AND e.film_stock != ''
ORDER BY e.film_stock
"""
    films = [r["name"] for r in
             _to_list(await env.cms.prepare(films_sql).bind(*params).all())]

    analog_sql = f"""
SELECT DISTINCT e.is_analog AS v FROM entry e WHERE {where_sql}
"""
    analog_vals = [r["v"] for r in
                   _to_list(await env.cms.prepare(analog_sql).bind(*params).all())]

    dates_sql = f"""
SELECT MIN(e.taken_at) AS min, MAX(e.taken_at) AS max FROM entry e
WHERE {where_sql} AND e.taken_at IS NOT NULL
"""
    dates = _to_dict(await env.cms.prepare(dates_sql).bind(*params).first())

    return {
        "tags": tags,
        "camera_models": cameras,
        "film_stocks": films,
        "is_analog": sorted(v for v in analog_vals if v is not None),
        "dates": dates,
    }


# --- posts ---------------------------------------------------------------------

async def get_posts(env, limit=None):
    sql = _SQL_POSTS
    if limit is not None:
        sql = sql + "\nLIMIT ?"
        res = await env.cms.prepare(sql).bind(limit).all()
    else:
        res = await env.cms.prepare(sql).all()
    return _to_list(res)
