#!/usr/bin/env python3
"""Render links.toml into LittleLink HTML and vendor requested icons."""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "links.toml"
DEFAULT_INDEX = ROOT / "site/index.html"
DEFAULT_ICONS = ROOT / "site/images/icons"

START_MARKER = "            <!-- BEGIN GENERATED LINKS: edit links.toml, not this block -->"
END_MARKER = "            <!-- END GENERATED LINKS -->"

# Immutable upstream revisions make icon resolution reproducible.
ICON_SOURCES = (
    (
        "LittleLink v3.11.0",
        "https://raw.githubusercontent.com/sethcottle/littlelink/"
        "3c9162c9f8324d5e002259ed8b4b3cc377b08486/images/icons/{filename}",
    ),
    (
        "LittleLink Extended d7034f8",
        "https://raw.githubusercontent.com/sethcottle/littlelink-extended/"
        "d7034f83a6bf71c4a663d2d046a03b37ec74ae45/images/icons-extended/{filename}",
    ),
)

# A few upstream filenames differ from the public-facing service slug.
ICON_ALIASES = {
    "nostr": ("nostr_logo_wht.svg",),
}

SAFE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SAFE_BUTTON = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_ICON_BYTES = 512 * 1024


class ConfigError(ValueError):
    """Raised when links.toml is unsafe or malformed."""


@dataclass(frozen=True)
class Link:
    label: str
    url: str
    icon: str
    button: str
    enabled: bool


def _required_string(raw: dict[str, object], key: str, position: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"links[{position}].{key} must be a non-empty string")
    return value.strip()


def load_links(path: Path) -> list[Link]:
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    if document.get("version") != 1:
        raise ConfigError("links.toml must contain version = 1")

    raw_links = document.get("links")
    if not isinstance(raw_links, list) or not raw_links:
        raise ConfigError("links.toml must contain at least one [[links]] entry")

    links: list[Link] = []
    active_urls: set[str] = set()
    for position, raw in enumerate(raw_links, start=1):
        if not isinstance(raw, dict):
            raise ConfigError(f"links[{position}] must be a TOML table")

        unknown = set(raw) - {"label", "url", "icon", "button", "enabled"}
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ConfigError(f"links[{position}] has unknown field(s): {names}")

        label = _required_string(raw, "label", position)
        url = _required_string(raw, "url", position)
        icon = _required_string(raw, "icon", position)
        button = raw.get("button", "pskc-outline")
        enabled = raw.get("enabled", True)

        if not isinstance(button, str) or not SAFE_BUTTON.fullmatch(button):
            raise ConfigError(f"links[{position}].button must be a safe CSS slug")
        if not SAFE_SLUG.fullmatch(icon):
            raise ConfigError(f"links[{position}].icon must be a lowercase icon slug")
        if not isinstance(enabled, bool):
            raise ConfigError(f"links[{position}].enabled must be true or false")

        if enabled:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ConfigError(
                    f"links[{position}].url must be an absolute HTTPS URL when enabled"
                )
            if url in active_urls:
                raise ConfigError(f"links[{position}].url duplicates an enabled link")
            active_urls.add(url)

        links.append(Link(label, url, icon, button, enabled))

    return links


def render_anchor(link: Link) -> list[str]:
    label = html.escape(link.label)
    url = html.escape(link.url, quote=True)
    icon = html.escape(link.icon, quote=True)
    button = html.escape(link.button, quote=True)
    return [
        f'            <a class="button button-{button}" href="{url}" target="_blank" rel="noopener noreferrer">',
        f'              <img class="icon" aria-hidden="true" src="images/icons/{icon}.svg" alt="">',
        f"              {label}",
        "            </a>",
    ]


def render_block(links: list[Link]) -> str:
    output = [START_MARKER]
    for link in links:
        output.append("")
        anchor = render_anchor(link)
        if link.enabled:
            output.extend(anchor)
        else:
            output.append(
                "            <!-- Disabled link: add its URL and set enabled = true in links.toml."
            )
            output.extend(anchor)
            output.append("            -->")
    output.extend(["", END_MARKER])
    return "\n".join(output)


