"""
Automated licensed-image retrieval for scenes where a real image
(not a hand-drawn diagram) genuinely helps -- e.g. "structure of a
MOSFET", "anatomy of a neuron" -- named physical objects/structures
that benefit from an authoritative illustration rather than a
from-scratch drawing.

Uses the Openverse API (https://openverse.org), which aggregates
openly-licensed and public-domain images with license/attribution
metadata attached to every result. No API key is required for
reasonable usage; register at https://api.openverse.org if you hit
rate limits.

This module NEVER scrapes arbitrary web/Google Images results --
only Openverse-indexed, license-tagged content is used, and every
selected image's license and attribution are recorded and displayed
in the final video.
"""

from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image
from app.common.paths import work_dir


OPENVERSE_SEARCH_URL = "https://api.openverse.org/v1/images/"

# Licenses that forbid derivative works entirely. A video that
# incorporates an image into a new composition IS a derivative
# work, so ND-licensed images cannot legally be used here no matter
# how relevant they look.
_EXCLUDED_LICENSES = {"nd", "by-nd", "by-nc-nd"}

# Licenses that are usable but come with obligations worth knowing
# about:
#   - "*-sa" (ShareAlike): the resulting video would need to be
#     released under the same license if you share it publicly.
#   - "*-nc" (NonCommercial): fine for a personal/educational
#     prototype, not for anything monetized.
_CAUTION_LICENSES = {"by-sa", "by-nc", "by-nc-sa"}

CACHE_DIR = work_dir("images", "cache")

MIN_WIDTH = 400
MIN_HEIGHT = 300


class ImageRetrievalError(RuntimeError):
    """Raised when image search/download fails outright."""


@dataclass
class ImageCandidate:
    id: str
    title: str
    creator: str
    creator_url: str
    license: str
    license_version: str
    license_url: str
    source: str
    foreign_landing_url: str
    image_url: str
    width: int
    height: int

    @property
    def attribution_text(self) -> str:
        creator_part = f" by {self.creator}" if self.creator else ""
        license_label = self.license.upper() if self.license else "Unknown"
        return (
            f'"{self.title}"{creator_part} \u2014 '
            f"{license_label} {self.license_version} "
            f"({self.source})"
        ).strip()

    @property
    def requires_attribution(self) -> bool:
        return self.license not in {"cc0", "pdm"}


def _aspect_ratio(width: int, height: int) -> float:
    if height == 0:
        return 0.0
    return width / height


def search_licensed_images(
    query: str,
    max_results: int = 10,
) -> list[ImageCandidate]:
    """
    Search Openverse for openly-licensed images matching `query`.
    Filters out ND-licensed (no-derivatives) results up front,
    since embedding an image into a generated video is itself a
    derivative work and ND licenses forbid that outright.
    """

    if not query or not query.strip():
        raise ImageRetrievalError(
            "Cannot search for images with an empty query."
        )

    params = {
        "q": query,
        "page_size": max_results,
        "mature": "false",
    }

    try:
        response = httpx.get(
            OPENVERSE_SEARCH_URL,
            params=params,
            timeout=30.0,
            headers={
                "User-Agent": _USER_AGENT
            },
        )
        response.raise_for_status()

    except httpx.HTTPError as exc:
        raise ImageRetrievalError(
            f"Could not reach Openverse: {exc}"
        ) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise ImageRetrievalError(
            "Openverse returned invalid JSON."
        ) from exc

    results = data.get("results", [])

    candidates = []

    for item in results:

        license_slug = (item.get("license") or "").lower()

        if license_slug in _EXCLUDED_LICENSES:
            continue

        image_url = item.get("url")

        if not image_url:
            continue

        candidates.append(
            ImageCandidate(
                id=str(item.get("id", "")),
                title=item.get("title") or "Untitled",
                creator=item.get("creator") or "",
                creator_url=item.get("creator_url") or "",
                license=license_slug,
                license_version=item.get("license_version") or "",
                license_url=item.get("license_url") or "",
                source=item.get("source") or "",
                foreign_landing_url=item.get(
                    "foreign_landing_url", ""
                ) or "",
                image_url=image_url,
                width=item.get("width") or 0,
                height=item.get("height") or 0,
            )
        )

    return candidates


def rank_candidates(
    candidates: list[ImageCandidate],
    target_aspect_ratio: float = 16 / 9,
) -> list[ImageCandidate]:
    """
    Ranks candidates by:
      - search relevance (Openverse already orders results by
        relevance, so earlier position = higher base score)
      - meeting a minimum usable resolution (candidates below this
        are dropped entirely -- a small image looks bad blown up)
      - how close the aspect ratio is to the target
      - preferring licenses with fewer downstream obligations
        (CC0/PDM > plain BY > BY-SA/BY-NC)
    """

    scored = []

    for index, candidate in enumerate(candidates):

        if (
            candidate.width < MIN_WIDTH
            or candidate.height < MIN_HEIGHT
        ):
            continue

        relevance_score = -index

        ratio = _aspect_ratio(candidate.width, candidate.height)
        aspect_penalty = -abs(ratio - target_aspect_ratio)

        if candidate.license in {"cc0", "pdm"}:
            license_score = 2
        elif candidate.license in _CAUTION_LICENSES:
            license_score = 0
        else:
            license_score = 1

        total = (
            relevance_score * 3
            + aspect_penalty * 2
            + license_score
        )

        scored.append((total, candidate))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    return [candidate for _, candidate in scored]


