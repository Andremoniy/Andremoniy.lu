"""Resolve official App Store artwork and update the built application catalogue."""

import json
from pathlib import Path
import re
import sys
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


APPLICATION_SLUGS = {
    "6761431119": "stoolytics",
    "6762096127": "syntax-alchemist",
    "6761403683": "angrywife",
}
MAXIMUM_DOWNLOAD_BYTES = 4 * 1024 * 1024
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_TIMEOUT_SECONDS = 25


def download_content(source_url):
    last_error = None
    for attempt_number in range(DOWNLOAD_ATTEMPTS):
        try:
            request = Request(
                source_url,
                headers={"User-Agent": "AndreyPortfolio/1.0 (+app-icon-build)"},
            )
            with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                content = response.read(MAXIMUM_DOWNLOAD_BYTES + 1)
            if len(content) > MAXIMUM_DOWNLOAD_BYTES:
                raise ValueError("Download exceeds the artwork size limit")
            return content
        except (URLError, OSError, ValueError) as error:
            last_error = error
            if attempt_number < DOWNLOAD_ATTEMPTS - 1:
                time.sleep(attempt_number + 1)
    raise RuntimeError(f"Unable to download Apple artwork from {source_url}: {last_error}") from last_error


def find_application_record(application_id):
    lookup_url = "https://itunes.apple.com/lookup?" + urlencode(
        {"id": application_id, "country": "lu"},
    )
    lookup_response = json.loads(download_content(lookup_url))
    for application_record in lookup_response.get("results", []):
        if str(application_record.get("trackId")) == application_id:
            return application_record
    raise RuntimeError(f"Apple lookup did not return expected app ID {application_id}")


def find_artwork_url(application_record, application_id):
    artwork_url = application_record.get("artworkUrl512") or application_record.get("artworkUrl100")
    if not artwork_url or not artwork_url.startswith("https://"):
        raise RuntimeError(f"No HTTPS artwork URL for {application_id}")
    return artwork_url


def identify_image_extension(image_content, application_id):
    if image_content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if image_content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if image_content.startswith(b"RIFF") and image_content[8:12] == b"WEBP":
        return "webp"
    raise RuntimeError(f"Unexpected image bytes for {application_id}")


def replace_image_source(image_tag, artwork_path):
    if re.search(r'\bsrc="[^"]*"', image_tag):
        return re.sub(r'\bsrc="[^"]*"', f'src="{artwork_path}"', image_tag, count=1)
    return image_tag[:-1] + f' src="{artwork_path}">'


def update_application_images(catalogue_html, application_id, artwork_path):
    image_pattern = re.compile(
        r'<img\b[^>]*\bdata-app-id="' + re.escape(application_id) + r'"[^>]*>',
    )
    updated_html, replacement_count = image_pattern.subn(
        lambda image_match: replace_image_source(image_match.group(0), artwork_path),
        catalogue_html,
    )
    if replacement_count == 0:
        raise RuntimeError(f"No image nodes found for app {application_id}")
    return updated_html, replacement_count


def mark_artwork_loaded(catalogue_html):
    return re.sub(
        r'(<a\s+class="[^"]*\bapple-artwork\b[^"]*)"',
        r'\1 is-loaded"',
        catalogue_html,
    )


def prepare_application_artwork(build_directory):
    catalogue_path = build_directory / "apps.html"
    catalogue_html = catalogue_path.read_text(encoding="utf-8")
    artwork_directory = build_directory / "assets" / "app-icons"
    artwork_directory.mkdir(parents=True, exist_ok=True)
    for application_id, application_slug in APPLICATION_SLUGS.items():
        application_record = find_application_record(application_id)
        artwork_url = find_artwork_url(application_record, application_id)
        image_content = download_content(artwork_url)
        image_extension = identify_image_extension(image_content, application_id)
        artwork_path = f"assets/app-icons/{application_slug}.{image_extension}"
        (build_directory / artwork_path).write_bytes(image_content)
        catalogue_html, replacement_count = update_application_images(
            catalogue_html, application_id, artwork_path,
        )
        print(
            f"{application_record.get('trackName')}: {artwork_path} "
            f"({len(image_content)} bytes, {replacement_count} references)"
        )
    catalogue_path.write_text(mark_artwork_loaded(catalogue_html), encoding="utf-8")


def main():
    build_directory = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve()
    prepare_application_artwork(build_directory)


if __name__ == "__main__":
    main()