def update_index(path: Path, links: list[Link], check: bool) -> bool:
    try:
        current = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    start = current.find(START_MARKER)
    end = current.find(END_MARKER)
    if start < 0 or end < 0 or end < start:
        raise ConfigError(f"{path} does not contain valid generated-link markers")
    if current.find(START_MARKER, start + 1) >= 0 or current.find(END_MARKER, end + 1) >= 0:
        raise ConfigError(f"{path} contains duplicate generated-link markers")

    end += len(END_MARKER)
    expected = current[:start] + render_block(links) + current[end:]
    changed = expected != current
    if changed and check:
        raise ConfigError(f"{path} is stale; run tools/render_links.py --sync-icons")
    if changed:
        path.write_text(expected, encoding="utf-8")
    return changed


def _validate_svg(data: bytes, source: str) -> None:
    if not data or len(data) > MAX_ICON_BYTES:
        raise ConfigError(f"icon from {source} is empty or larger than {MAX_ICON_BYTES} bytes")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ConfigError(f"icon from {source} is not valid SVG: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ConfigError(f"icon from {source} does not have an SVG root element")


def download_icon(slug: str, destination: Path) -> str:
    filenames = (f"{slug}.svg", *ICON_ALIASES.get(slug, ()))
    attempts: list[str] = []
    destination.parent.mkdir(parents=True, exist_ok=True)
    for source_name, template in ICON_SOURCES:
        for filename in filenames:
            url = template.format(filename=filename)
            attempts.append(f"{source_name}:{filename}")
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=f".{slug}.", delete=False
            ) as stream:
                temporary = Path(stream.name)

            try:
                result = subprocess.run(
                    [
                        "curl",
                        "--fail",
                        "--location",
                        "--silent",
                        "--show-error",
                        "--connect-timeout",
                        "5",
                        "--max-time",
                        "20",
                        "--max-filesize",
                        str(MAX_ICON_BYTES),
                        "--output",
                        str(temporary),
                        "--write-out",
                        "%{http_code}",
                        url,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError as exc:
                temporary.unlink(missing_ok=True)
                raise ConfigError("curl is required to resolve missing icons") from exc

            status = result.stdout.strip()
            if status == "404":
                temporary.unlink(missing_ok=True)
                continue
            if result.returncode != 0:
                temporary.unlink(missing_ok=True)
                detail = result.stderr.strip() or f"curl exit status {result.returncode}"
                raise ConfigError(f"could not retrieve {url}: {detail}")

            data = temporary.read_bytes()
            _validate_svg(data, url)
            temporary.replace(destination)
            return f"{source_name}:{filename}"

    searched = ", ".join(attempts)
    raise ConfigError(
        f"icon '{slug}' was not found in the pinned LittleLink catalogs "
        f"(searched {searched}). Add a reviewed SVG at {destination}."
    )


def sync_icons(links: list[Link], directory: Path, allow_downloads: bool) -> list[str]:
    messages: list[str] = []
    for slug in dict.fromkeys(link.icon for link in links):
        destination = directory / f"{slug}.svg"
        if destination.is_file():
            _validate_svg(destination.read_bytes(), str(destination))
            continue
        if not allow_downloads:
            raise ConfigError(
                f"missing icon {destination}; run tools/render_links.py --sync-icons"
            )
        source = download_icon(slug, destination)
        messages.append(f"Added {destination.relative_to(ROOT)} from {source}")
    return messages


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--icons-dir", type=Path, default=DEFAULT_ICONS)
    parser.add_argument(
        "--sync-icons",
        action="store_true",
        help="download missing icons from pinned LittleLink catalogs",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify generated HTML and icons without modifying files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        links = load_links(args.config)
        messages = sync_icons(links, args.icons_dir, args.sync_icons and not args.check)
        changed = update_index(args.index, links, args.check)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for message in messages:
        print(message)
    if changed:
        print(f"Updated {args.index}")
    elif not messages:
        print("Links and icons are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
