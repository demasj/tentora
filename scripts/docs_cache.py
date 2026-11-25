"""Local cache of the official documentation that generated questions are grounded in.

Instead of letting the model search and fetch the docs itself (many expensive
agentic turns per call), the generator reads them here, once, with plain HTTP:

- ``index(profile)`` — each source domain's ``/llms.txt`` (the site's own list
  of its main pages, e.g. https://docs.databricks.com/llms.txt).
- ``page(profile, url)`` — one doc page as plain text (the ``<article>`` only)
  plus the links in it, so overview pages lead to their detail pages.

Everything is cached under ``results/<slug>/docs/``. Pages and llms.txt are
refetched after PAGE_MAX_AGE_DAYS (fetching costs no tokens), so questions are
checked against what the docs say now. Delete that folder to force a refresh.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin

import bank_tools
import exam_config as cfg
import score

PAGE_MAX_AGE_DAYS = 1
USER_AGENT = "Tentora-docs-cache/1.0"
_INDEX_LINE = re.compile(r"^\s*-\s*\[(.+?)\]\((https://[^)\s]+)\)(?:\s*[-:]\s*(.*))?")


def cache_dir(profile: cfg.Profile) -> str:
    return os.path.join(score.results_dir(profile.slug), "docs")


def fresh(path: str, days: float = PAGE_MAX_AGE_DAYS) -> bool:
    return os.path.exists(path) and time.time() - os.path.getmtime(path) < days * 86400


def _get(url: str) -> tuple[str, str]:
    """(final URL after redirects, body text)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.geturl(), resp.read().decode(resp.headers.get_content_charset() or "utf-8", "replace")


def parse_index(text: str) -> list[dict[str, str]]:
    """The ``- [title](url) - description`` entries of an llms.txt file."""
    out = []
    for line in text.splitlines():
        m = _INDEX_LINE.match(line)
        if m and not m.group(2).endswith(".txt"):  # skip links to other llms.txt files
            out.append({"url": m.group(2), "title": m.group(1), "desc": (m.group(3) or "").strip()})
    return out


def index(profile: cfg.Profile) -> list[dict[str, str]]:
    """Every page listed in the source domains' llms.txt (fetched at most every PAGE_MAX_AGE_DAYS)."""
    os.makedirs(cache_dir(profile), exist_ok=True)
    entries = []
    for domain in profile.source_domains:
        path = os.path.join(cache_dir(profile), f"llms-{domain}.txt")
        if not fresh(path):
            _, text = _get(f"https://{domain}/llms.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        with open(path, encoding="utf-8") as f:
            entries += [e for e in parse_index(f.read()) if bank_tools.is_official(e["url"], profile)]
    return entries


class _Article(HTMLParser):
    """Text and links of the page's <article> (falls back to <main>), minus chrome."""

    SKIP = {"script", "style", "nav", "footer", "svg", "button", "noscript", "form"}
    BLOCK = {"p", "div", "li", "pre", "tr", "br", "section", "table", "ul", "ol", "dt", "dd",
             "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}

    def __init__(self, base: str, container: str):
        super().__init__(convert_charrefs=True)
        self.base, self.container = base, container
        self.inside = self.skip = 0
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == self.container:
            self.inside += 1
        if not self.inside:
            return
        if tag in self.SKIP:
            self.skip += 1
        elif not self.skip:
            if tag in self.BLOCK:
                self.parts.append("\n" + ("#" * int(tag[1]) + " " if tag[0] == "h" and tag[1:].isdigit() else ""))
            if tag == "a":
                href = dict(attrs).get("href")
                self._href, self._anchor = (urldefrag(urljoin(self.base, href))[0] if href else None), []

    def handle_endtag(self, tag):
        if self.inside and tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._anchor).split())))
            self._href = None
        if tag == self.container and self.inside:
            self.inside -= 1

    def handle_data(self, data):
        if self.inside and not self.skip:
            self.parts.append(data)
            if self._href:
                self._anchor.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_page(html: str, url: str) -> dict:
    """{"text", "links": [[url, anchor], ...]} of one doc page's main content."""
    for container in ("article", "main", "body"):
        parser = _Article(url, container)
        parser.feed(html)
        if parser.text():
            break
    seen, links = set(), []
    for href, anchor in parser.links:
        if anchor and href != url and href not in seen:
            seen.add(href)
            links.append([href, anchor])
    return {"text": parser.text(), "links": links}


def page(profile: cfg.Profile, url: str) -> dict:
    """One official doc page as {"url", "text", "links"}; cached for PAGE_MAX_AGE_DAYS."""
    if not bank_tools.is_official(url, profile):
        raise ValueError(f"not an official doc URL: {url}")
    path = os.path.join(cache_dir(profile), "pages", hashlib.sha1(url.encode()).hexdigest() + ".json")
    if fresh(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    final, html = _get(url)
    data = {"url": url, **html_to_page(html, final)}
    data["links"] = [l for l in data["links"] if bank_tools.is_official(l[0], profile)]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data
