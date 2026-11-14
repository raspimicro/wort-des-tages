import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from feed_updater import DailyWord, DailyWordParser, update_feed


class DailyWordParserTests(unittest.TestCase):
    def test_extracts_word_and_part_of_speech(self):
        parser = DailyWordParser()
        parser.feed(
            '<section class="scene"><header><h2 class="scene__title">'
            '<a class="scene__title-link" href="/rechtschreibung/juxig">'
            'ju\u00ad<span>xig</span></a></h2>'
            '<a class="scene__main" href="/rechtschreibung/juxig">Adjektiv</a>'
            "</header></section>"
        )

        self.assertEqual(
            parser.result(),
            DailyWord(
                title="juxig",
                link="https://www.duden.de/rechtschreibung/juxig",
                part_of_speech="Adjektiv",
            ),
        )


class FeedUpdateTests(unittest.TestCase):
    def test_replaces_todays_item_and_preserves_history(self):
        feed = """// phpcs:ignoreFile
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Wort des Tages</title>
    <link>https://www.duden.de/</link>
    <description></description>
    <language>de</language>
    <item>
      <title>Zukunftswort</title>
      <link>https://www.duden.de/rechtschreibung/zukunft</link>
      <description>Adjektiv</description>
      <pubDate>Mon, 16 Nov 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>alter Eintrag</title>
      <link>https://www.duden.de/rechtschreibung/alt</link>
      <description>Substantiv</description>
      <pubDate>Sun, 15 Nov 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Vortag</title>
      <link>https://www.duden.de/rechtschreibung/vortag</link>
      <description>Adjektiv</description>
      <pubDate>Sat, 14 Nov 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""
        word = DailyWord(
            title="juxig",
            link="https://www.duden.de/rechtschreibung/juxig",
            part_of_speech="Adjektiv",
        )
        published = datetime(2026, 11, 15, 11, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feed.xml"
            path.write_text(feed, encoding="utf-8")

            self.assertTrue(update_feed(path, word, published))
            updated = ET.parse(path).getroot()
            channel = updated.find("channel")
            items = channel.findall("item")
            self.assertEqual(items[0].findtext("title"), "juxig")
            self.assertEqual(items[0].findtext("link"), word.link)
            self.assertEqual(items[1].findtext("title"), "Vortag")
            self.assertEqual(len(items), 2)
            self.assertEqual(channel.findtext("link"), "https://www.duden.de/wort-des-tages")
            self.assertNotIn("// phpcs:ignoreFile", path.read_text(encoding="utf-8"))
            self.assertFalse(update_feed(path, word, published))
