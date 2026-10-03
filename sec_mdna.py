"""MD&A (Item 7 del 10-K / Item 2 del 10-Q) desde SEC EDGAR. Spec: SPEC-sec-mdna.md."""
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = "Marco Corpa marcocorpacriado@gmail.com"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
CACHE_DIR = Path("cache/sec")
MIN_CHARS = 2000
PAUSE = 0.15  # < 10 peticiones/s (límite de la SEC)
FORMS = ("10-K", "10-Q")
requests_made = 0


@dataclass(frozen=True)
class Filing:
    symbol: str
    cik: str
    form: str
    accession: str
    filing_date: str
    report_date: str
    url: str


@dataclass(frozen=True)
class MDNAContext:
    filing: Filing
    text: str
    sha256: str


# --- HTML → texto ---------------------------------------------------------

SKIP = {"script", "style", "head", "title", "ix:header"}
VOID = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr"}
BLOCK = {"p", "div", "br", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6"}
HIDDEN = re.compile(r"display\s*:\s*none", re.I)


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.buf = [], []
        self.skip_tag, self.skip_depth = None, 0
        self.row, self.cell = None, None

    def handle_starttag(self, tag, attrs):
        if self.skip_depth:
            self.skip_depth += tag == self.skip_tag
            return
        if tag not in VOID and (tag in SKIP or HIDDEN.search(dict(attrs).get("style") or "")):
            self.skip_tag, self.skip_depth = tag, 1
            return
        if tag == "tr":
            self._flush()
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag in BLOCK:
            self._break()

    def handle_endtag(self, tag):
        if self.skip_depth:
            self.skip_depth -= tag == self.skip_tag
            return
        if tag in ("td", "th") and self.cell is not None:
            if self.row is not None:
                self.row.append("".join(self.cell))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            line = " | ".join(_merge_cells(self.row))
            if line:
                self.lines.append(line)
            self.row = None
        elif tag in BLOCK:
            self._break()

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.cell is not None:
            self.cell.append(data)
        elif self.row is None:
            self.buf.append(data)

    def _break(self):
        if self.cell is not None:
            self.cell.append(" ")  # un bloque dentro de una celda sólo separa palabras
        else:
            self._flush()

    def _flush(self):
        line = _clean("".join(self.buf))
        if line:
            self.lines.append(line)
        self.buf = []


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _merge_cells(cells: list[str]) -> list[str]:
    """Une las celdas que EDGAR separa: '$' con la cifra siguiente; ')' y '%' con la anterior."""
    out = []
    for c in map(_clean, cells):
        if not c:
            continue
        if out and out[-1] == "$":
            out[-1] += c
        elif out and c in (")", "%", ")%", "%)"):
            out[-1] += c
        else:
            out.append(c)
    return out


def html_to_text(html: str) -> str:
    """Texto plano del documento: bloques como líneas y filas de tabla como 'a | b'."""
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    parser._flush()
    return "\n".join(parser.lines)


# --- Localizar el MD&A ----------------------------------------------------

_TITLE = r"[\s|.:\-–—]*management[’'‘]?s\s+discussion"
_START = {
    "10-K": re.compile(r"^\s*item\s*7(?![0-9a-z])" + _TITLE, re.I),
    "10-Q": re.compile(r"^\s*item\s*2(?![0-9a-z])" + _TITLE, re.I),
}
_END = {
    "10-K": re.compile(r"^\s*item\s*(7a|8)(?![0-9a-z])", re.I),
    "10-Q": re.compile(r"^\s*(item\s*[34](?![0-9a-z])|part\s+ii\b)", re.I),
}


def extract_section(text: str, form: str) -> str:
    """MD&A de text; ante varios encabezados (índice y cuerpo) gana la sección más larga."""
    lines = text.split("\n")
    best = ""
    for i, line in enumerate(lines):
        # Se evalúa con la línea siguiente por si "Item 7." y el título van en líneas distintas.
        if not _START[form].match(line + " " + (lines[i + 1] if i + 1 < len(lines) else "")):
            continue
        end = next((j for j in range(i + 1, len(lines)) if _END[form].match(lines[j])), len(lines))
        section = "\n".join(lines[i:end])
        if len(section) > len(best):
            best = section
    if len(best) < MIN_CHARS:
        raise ValueError(f"MD&A no localizado en el {form} ({len(best)} caracteres)")
    return best


# --- EDGAR ----------------------------------------------------------------

def _get(url: str) -> bytes:
    global requests_made
    time.sleep(PAUSE)
    requests_made += 1
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            gzipped = resp.headers.get("Content-Encoding") == "gzip"
    except urllib.error.HTTPError as e:
        e.close()
        hint = " (revisa User-Agent / límite de peticiones)" if e.code == 403 else ""
        raise RuntimeError(f"SEC HTTP {e.code}: {url}{hint}") from None
    return gzip.decompress(body) if gzipped else body


def _lookup_cik(path: Path, symbol: str) -> str | None:
    key = symbol.replace(".", "-")  # la SEC escribe BRK-B, no BRK.B
    for row in json.loads(path.read_text(encoding="utf-8")).values():
        if str(row.get("ticker", "")).upper() == key:
            return f"{int(row['cik_str']):010d}"
    return None


def _cik(symbol: str) -> str:
    path = CACHE_DIR / "company_tickers.json"
    fresh = not path.exists()
    if fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_get(TICKERS_URL))
    cik = _lookup_cik(path, symbol)
    if cik is None and not fresh:  # puede ser un alta reciente: se refresca el mapa una vez
        path.write_bytes(_get(TICKERS_URL))
        cik = _lookup_cik(path, symbol)
    if cik is None:
        raise ValueError(f"{symbol}: ticker no encontrado en el mapa de la SEC")
    return cik


