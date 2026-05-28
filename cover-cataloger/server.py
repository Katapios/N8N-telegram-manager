import datetime as dt
import base64
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
INPUT_DIR = Path(os.getenv("COVER_CATALOG_INPUT_DIR", "/data/covers"))
OUTPUT_DIR = Path(os.getenv("COVER_CATALOG_OUTPUT_DIR", "/data/reports"))
MAX_FILES = int(os.getenv("COVER_CATALOG_MAX_FILES", "500"))
SEARCH_DELAY = float(os.getenv("COVER_CATALOG_SEARCH_DELAY_SECONDS", "0.8"))
USER_AGENT = os.getenv("COVER_CATALOG_USER_AGENT", "LazyBonesCoverCataloger/1.0 (local)")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "").strip().rstrip("/")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b").strip()
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "").strip()
VISION_MAX_IMAGE_BYTES = int(os.getenv("COVER_CATALOG_VISION_MAX_IMAGE_BYTES", "8000000"))
JOBS_LOCK = threading.Lock()
JOBS: dict[str, dict[str, Any]] = {}


def http_json(
    url: str,
    payload: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: int = 45,
) -> Any:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
        method = "POST"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def find_images() -> list[Path]:
    if not INPUT_DIR.exists():
        return []
    files = [p for p in INPUT_DIR.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
    return sorted(files, key=lambda p: str(p.relative_to(INPUT_DIR)).lower())[:MAX_FILES]


def clean_stem(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem)
    stem = re.sub(r"\b(front|cover|scan|folder|album|cd|vinyl|lp|hq|hires)\b", " ", stem, flags=re.I)
    stem = re.sub(r"\[[^\]]+\]|\([^\)]*(?:\d{3,4}x\d{3,4}|front|cover|scan)[^\)]*\)", " ", stem, flags=re.I)
    return re.sub(r"\s+", " ", stem).strip(" -")


def parse_filename(path: Path) -> dict[str, str]:
    text = clean_stem(path)
    parts = [p.strip() for p in re.split(r"\s+-\s+|\s+--\s+|\s+–\s+|\s+—\s+", text, maxsplit=1) if p.strip()]
    artist = parts[0] if len(parts) == 2 else ""
    title = parts[1] if len(parts) == 2 else text
    return {"filename_hint": text, "artist_hint": artist, "title_hint": title}


def identify_from_cover(path: Path, hints: dict[str, str]) -> dict[str, Any]:
    if not OLLAMA_BASE_URL or not OLLAMA_VISION_MODEL:
        return {
            "album_title": "",
            "performer_artist": hints["artist_hint"],
            "visible_text": "",
            "confidence": 0,
            "notes": "Vision model is not configured.",
        }
    try:
        image_bytes = path.read_bytes()
    except Exception as exc:
        return {"album_title": "", "performer_artist": "", "visible_text": "", "confidence": 0, "notes": f"Cannot read image: {exc}"}
    if len(image_bytes) > VISION_MAX_IMAGE_BYTES:
        return {
            "album_title": "",
            "performer_artist": hints["artist_hint"],
            "visible_text": "",
            "confidence": 0,
            "notes": f"Image is larger than COVER_CATALOG_VISION_MAX_IMAGE_BYTES ({VISION_MAX_IMAGE_BYTES}).",
        }

    prompt = {
        "task": (
            "Identify this music album cover. Read all visible text and infer the most likely album/release. "
            "Return only strict JSON. If uncertain, provide best candidates in notes, but do not fabricate credits."
        ),
        "file_name": path.name,
        "filename_hint": hints["filename_hint"],
        "required_json_schema": {
            "album_title": "string",
            "performer_artist": "string",
            "visible_text": "string",
            "confidence": "number 0..1",
            "notes": "short string",
        },
    }
    payload = {
        "model": OLLAMA_VISION_MODEL,
        "stream": False,
        "options": {"temperature": 0},
        "messages": [
            {
                "role": "user",
                "content": json.dumps(prompt, ensure_ascii=False),
                "images": [base64.b64encode(image_bytes).decode("ascii")],
            }
        ],
    }
    headers = {"Authorization": f"Bearer {OLLAMA_API_KEY}"} if OLLAMA_API_KEY else {}
    try:
        data = http_json(f"{OLLAMA_BASE_URL}/api/chat", payload=payload, headers=headers, timeout=180)
        content = data.get("message", {}).get("content", "")
        match = re.search(r"\{.*\}", content, flags=re.S)
        parsed = json.loads(match.group(0) if match else content)
    except Exception as exc:
        return {"album_title": "", "performer_artist": hints["artist_hint"], "visible_text": "", "confidence": 0, "notes": f"Vision extraction failed: {exc.__class__.__name__}"}

    return {
        "album_title": str(parsed.get("album_title", "") or ""),
        "performer_artist": str(parsed.get("performer_artist", "") or hints["artist_hint"] or ""),
        "visible_text": str(parsed.get("visible_text", "") or ""),
        "confidence": parsed.get("confidence", 0) if isinstance(parsed.get("confidence", 0), (int, float)) else 0,
        "notes": str(parsed.get("notes", "") or ""),
    }


def enrich_hints_with_vision(hints: dict[str, str], vision: dict[str, Any]) -> dict[str, str]:
    title = str(vision.get("album_title", "") or "").strip()
    artist = str(vision.get("performer_artist", "") or "").strip()
    visible_text = str(vision.get("visible_text", "") or "").strip()
    return {
        "filename_hint": hints["filename_hint"],
        "artist_hint": artist or hints["artist_hint"],
        "title_hint": title or hints["title_hint"],
        "visible_text": visible_text,
    }


def musicbrainz_search(hints: dict[str, str]) -> list[dict[str, Any]]:
    query_parts = []
    if hints["title_hint"]:
        query_parts.append(f'release:"{hints["title_hint"]}"')
    if hints["artist_hint"]:
        query_parts.append(f'artist:"{hints["artist_hint"]}"')
    query = " AND ".join(query_parts) or hints["filename_hint"]
    params = urllib.parse.urlencode({"query": query, "fmt": "json", "limit": "5"})
    try:
        data = http_json(f"https://musicbrainz.org/ws/2/release/?{params}", timeout=30)
    except Exception:
        return []

    result = []
    for release in data.get("releases", [])[:5]:
        artists = release.get("artist-credit", [])
        artist_name = "".join(str(a.get("name", "")) for a in artists if isinstance(a, dict)).strip()
        release_id = release.get("id", "")
        result.append({
            "title": release.get("title", ""),
            "artist": artist_name,
            "date": release.get("date", ""),
            "country": release.get("country", ""),
            "score": release.get("score", ""),
            "source": f"https://musicbrainz.org/release/{release_id}" if release_id else "",
        })
    return result


def tavily_search(hints: dict[str, str]) -> list[dict[str, str]]:
    if not TAVILY_API_KEY:
        return []
    visible_text = hints.get("visible_text", "")
    query = f'{hints["artist_hint"]} {hints["title_hint"]} {visible_text} album cover designer photographer producer artwork credits'.strip()
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": 6,
        "include_answer": True,
        "include_raw_content": False,
    }
    try:
        data = http_json("https://api.tavily.com/search", payload=payload, timeout=60)
    except Exception:
        return []

    compact = []
    if data.get("answer"):
        compact.append({"title": "Tavily answer", "url": "", "content": str(data["answer"])[:1500]})
    for item in data.get("results", [])[:6]:
        compact.append({
            "title": str(item.get("title", ""))[:200],
            "url": str(item.get("url", ""))[:500],
            "content": str(item.get("content", ""))[:1500],
        })
    return compact


