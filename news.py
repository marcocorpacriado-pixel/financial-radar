"""Titulares de Google News RSS por ticker, filtrados y deduplicados. Spec: SPEC-news.md."""
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

USER_AGENT = "financial-radar/1.0 (personal RSS reader)"
MAX_BYTES = 2_000_000
LOCALES = {"en": "hl=en-US&gl=US&ceid=US:en", "es": "hl=es&gl=ES&ceid=ES:es"}  # edición de Google News
_STOPWORDS = {"a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "as", "is", "are", "it", "its", "at",
              "with", "after", "by", "from", "this", "that", "why", "what", "how", "today", "stock", "stocks",
              "shares", "inc", "corp"}


@dataclass(frozen=True)
class Headline:
    title: str
    source: str
    published: datetime
    link: str


def feed_url(ticker: str, days: int = 14, query: str | None = None, lang: str = "en") -> str:
    """Por defecto "<ticker> stock" en la edición en inglés; query sustituye a esa búsqueda tal cual."""
    q = urllib.parse.quote_plus(query) if query else f"{urllib.parse.quote_plus(ticker)}+stock"
    return f"https://news.google.com/rss/search?q={q}+when:{days}d&{LOCALES[lang]}"


def parse_feed(xml: bytes, now: datetime, days: int = 14) -> list[Headline]:
    """Items válidos dentro de la ventana, en el orden del feed (relevancia de Google)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ValueError(f"RSS inválido: {e}") from None
    oldest, newest = now - timedelta(days=days), now + timedelta(days=1)  # margen para desfases horarios
    out = []
    for item in root.iterfind("./channel/item"):
        title, link = (item.findtext("title") or "").strip(), (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "").strip()
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if not title or not link or not oldest <= published <= newest:
            continue
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        out.append(Headline(title, source, published, link))
    return out


def _tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[\w$%]+", title.lower()) if w not in _STOPWORDS}  # \w: tildes y ñ


def dedupe(headlines: list[Headline], threshold: float = 0.5) -> list[Headline]:
    """Quita titulares casi idénticos (Jaccard de palabras >= threshold); conserva el primero."""
    kept, seen = [], []
    for h in headlines:
        t = _tokens(h.title)
        if any(len(t & s) / len(t | s) >= threshold for s in seen if t | s):
            continue
        kept.append(h)
        seen.append(t)
    return kept


def fetch_news(ticker: str, limit: int = 10, now: datetime | None = None, query: str | None = None,
               lang: str = "en") -> list[Headline]:
    """Hasta limit titulares deduplicados de los últimos 14 días."""
    req = urllib.request.Request(feed_url(ticker, query=query, lang=lang), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            xml = resp.read(MAX_BYTES)
    except urllib.error.HTTPError as e:
        e.close()
        raise RuntimeError(f"Google News {ticker}: HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Google News {ticker}: sin conexión ({e.reason})") from None
    return dedupe(parse_feed(xml, now or datetime.now(timezone.utc)))[:limit]


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    for h in fetch_news(sys.argv[1]):
        print(f"{h.published:%d-%m} {h.source}: {h.title}")
