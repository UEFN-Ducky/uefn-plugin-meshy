"""Meshy AI API — HTTP client + MCP tools (standalone Store plugin).

Calls https://api.meshy.ai/openapi with a Bearer key from DPAPI credentials
(secret key ``meshy_api_key``). Stdlib only (urllib).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("uefn.plugin.meshy")

SECRET_KEY = "meshy_api_key"
BASE_URL = "https://api.meshy.ai"
SITE_URL = "https://www.meshy.ai"
ANIM_CATALOG_URL = "https://api.meshy.ai/web/public/animations/resources"
DEFAULT_TIMEOUT = 60
INTENT = (
    r"\b(meshy|meshy\.ai|text[- ]?to[- ]?3d|image[- ]?to[- ]?3d|"
    r"rig(?:ging)?|animat(?:e|ion)s?|discover|community)\b"
)
_MODEL_PATH_RE = re.compile(
    r"/3d-models/([A-Za-z0-9][A-Za-z0-9_.%-]{2,200})",
    re.I,
)
_MODEL_UUID_RE = re.compile(
    r"^(?P<title>.+)-(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.I,
)
_FREE_MESH_EXTS = (".glb", ".gltf", ".fbx", ".obj", ".stl", ".usdz")
# Task kinds that prefer FBX over GLB when downloading (UEFN skeletal path).
FBX_FIRST_KINDS = frozenset({"rigging", "animations", "rig", "animation"})

# kind → GET path for task status (POST create paths differ slightly)
TASK_GET_PATHS: dict[str, str] = {
    "text-to-3d": "/openapi/v2/text-to-3d/{id}",
    "image-to-3d": "/openapi/v1/image-to-3d/{id}",
    "multi-image-to-3d": "/openapi/v1/multi-image-to-3d/{id}",
    "text-to-image": "/openapi/v1/text-to-image/{id}",
    "image-to-image": "/openapi/v1/image-to-image/{id}",
    "remesh": "/openapi/v1/remesh/{id}",
    "retexture": "/openapi/v1/retexture/{id}",
    "convert": "/openapi/v1/convert/{id}",
    "resize": "/openapi/v1/resize/{id}",
    "uv-unwrap": "/openapi/v1/uv-unwrap/{id}",
    "rigging": "/openapi/v1/rigging/{id}",
    "animations": "/openapi/v1/animations/{id}",
}


def _api_key() -> str:
    from backend.agent.secrets import get_key

    return (get_key(SECRET_KEY) or "").strip()


def encode_image(path: str) -> str:
    """Local image path → data URI."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"image not found: {path}")
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(p.suffix.lower(), "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def resolve_image(image: str) -> str:
    """Pass through http(s)/data URIs; encode local paths."""
    s = (image or "").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://", "data:")):
        return s
    return encode_image(s)


def auth_header(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def normalize_kind(kind: str) -> str:
    k = (kind or "").strip().lower().replace("_", "-")
    aliases = {
        "textto3d": "text-to-3d",
        "t23d": "text-to-3d",
        "imageto3d": "image-to-3d",
        "i23d": "image-to-3d",
        "multiimage": "multi-image-to-3d",
        "multi-image": "multi-image-to-3d",
        "texttoimage": "text-to-image",
        "t2i": "text-to-image",
        "imagetoimage": "image-to-image",
        "i2i": "image-to-image",
        "animation": "animations",
        "animate": "animations",
        "rig": "rigging",
        "uvunwrap": "uv-unwrap",
        "uv": "uv-unwrap",
    }
    return aliases.get(k, k)


def _format_from_url_key(key: str, url: str) -> str:
    """Infer file format from a Meshy URL field name or path."""
    kl = (key or "").lower()
    for fmt in ("fbx", "glb", "gltf", "usdz", "obj", "stl", "blend", "3mf", "png", "jpg"):
        if kl.endswith(f"_{fmt}_url") or kl.endswith(f"_{fmt}") or f".{fmt}" in kl:
            return fmt
    path = str(url or "").split("?")[0].lower()
    for fmt in ("fbx", "glb", "gltf", "usdz", "obj", "stl", "blend", "3mf", "png", "jpg", "jpeg", "webp"):
        if path.endswith(f".{fmt}"):
            return "jpg" if fmt == "jpeg" else fmt
    return ""


def _append_url_asset(
    assets: list[dict[str, Any]],
    url: Any,
    *,
    key: str = "",
    asset_type: str = "3D_MODEL",
) -> None:
    if not isinstance(url, str) or not url.strip():
        return
    fmt = _format_from_url_key(key, url)
    row: dict[str, Any] = {"asset": url.strip(), "asset_type": asset_type}
    if fmt:
        row["format"] = fmt
    if key:
        row["key"] = key
    assets.append(row)


def _flatten_result_urls(result: Any, assets: list[dict[str, Any]]) -> None:
    """Pull nested rig/anim result URLs into the flat results list."""
    if not isinstance(result, dict):
        return
    for key, val in result.items():
        if key == "basic_animations" and isinstance(val, dict):
            for anim_key, anim_url in val.items():
                _append_url_asset(assets, anim_url, key=anim_key, asset_type="ANIMATION")
            continue
        if isinstance(val, str) and (
            key.endswith("_url") or key.endswith("_glb") or key.endswith("_fbx")
        ):
            atype = "ANIMATION" if "animation" in key.lower() or "walking" in key.lower() or "running" in key.lower() else "3D_MODEL"
            if "rigged" in key.lower() or "character" in key.lower() or "armature" in key.lower():
                atype = "3D_MODEL"
            _append_url_asset(assets, val, key=key, asset_type=atype)


def sort_download_assets(
    assets: list[dict[str, Any]],
    *,
    kind: str = "",
) -> list[dict[str, Any]]:
    """Order assets for download — FBX-first for rig/anim, else GLB-first."""
    k = normalize_kind(kind) if kind else ""
    fbx_first = k in FBX_FIRST_KINDS or k in {"rigging", "animations"}

    def rank(item: dict[str, Any]) -> tuple[int, str]:
        fmt = str(item.get("format") or "").lower()
        url = str(item.get("asset") or "").lower()
        if fbx_first:
            if fmt == "fbx" or url.endswith(".fbx") or ".fbx?" in url:
                return (0, fmt)
            if fmt == "glb" or url.endswith(".glb") or ".glb?" in url:
                return (1, fmt)
        else:
            if fmt == "glb" or url.endswith(".glb") or ".glb?" in url:
                return (0, fmt)
            if fmt == "fbx" or url.endswith(".fbx") or ".fbx?" in url:
                return (1, fmt)
        if item.get("asset_type") == "IMAGE":
            return (3, fmt)
        if item.get("asset_type") == "THUMBNAIL":
            return (4, fmt)
        return (2, fmt)

    return sorted([a for a in assets if isinstance(a, dict) and a.get("asset")], key=rank)


def parse_task_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Meshy task JSON body (used by tools + self-check)."""
    status = str(raw.get("status") or "")
    progress = raw.get("progress", 0)
    err = raw.get("task_error") if isinstance(raw.get("task_error"), dict) else {}
    err_msg = str(err.get("message") or "").strip() if err else ""
    model_urls = raw.get("model_urls") if isinstance(raw.get("model_urls"), dict) else {}
    texture_urls = raw.get("texture_urls") if isinstance(raw.get("texture_urls"), list) else []
    image_urls = raw.get("image_urls") if isinstance(raw.get("image_urls"), list) else []
    # Single-image fields used by some 2D endpoints.
    single_image = raw.get("image_url")
    result_obj = raw.get("result") if isinstance(raw.get("result"), dict) else None
    assets: list[dict[str, Any]] = []
    for fmt, url in model_urls.items():
        if url:
            assets.append({"asset": url, "asset_type": "3D_MODEL", "format": str(fmt)})
    for url in image_urls:
        if url:
            assets.append({"asset": url, "asset_type": "IMAGE"})
    if isinstance(single_image, str) and single_image.strip():
        assets.append({"asset": single_image.strip(), "asset_type": "IMAGE"})
    for item in texture_urls:
        if isinstance(item, dict):
            for key, url in item.items():
                if url and isinstance(url, str):
                    assets.append({"asset": url, "asset_type": "TEXTURE", "map": key})
        elif isinstance(item, str) and item:
            assets.append({"asset": item, "asset_type": "TEXTURE"})
    _flatten_result_urls(result_obj, assets)
    thumb = raw.get("thumbnail_url")
    if thumb:
        assets.append({"asset": thumb, "asset_type": "THUMBNAIL"})
    return {
        "id": raw.get("id"),
        "type": raw.get("type"),
        "status": status,
        "progress": progress,
        "failure_reason": err_msg or None,
        "model_urls": model_urls,
        "image_urls": image_urls,
        "texture_urls": texture_urls,
        "thumbnail_url": thumb,
        "result": result_obj,
        "results": assets,
        "finished": status == "SUCCEEDED",
        "failed": status in {"FAILED", "CANCELED"},
        "consumed_credits": raw.get("consumed_credits"),
        "face_count": raw.get("face_count"),
    }


class MeshyError(Exception):
    def __init__(self, message: str, *, status: int | None = None, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class MeshyClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        key = (api_key if api_key is not None else _api_key()).strip()
        if not key:
            raise MeshyError("Meshy API key not set. Paste it in Settings → Meshy.")
        self.api_key = key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = auth_header(self.api_key)
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail_body = ""
            try:
                detail_body = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            detail: Any = detail_body
            try:
                detail = json.loads(detail_body) if detail_body else {}
            except (ValueError, TypeError):
                pass
            msg = _http_error_message(exc.code, detail)
            raise MeshyError(msg, status=exc.code, detail=detail) from exc
        except urllib.error.URLError as exc:
            raise MeshyError(f"Meshy network error: {exc.reason}") from exc
        if not body.strip():
            return {}
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise MeshyError(f"Meshy returned non-JSON: {body[:200]}") from exc
        if isinstance(parsed, dict):
            return parsed
        # Some list endpoints return arrays — wrap for callers that expect dict.
        return {"items": parsed}

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, payload)

    def balance(self) -> dict[str, Any]:
        return self.get("/openapi/v1/balance")

    def submit(self, path: str, payload: dict[str, Any]) -> str:
        r = self.post(path, payload)
        task_id = r.get("result")
        if not task_id:
            raise MeshyError(f"No result task id in response: {r}")
        return str(task_id)

    def status(self, kind: str, task_id: str) -> dict[str, Any]:
        k = normalize_kind(kind)
        tmpl = TASK_GET_PATHS.get(k)
        if not tmpl:
            raise MeshyError(
                f"Unknown task kind {kind!r}. Use one of: {', '.join(sorted(TASK_GET_PATHS))}"
            )
        raw = self.get(tmpl.format(id=task_id.strip()))
        parsed = parse_task_payload(raw)
        parsed["kind"] = k
        return parsed

    def wait(
        self,
        kind: str,
        task_id: str,
        *,
        poll_interval: int = 5,
        max_attempts: int = 180,
    ) -> dict[str, Any]:
        for _ in range(max_attempts):
            result = self.status(kind, task_id)
            if result["finished"] or result["failed"]:
                return result
            time.sleep(max(1, poll_interval))
        raise MeshyError(
            f"Task {task_id} did not finish within {max_attempts * poll_interval}s"
        )

    def download_url(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            dest.write_bytes(resp.read())
        return dest

    def download_results(self, status_result: dict[str, Any], output_dir: str | Path) -> list[str]:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        kind = str(status_result.get("kind") or status_result.get("type") or "")
        ordered = sort_download_assets(
            list(status_result.get("results") or []),
            kind=kind,
        )
        for item in ordered:
            url = str(item["asset"])
            name = url.split("/")[-1].split("?")[0] or "asset.bin"
            key = str(item.get("key") or "")
            fmt = item.get("format")
            if key and ("/" not in name or name.startswith("output")):
                # Prefer stable names from Meshy field keys for nested result URLs.
                ext = f".{fmt}" if fmt and not name.lower().endswith(f".{fmt}") else ""
                if "." not in Path(name).name:
                    name = f"{key}{ext or ('.bin' if not fmt else '')}"
                elif key and not name.lower().startswith(key.lower()[:8]):
                    stem = Path(name).stem
                    name = f"{key}_{stem}{Path(name).suffix or ext}"
            if fmt and "." not in name:
                name = f"{name}.{fmt}"
            dest = out / name
            if dest.exists():
                stem, suf = dest.stem, dest.suffix
                dest = out / f"{stem}_{len(paths)}{suf}"
            self.download_url(url, dest)
            paths.append(str(dest))
        return paths


def _http_error_message(code: int, detail: Any) -> str:
    snippet = ""
    if isinstance(detail, dict):
        snippet = str(
            detail.get("message")
            or detail.get("detail")
            or detail.get("error")
            or detail
        )[:240]
    elif detail:
        snippet = str(detail)[:240]
    if code == 401:
        return (
            "Meshy rejected the API key (401). Re-check Settings → Meshy."
            + (f" ({snippet})" if snippet else "")
        )
    if code == 402:
        return "Meshy: insufficient credits (402). Buy credits at meshy.ai."
    if code == 429:
        return "Meshy rate limited (429). Wait and retry."
    return f"Meshy error {code}" + (f": {snippet}" if snippet else "")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)



def credit_gate(confirm_spend: bool, estimated_credits: int, label: str) -> str | None:
    """Hard spend lock — paid jobs need confirm_spend=true after ducky_ask_user OK."""
    if int(estimated_credits or 0) <= 0:
        return None
    if confirm_spend:
        return None
    return (
        f"Error: CREDIT GATE — {label} costs ~{estimated_credits} credits. "
        "0) Call meshy_discover_search first for FREE community matches and show links. "
        "1) Call meshy_balance. "
        "2) Call ducky_ask_user with a yes/no spend question (include the ~credit estimate) — "
        "never ask only in chat text. "
        "3) Only if they approve: retry with confirm_spend=true. Do not invent approval."
    )


def format_balance_detail(balance_payload: dict[str, Any]) -> str:
    """Human line for Settings → Test (and self-check)."""
    bal = balance_payload.get("balance")
    if bal is None:
        return "Connected"
    return f"Connected — {bal} credits"


def test_api_key(api_key: str = "") -> dict[str, Any]:
    """Settings → Test: GET /openapi/v1/balance with a draft or saved key."""
    key = (api_key or "").strip()
    if not key:
        return {"ok": False, "detail": "Paste a Meshy API key first"}
    try:
        return {"ok": True, "detail": format_balance_detail(MeshyClient(api_key=key).balance())}
    except MeshyError as exc:
        return {"ok": False, "detail": str(exc)}


def _default_download_dir(task_id: str) -> Path:
    return Path(tempfile.gettempdir()) / "uefn-ducky-meshy" / task_id


def fetch_animation_catalog() -> list[dict[str, Any]]:
    """Public Meshy animation library (no API key)."""
    req = urllib.request.Request(ANIM_CATALOG_URL, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise MeshyError(f"Animation catalog network error: {exc.reason}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise MeshyError(f"Animation catalog non-JSON: {body[:200]}") from exc
    result = parsed.get("result") if isinstance(parsed, dict) else None
    rows = result.get("list") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise MeshyError("Animation catalog missing result.list")
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "action_id": row.get("id"),
                "name": row.get("name"),
                "category": row.get("category"),
                "sub_category": row.get("subCategory"),
                "key": row.get("key"),
                "rig_type": row.get("rigType"),
                "is_free": row.get("isFree"),
                "preview_url": row.get("previewUrl"),
            }
        )
    return out


def filter_animations(
    rows: list[dict[str, Any]],
    *,
    query: str = "",
    category: str = "",
    limit: int = 40,
) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    cat = (category or "").strip().lower()
    matched: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name") or "").lower()
        key = str(row.get("key") or "").lower()
        c = str(row.get("category") or "").lower()
        sub = str(row.get("sub_category") or "").lower()
        if cat and cat not in c and cat not in sub:
            continue
        if q and q not in name and q not in key and q not in c and q not in sub:
            continue
        matched.append(row)
        if len(matched) >= max(1, int(limit or 40)):
            break
    return matched


def slugify_discover_query(query: str) -> str:
    """Turn a free-text query into a Meshy /tags/ slug."""
    s = re.sub(r"[^a-z0-9]+", "-", (query or "").strip().lower()).strip("-")
    return (s[:80] if s else "free")


def parse_model_slug(slug: str) -> dict[str, str]:
    """Parse `/3d-models/<Title-uuid>` slug → id + title."""
    raw = urllib.parse.unquote((slug or "").strip().strip("/"))
    if raw.startswith("3d-models/"):
        raw = raw.split("/", 1)[1]
    m = _MODEL_UUID_RE.match(raw)
    if not m:
        return {"id": "", "title": raw.replace("-", " ").strip(), "slug": raw}
    title = m.group("title").replace("-", " ").strip()
    return {"id": m.group("id").lower(), "title": title, "slug": raw}


def _http_get_text(url: str, *, timeout: int = DEFAULT_TIMEOUT) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "UEFN-Ducky-Meshy/1.0", "Accept": "text/html,application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        raise MeshyError(f"HTTP {exc.code} fetching {url}: {body[:160]}") from exc
    except urllib.error.URLError as exc:
        raise MeshyError(f"Network error fetching {url}: {exc.reason}") from exc


def model_entry_from_slug(slug: str) -> dict[str, Any]:
    parsed = parse_model_slug(slug)
    page_url = f"{SITE_URL}/3d-models/{parsed['slug']}"
    return {
        "id": parsed["id"],
        "title": parsed["title"] or parsed["slug"],
        "slug": parsed["slug"],
        "page_url": page_url,
        "free_community": True,
    }


def extract_model_slugs(html: str) -> list[str]:
    """Deduped `/3d-models/...` slugs from Meshy SEO/Discover HTML."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _MODEL_PATH_RE.finditer(html or ""):
        slug = urllib.parse.unquote(m.group(1))
        if slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return out


def discover_search(query: str, limit: int = 8) -> dict[str, Any]:
    """Browse FREE Meshy community / tag pages (no API key, no credits)."""
    q = (query or "").strip()
    if not q:
        raise MeshyError("query is required")
    n = max(1, min(int(limit or 8), 24))
    tag = slugify_discover_query(q)
    urls = [
        f"{SITE_URL}/tags/{urllib.parse.quote(tag)}",
        f"{SITE_URL}/3d-models?q={urllib.parse.quote(q)}",
        f"{SITE_URL}/discover?q={urllib.parse.quote(q)}",
    ]
    # Also try the first keyword alone when multi-word tags miss.
    words = [w for w in re.split(r"[^a-z0-9]+", q.lower()) if len(w) >= 3]
    if words and words[0] != tag:
        urls.insert(1, f"{SITE_URL}/tags/{urllib.parse.quote(words[0])}")

    slugs: list[str] = []
    seen: set[str] = set()
    errors: list[str] = []
    for url in urls:
        try:
            html = _http_get_text(url)
        except MeshyError as exc:
            errors.append(str(exc))
            continue
        for slug in extract_model_slugs(html):
            if slug in seen:
                continue
            seen.add(slug)
            slugs.append(slug)
            if len(slugs) >= n:
                break
        if len(slugs) >= n:
            break

    results = [model_entry_from_slug(s) for s in slugs[:n]]
    browse_url = f"{SITE_URL}/discover?q={urllib.parse.quote(q)}"
    return {
        "query": q,
        "tag": tag,
        "count": len(results),
        "browse_url": browse_url,
        "results": results,
        "chat_links": [
            f"- [{r['title']}]({r['page_url']})" + (f" (`{r['id']}`)" if r.get("id") else "")
            for r in results
        ],
        "note": (
            "FREE community browse only — no generation credits. "
            "Paste these links in chat so the user can preview. "
            "Meshy does not expose free GLB via OpenAPI; user downloads GLB/FBX on the page "
            "(account login; community download quota — not generation credits), "
            "then meshy_import_to_blender / meshy_import_to_uefn with the local path."
        ),
        "errors": errors,
    }


def discover_get(url_or_slug: str) -> dict[str, Any]:
    """Fetch one FREE community model page (preview metadata + page link)."""
    raw = (url_or_slug or "").strip()
    if not raw:
        raise MeshyError("url_or_slug is required")
    if raw.startswith(("http://", "https://")):
        path = urllib.parse.urlparse(raw).path
        m = _MODEL_PATH_RE.search(path)
        if not m:
            raise MeshyError(f"Not a Meshy model page URL: {raw}")
        slug = urllib.parse.unquote(m.group(1))
        page_url = f"{SITE_URL}/3d-models/{slug}"
    else:
        parsed = parse_model_slug(raw)
        slug = parsed["slug"]
        page_url = f"{SITE_URL}/3d-models/{slug}"

    html = _http_get_text(page_url)
    entry = model_entry_from_slug(slug)
    og_title = re.search(r'property="og:title" content="([^"]*)"', html)
    og_image = re.search(r'property="og:image" content="([^"]*)"', html)
    og_desc = re.search(r'property="og:description" content="([^"]*)"', html)
    if og_title:
        title = urllib.parse.unquote_plus(
            og_title.group(1).replace("&quot;", '"').replace("&#39;", "'")
        ).strip().strip('"')
        if title:
            entry["title"] = title
    if og_image:
        entry["thumbnail_url"] = og_image.group(1)
    if og_desc:
        entry["description"] = (
            og_desc.group(1).replace("&quot;", '"').replace("&amp;", "&")[:300]
        )

    # Public pages expose proprietary .meshy preview URLs — not free GLB via API.
    mu = re.search(r'modelUrl\\?":\\?"(https:[^"\\]+)', html)
    if mu:
        entry["preview_model_url"] = mu.group(1)
    fc = re.search(r'faceCount\\?":(\d+)', html)
    if fc:
        entry["face_count"] = int(fc.group(1))

    free_urls = [
        u
        for u in re.findall(r'https://[^"\'\\\s<>]+', html)
        if any(u.lower().split("?")[0].endswith(ext) for ext in _FREE_MESH_EXTS)
        and "meshy.ai" in u.lower()
    ]
    entry["free_mesh_urls"] = sorted(set(free_urls))[:8]
    entry["page_url"] = page_url
    entry["free_community"] = True
    entry["download_hint"] = (
        "Open page_url while logged into Meshy, download FREE community GLB/FBX "
        "(uses community download quota — not generation credits), then import the local file. "
        "Do NOT spend credits to regenerate if this model fits."
    )
    return entry


def _looks_like_model_page(src: str) -> bool:
    s = (src or "").strip()
    if "/3d-models/" in s or s.startswith("3d-models/"):
        return True
    if s.startswith(("http://", "https://")):
        return False
    if Path(s).expanduser().is_file():
        return False
    lower = s.lower().split("?")[0]
    if any(lower.endswith(ext) for ext in _FREE_MESH_EXTS):
        return False
    # Bare slug / uuid → treat as community page id.
    return bool(s)


def discover_download_free(url_or_path: str, output_dir: str = "") -> dict[str, Any]:
    """Download ONLY a free standard mesh (glb/fbx/obj/stl/usdz). Refuses paid/proprietary."""
    src = (url_or_path or "").strip()
    if not src:
        raise MeshyError("url_or_path is required")

    if _looks_like_model_page(src):
        meta = discover_get(src)
        free = list(meta.get("free_mesh_urls") or [])
        if not free:
            raise MeshyError(
                "FREE DOWNLOAD GATE — no public GLB/FBX URL on this community page. "
                f"Open {meta.get('page_url')} in the browser (logged in), download the free "
                "community mesh (quota, not generation credits), then call again with the "
                "local file path or a direct .glb/.fbx URL. Do not generate with credits."
            )
        src = free[0]

    lower = src.split("?")[0].lower()
    if lower.endswith(".meshy"):
        raise MeshyError(
            "FREE DOWNLOAD GATE — refusing proprietary .meshy preview files. "
            "Download GLB/FBX from the Meshy community page in the browser (free quota), "
            "or pass a local .glb/.fbx path."
        )
    if src.startswith(("http://", "https://")) and not any(
        lower.endswith(ext) for ext in _FREE_MESH_EXTS
    ):
        raise MeshyError(
            "FREE DOWNLOAD GATE — only free .glb/.gltf/.fbx/.obj/.stl/.usdz URLs or local paths. "
            "No credit-spend downloads."
        )
    if not src.startswith(("http://", "https://")):
        local_path = Path(src).expanduser()
        if local_path.is_file() and not any(
            local_path.suffix.lower() == ext for ext in _FREE_MESH_EXTS
        ):
            raise MeshyError(
                f"FREE DOWNLOAD GATE — unsupported free format {local_path.suffix!r}. "
                "Use .glb/.fbx/.obj/.stl/.usdz."
            )

    dest_root = Path(output_dir.strip()) if output_dir.strip() else (
        Path(tempfile.gettempdir()) / "uefn-ducky-meshy" / "discover"
    )
    dest_root.mkdir(parents=True, exist_ok=True)
    local = resolve_local_or_download(src)
    src_path = Path(local)
    target = dest_root / src_path.name
    if src_path.resolve() != target.resolve():
        target.write_bytes(src_path.read_bytes())
        local = str(target.resolve())
    return {
        "ok": True,
        "path": local,
        "source": src,
        "free_only": True,
        "note": "Free mesh only — no Meshy generation credits spent.",
    }


def resolve_local_or_download(url_or_path: str, *, default_ext: str = ".glb") -> str:
    """Return a local filesystem path; download http(s) URLs into temp (CDN, no auth)."""
    src = (url_or_path or "").strip()
    if not src:
        raise MeshyError("url_or_path is required")
    if src.startswith(("http://", "https://")):
        name = src.split("/")[-1].split("?")[0] or f"model{default_ext}"
        lower = name.lower()
        if not any(lower.endswith(ext) for ext in (".glb", ".gltf", ".fbx", ".obj", ".usdz")):
            name = f"{name}{default_ext}"
        dest = Path(tempfile.gettempdir()) / "uefn-ducky-meshy" / "imports" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(src, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                dest.write_bytes(resp.read())
        except urllib.error.URLError as exc:
            raise MeshyError(f"Download failed: {exc.reason}") from exc
        return str(dest.resolve())
    local = str(Path(src).expanduser().resolve())
    if not Path(local).is_file():
        raise MeshyError(f"file not found: {local}")
    return local


def blender_import_code(local_path: str) -> str:
    """bpy import snippet for glb/gltf/fbx."""
    escaped = local_path.replace("\\", "\\\\").replace("'", "\\'")
    suffix = Path(local_path).suffix.lower()
    if suffix in {".glb", ".gltf"}:
        op = f"bpy.ops.import_scene.gltf(filepath=r'{escaped}')"
    elif suffix == ".fbx":
        op = f"bpy.ops.import_scene.fbx(filepath=r'{escaped}')"
    else:
        raise MeshyError(f"Unsupported Blender import format: {suffix or '(none)'} (use .glb/.gltf/.fbx)")
    return f"import bpy\n{op}\n'imported'\n"


def _maybe_wait_download(
    client: MeshyClient,
    kind: str,
    task_id: str,
    *,
    wait: bool,
    output_dir: str,
) -> str:
    payload: dict[str, Any] = {"task_id": task_id, "kind": kind, "status": "submitted"}
    if not wait:
        return _dumps(payload)
    result = client.wait(kind, task_id)
    payload.update(result)
    if result.get("finished"):
        dest = output_dir.strip() or str(_default_download_dir(task_id))
        payload["downloaded"] = client.download_results(result, dest)
        payload["output_dir"] = dest
    return _dumps(payload)


def register_tools(api: Any) -> None:
    """Register meshy_* tools on the shared MCP server."""

    def _client() -> MeshyClient:
        return MeshyClient()

    if hasattr(api, "register_secret_test"):
        api.register_secret_test(SECRET_KEY, test_api_key)

    @api.tool(name="meshy_balance", intent=INTENT)
    def meshy_balance() -> str:
        """Check Meshy credit balance."""
        try:
            return _dumps(_client().balance())
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_discover_search", intent=INTENT)
    def meshy_discover_search(query: str, limit: int = 8) -> str:
        """Search FREE Meshy community / Discover models (no credits). Always run before paid generate. Paste chat_links for the user to preview."""
        try:
            return _dumps(discover_search(query, limit=limit))
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_discover_get", intent=INTENT)
    def meshy_discover_get(url_or_slug: str) -> str:
        """Get one FREE community model page (title, thumbnail, page_url). No credits."""
        try:
            return _dumps(discover_get(url_or_slug))
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_discover_download", intent=INTENT)
    def meshy_discover_download(url_or_path: str, output_dir: str = "") -> str:
        """FREE-ONLY download: local free mesh path, or direct .glb/.fbx URL. Community pages have no public GLB API — user downloads on meshy.ai then pass the local path. Never spends generation credits."""
        try:
            return _dumps(discover_download_free(url_or_path, output_dir=output_dir))
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_status", intent=INTENT)
    def meshy_status(
        task_id: str,
        kind: str,
        poll: bool = False,
        poll_interval: int = 5,
    ) -> str:
        """Poll a Meshy task. kind: text-to-3d|image-to-3d|text-to-image|…. Set poll=true to wait until SUCCEEDED/FAILED."""
        try:
            client = _client()
            if poll:
                return _dumps(
                    client.wait(
                        kind, task_id, poll_interval=max(1, int(poll_interval or 5))
                    )
                )
            return _dumps(client.status(kind, task_id))
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_download", intent=INTENT)
    def meshy_download(task_id: str, kind: str, output_dir: str = "") -> str:
        """Download assets for a SUCCEEDED task to output_dir (default temp folder)."""
        try:
            client = _client()
            result = client.status(kind, task_id)
            if not result.get("finished"):
                return _dumps(
                    {
                        "error": f"Task is {result.get('status')}, not SUCCEEDED",
                        **result,
                    }
                )
            dest = output_dir.strip() or str(_default_download_dir(task_id))
            paths = client.download_results(result, dest)
            return _dumps({"downloaded": paths, "output_dir": dest, **result})
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_text_to_3d_preview", intent=INTENT)
    def meshy_text_to_3d_preview(
        prompt: str,
        ai_model: str = "latest",
        model_type: str = "standard",
        should_remesh: bool = True,
        topology: str = "triangle",
        target_polycount: int = 0,
        pose_mode: str = "",
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Meshy text→3D preview (untextured mesh). Then call meshy_text_to_3d_refine with this task_id."""
        try:
            blocked = credit_gate(confirm_spend, 15, "text_to_3d_preview")
            if blocked:
                return blocked
            client = _client()
            if not prompt.strip():
                return "Error: prompt is required"
            payload: dict[str, Any] = {
                "mode": "preview",
                "prompt": prompt.strip()[:600],
                "ai_model": ai_model or "latest",
                "model_type": model_type or "standard",
                "should_remesh": bool(should_remesh),
            }
            if should_remesh and topology:
                payload["topology"] = topology
            if target_polycount:
                payload["target_polycount"] = int(target_polycount)
            if pose_mode.strip():
                payload["pose_mode"] = pose_mode.strip()
            task_id = client.submit("/openapi/v2/text-to-3d", payload)
            return _maybe_wait_download(
                client, "text-to-3d", task_id, wait=wait, output_dir=output_dir
            )
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_text_to_3d_refine", intent=INTENT)
    def meshy_text_to_3d_refine(
        preview_task_id: str,
        enable_pbr: bool = True,
        texture_prompt: str = "",
        texture_image: str = "",
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Meshy text→3D refine (texture a completed preview). Pass preview_task_id from preview."""
        try:
            blocked = credit_gate(confirm_spend, 10, "text_to_3d_refine")
            if blocked:
                return blocked
            client = _client()
            if not preview_task_id.strip():
                return "Error: preview_task_id is required"
            payload: dict[str, Any] = {
                "mode": "refine",
                "preview_task_id": preview_task_id.strip(),
                "enable_pbr": bool(enable_pbr),
            }
            if texture_prompt.strip():
                payload["texture_prompt"] = texture_prompt.strip()[:600]
            elif texture_image.strip():
                payload["texture_image_url"] = resolve_image(texture_image)
            task_id = client.submit("/openapi/v2/text-to-3d", payload)
            return _maybe_wait_download(
                client, "text-to-3d", task_id, wait=wait, output_dir=output_dir
            )
        except (MeshyError, FileNotFoundError, OSError) as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_text_to_3d", intent=INTENT)
    def meshy_text_to_3d(
        prompt: str,
        ai_model: str = "latest",
        enable_pbr: bool = True,
        texture_prompt: str = "",
        should_remesh: bool = True,
        confirm_spend: bool = False,
        wait: bool = True,
        output_dir: str = "",
    ) -> str:
        """Full Meshy text→3D: preview then refine. Default wait=true (can take several minutes)."""
        try:
            blocked = credit_gate(confirm_spend, 25, "text_to_3d")
            if blocked:
                return blocked
            client = _client()
            if not prompt.strip():
                return "Error: prompt is required"
            preview_payload: dict[str, Any] = {
                "mode": "preview",
                "prompt": prompt.strip()[:600],
                "ai_model": ai_model or "latest",
                "should_remesh": bool(should_remesh),
            }
            preview_id = client.submit("/openapi/v2/text-to-3d", preview_payload)
            if not wait:
                return _dumps(
                    {
                        "task_id": preview_id,
                        "kind": "text-to-3d",
                        "stage": "preview",
                        "status": "submitted",
                        "next": "meshy_status then meshy_text_to_3d_refine",
                    }
                )
            preview = client.wait("text-to-3d", preview_id)
            if preview.get("failed"):
                return _dumps({"stage": "preview", **preview})
            refine_payload: dict[str, Any] = {
                "mode": "refine",
                "preview_task_id": preview_id,
                "enable_pbr": bool(enable_pbr),
            }
            if texture_prompt.strip():
                refine_payload["texture_prompt"] = texture_prompt.strip()[:600]
            refine_id = client.submit("/openapi/v2/text-to-3d", refine_payload)
            result = client.wait("text-to-3d", refine_id)
            out: dict[str, Any] = {
                "preview_task_id": preview_id,
                "task_id": refine_id,
                "kind": "text-to-3d",
                **result,
            }
            if result.get("finished"):
                dest = output_dir.strip() or str(_default_download_dir(refine_id))
                out["downloaded"] = client.download_results(result, dest)
                out["output_dir"] = dest
            return _dumps(out)
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_image_to_3d", intent=INTENT)
    def meshy_image_to_3d(
        image: str = "",
        input_task_id: str = "",
        ai_model: str = "latest",
        model_type: str = "standard",
        should_texture: bool = True,
        enable_pbr: bool = True,
        texture_prompt: str = "",
        should_remesh: bool = True,
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Meshy image→3D. Provide image (path/URL/data URI) or input_task_id from meshy_text_to_image."""
        try:
            blocked = credit_gate(confirm_spend, 25, "image_to_3d")
            if blocked:
                return blocked
            client = _client()
            payload: dict[str, Any] = {
                "ai_model": ai_model or "latest",
                "model_type": model_type or "standard",
                "should_texture": bool(should_texture),
                "should_remesh": bool(should_remesh),
            }
            if should_texture:
                payload["enable_pbr"] = bool(enable_pbr)
            if texture_prompt.strip():
                payload["texture_prompt"] = texture_prompt.strip()[:600]
            if input_task_id.strip():
                payload["input_task_id"] = input_task_id.strip()
            elif image.strip():
                payload["image_url"] = resolve_image(image)
            else:
                return "Error: provide image or input_task_id"
            task_id = client.submit("/openapi/v1/image-to-3d", payload)
            return _maybe_wait_download(
                client, "image-to-3d", task_id, wait=wait, output_dir=output_dir
            )
        except (MeshyError, FileNotFoundError, OSError) as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_multi_image_to_3d", intent=INTENT)
    def meshy_multi_image_to_3d(
        images: str,
        ai_model: str = "latest",
        should_texture: bool = True,
        enable_pbr: bool = True,
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Meshy multi-image→3D. images: JSON array of paths/URLs, or newline/comma-separated list."""
        try:
            blocked = credit_gate(confirm_spend, 25, "multi_image_to_3d")
            if blocked:
                return blocked
            client = _client()
            raw = (images or "").strip()
            if not raw:
                return "Error: images is required"
            urls: list[str] = []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    return f"Error: images JSON invalid: {exc}"
                if not isinstance(parsed, list):
                    return "Error: images JSON must be an array"
                urls = [resolve_image(str(x)) for x in parsed if str(x).strip()]
            else:
                parts = [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]
                urls = [resolve_image(p) for p in parts]
            if len(urls) < 2:
                return "Error: need at least 2 images"
            payload: dict[str, Any] = {
                "image_urls": urls,
                "ai_model": ai_model or "latest",
                "should_texture": bool(should_texture),
                "enable_pbr": bool(enable_pbr),
            }
            task_id = client.submit("/openapi/v1/multi-image-to-3d", payload)
            return _maybe_wait_download(
                client, "multi-image-to-3d", task_id, wait=wait, output_dir=output_dir
            )
        except (MeshyError, FileNotFoundError, OSError) as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_text_to_image", intent=INTENT)
    def meshy_text_to_image(
        prompt: str,
        ai_model: str = "nano-banana",
        aspect_ratio: str = "1:1",
        generate_multi_view: bool = False,
        pose_mode: str = "",
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Meshy text→image (for concept art or as input to meshy_image_to_3d via input_task_id)."""
        try:
            blocked = credit_gate(confirm_spend, 5, "text_to_image")
            if blocked:
                return blocked
            client = _client()
            if not prompt.strip():
                return "Error: prompt is required"
            payload: dict[str, Any] = {
                "ai_model": ai_model or "nano-banana",
                "prompt": prompt.strip(),
            }
            if generate_multi_view:
                payload["generate_multi_view"] = True
            else:
                payload["aspect_ratio"] = aspect_ratio or "1:1"
            if pose_mode.strip():
                payload["pose_mode"] = pose_mode.strip()
            task_id = client.submit("/openapi/v1/text-to-image", payload)
            return _maybe_wait_download(
                client, "text-to-image", task_id, wait=wait, output_dir=output_dir
            )
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_image_to_image", intent=INTENT)
    def meshy_image_to_image(
        image: str,
        prompt: str,
        ai_model: str = "nano-banana",
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Meshy image→image transform via prompt guidance."""
        try:
            blocked = credit_gate(confirm_spend, 5, "image_to_image")
            if blocked:
                return blocked
            client = _client()
            if not image.strip() or not prompt.strip():
                return "Error: image and prompt are required"
            payload: dict[str, Any] = {
                "ai_model": ai_model or "nano-banana",
                "image_url": resolve_image(image),
                "prompt": prompt.strip(),
            }
            task_id = client.submit("/openapi/v1/image-to-image", payload)
            return _maybe_wait_download(
                client, "image-to-image", task_id, wait=wait, output_dir=output_dir
            )
        except (MeshyError, FileNotFoundError, OSError) as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_retexture", intent=INTENT)
    def meshy_retexture(
        input_task_id: str = "",
        model_url: str = "",
        text_style_prompt: str = "",
        image_style: str = "",
        enable_pbr: bool = True,
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Retexture an existing Meshy model (input_task_id from a 3D task, or model_url)."""
        try:
            blocked = credit_gate(confirm_spend, 10, "retexture")
            if blocked:
                return blocked
            client = _client()
            payload: dict[str, Any] = {"enable_pbr": bool(enable_pbr)}
            if input_task_id.strip():
                payload["input_task_id"] = input_task_id.strip()
            elif model_url.strip():
                payload["model_url"] = model_url.strip()
            else:
                return "Error: provide input_task_id or model_url"
            if text_style_prompt.strip():
                payload["text_style_prompt"] = text_style_prompt.strip()[:600]
            elif image_style.strip():
                payload["image_style_url"] = resolve_image(image_style)
            else:
                return "Error: provide text_style_prompt or image_style"
            task_id = client.submit("/openapi/v1/retexture", payload)
            return _maybe_wait_download(
                client, "retexture", task_id, wait=wait, output_dir=output_dir
            )
        except (MeshyError, FileNotFoundError, OSError) as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_remesh", intent=INTENT)
    def meshy_remesh(
        input_task_id: str = "",
        model_url: str = "",
        topology: str = "triangle",
        target_polycount: int = 30000,
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Remesh / change polycount of an existing model."""
        try:
            blocked = credit_gate(confirm_spend, 5, "remesh")
            if blocked:
                return blocked
            client = _client()
            payload: dict[str, Any] = {
                "topology": topology or "triangle",
                "target_polycount": int(target_polycount or 30000),
            }
            if input_task_id.strip():
                payload["input_task_id"] = input_task_id.strip()
            elif model_url.strip():
                payload["model_url"] = model_url.strip()
            else:
                return "Error: provide input_task_id or model_url"
            task_id = client.submit("/openapi/v1/remesh", payload)
            return _maybe_wait_download(
                client, "remesh", task_id, wait=wait, output_dir=output_dir
            )
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_convert", intent=INTENT)
    def meshy_convert(
        target_formats: str = "fbx",
        input_task_id: str = "",
        model_url: str = "",
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Convert a Meshy model to other formats (fbx,glb,obj,usdz,stl,blend,3mf)."""
        try:
            blocked = credit_gate(confirm_spend, 1, "convert")
            if blocked:
                return blocked
            client = _client()
            raw_fmts = (target_formats or "fbx").strip()
            if raw_fmts.startswith("["):
                fmts = [str(x).strip().lower() for x in json.loads(raw_fmts)]
            else:
                fmts = [p.strip().lower() for p in raw_fmts.replace("\n", ",").split(",") if p.strip()]
            if not fmts:
                return "Error: target_formats required (e.g. fbx or fbx,glb)"
            payload: dict[str, Any] = {"target_formats": fmts}
            if input_task_id.strip():
                payload["input_task_id"] = input_task_id.strip()
            elif model_url.strip():
                payload["model_url"] = model_url.strip()
            else:
                return "Error: provide input_task_id or model_url"
            task_id = client.submit("/openapi/v1/convert", payload)
            return _maybe_wait_download(
                client, "convert", task_id, wait=wait, output_dir=output_dir
            )
        except (MeshyError, json.JSONDecodeError, ValueError) as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_resize", intent=INTENT)
    def meshy_resize(
        input_task_id: str = "",
        model_url: str = "",
        resize_height: float = 0.0,
        resize_longest_side: float = 0.0,
        auto_size: bool = False,
        origin_at: str = "bottom",
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Resize a model to real-world meters (height, longest side, or auto_size)."""
        try:
            blocked = credit_gate(confirm_spend, 1, "resize")
            if blocked:
                return blocked
            client = _client()
            modes = sum(
                [
                    1 if float(resize_height or 0) > 0 else 0,
                    1 if float(resize_longest_side or 0) > 0 else 0,
                    1 if auto_size else 0,
                ]
            )
            if modes != 1:
                return "Error: set exactly one of resize_height, resize_longest_side, or auto_size=true"
            payload: dict[str, Any] = {"origin_at": origin_at or "bottom"}
            if float(resize_height or 0) > 0:
                payload["resize_height"] = float(resize_height)
            elif float(resize_longest_side or 0) > 0:
                payload["resize_longest_side"] = float(resize_longest_side)
            else:
                payload["auto_size"] = True
            if input_task_id.strip():
                payload["input_task_id"] = input_task_id.strip()
            elif model_url.strip():
                payload["model_url"] = model_url.strip()
            else:
                return "Error: provide input_task_id or model_url"
            task_id = client.submit("/openapi/v1/resize", payload)
            return _maybe_wait_download(
                client, "resize", task_id, wait=wait, output_dir=output_dir
            )
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_uv_unwrap", intent=INTENT)
    def meshy_uv_unwrap(
        input_task_id: str = "",
        model_url: str = "",
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Generate clean UVs for a model (GLB, typically ≤40k faces) before external texturing."""
        try:
            blocked = credit_gate(confirm_spend, 5, "uv_unwrap")
            if blocked:
                return blocked
            client = _client()
            payload: dict[str, Any] = {}
            if input_task_id.strip():
                payload["input_task_id"] = input_task_id.strip()
            elif model_url.strip():
                payload["model_url"] = model_url.strip()
            else:
                return "Error: provide input_task_id or model_url"
            task_id = client.submit("/openapi/v1/uv-unwrap", payload)
            return _maybe_wait_download(
                client, "uv-unwrap", task_id, wait=wait, output_dir=output_dir
            )
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_rig", intent=INTENT)
    def meshy_rig(
        input_task_id: str = "",
        model_url: str = "",
        height_meters: float = 1.7,
        texture_image: str = "",
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Auto-rig a textured humanoid (≤300k faces). Remesh first if needed. Includes walk/run."""
        try:
            blocked = credit_gate(confirm_spend, 5, "rig")
            if blocked:
                return blocked
            client = _client()
            payload: dict[str, Any] = {
                "height_meters": float(height_meters or 1.7),
            }
            if input_task_id.strip():
                payload["input_task_id"] = input_task_id.strip()
            elif model_url.strip():
                payload["model_url"] = model_url.strip()
            else:
                return "Error: provide input_task_id or model_url"
            if texture_image.strip():
                payload["texture_image_url"] = resolve_image(texture_image)
            task_id = client.submit("/openapi/v1/rigging", payload)
            return _maybe_wait_download(
                client, "rigging", task_id, wait=wait, output_dir=output_dir
            )
        except (MeshyError, FileNotFoundError, OSError) as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_list_animations", intent=INTENT)
    def meshy_list_animations(
        query: str = "",
        category: str = "",
        limit: int = 40,
    ) -> str:
        """Search Meshy animation library; returns action_id values for meshy_animate."""
        try:
            rows = fetch_animation_catalog()
            matched = filter_animations(
                rows, query=query, category=category, limit=limit
            )
            return _dumps(
                {
                    "total_catalog": len(rows),
                    "count": len(matched),
                    "animations": matched,
                }
            )
        except MeshyError as exc:
            return f"Error: {exc}"

    @api.tool(name="meshy_animate", intent=INTENT)
    def meshy_animate(
        rig_task_id: str,
        action_id: int,
        post_process_op: str = "",
        fps: int = 30,
        confirm_spend: bool = False,
        wait: bool = False,
        output_dir: str = "",
    ) -> str:
        """Apply a library animation to a completed meshy_rig task (action_id from meshy_list_animations)."""
        try:
            blocked = credit_gate(confirm_spend, 3, "animate")
            if blocked:
                return blocked
            client = _client()
            if not str(rig_task_id or "").strip():
                return "Error: rig_task_id is required"
            if not action_id:
                return "Error: action_id is required (use meshy_list_animations)"
            payload: dict[str, Any] = {
                "rig_task_id": str(rig_task_id).strip(),
                "action_id": int(action_id),
            }
            op = (post_process_op or "").strip()
            if op:
                pp: dict[str, Any] = {"operation_type": op}
                if op == "change_fps":
                    pp["fps"] = int(fps or 30)
                payload["post_process"] = pp
            task_id = client.submit("/openapi/v1/animations", payload)
            return _maybe_wait_download(
                client, "animations", task_id, wait=wait, output_dir=output_dir
            )
        except MeshyError as exc:
            return f"Error: {exc}"

    def _import_to_blender(url_or_path: str) -> str:
        try:
            local = resolve_local_or_download(url_or_path)
            from .blender_import import execute_code

            result = execute_code(blender_import_code(local))
            return _dumps({"ok": True, "path": local, "blender": result})
        except (MeshyError, OSError, ConnectionError) as exc:
            return f"Error importing to Blender: {exc}"
        except Exception as exc:
            return f"Error importing to Blender: {exc}"

    @api.tool(name="meshy_import_to_blender", intent=INTENT)
    def meshy_import_to_blender(url_or_path: str) -> str:
        """Download (if URL) and import a GLB/GLTF/FBX into the connected Blender scene."""
        return _import_to_blender(url_or_path)

    @api.tool(name="meshy_import_glb_to_blender", intent=INTENT)
    def meshy_import_glb_to_blender(url_or_path: str) -> str:
        """Alias for meshy_import_to_blender (GLB/GLTF/FBX)."""
        return _import_to_blender(url_or_path)

    @api.tool(name="meshy_import_to_uefn", intent=INTENT)
    def meshy_import_to_uefn(
        url_or_path: str,
        destination_path: str = "",
        replace_existing: bool = True,
    ) -> str:
        """Download (if URL) then import_asset into UEFN Content Browser. Listener required.

        destination_path: project content path (e.g. /MyProject/Meshy) or relative
        (e.g. Meshy/Props). Empty defaults to relative "Meshy" — listener pins to content_root.
        """
        try:
            local = resolve_local_or_download(url_or_path, default_ext=".fbx")
            dest = (destination_path or "").strip() or "Meshy"
            result = api.listener(
                "import_asset",
                {
                    "source_file": local,
                    "destination_path": dest,
                    "replace_existing": bool(replace_existing),
                },
            )
            return _dumps({"ok": True, "path": local, "destination_path": dest, "uefn": result})
        except MeshyError as exc:
            return f"Error importing to UEFN: {exc}"
        except Exception as exc:
            return (
                f"Error importing to UEFN: {exc}. "
                "Is the Fortnite/UEFN listener online? For skeletal cleanup use Blender first."
            )

    api.log("meshy tools registered")