def ollama_extract(path: Path, hints: dict[str, str], vision: dict[str, Any], mb: list[dict[str, Any]], web: list[dict[str, str]]) -> dict[str, Any]:
    fallback = {
        "file_name": path.name,
        "relative_path": str(path.relative_to(INPUT_DIR)),
        "album_title": str(vision.get("album_title", "") or hints["title_hint"]),
        "performer_artist": str(vision.get("performer_artist", "") or hints["artist_hint"]),
        "cover_designer": "",
        "photographer": "",
        "producer": "",
        "illustrator_or_artist": "",
        "release_year": "",
        "label": "",
        "source_urls": [],
        "confidence": vision.get("confidence", 0.25 if hints["title_hint"] else 0.0),
        "notes": "Fallback without LLM extraction.",
    }
    if not OLLAMA_BASE_URL:
        return fallback

    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "options": {"temperature": 0},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You catalog music album covers. Return only strict JSON. "
                    "Use evidence from the supplied search results. If a field is not supported, use an empty string. "
                    "Do not invent credits."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "image_file": str(path.relative_to(INPUT_DIR)),
                    "filename_hints": hints,
                    "vision_cover_identification": vision,
                    "musicbrainz_candidates": mb,
                    "web_search_results": web,
                    "required_json_schema": {
                        "album_title": "string",
                        "performer_artist": "string",
                        "cover_designer": "string",
                        "photographer": "string",
                        "producer": "string",
                        "illustrator_or_artist": "string",
                        "release_year": "string",
                        "label": "string",
                        "source_urls": ["string"],
                        "confidence": "number 0..1",
                        "notes": "short string in Russian",
                    },
                }, ensure_ascii=False),
            },
        ],
    }
    headers = {"Authorization": f"Bearer {OLLAMA_API_KEY}"} if OLLAMA_API_KEY else {}
    try:
        data = http_json(f"{OLLAMA_BASE_URL}/api/chat", payload=payload, headers=headers, timeout=120)
        content = data.get("message", {}).get("content", "")
        match = re.search(r"\{.*\}", content, flags=re.S)
        parsed = json.loads(match.group(0) if match else content)
    except Exception as exc:
        fallback["notes"] = f"LLM extraction failed: {exc.__class__.__name__}"
        return fallback

    fallback.update({k: parsed.get(k, fallback.get(k, "")) for k in fallback if k in parsed})
    fallback["file_name"] = path.name
    fallback["relative_path"] = str(path.relative_to(INPUT_DIR))
    if not isinstance(fallback.get("source_urls"), list):
        fallback["source_urls"] = []
    return fallback


