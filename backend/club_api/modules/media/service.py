import asyncio
import io
import os
import re
import shutil
import stat
import time
import warnings
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from django.http import HttpResponse, StreamingHttpResponse
from PIL import Image, ImageOps
from psycopg.types.json import Jsonb

from club_api.core.errors import ApiError
from club_api.core.models import guid
from club_api.db.queries import Query
from club_api.db.store import MEDIA_FIELDS, normalize

THUMBNAIL_WORKERS = 1
BUSY_MESSAGE = "Сервер занят обработкой изображений. Попробуйте позже."
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 128 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}
UPLOAD_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
}
SANDBOX_CSP = "default-src 'none'; sandbox"
UUID_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


class FileResponse(StreamingHttpResponse):
    def __init__(self, *args, descriptor, **kwargs):
        super().__init__(*args, **kwargs)
        self.descriptor = descriptor

    def close(self):
        if not self.closed:
            os.close(self.descriptor)
        super().close()


def media_id(value):
    if not isinstance(value, str):
        return None
    if re.fullmatch(UUID_PATTERN, value, re.I):
        return value.lower()
    try:
        url = urlsplit(value)
        if url.scheme and url.scheme not in ("http", "https"):
            return None
        match = re.fullmatch("/(?:api/media|assets)/(" + UUID_PATTERN + ")(?:/[^/]*)?/?", url.path, re.I)
        return match[1].lower() if match else None
    except ValueError:
        return None


