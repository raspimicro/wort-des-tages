#!/usr/bin/env python3
"""Refresh feed.xml with the word currently shown on Duden's word-of-the-day page."""

from __future__ import annotations

import html
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from email.utils import format_datetime, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


DUDEN_URL = "https://www.duden.de/wort-des-tages"
FEED_PATH = Path(__file__).with_name("feed.xml")
DC_NS = "http://purl.org/dc/elements/1.1/"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"

ET.register_namespace("dc", DC_NS)
ET.register_namespace("content", CONTENT_NS)


@dataclass(frozen=True)
class DailyWord:
    title: str
    link: str
    part_of_speech: str


class DailyWordParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_scene = False
        self.in_title = False
        self.capture: str | None = None
        self.href = ""
        self.title_parts: list[str] = []
        self.pos_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())

        if tag == "section" and "scene" in classes and not self.in_scene:
            self.in_scene = True
        elif self.in_scene and tag == "h2" and "scene__title" in classes:
            self.in_title = True
        elif self.in_scene and tag == "a":
            if self.in_title and "scene__title-link" in classes:
                self.capture = "title"
                self.href = attributes.get("href") or ""
            elif "scene__main" in classes:
                self.capture = "pos"

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self.capture = None
        elif tag == "h2":
            self.in_title = False
        elif tag == "section" and self.in_scene:
            self.in_scene = False

    def handle_data(self, data: str) -> None:
        if self.capture == "title":
            self.title_parts.append(data)
        elif self.capture == "pos":
            self.pos_parts.append(data)

    def result(self) -> DailyWord:
        title = re.sub(r"\s+", " ", "".join(self.title_parts).replace("\u00ad", "")).strip()
        part_of_speech = re.sub(r"\s+", " ", "".join(self.pos_parts)).strip()
        link = urljoin(DUDEN_URL, self.href)
        if (
            not title
            or not part_of_speech
            or urlparse(link).netloc not in {"www.duden.de", "duden.de"}
            or not urlparse(link).path.startswith("/rechtschreibung/")
        ):
            raise ValueError("Could not find the current word and word class on Duden's page.")
        return DailyWord(title=title, link=link, part_of_speech=part_of_speech)


def fetch_daily_word() -> DailyWord:
    request = Request(
        DUDEN_URL,
        headers={"User-Agent": "wort-des-tages-feed/1.0 (+https://github.com/raspimicro/wort-des-tages)"},
    )
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"Duden returned HTTP {response.status}.")
        page = response.read().decode(response.headers.get_content_charset() or "utf-8")

    parser = DailyWordParser()
    parser.feed(page)
    parser.close()
    return parser.result()


def _item_date(item: ET.Element) -> datetime | None:
    pub_date = item.findtext("pubDate")
    if not pub_date:
        return None
    try:
        parsed = parsedate_to_datetime(pub_date.strip())
    except (TypeError, ValueError):
        return None
    return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()


def _new_item(word: DailyWord, published: datetime) -> ET.Element:
    item = ET.Element("item")
    ET.SubElement(item, "title").text = word.title
    ET.SubElement(item, "link").text = word.link

    escaped_title = html.escape(word.title)
    escaped_link = html.escape(word.link, quote=True)
    escaped_pos = html.escape(word.part_of_speech)
    description = f'<a href="{escaped_link}">{escaped_title}</a><br />{escaped_pos}'
    ET.SubElement(item, "description").text = description
    ET.SubElement(item, f"{{{CONTENT_NS}}}encoded").text = (
        f'<a href="{escaped_link}">{escaped_title}</a><p>{escaped_pos}</p>'
    )
    ET.SubElement(item, "pubDate").text = format_datetime(published)
    ET.SubElement(item, f"{{{DC_NS}}}creator").text = "Duden Redaktion"
    slug = urlparse(word.link).path.rsplit("/", 1)[-1]
    guid = f"{published.date().isoformat()}-{slug}"
    ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = guid
    return item