def write_xlsx(rows: list[dict[str, Any]], output_path: Path) -> None:
    columns = [
        ("relative_path", "Файл"),
        ("album_title", "Название диска"),
        ("performer_artist", "Исполнитель / автор"),
        ("cover_designer", "Дизайнер обложки"),
        ("photographer", "Фотограф"),
        ("illustrator_or_artist", "Художник / иллюстратор"),
        ("producer", "Продюсер"),
        ("release_year", "Год"),
        ("label", "Лейбл"),
        ("confidence", "Уверенность"),
        ("vision_visible_text", "Текст с обложки"),
        ("vision_notes", "Заметки vision"),
        ("source_urls", "Источники"),
        ("notes", "Примечания"),
    ]

    def col_name(index: int) -> str:
        name = ""
        while index:
            index, rem = divmod(index - 1, 26)
            name = chr(65 + rem) + name
        return name

    def cell(value: Any, ref: str) -> str:
        if isinstance(value, list):
            value = "\n".join(str(v) for v in value if v)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value or ""))}</t></is></c>'

    sheet_rows = []
    header_cells = [cell(label, f"{col_name(i)}1") for i, (_, label) in enumerate(columns, start=1)]
    sheet_rows.append(f'<row r="1">{"".join(header_cells)}</row>')
    for r_index, row in enumerate(rows, start=2):
        cells = [cell(row.get(key, ""), f"{col_name(c_index)}{r_index}") for c_index, (key, _) in enumerate(columns, start=1)]
        sheet_rows.append(f'<row r="{r_index}">{"".join(cells)}</row>')

    worksheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <cols>
    <col min="1" max="1" width="42" customWidth="1"/>
    <col min="2" max="9" width="24" customWidth="1"/>
    <col min="10" max="10" width="14" customWidth="1"/>
    <col min="11" max="12" width="60" customWidth="1"/>
  </cols>
  <sheetData>{''.join(sheet_rows)}</sheetData>
