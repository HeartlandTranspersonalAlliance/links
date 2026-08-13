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
    section: str | None = None


@dataclass(frozen=True)
class Section:
    id: str
    label: str
    enabled: bool


@dataclass(frozen=True)
class Config:
    links: tuple[Link, ...]
    sections: tuple[Section, ...]


def _required_string(
    raw: dict[str, object], key: str, position: int, kind: str = "links"
) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{kind}[{position}].{key} must be a non-empty string")
    return value.strip()


def load_config(path: Path) -> Config:
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    if document.get("version") != 1:
        raise ConfigError("links.toml must contain version = 1")

    unknown_document_keys = set(document) - {"version", "links", "sections"}
    if unknown_document_keys:
        names = ", ".join(sorted(unknown_document_keys))
        raise ConfigError(f"links.toml has unknown top-level field(s): {names}")

    raw_sections = document.get("sections", [])
    if not isinstance(raw_sections, list):
        raise ConfigError("links.toml sections must use [[sections]] tables")

    sections: list[Section] = []
    section_ids: set[str] = set()
    for position, raw in enumerate(raw_sections, start=1):
        if not isinstance(raw, dict):
            raise ConfigError(f"sections[{position}] must be a TOML table")
        unknown = set(raw) - {"id", "label", "enabled"}
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ConfigError(f"sections[{position}] has unknown field(s): {names}")

        section_id = _required_string(raw, "id", position, "sections")
        label = _required_string(raw, "label", position, "sections")
        enabled = raw.get("enabled", True)
        if not SAFE_SLUG.fullmatch(section_id):
            raise ConfigError(f"sections[{position}].id must be a lowercase slug")
        if section_id in section_ids:
            raise ConfigError(f"sections[{position}].id duplicates '{section_id}'")
        if not isinstance(enabled, bool):
            raise ConfigError(f"sections[{position}].enabled must be true or false")
        section_ids.add(section_id)
        sections.append(Section(section_id, label, enabled))

    raw_links = document.get("links")
    if not isinstance(raw_links, list) or not raw_links:
        raise ConfigError("links.toml must contain at least one [[links]] entry")

    links: list[Link] = []
    active_urls: set[str] = set()
    for position, raw in enumerate(raw_links, start=1):
        if not isinstance(raw, dict):
            raise ConfigError(f"links[{position}] must be a TOML table")

        unknown = set(raw) - {"label", "url", "icon", "button", "enabled", "section"}
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ConfigError(f"links[{position}] has unknown field(s): {names}")

        label = _required_string(raw, "label", position)
        url = _required_string(raw, "url", position)
        icon = _required_string(raw, "icon", position)
        button = raw.get("button", "pskc-outline")
        enabled = raw.get("enabled", True)
        section = raw.get("section")

        if not isinstance(button, str) or not SAFE_BUTTON.fullmatch(button):
            raise ConfigError(f"links[{position}].button must be a safe CSS slug")
        if not SAFE_SLUG.fullmatch(icon):
            raise ConfigError(f"links[{position}].icon must be a lowercase icon slug")
        if not isinstance(enabled, bool):
            raise ConfigError(f"links[{position}].enabled must be true or false")
        if section is not None:
            if not isinstance(section, str) or not SAFE_SLUG.fullmatch(section):
                raise ConfigError(f"links[{position}].section must be a lowercase slug")
            if section not in section_ids:
                raise ConfigError(
                    f"links[{position}].section references unknown section '{section}'"
                )

        if enabled:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ConfigError(
                    f"links[{position}].url must be an absolute HTTPS URL when enabled"
                )
            if url in active_urls:
                raise ConfigError(f"links[{position}].url duplicates an enabled link")
            active_urls.add(url)

        links.append(Link(label, url, icon, button, enabled, section))

    empty_sections = [
        section.id
        for section in sections
        if not any(link.section == section.id for link in links)
    ]
    if empty_sections:
        raise ConfigError("section(s) contain no links: " + ", ".join(empty_sections))

    return Config(tuple(links), tuple(sections))


def render_anchor(link: Link, indent: str = "            ") -> list[str]:
    label = html.escape(link.label)
    url = html.escape(link.url, quote=True)
    icon = html.escape(link.icon, quote=True)
    button = html.escape(link.button, quote=True)
    return [
        f'{indent}<a class="button button-{button}" href="{url}" target="_blank" rel="noopener noreferrer">',
        f'{indent}  <img class="icon" aria-hidden="true" src="images/icons/{icon}.svg" alt="">',
        f"{indent}  {label}",
        f"{indent}</a>",
    ]


def append_link(output: list[str], link: Link, indent: str = "            ") -> None:
    output.append("")
    anchor = render_anchor(link, indent)
    if link.enabled:
        output.extend(anchor)
    else:
        output.append(
            f"{indent}<!-- Disabled link: add its URL and set enabled = true in links.toml."
        )
        output.extend(anchor)
        output.append(f"{indent}-->")


def append_section(output: list[str], section: Section, links: list[Link]) -> None:
    section_id = html.escape(section.id, quote=True)
    label = html.escape(section.label)
    output.append("")
    if not section.enabled:
        output.append(
            f'            <template data-disabled-section="{section_id}">'
        )
        output.append(
            f'              <section class="link-section" aria-labelledby="section-{section_id}">'
        )
        output.append(
            f'                <h2 id="section-{section_id}" class="link-section__title">{label}</h2>'
        )
        for link in links:
            output.append("")
            output.extend(render_anchor(link, "                "))
        output.append("              </section>")
        output.append("            </template>")
        return

    output.append(
        f'            <section class="link-section" aria-labelledby="section-{section_id}">'
    )
    output.append(
        f'              <h2 id="section-{section_id}" class="link-section__title">{label}</h2>'
    )
    for link in links:
        append_link(output, link, "              ")
    output.append("            </section>")


def render_block(config: Config) -> str:
    output = [START_MARKER]
    for link in config.links:
        if link.section is None:
            append_link(output, link)

    for section in config.sections:
        section_links = [link for link in config.links if link.section == section.id]
        append_section(output, section, section_links)

    output.extend(["", END_MARKER])
    return "\n".join(output)


def load_links(path: Path) -> list[Link]:
    """Compatibility helper returning only the configured links."""
    return list(load_config(path).links)


def render_links(links: list[Link]) -> str:
    """Compatibility helper for callers that do not use sections."""
    return render_block(Config(tuple(links), ()))


def update_index(path: Path, config: Config, check: bool) -> bool:
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
    expected = current[:start] + render_block(config) + current[end:]
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
        config = load_config(args.config)
        messages = sync_icons(list(config.links), args.icons_dir, args.sync_icons and not args.check)
        changed = update_index(args.index, config, args.check)
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
