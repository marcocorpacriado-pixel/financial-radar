import io
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch
from xml.sax.saxutils import escape

import news

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def item(title, source="Reuters", date="Fri, 02 Oct 2026 14:00:00 GMT", link="https://news.google.com/rss/articles/a1"):
    parts = [f"<title>{escape(title)}</title>" if title is not None else "",
             f"<link>{link}</link>" if link is not None else "",
             f"<pubDate>{date}</pubDate>",
             f'<source url="https://example.com">{escape(source)}</source>']
    return "<item>" + "".join(parts) + "</item>"


def feed(*items):
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>q</title>'
            + "".join(items) + "</channel></rss>").encode()


class ParseFeedTest(unittest.TestCase):
    def test_valid_items_in_feed_order(self):
        xml = feed(
            item("Microsoft gives Copilot an overhaul - CNBC", "CNBC", "Fri, 25 Sep 2026 18:38:38 GMT", "https://g/1"),
            item("Viejo titular - Reuters", date="Sat, 05 Sep 2026 10:00:00 GMT"),        # fuera de 14 días
            item("Fecha rota - Reuters", date="ayer por la tarde"),
            item(None),                                                                   # sin título
            item("Sin enlace - Reuters", link=None),
            item("Has MSFT Stock Run Out Of Steam? - trefis.com", "trefis.com", "Tue, 29 Sep 2026 20:31:29 GMT", "https://g/2"),
            item("Titular sin sufijo de fuente", "Bloomberg", "Sat, 03 Oct 2026 09:00:00 +0200", "https://g/3"),
        )
        self.assertEqual(news.parse_feed(xml, NOW), [
            news.Headline("Microsoft gives Copilot an overhaul", "CNBC", datetime(2026, 9, 25, 18, 38, 38, tzinfo=timezone.utc), "https://g/1"),
            news.Headline("Has MSFT Stock Run Out Of Steam?", "trefis.com", datetime(2026, 9, 29, 20, 31, 29, tzinfo=timezone.utc), "https://g/2"),
            news.Headline("Titular sin sufijo de fuente", "Bloomberg", datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc), "https://g/3"),
        ])

    def test_window_edges(self):
        xml = feed(item("Hace 13 días - Reuters", date="Sun, 20 Sep 2026 13:00:00 GMT"),
                   item("Hace 15 días - Reuters", date="Fri, 18 Sep 2026 11:00:00 GMT"),
                   item("Mañana por desfase horario - Reuters", date="Sat, 03 Oct 2026 23:00:00 GMT"))
        self.assertEqual([h.title for h in news.parse_feed(xml, NOW)], ["Hace 13 días", "Mañana por desfase horario"])

    def test_invalid_xml(self):
        with self.assertRaises(ValueError):
            news.parse_feed(b"<rss><channel><item>", NOW)


class DedupeTest(unittest.TestCase):
    def h(self, title):
        return news.Headline(title, "S", NOW, "https://g/" + title)

    def test_same_story_from_two_outlets_keeps_first(self):
        first = self.h("Microsoft unveils Copilot overhaul, stock soars")
        out = news.dedupe([first, self.h("Microsoft stock soars as it unveils Copilot overhaul"), self.h("Azure wins Pentagon cloud deal")])
        self.assertEqual([x.title for x in out], [first.title, "Azure wins Pentagon cloud deal"])

    def test_different_stories_same_company_are_kept(self):
        titles = ["Microsoft stock rises", "Microsoft stock falls", "Microsoft raises dividend 10%"]
        self.assertEqual([x.title for x in news.dedupe([self.h(t) for t in titles])], titles)


class FetchNewsTest(unittest.TestCase):
    def test_url_matches_template(self):
        self.assertEqual(news.feed_url("MSFT"),
                         "https://news.google.com/rss/search?q=MSFT+stock+when:14d&hl=en-US&gl=US&ceid=US:en")

    def test_fetch_dedupes_then_limits(self):
        xml = feed(item("Microsoft unveils Copilot overhaul, stock soars - CNBC", "CNBC"),
                   item("Microsoft stock soars as it unveils Copilot overhaul - Reuters"),
                   item("Azure wins Pentagon cloud deal - Bloomberg", "Bloomberg"),
                   item("Microsoft raises dividend 10% - WSJ", "WSJ"))
        with patch("news.urllib.request.urlopen", return_value=io.BytesIO(xml)) as urlopen:
            out = news.fetch_news("MSFT", limit=2, now=NOW)
        self.assertEqual([h.title for h in out], ["Microsoft unveils Copilot overhaul, stock soars", "Azure wins Pentagon cloud deal"])
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, news.feed_url("MSFT"))
        self.assertEqual(req.get_header("User-agent"), "financial-radar/1.0 (personal RSS reader)")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 20)

    def test_http_error(self):
        err = urllib.error.HTTPError("u", 503, "Unavailable", {}, io.BytesIO(b""))
        with patch("news.urllib.request.urlopen", side_effect=err):
            with self.assertRaisesRegex(RuntimeError, "MSFT.*503"):
                news.fetch_news("MSFT", now=NOW)

    def test_broken_xml(self):
        with patch("news.urllib.request.urlopen", return_value=io.BytesIO(b"<html>captcha")):
            with self.assertRaises(ValueError):
                news.fetch_news("MSFT", now=NOW)


if __name__ == "__main__":
    unittest.main()
