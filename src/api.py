"""Public, read-only JSON API for the personal photo/video/post CMS.

Cloudflare Python Worker (FastAPI). Serves only GET under /api/*. Shares D1
``cms`` and R2 ``cms_media`` with the admin CMS. No Cloudflare Access — public.
"""

from workers import WorkerEntrypoint
import asgi
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import db

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# --- helpers -------------------------------------------------------------------

def _env(request: Request):
    return request.scope["env"]


def _media_url(env, file_path):
    if not file_path:
        return None
    return f"{env.R2_PUBLIC_BASE}/{file_path}"


def _excerpt(body, length=200):
    if not body:
        return ""
    text = body.strip()
    return text[:length]


def _parse_tags(tags):
    if not tags:
        return None
    parsed = [t.strip() for t in tags.split(",") if t.strip()]
    return parsed or None


# --- entry detail --------------------------------------------------------------

@app.get("/api/entries/{id}")
async def get_entry(id: int, request: Request):
    env = _env(request)
    entry = await db.get_published_detail(env, id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Not found")
    entry["url"] = _media_url(env, entry.get("file_path"))
    return entry


@app.get("/api/entries/by-slug/{slug}")
async def get_entry_by_slug(slug: str, request: Request):
    env = _env(request)
    entry = await db.get_published_detail_by_slug(env, slug)
    if entry is None:
        raise HTTPException(status_code=404, detail="Not found")
    entry["url"] = _media_url(env, entry.get("file_path"))
    return entry


# --- albums --------------------------------------------------------------------

@app.get("/api/albums")
async def get_albums(request: Request):
    env = _env(request)
    overview = await db.get_albums_overview(env)

    result = []
    for a in overview["albums"]:
        result.append({
            "id": a["id"],
            "name": a["name"],
            "count": a["count"],
            "cover_url": _media_url(env, a.get("cover")),
        })

    all_row = overview["all"]
    none_row = overview["none"]
    result.append({
        "id": "all",
        "name": "Todas",
        "count": all_row["count"],
        "cover_url": _media_url(env, all_row.get("cover")),
    })
    result.append({
        "id": "none",
        "name": "Sin album",
        "count": none_row["count"],
        "cover_url": _media_url(env, none_row.get("cover")),
    })
    return result


# --- media (photos / videos) ---------------------------------------------------

def _media_item(env, row):
    return {
        "id": row["id"],
        "slug": row["slug"],
        "title": row["title"],
        "description": row["description"],
        "taken_at": row["taken_at"],
        "is_analog": row["is_analog"],
        "camera_model": row["camera_model"],
        "film_stock": row["film_stock"],
        "width": row["width"],
        "height": row["height"],
        "album_name": row.get("album_name"),
        "tags": row.get("tags", []),
        "url": _media_url(env, row.get("file_path")),
    }


async def _media_list(request, media_type, *, album, tags, date_from, date_to,
                      is_analog, camera_model, film_stock, limit, offset):
    env = _env(request)
    rows = await db.get_media(
        env, media_type,
        album=album, tags=_parse_tags(tags),
        date_from=date_from, date_to=date_to, is_analog=is_analog,
        camera_model=camera_model, film_stock=film_stock,
        limit=limit, offset=offset,
    )
    return [_media_item(env, r) for r in rows]


@app.get("/api/photos")
async def get_photos(
    request: Request,
    album: str | None = None,
    tags: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    is_analog: int | None = None,
    camera_model: str | None = None,
    film_stock: str | None = None,
    limit: int = 24,
    offset: int = 0,
):
    return await _media_list(
        request, "photo",
        album=album, tags=tags, date_from=date_from, date_to=date_to,
        is_analog=is_analog, camera_model=camera_model, film_stock=film_stock,
        limit=limit, offset=offset,
    )


@app.get("/api/photos/filters")
async def get_photo_filters(request: Request, album: str | None = None):
    env = _env(request)
    return await db.get_media_facets(env, "photo", album=album)


@app.get("/api/photos/random")
async def get_random_photos(request: Request):
    env = _env(request)
    rows = await db.get_random_photos(env)
    return [
        {
            "id": r["id"],
            "slug": r["slug"],
            "title": r["title"],
            "url": _media_url(env, r.get("file_path")),
        }
        for r in rows
    ]


@app.get("/api/videos")
async def get_videos(
    request: Request,
    tags: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    is_analog: int | None = None,
    camera_model: str | None = None,
    film_stock: str | None = None,
    limit: int = 24,
    offset: int = 0,
):
    return await _media_list(
        request, "video",
        album=None, tags=tags, date_from=date_from, date_to=date_to,
        is_analog=is_analog, camera_model=camera_model, film_stock=film_stock,
        limit=limit, offset=offset,
    )


@app.get("/api/videos/filters")
async def get_video_filters(request: Request):
    env = _env(request)
    return await db.get_media_facets(env, "video", album=None)


# --- posts ---------------------------------------------------------------------

@app.get("/api/posts")
async def get_posts(request: Request, limit: int | None = None):
    env = _env(request)
    rows = await db.get_posts(env, limit)
    return [
        {
            "id": r["id"],
            "slug": r["slug"],
            "title": r["title"],
            "published_at": r["published_at"],
            "excerpt": _excerpt(r.get("body")),
        }
        for r in rows
    ]


# --- worker entrypoint ---------------------------------------------------------

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)