def download_image(candidate: ImageCandidate) -> str:
    """
    Downloads the selected candidate to a local cache directory and
    verifies it's a real, openable image before returning the path.
    """

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    suffix = Path(candidate.image_url).suffix.split("?")[0]

    if not suffix or len(suffix) > 5:
        suffix = ".jpg"

    local_path = CACHE_DIR / f"{candidate.id or 'image'}{suffix}"

    if local_path.exists():
        return str(local_path)

    try:
        response = httpx.get(
            candidate.image_url,
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": _USER_AGENT
            },
        )
        response.raise_for_status()

    except httpx.HTTPError as exc:
        raise ImageRetrievalError(
            f"Could not download image: {exc}"
        ) from exc

    local_path.write_bytes(response.content)

    try:
        with Image.open(local_path) as img:
            img.verify()

    except Exception as exc:
        local_path.unlink(missing_ok=True)
        raise ImageRetrievalError(
            f"Downloaded file is not a valid image: {exc}"
        ) from exc

    return str(local_path)


def find_best_image(
    query: str,
    max_results: int = 10,
) -> tuple[ImageCandidate, str] | None:
    """
    End-to-end: search, rank, download the best candidate.

    Returns (candidate, local_path), or None if nothing suitable was
    found -- callers should fall back to the programmatic diagram
    renderer in that case (per the fallback requirement).
    """

    try:
        candidates = search_licensed_images(
            query, max_results=max_results
        )
    except ImageRetrievalError as exc:
        print(
            f"[image_retrieval] Search failed, will fall back to "
            f"a programmatic diagram: {exc}"
        )
        return None

    ranked = rank_candidates(candidates)

    for candidate in ranked:
        try:
            local_path = download_image(candidate)
            return candidate, local_path

        except ImageRetrievalError as exc:
            print(
                f"[image_retrieval] Candidate '{candidate.title}' "
                f"failed to download, trying next candidate: {exc}"
            )
            continue

    return None

# ---------------------------------------------------------------------
# Wikimedia Commons search + vision relevance check
# ---------------------------------------------------------------------

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_USER_AGENT = "NoteTutorAI/0.2 (educational video generator; https://www.mediawiki.org/wiki/API:Etiquette) httpx"
_BLOCKED_LICENSE_WORDS = ("nd", "fair use", "non-free", "all rights")


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text or "").strip()


def search_commons(query: str, max_results: int = 10) -> list[ImageCandidate]:
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": max_results,
        "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime",
        "iiurlwidth": 1280,
    }
    try:
        response = httpx.get(COMMONS_API, params=params, timeout=30.0,
                             headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
    except (httpx.HTTPError, ValueError) as exc:
        raise ImageRetrievalError(f"Could not reach Wikimedia Commons: {exc}") from exc

    candidates = []
    for page in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/png", "image/jpeg", "image/svg+xml"):
            continue
        meta = info.get("extmetadata", {})
        license_name = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
        lowered = f" {license_name.lower()} "
        if not license_name or any(f" {w} " in lowered or f"-{w}" in lowered for w in _BLOCKED_LICENSE_WORDS):
            continue
        candidates.append(ImageCandidate(
            id=f"commons_{page.get('pageid')}",
            title=page.get("title", "").removeprefix("File:").rsplit(".", 1)[0],
            creator=_strip_html(meta.get("Artist", {}).get("value", ""))[:60],
            creator_url="",
            license="pdm" if "public domain" in license_name.lower() else license_name.lower(),
            license_version="",
            license_url=meta.get("LicenseUrl", {}).get("value", ""),
            source="Wikimedia Commons",
            foreign_landing_url=info.get("descriptionurl", ""),
            image_url=info.get("thumburl") or info.get("url", ""),
            width=info.get("thumbwidth") or info.get("width") or 0,
            height=info.get("thumbheight") or info.get("height") or 0,
        ))
    return candidates


def _is_relevant(local_path: str, query: str, context: str) -> bool:
    """Asks the vision model whether the image really shows the concept."""
    from app.common.llm_client import LLMCallError, call_groq_vision, groq_available
    import json

    if not groq_available():
        return True  # no checker available; keep search ranking
    prompt = (
        f'An educational video about "{context}" needs a picture of: "{query}".\n'
        "Look at this image. Would it help a learner see what that is? It must "
        "actually depict it accurately and be reasonably clear; artistic style, "
        "age or missing labels do not matter. Reject it if it shows something "
        "else, is mostly text, has labels in a language other than English, or is a photo of an unrelated scene. "
        'Reply only with JSON: {"relevant": true|false, "reason": "short"}'
    )
    try:
        verdict = json.loads(call_groq_vision(prompt, local_path, max_tokens=200, json_mode=True))
        print(f"[image_retrieval] '{query}': {verdict}")
        return bool(verdict.get("relevant"))
    except (LLMCallError, ValueError) as exc:
        print(f"[image_retrieval] relevance check failed: {exc}")
        return False


def find_teaching_image(query: str, context: str, max_checks: int = 4):
    """
    Returns (local_path, credit_text) for a licensed image that the vision
    model confirms shows `query`, or None.
    """
    candidates: list[ImageCandidate] = []
    for search in (search_commons, search_licensed_images):
        try:
            candidates.extend(rank_candidates(search(query)))
        except ImageRetrievalError as exc:
            print(f"[image_retrieval] {exc}")

    checked = 0
    for candidate in candidates:
        if checked >= max_checks:
            break
        try:
            local_path = download_image(candidate)
        except ImageRetrievalError:
            continue
        checked += 1
        if _is_relevant(local_path, query, context):
            credit = candidate.attribution_text if candidate.requires_attribution else f"{candidate.source} (public domain)"
            return local_path, credit
    return None