def sniff_media(header):
    if len(header) < 12:
        return None
    if header[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if header[4:8] == b"ftyp":
        if b"avif" in header[8:40] or b"avis" in header[8:40]:
            return "image/avif"
        if any(brand in header[8:40] for brand in (b"M4A ", b"M4B ", b"isom", b"mp42")):
            return "audio/mp4"
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "audio/wav"
    if header[:4] == b"OggS" and (b"OpusHead" in header or b"vorbis" in header):
        return "audio/ogg"
    if header[:3] == b"ID3" or (header[0] == 255 and header[1] & 230 == 226):
        return "audio/mpeg"
    return None


def parse_range(value, size):
    if not value:
        return None
    match = re.fullmatch("bytes=([0-9]*)-([0-9]*)", value)
    if not match or not (match[1] or match[2]) or any(len(part) > 16 for part in match.groups()):
        return False
    suffix = not match[1]
    first = int(match[1] or match[2])
    last = int(match[2]) if match[2] and (not suffix) else size - 1
    if first > 9007199254740991 or last > 9007199254740991 or (suffix and (not first)):
        return False
    start, end = (max(0, size - first) if suffix else first, min(last, size - 1))
    return False if start >= size or start > end else (start, end)


def render_thumbnail(data):
    if sniff_media(data[:512]) not in IMAGE_TYPES:
        raise ApiError(415, "Не удалось прочитать изображение.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if original.width * original.height > 16000000:
                    raise ValueError
                original.seek(0)
                oriented = ImageOps.exif_transpose(original)
                oriented.thumbnail((256, 256), Image.Resampling.LANCZOS)
                mode = "RGBA" if "A" in oriented.getbands() else "RGB"
                clean = Image.new(mode, oriented.size)
                clean.paste(oriented.convert(mode))
                output = io.BytesIO()
                clean.save(output, format="PNG")
                return output.getvalue()
    except ApiError:
        raise
    except Exception:
        raise ApiError(415, "Не удалось прочитать изображение.") from None


class Media:
    def __init__(self, state):
        self.state = state
        self.slots = asyncio.Semaphore(THUMBNAIL_WORKERS)
        self.pending, self.cache = ({}, OrderedDict())
        self.cache_bytes = 0

    async def directory(self):

        def prepare():
            path = self.state.settings.UPLOADS_PATH
            path.mkdir(parents=True, mode=488, exist_ok=True)
            return path.resolve()

        return await asyncio.to_thread(prepare)

    async def path(self, filename):
        if not re.fullmatch("[A-Za-z0-9][A-Za-z0-9._-]{0,254}", filename or ""):
            raise ApiError(404, "Файл не найден")
        return await self.directory() / filename

    async def get(self, id):
        rows = await self.state.database.rows("SELECT * FROM directus_files WHERE id=%s", (guid(id),))
        return normalize(rows[0]) if rows else None

    async def is_avatar(self, id):
        rows = await self.state.database.rows("SELECT id FROM alumni WHERE avatar=%s LIMIT 1", (id,))
        return bool(rows)

    async def is_published_image(self, id):
        file = await self.get(id)
        if not file or (file["metadata"] or {}).get("club_upload_kind") == "avatar" or await self.is_avatar(id):
            return False
        for table in sorted(MEDIA_FIELDS):
            if table == "alumni":
                continue
            fields = [name for name in MEDIA_FIELDS[table] if name != "audio_url"]
            condition = " OR ".join(f'''strpos(COALESCE("{field}"::text,''),%s)>0''' for field in fields)
            if self.state.database.vendor == "mysql":
                condition = " OR ".join(f'''LOCATE(%s,COALESCE(CAST("{field}" AS CHAR),''))>0''' for field in fields)
            status = "status IN ('published','done')" if table == "events" else "status='published'"
            query = f'''SELECT id FROM "{table}" WHERE {status} AND ({condition}) LIMIT 1'''
            if await self.state.database.rows(query, [id] * len(fields)):
                return True
        return False

    async def list(self, page, limit, search):
        pattern = "%" + re.sub("[\\\\%_]", lambda match: "\\" + match[0], search) + "%"
        where = "NOT EXISTS(SELECT 1 FROM alumni a WHERE a.avatar=f.id::text) AND COALESCE(f.metadata->>'club_upload_kind','')<>'avatar' AND (COALESCE(f.title,'') ILIKE %s OR f.filename_download ILIKE %s)"
        if self.state.database.vendor == "mysql":
            where = "NOT EXISTS(SELECT 1 FROM alumni a WHERE a.avatar=CAST(f.id AS CHAR(36))) AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(f.metadata,'$.club_upload_kind')),'')<>'avatar' AND (LOWER(COALESCE(f.title,'')) LIKE LOWER(%s) OR LOWER(f.filename_download) LIKE LOWER(%s))"
        rows = await self.state.database.rows(
            "SELECT f.* FROM directus_files f WHERE " + where + " ORDER BY f.created_on DESC,f.id LIMIT %s OFFSET %s",
            (pattern, pattern, limit, (page - 1) * limit),
        )
        count_sql = (
            Query(
                "SELECT count(*)::int AS count FROM directus_files f WHERE ",
                "SELECT count(*) AS count FROM directus_files f WHERE ",
            )
            + where
        )
        total = await self.state.database.rows(count_sql, (pattern, pattern))
        return {
            "items": [
                {
                    "id": str(row["id"]),
                    "title": row["title"],
                    "filename": row["filename_download"],
                    "type": row["type"],
                    "size": int(row["filesize"]),
                    "created_at": normalize(row["created_on"]),
                }
                for row in rows
            ],
            "total": total[0]["count"],
            "page": page,
            "limit": limit,
        }

    async def save(self, upload, *, filename, uploaded_by=None, kind="office", max_bytes=MAX_AUDIO_BYTES):
        directory = await self.directory()
        if (await asyncio.to_thread(shutil.disk_usage, directory)).free < max_bytes + 64 * 1024 * 1024:
            raise ApiError(507, "Недостаточно места для загрузки")
        id = str(uuid4())
        temporary, final = (directory / (id + ".upload"), None)
        descriptor = await asyncio.to_thread(
            os.open, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 384
        )
        file = os.fdopen(descriptor, "wb")
        size, header = (0, b"")
        try:
            while chunk := (await asyncio.to_thread(upload.read, 65536)):
                size += len(chunk)
                header = (header + chunk)[:512] if len(header) < 512 else header
                detected = sniff_media(header)
                maximum = min(max_bytes, MAX_IMAGE_BYTES) if detected in IMAGE_TYPES else max_bytes
                if size > maximum:
                    raise ApiError(413, "Файл превышает допустимый размер")
                await asyncio.to_thread(file.write, chunk)
            await asyncio.to_thread(file.close)
            detected = sniff_media(header)
            if detected not in UPLOAD_TYPES or (kind == "avatar" and detected not in IMAGE_TYPES):
                raise ApiError(415, "Поддерживаются JPEG, PNG, WebP, MP3, WAV, OGG и M4A")
            if kind == "avatar":
                await self.thumbnail(temporary.name)
            disk_name = id + "." + UPLOAD_TYPES[detected]
            final = directory / disk_name
            await asyncio.to_thread(os.rename, temporary, final)
            download = (
                "".join(
                    char for char in Path(filename.replace("\\", "/")).name if ord(char) >= 32 and ord(char) != 127
                )[:200]
                or disk_name
            )
            rows = await self.state.database.rows(
                "INSERT INTO directus_files(id,storage,filename_disk,filename_download,title,type,uploaded_by,uploaded_on,filesize,metadata) VALUES(%s,'local',%s,%s,%s,%s,%s,now(),%s,%s) RETURNING *",
                (id, disk_name, download, download, detected, uploaded_by, size, Jsonb({"club_upload_kind": kind})),
            )
            return normalize(rows[0])
        except BaseException:
            await asyncio.to_thread(file.close)
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
            if final:
                await asyncio.to_thread(final.unlink, missing_ok=True)
            raise

    async def delete(self, id):
        id, path = (guid(id), None)
        async with self.state.database.transaction() as connection:
            tables = ",".join(f'"{table}"' for table in sorted(MEDIA_FIELDS))
            if self.state.database.vendor == "mysql":
                await connection.lock("media.references")
            else:
                await connection.execute(f"LOCK TABLE {tables} IN SHARE MODE")
            file = await (
                await connection.execute("SELECT * FROM directus_files WHERE id=%s FOR UPDATE", (id,))
            ).fetchone()
            if not file:
                return
            shared = await (
                await connection.execute(
                    Query(
                        "SELECT id FROM directus_files WHERE storage=%s AND filename_disk=%s AND id<>%s FOR SHARE",
                        "SELECT id FROM directus_files WHERE storage=%s AND filename_disk=%s AND id<>%s LOCK IN SHARE MODE",
                    ),
                    (file["storage"], file["filename_disk"], id),
                )
            ).fetchone()
            if shared:
                raise ApiError(409, "Файл связан с другой записью медиатеки")
            for table, fields in sorted(MEDIA_FIELDS.items()):
                condition = " OR ".join(f'''strpos(COALESCE("{field}"::text,''),%s)>0''' for field in fields)
                if self.state.database.vendor == "mysql":
                    condition = " OR ".join(
                        f'''LOCATE(%s,COALESCE(CAST("{field}" AS CHAR),''))>0''' for field in fields
                    )
                query = f'SELECT id FROM "{table}" WHERE {condition} LIMIT 1'
                if await (await connection.execute(query, [id] * len(fields))).fetchone():
                    raise ApiError(409, "Файл используется. Сначала уберите его из материалов или профиля.")
            if file["storage"] != "local":
                raise ApiError(409, "Файл хранится вне локального каталога")
            path = await self.path(file["filename_disk"])
            await connection.execute("DELETE FROM directus_files WHERE id=%s", (id,))
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def thumbnail(self, filename):
        path = await self.path(filename)
        descriptor = await asyncio.to_thread(os.open, path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = await asyncio.to_thread(os.fstat, descriptor)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_IMAGE_BYTES:
                raise ApiError(415, "Не удалось прочитать изображение.")
            key = (str(path), info.st_size, info.st_mtime_ns)
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 300:
                self.cache.move_to_end(key)
                return cached[1]
            task = self.pending.get(key)
            if task is None:
                if len(self.pending) >= 8:
                    raise ApiError(503, BUSY_MESSAGE)
                owned = os.dup(descriptor)

                async def render():
                    started = False
                    try:
                        async with self.slots:

                            def load():
                                with os.fdopen(owned, "rb") as file:
                                    return render_thumbnail(file.read(MAX_IMAGE_BYTES + 1))

                            started = True
                            work = asyncio.create_task(asyncio.to_thread(load))
                            try:
                                result = await asyncio.shield(work)
                            except asyncio.CancelledError:
                                await work
                                raise
                        if key in self.cache:
                            self.cache_bytes -= len(self.cache.pop(key)[1])
                        self.cache[key] = (time.monotonic(), result)
                        self.cache_bytes += len(result)
                        while len(self.cache) > 64 or self.cache_bytes > 16 * 1024 * 1024:
                            self.cache_bytes -= len(self.cache.popitem(last=False)[1][1])
                        return result
                    finally:
                        if not started:
                            os.close(owned)
                        self.pending.pop(key, None)

                task = asyncio.create_task(render())
                self.pending[key] = task
                task.add_done_callback(lambda finished: None if finished.cancelled() else finished.exception())
            try:
                return await asyncio.wait_for(asyncio.shield(task), 3)
            except TimeoutError:
                raise ApiError(503, BUSY_MESSAGE) from None
        finally:
            await asyncio.to_thread(os.close, descriptor)

    async def close(self):
        if self.pending:
            await asyncio.gather(*self.pending.values(), return_exceptions=True)

    async def avatar(self, id):
        file = await self.get(id)
        if not file or file["storage"] != "local":
            raise ApiError(404, "Файл не найден")
        try:
            data = await self.thumbnail(file["filename_disk"])
        except ApiError as error:
            if error.status == 503:
                raise
            raise ApiError(404, "Файл не найден") from None
        except OSError:
            raise ApiError(404, "Файл не найден") from None
        return HttpResponse(
            data,
            content_type="image/png",
            headers={"Content-Security-Policy": SANDBOX_CSP, "Cache-Control": "public, max-age=300"},
        )

    async def stream(self, id, *, kind, range=None, cache="private, no-store"):
        file = await self.get(id)
        if not file or file["storage"] != "local":
            raise ApiError(404, "Файл не найден")
        try:
            descriptor = await asyncio.to_thread(
                os.open, await self.path(file["filename_disk"]), os.O_RDONLY | os.O_NOFOLLOW
            )
        except OSError:
            raise ApiError(404, "Файл не найден") from None
        owned = True
        try:
            info = await asyncio.to_thread(os.fstat, descriptor)
            if not stat.S_ISREG(info.st_mode) or not info.st_size:
                raise ApiError(404, "Файл не найден")
            detected = sniff_media(await asyncio.to_thread(os.read, descriptor, 512))
            if not detected or (detected not in IMAGE_TYPES if kind == "image" else not detected.startswith("audio/")):
                raise ApiError(404, "Файл не найден")
            selected = parse_range(range, info.st_size)
            if selected is False:
                return HttpResponse(status=416, headers={"Content-Range": f"bytes */{info.st_size}"})
            start, end = selected or (0, info.st_size - 1)
            await asyncio.to_thread(os.lseek, descriptor, start, os.SEEK_SET)

            async def chunks():
                remaining = end - start + 1
                while remaining:
                    chunk = await asyncio.to_thread(os.read, descriptor, min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

            headers = {
                "Content-Security-Policy": SANDBOX_CSP,
                "Cache-Control": cache,
                "Accept-Ranges": "bytes",
                "Content-Length": str(end - start + 1),
            }
            if selected:
                headers["Content-Range"] = f"bytes {start}-{end}/{info.st_size}"
            owned = False
            return FileResponse(
                chunks(), descriptor=descriptor, content_type=detected, status=206 if selected else 200, headers=headers
            )
        finally:
            if owned:
                await asyncio.to_thread(os.close, descriptor)
