"""The extension's own pages: links are real links, and the setup steps say what blocks them."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "ui" / "pages"

# Core's page Markdown links only ``[text](https://…)``; a bare address stays plain text.
_LINK = re.compile(r"\[([^\]\n]+)\]\((https://[^\s)]+)\)")
_URL = re.compile(r"https://[^\s)\]]+")


def _components(page: str) -> dict[str, dict]:
    data = yaml.safe_load((PAGES / page).read_text(encoding="utf-8"))
    return {item["component_id"]: item for item in data["components"]}


def _markdown_bodies():
    for path in sorted(PAGES.glob("*.yaml")):
        for component in yaml.safe_load(path.read_text(encoding="utf-8")).get("components", []):
            if component.get("type") == "markdown":
                yield path.name, component["component_id"], component.get("body") or ""


def test_every_address_in_a_page_is_a_named_link():
    for page, component, body in _markdown_bodies():
        linked = {url for _text, url in _LINK.findall(body)}
        bare = [url for url in _URL.findall(_LINK.sub("", body)) if url not in linked]
        assert bare == [], f"{page}/{component} shows addresses as plain text: {bare}"


def test_amazon_documentation_lists_seven_named_official_links():
    body = _components("setup-guide.yaml")["amazon-docs"]["body"]
    links = _LINK.findall(body)
    assert len(links) == 7
    for text, url in links:
        assert text.strip() and not text.startswith("http"), (text, url)
        assert url.startswith(
            ("https://developer-docs.amazon/", "https://developer-docs.shipping.amazon.com/")
        )


def test_step_five_says_the_connection_page_waits_for_a_ready_configuration():
    body = _components("setup-guide.yaml")["connect"]["body"]
    first_paragraph = body.split("\n\n", 1)[0]
    assert "Ready for" in first_paragraph and "authorization" in first_paragraph
    assert "invalid_client" in first_paragraph and "**Replace**" in first_paragraph
    assert body.index("greyed out") < body.index("Then open the **Connection** page")
