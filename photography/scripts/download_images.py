#!/usr/bin/env python3
"""Download the original Squarespace images referenced by index.html.

Images are grouped by their page section, filenames are preserved, and the
HTML is rewritten to use URL-safe local asset paths. A manifest records the
original CDN URL and local destination for every image.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
ASSET_ROOT = ROOT / "assets" / "images"
MANIFEST = ASSET_ROOT / "manifest.json"

IMAGE_URL_RE = re.compile(
    r'https://images\.squarespace-cdn\.com/content/[^"\']+?'
    r'\.(?:jpe?g|png|gif|webp|avif)(?:\?[^"\']*)?',
    re.IGNORECASE,
)


def original_url(source_url: str) -> str:
    """Remove Squarespace transformation parameters to request the upload."""
    parts = urlsplit(source_url.replace("&amp;", "&"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def section_for_offset(html: str, offset: int) -> str:
    before = html[:offset]
    marker = before.rfind("<!-- =====")
    heading = before[marker : before.find("-->", marker) + 3] if marker >= 0 else ""

    if "HERO GALLERY" in heading:
        return "hero"
    if "FEATURED EVENTS" in heading:
        return "featured-events"
    if "TOURNAMENT CAROUSEL" in heading:
        tournament_markup = before[marker:]
        return (
            "tournaments/cards"
            if 'class="carousel-wrapper"' in tournament_markup
            else "tournaments/background"
        )
    if "ABOUT SECTION" in heading:
        return "about"
    if "PHOTO STRIPS" in heading:
        rows = list(re.finditer(r"<!-- Row (\d+):", before[marker:]))
        row = int(rows[-1].group(1)) if rows else 1
        return f"photo-strips/row-{row:02d}"
    return "misc"


def unique_destination(directory: Path, filename: str, remote_url: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    digest = hashlib.sha256(remote_url.encode()).hexdigest()[:8]
    return directory / f"{candidate.stem}-{digest}{candidate.suffix}"


def download(remote_url: str, destination: Path) -> tuple[int, str, str]:
    request = Request(
        remote_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; EmilyYuPortfolioArchive/1.0)",
            # Exclude WebP/AVIF so Squarespace returns bytes matching the
            # preserved upload extension instead of a negotiated derivative.
            "Accept": "image/jpeg,image/png,image/gif,*/*;q=0.1",
        },
    )
    with urlopen(request, timeout=90) as response:
        payload = response.read()
        content_type = response.headers.get_content_type()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return len(payload), hashlib.sha256(payload).hexdigest(), content_type


def main() -> None:
    html = INDEX.read_text(encoding="utf-8")
    matches = list(IMAGE_URL_RE.finditer(html))
    if not matches:
        if not MANIFEST.exists():
            raise SystemExit("No Squarespace CDN URLs or existing manifest found")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        records = manifest["images"]
        for number, record in enumerate(records, start=1):
            destination = ROOT / record["local_path"]
            print(
                f"[{number:02d}/{len(records):02d}] {record['local_path']}",
                flush=True,
            )
            size, sha256, content_type = download(record["original_url"], destination)
            record.update(bytes=size, sha256=sha256, content_type=content_type)
        MANIFEST.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        total = sum(int(record["bytes"]) for record in records)
        print(f"Refreshed {len(records)} originals ({total / 1024 / 1024:.1f} MiB)")
        return

    replacements: dict[str, str] = {}
    records: list[dict[str, object]] = []
    used_destinations: set[Path] = set()

    for number, match in enumerate(matches, start=1):
        source = match.group(0)
        if source in replacements:
            continue

        remote = original_url(source)
        section = section_for_offset(html, match.start())
        filename = unquote(Path(urlsplit(remote).path).name)
        directory = ASSET_ROOT / section
        destination = directory / filename
        if destination in used_destinations:
            digest = hashlib.sha256(remote.encode()).hexdigest()[:8]
            destination = directory / f"{destination.stem}-{digest}{destination.suffix}"
        used_destinations.add(destination)

        print(f"[{number:02d}/{len(matches):02d}] {section}/{destination.name}", flush=True)
        size, sha256, content_type = download(remote, destination)
        local_path = destination.relative_to(ROOT).as_posix()
        local_url = quote(local_path, safe="/()+,._-+")
        replacements[source] = local_url
        records.append(
            {
                "filename": destination.name,
                "section": section,
                "local_path": local_path,
                "source_url": source,
                "original_url": remote,
                "content_type": content_type,
                "bytes": size,
                "sha256": sha256,
            }
        )

    for source, local_url in replacements.items():
        html = html.replace(source, local_url)
    INDEX.write_text(html, encoding="utf-8")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(
            {
                "source_site": "https://emilycyu.squarespace.com/",
                "download_mode": "Squarespace original uploads (transform query removed)",
                "image_count": len(records),
                "images": records,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    total = sum(int(record["bytes"]) for record in records)
    print(f"Downloaded {len(records)} originals ({total / 1024 / 1024:.1f} MiB)")
    print(f"Updated {INDEX}")
    print(f"Wrote {MANIFEST}")


if __name__ == "__main__":
    main()