def _item_xml(word: DailyWord, published: datetime) -> str:
    item = _new_item(word, published)
    title = html.escape(item.findtext("title") or "", quote=True)
    link = html.escape(item.findtext("link") or "", quote=True)
    description = html.escape(item.findtext("description") or "")
    encoded = item.findtext(f"{{{CONTENT_NS}}}encoded") or ""
    pub_date = html.escape(item.findtext("pubDate") or "")
    guid = html.escape(item.findtext("guid") or "", quote=True)
    creator = html.escape(item.findtext(f"{{{DC_NS}}}creator") or "")
    return f"""<item>
  <title>{title}</title>
  <link>{link}</link>
  <description>{description}</description>
  <content:encoded><![CDATA[{encoded}]]></content:encoded>
  <pubDate>{pub_date}</pubDate>
  <dc:creator>{creator}</dc:creator>
  <guid isPermaLink="false">{guid}</guid>
  </item>"""


def update_feed(feed_path: Path, word: DailyWord, published: datetime) -> bool:
    raw_feed = feed_path.read_text(encoding="utf-8")
    if raw_feed.startswith("// phpcs:ignoreFile"):
        raw_feed = raw_feed.split("\n", 1)[1]
    root = ET.fromstring(raw_feed)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("The RSS file has no channel element.")
    if channel.find("link") is None or channel.find("description") is None:
        raise ValueError("The RSS channel is missing its link or description.")

    item_pattern = re.compile(r"<item(?:\s[^>]*)?>.*?</item>", re.DOTALL)
    item_matches = list(item_pattern.finditer(raw_feed))
    today_matches = []
    feed_items = channel.findall("item")
    if len(item_matches) != len(feed_items):
        raise ValueError("The RSS file contains an item that could not be matched.")
    future_items = []
    todays_items = []
    for match, item in zip(item_matches, feed_items):
        item_date = _item_date(item)
        if item_date is not None:
            if item_date.date() > published.date():
                future_items.append((match, item))
            elif item_date.date() == published.date():
                todays_items.append((match, item))
    today_matches = todays_items

    already_current = (
        not future_items and len(today_matches) == 1
        and bool(item_matches)
        and today_matches[0][0].start() == item_matches[0].start()
        and today_matches[0][1].findtext("title") == word.title
        and today_matches[0][1].findtext("link") == word.link
        and word.part_of_speech in (today_matches[0][1].findtext("description") or "")
    )
    updated_feed = raw_feed
    if not already_current:
        new_item = _item_xml(word, published)
        for match, _ in sorted(
            todays_items + future_items,
            key=lambda entry: entry[0].start(),
            reverse=True,
        ):
            updated_feed = updated_feed[:match.start()] + updated_feed[match.end():]
        first_item = item_pattern.search(updated_feed)
        insert_at = first_item.start() if first_item else updated_feed.index("</channel>")
        updated_feed = updated_feed[:insert_at] + new_item + "\n" + updated_feed[insert_at:]

    item_match = item_pattern.search(updated_feed)
    header_end = item_match.start() if item_match else updated_feed.index("</channel>")
    header = updated_feed[:header_end]
    header, link_replacements = re.subn(
        r"(<link>).*?(</link>)",
        lambda match: f"{match.group(1)}{html.escape(DUDEN_URL)}{match.group(2)}",
        header,
        count=1,
        flags=re.DOTALL,
    )
    header, description_replacements = re.subn(
        r"(<description>).*?(</description>)",
        lambda match: f"{match.group(1)}Das aktuelle Wort des Tages von Duden.{match.group(2)}",
        header,
        count=1,
        flags=re.DOTALL,
    )
    if link_replacements != 1 or description_replacements != 1:
        raise ValueError("The RSS channel is missing its link or description.")
    updated_feed = header + updated_feed[header_end:]

    if updated_feed == raw_feed:
        return False

    ET.fromstring(updated_feed)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=feed_path.parent, prefix=feed_path.name, suffix=".tmp", delete=False
    ) as temporary:
        temporary.write(updated_feed.encode("utf-8"))
        temporary_path = Path(temporary.name)

    try:
        os.replace(temporary_path, feed_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return True


def main() -> None:
    word = fetch_daily_word()
    published = datetime.now().astimezone()
    if update_feed(FEED_PATH, word, published):
        print(f"Updated feed with {word.title} ({word.part_of_speech}).")
    else:
        print(f"Feed already contains {word.title} for today.")


if __name__ == "__main__":
    main()