</worksheet>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Album covers" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'''

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", worksheet)


def build_catalog(job_id: str | None = None) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc)
    run_id = job_id or started.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    images = find_images()
    rows = []
    for index, path in enumerate(images, start=1):
        if job_id:
            update_job(job_id, {
                "status": "running",
                "current": index,
                "total": len(images),
                "current_file": str(path.relative_to(INPUT_DIR)),
            })
        filename_hints = parse_filename(path)
        vision = identify_from_cover(path, filename_hints)
        hints = enrich_hints_with_vision(filename_hints, vision)
        mb = musicbrainz_search(hints)
        if SEARCH_DELAY:
            time.sleep(SEARCH_DELAY)
        web = tavily_search(hints)
        row = ollama_extract(path, hints, vision, mb, web)
        row["vision_visible_text"] = vision.get("visible_text", "")
        row["vision_notes"] = vision.get("notes", "")
        row["row_number"] = index
        rows.append(row)
        if SEARCH_DELAY and index < len(images):
            time.sleep(SEARCH_DELAY)

    xlsx_path = OUTPUT_DIR / f"album-cover-catalog-{run_id}.xlsx"
    json_path = OUTPUT_DIR / f"album-cover-catalog-{run_id}.json"
    write_xlsx(rows, xlsx_path)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "run_id": run_id,
        "input_dir": str(INPUT_DIR),
        "output_dir": str(OUTPUT_DIR),
        "image_count": len(images),
        "xlsx_path": str(xlsx_path),
        "json_path": str(json_path),
        "started_at": started.isoformat(),
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def update_job(job_id: str, patch: dict[str, Any]) -> None:
    with JOBS_LOCK:
        current = JOBS.get(job_id, {})
        current.update(patch)
        current["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        JOBS[job_id] = current


def run_job(job_id: str) -> None:
    try:
        update_job(job_id, {"status": "running"})
        result = build_catalog(job_id)
        update_job(job_id, {"status": "completed", "result": result})
    except Exception as exc:
        update_job(job_id, {"status": "failed", "error": str(exc), "type": exc.__class__.__name__})


def start_background_job() -> dict[str, Any]:
    with JOBS_LOCK:
        for existing_id, job in JOBS.items():
            if job.get("status") in {"queued", "running"}:
                return {
                    "ok": False,
                    "error": "A catalog job is already running.",
                    "job_id": existing_id,
                    "status": job,
                }
        job_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "current": 0,
            "total": None,
            "current_file": "",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    thread.start()
    return {
        "ok": True,
        "job_id": job_id,
        "status_url": f"/status/{job_id}",
        "message": "Catalog job started. The Excel file will be written to the reports folder when the job completes.",
    }


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"ok": True, "service": "cover-cataloger"})
            return
        if self.path == "/status":
            with JOBS_LOCK:
                self.send_json(200, {"ok": True, "jobs": JOBS})
            return
        if self.path.startswith("/status/"):
            job_id = self.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self.send_json(404, {"ok": False, "error": "Job not found", "job_id": job_id})
                return
            self.send_json(200, {"ok": True, "job": job})
            return
        self.send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        if self.path == "/start":
            result = start_background_job()
            self.send_json(202 if result.get("ok") else 409, result)
            return
        if self.path != "/run":
            self.send_json(404, {"ok": False, "error": "Not found"})
            return
        try:
            self.send_json(200, build_catalog())
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc), "type": exc.__class__.__name__})

    def log_message(self, format: str, *args: Any) -> None:
        return


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.serve_forever()
