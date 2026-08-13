from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import render_links


class RenderLinksTests(unittest.TestCase):
    def write_config(self, directory: Path, body: str) -> Path:
        path = directory / "links.toml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_load_and_render_enabled_and_disabled_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.write_config(
                Path(temporary),
                """
                version = 1

                [[links]]
                label = "LinkedIn & friends"
                url = "https://www.linkedin.com/company/example/"
                icon = "linkedin"
                enabled = true

                [[links]]
                label = "Matrix"
                url = "MATRIX_ROOM_URL"
                icon = "matrix"
                button = "matrix"
                enabled = false
                """,
            )

            links = render_links.load_links(config)
            rendered = render_links.render_links(links)

            self.assertIn("LinkedIn &amp; friends", rendered)
            self.assertIn("button-pskc-outline", rendered)
            self.assertIn("images/icons/linkedin.svg", rendered)
            self.assertIn("Disabled link", rendered)
            self.assertIn("MATRIX_ROOM_URL", rendered)

    def test_enabled_and_disabled_sections_render_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = self.write_config(
                Path(temporary),
                """
                version = 1

                [[sections]]
                id = "community"
                label = "Community & conversation"
                enabled = true

                [[sections]]
                id = "media"
                label = "Media"
                enabled = false

                [[links]]
                label = "Matrix"
                url = "https://matrix.to/#/#example:matrix.org"
                icon = "matrix"
                section = "community"

                [[links]]
                label = "Spotify"
                url = "SPOTIFY_PROFILE_URL"
                icon = "spotify"
                enabled = false
                section = "media"
                """,
            )

            config = render_links.load_config(config_path)
            rendered = render_links.render_block(config)

            self.assertIn('class="link-section"', rendered)
            self.assertIn("Community &amp; conversation", rendered)
            self.assertIn('data-disabled-section="media"', rendered)
            self.assertIn("SPOTIFY_PROFILE_URL", rendered)

    def test_link_cannot_reference_unknown_section(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.write_config(
                Path(temporary),
                """
                version = 1
                [[links]]
                label = "Example"
                url = "https://example.com/"
                icon = "generic-website"
                section = "missing"
                """,
            )

            with self.assertRaisesRegex(render_links.ConfigError, "unknown section"):
                render_links.load_config(config)

    def test_enabled_link_requires_https(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.write_config(
                Path(temporary),
                """
                version = 1
                [[links]]
                label = "Unsafe"
                url = "javascript:alert(1)"
                icon = "github"
                enabled = true
                """,
            )

            with self.assertRaisesRegex(render_links.ConfigError, "absolute HTTPS"):
                render_links.load_links(config)

    def test_icon_slug_cannot_escape_icon_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self.write_config(
                Path(temporary),
                """
                version = 1
                [[links]]
                label = "Bad icon"
                url = "https://example.com/"
                icon = "../secret"
                """,
            )

            with self.assertRaisesRegex(render_links.ConfigError, "icon slug"):
                render_links.load_links(config)

    def test_update_index_is_idempotent(self) -> None:
        link = render_links.Link(
            label="Example",
            url="https://example.com/",
            icon="generic-website",
            button="pskc-outline",
            enabled=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            index = Path(temporary) / "index.html"
            index.write_text(
                f"before\n{render_links.START_MARKER}\n{render_links.END_MARKER}\nafter\n",
                encoding="utf-8",
            )

            config = render_links.Config((link,), ())
            self.assertTrue(render_links.update_index(index, config, check=False))
            self.assertFalse(render_links.update_index(index, config, check=True))


if __name__ == "__main__":
    unittest.main()