def filings(symbol: str, form_type: str) -> list[Filing]:
    """Filings 10-K o 10-Q del ticker, más reciente primero (sin enmiendas /A)."""
    if form_type not in FORMS:
        raise ValueError(f"form_type debe ser 10-K o 10-Q, no {form_type!r}")
    symbol = symbol.upper()
    cik = _cik(symbol)
    recent = json.loads(_get(SUBMISSIONS_URL.format(cik=cik)))["filings"]["recent"]
    found = [
        Filing(symbol, cik, form, acc, filed, period,
               f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}")
        for form, acc, filed, period, doc in zip(recent["form"], recent["accessionNumber"], recent["filingDate"],
                                                 recent["reportDate"], recent["primaryDocument"])
        if form == form_type
    ]
    return sorted(found, key=lambda f: f.filing_date, reverse=True)


def _mdna(filing: Filing) -> MDNAContext:
    path = CACHE_DIR / f"{filing.symbol}_{filing.form}_{filing.report_date}.txt"
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        # ponytail: utf-8 con replace; si aparecen filings cp1252 con ’ rotos, leer el charset del <meta>
        html = _get(filing.url).decode("utf-8", errors="replace")
        try:
            text = extract_section(html_to_text(html), filing.form)
        except ValueError as e:
            raise ValueError(f"{filing.symbol}: {e}: {filing.url}") from None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return MDNAContext(filing, text, hashlib.sha256(text.encode()).hexdigest())


def fetch_mdna(symbol: str, form_type: str = "10-Q") -> tuple[MDNAContext, MDNAContext | None]:
    """MD&A del último filing de form_type y del inmediatamente anterior del mismo tipo."""
    found = filings(symbol, form_type)
    if not found:
        raise ValueError(f"{symbol}: sin filings {form_type} en EDGAR")
    current, previous = _mdna(found[0]), None
    if len(found) > 1:
        try:
            previous = _mdna(found[1])
        except ValueError:
            pass  # sin histórico comparable no se bloquea el análisis del actual
    return current, previous


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    for ctx in fetch_mdna(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "10-Q"):
        if ctx is None:
            print("== anterior: no disponible")
            continue
        f = ctx.filing
        print(f"== {f.symbol} {f.form} periodo {f.report_date} (presentado {f.filing_date}) {f.accession}")
        print(f"   {f.url}\n   {len(ctx.text):,} caracteres  sha256 {ctx.sha256[:16]}")
        print(ctx.text[:300], "\n   [...]\n", ctx.text[-300:], "\n")
    print(f"peticiones SEC: {requests_made}", file=sys.stderr)
