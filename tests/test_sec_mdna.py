import gzip
import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import sec_mdna

FILLER = "Net sales increased due to higher iPhone demand and Services growth. " * 40  # ~2.800 caracteres


# --- HTML → texto ---------------------------------------------------------

class HtmlToTextTest(unittest.TestCase):
    def test_skips_hidden_and_noise(self):
        html = """<html><head><title>T</title><style>p{color:red}</style></head><body>
        <script>var x = 1;</script>
        <ix:header><ix:hidden>fact oculto</ix:hidden></ix:header>
        <div style="display:none"><div>anidado oculto</div>sigue oculto</div>
        <div style="color:red; display: none">también oculto</div>
        <meta style="display:none"><br style="display:none">
        <p>visible</p></body></html>"""
        self.assertEqual(sec_mdna.html_to_text(html), "visible")

    def test_blocks_make_lines(self):
        html = "<p>Uno</p><p>Dos<br>Tres</p><div><span>Cua</span><span>tro</span></div><ul><li>Cinco</li></ul>"
        self.assertEqual(sec_mdna.html_to_text(html), "Uno\nDos\nTres\nCuatro\nCinco")

    def test_entities_and_whitespace(self):
        html = "<p>R&amp;D&#8217;s&#160;&#160;spend\n   rose</p>"
        self.assertEqual(sec_mdna.html_to_text(html), "R&D’s spend rose")

    def test_tables_keep_figures(self):
        html = """<p>Antes</p><table>
        <tr><td></td><td colspan="3"><div>Three Months Ended</div><div>June 27,<br>2026</div></td><td></td></tr>
        <tr><td><span>Products</span></td><td>$</td><td>78,678</td><td></td><td>$</td><td>66,613</td></tr>
        <tr><td>Other income</td><td>(1,234</td><td>)</td><td>12</td><td>%</td></tr>
        <tr><td> </td><td>&#160;</td></tr>
        </table><p>Después</p>"""
        self.assertEqual(sec_mdna.html_to_text(html), "\n".join([
            "Antes",
            "Three Months Ended June 27, 2026",
            "Products | $78,678 | $66,613",
            "Other income | (1,234) | 12%",
            "Después",
        ]))


# --- Localizar la sección -------------------------------------------------

def lines(*parts):
    return "\n".join(parts)


TOC_10Q = [
    "Item 1. | Financial Statements | 1",
    "Item 2. | Management’s Discussion and Analysis of Financial Condition and Results of Operations | 13",
    "Item 3. | Quantitative and Qualitative Disclosures About Market Risk | 20",
    "Item 4. | Controls and Procedures | 21",
]


class ExtractSectionTest(unittest.TestCase):
    def test_10q_body_not_toc(self):
        text = lines(
            *TOC_10Q,
            "Item 1. Financial Statements", FILLER,
            "Item 2. Management’s Discussion and Analysis of Financial Condition and Results of Operations",
            FILLER, "Products | $78,678 | $66,613",
            "Item 3. Quantitative and Qualitative Disclosures About Market Risk", "Market risk text.",
            "Item 4. Controls and Procedures",
            "PART II — OTHER INFORMATION",
            "Item 2. Unregistered Sales of Equity Securities and Use of Proceeds", FILLER * 3,
        )
        section = sec_mdna.extract_section(text, "10-Q")
        self.assertTrue(section.startswith("Item 2. Management’s Discussion"))
        self.assertTrue(section.endswith("Products | $78,678 | $66,613"))
        self.assertNotIn("Unregistered", section)

    def test_10q_ends_at_item_4_or_part_ii(self):
        head = "Item 2. Management's Discussion and Analysis"
        self.assertEqual(sec_mdna.extract_section(lines(head, FILLER, "Item 4. Controls", "x"), "10-Q"), lines(head, FILLER))
        self.assertEqual(sec_mdna.extract_section(lines(head, FILLER, "PART II", "x"), "10-Q"), lines(head, FILLER))

    def test_10k_item_7_not_7a(self):
        text = lines(
            "Item 7. | Management's Discussion and Analysis | 20",
            "Item 7A. | Quantitative and Qualitative Disclosures About Market Risk | 35",
            "Item 7. Management's Discussion and Analysis of Financial Condition", FILLER,
            "Item 7A. Quantitative and Qualitative Disclosures About Market Risk", FILLER * 2,
            "Item 8. Financial Statements",
        )
        self.assertEqual(sec_mdna.extract_section(text, "10-K"),
                         lines("Item 7. Management's Discussion and Analysis of Financial Condition", FILLER))

    def test_10k_ends_at_item_8_without_7a(self):
        head = "ITEM 7. MANAGEMENT’S DISCUSSION AND ANALYSIS"
        self.assertEqual(sec_mdna.extract_section(lines(head, FILLER, "ITEM 8. FINANCIAL STATEMENTS", "x"), "10-K"),
                         lines(head, FILLER))

    def test_heading_split_over_two_lines(self):
        text = lines("Item 7.", "Management's Discussion and Analysis", FILLER, "Item 8. Financial Statements")
        self.assertEqual(sec_mdna.extract_section(text, "10-K"), lines("Item 7.", "Management's Discussion and Analysis", FILLER))

    def test_cross_reference_mid_line_is_ignored(self):
        text = lines("As discussed in Item 7. Management's Discussion and Analysis of our 10-K,", FILLER, "Item 8.")
        with self.assertRaises(ValueError):
            sec_mdna.extract_section(text, "10-K")

    def test_too_short_raises(self):
        text = lines("Item 7. Management's Discussion and Analysis", "See Exhibit 13, incorporated by reference.", "Item 8.")
        with self.assertRaisesRegex(ValueError, "MD&A"):
            sec_mdna.extract_section(text, "10-K")


# --- SEC simulada ---------------------------------------------------------

def doc_html(quarter_sales, item="2", end="3"):
    return f"""<html><body>
    <table><tr><td>Item {item}.</td><td><a href="#mda">Management&#8217;s Discussion and Analysis of Financial Condition and Results of Operations</a></td><td>13</td></tr>
    <tr><td>Item {end}.</td><td>Quantitative and Qualitative Disclosures About Market Risk</td><td>20</td></tr></table>
    <div id="mda"><span>Item {item}.</span><span> Management&#8217;s Discussion and Analysis of Financial Condition and Results of Operations</span></div>
    <p>{FILLER}</p>
    <table><tr><td>Products</td><td>$</td><td>{quarter_sales}</td></tr></table>
    <div>Item {end}. Quantitative and Qualitative Disclosures About Market Risk</div><p>Riesgo.</p>
    <div>PART II</div><div>Item 2. Unregistered Sales of Equity Securities</div><p>{FILLER * 3}</p>
    </body></html>""".encode()


TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK0000320193.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/320193/"
Q3_URL = ARCHIVE + "000032019326000020/aapl-20260627.htm"
Q2_URL = ARCHIVE + "000032019326000013/aapl-20260328.htm"
K_URL = ARCHIVE + "000032019325000079/aapl-20250927.htm"


def submissions(*filings):
    keys = ("form", "accessionNumber", "filingDate", "reportDate", "primaryDocument")
    return json.dumps({"filings": {"recent": {k: [f[i] for f in filings] for i, k in enumerate(keys)}}}).encode()


SUBMISSIONS = submissions(
    ("4", "0000320193-26-000030", "2026-09-01", "", "xslF345X05/form4.xml"),
    ("10-Q", "0000320193-26-000020", "2026-07-31", "2026-06-27", "aapl-20260627.htm"),
    ("10-Q/A", "0000320193-26-000015", "2026-06-15", "2026-03-28", "aapl-a.htm"),
    ("10-Q", "0000320193-26-000013", "2026-05-01", "2026-03-28", "aapl-20260328.htm"),
    ("10-K", "0000320193-25-000079", "2025-10-31", "2025-09-27", "aapl-20250927.htm"),
)


class FakeResponse(io.BytesIO):
    def __init__(self, body, gzipped):
        super().__init__(gzip.compress(body) if gzipped else body)
        self.headers = {"Content-Encoding": "gzip"} if gzipped else {}


class FakeSEC:
    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __call__(self, req, timeout):
        self.requests.append(req)
        assert timeout == 30
        body = self.routes[req.full_url]
        if isinstance(body, Exception):
            raise body
        return FakeResponse(body, gzipped=req.full_url != Q2_URL)  # mezcla gzip y sin comprimir

    def urls(self):
        return [r.full_url for r in self.requests]


class SecTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name) / "sec"
        self.sec = FakeSEC({
            TICKERS_URL: json.dumps({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}).encode(),
            SUBMISSIONS_URL: SUBMISSIONS,
            Q3_URL: doc_html("78,678"),
            Q2_URL: doc_html("70,000"),
            K_URL: doc_html("1", item="7", end="7A"),
        })
        for p in (patch("sec_mdna.CACHE_DIR", self.cache),
                  patch("sec_mdna.time.sleep"),
                  patch("sec_mdna.urllib.request.urlopen", self.sec)):
            p.start()
            self.addCleanup(p.stop)


class FilingsTest(SecTestCase):
    def test_lists_form_newest_first_without_amendments(self):
        found = sec_mdna.filings("aapl", "10-Q")
        self.assertEqual([f.accession for f in found], ["0000320193-26-000020", "0000320193-26-000013"])
        self.assertEqual(found[0], sec_mdna.Filing(
            symbol="AAPL", cik="0000320193", form="10-Q", accession="0000320193-26-000020",
            filing_date="2026-07-31", report_date="2026-06-27", url=Q3_URL))
        self.assertEqual([f.url for f in sec_mdna.filings("AAPL", "10-K")], [K_URL])

    def test_invalid_form(self):
        with self.assertRaises(ValueError):
            sec_mdna.filings("AAPL", "8-K")

    def test_every_request_has_user_agent_and_gzip(self):
        sec_mdna.fetch_mdna("AAPL")
        for req in self.sec.requests:
            self.assertEqual(req.get_header("User-agent"), "Marco Corpa marcocorpacriado@gmail.com")
            self.assertEqual(req.get_header("Accept-encoding"), "gzip")

    def test_cik_map_is_cached(self):
        sec_mdna.filings("AAPL", "10-Q")
        sec_mdna.filings("AAPL", "10-Q")
        self.assertEqual(self.sec.urls().count(TICKERS_URL), 1)

    def test_missing_ticker_refreshes_map_once(self):
        sec_mdna.filings("AAPL", "10-Q")  # mapa en caché sin NEWCO
        self.sec.routes[TICKERS_URL] = json.dumps({
            "0": {"cik_str": 320193, "ticker": "AAPL"}, "1": {"cik_str": 999, "ticker": "NEWCO"}}).encode()
        self.sec.routes["https://data.sec.gov/submissions/CIK0000000999.json"] = submissions()
        self.assertEqual(sec_mdna.filings("NEWCO", "10-Q"), [])
        with self.assertRaisesRegex(ValueError, "ZZZZ"):
            sec_mdna.filings("ZZZZ", "10-Q")
        self.assertEqual(self.sec.urls().count(TICKERS_URL), 3)  # inicial + NEWCO + ZZZZ, una por ticker ausente

    def test_http_403_hints_user_agent(self):
        self.sec.routes[SUBMISSIONS_URL] = urllib.error.HTTPError(SUBMISSIONS_URL, 403, "Forbidden", {}, io.BytesIO(b""))
        with self.assertRaisesRegex(RuntimeError, "403.*User-Agent"):
            sec_mdna.filings("AAPL", "10-Q")


class FetchMdnaTest(SecTestCase):
    def test_current_and_previous(self):
        current, previous = sec_mdna.fetch_mdna("AAPL", "10-Q")
        self.assertEqual(current.filing.report_date, "2026-06-27")
        self.assertEqual(previous.filing.report_date, "2026-03-28")
        self.assertTrue(current.text.startswith("Item 2. Management’s Discussion"))
        self.assertIn("Products | $78,678", current.text)
        self.assertIn("Products | $70,000", previous.text)
        self.assertNotIn("Unregistered", current.text)
        self.assertEqual(current.sha256, hashlib.sha256(current.text.encode()).hexdigest())

    def test_second_call_uses_disk_cache(self):
        first = sec_mdna.fetch_mdna("AAPL", "10-Q")
        n = len(self.sec.requests)
        second = sec_mdna.fetch_mdna("AAPL", "10-Q")
        self.assertEqual(self.sec.urls()[n:], [SUBMISSIONS_URL])
        self.assertEqual(first, second)
        self.assertTrue((self.cache / "AAPL_10-Q_2026-06-27.txt").exists())

    def test_single_filing_has_no_previous(self):
        current, previous = sec_mdna.fetch_mdna("AAPL", "10-K")
        self.assertEqual(current.filing.url, K_URL)
        self.assertTrue(current.text.startswith("Item 7. Management’s Discussion"))
        self.assertIsNone(previous)

    def test_unextractable_previous_is_none(self):
        self.sec.routes[Q2_URL] = b"<p>MD&A incorporated by reference to Exhibit 13.</p>"
        current, previous = sec_mdna.fetch_mdna("AAPL", "10-Q")
        self.assertIsNotNone(current)
        self.assertIsNone(previous)

    def test_unextractable_current_raises_with_url(self):
        self.sec.routes[Q3_URL] = b"<p>nada</p>"
        with self.assertRaisesRegex(ValueError, "aapl-20260627.htm"):
            sec_mdna.fetch_mdna("AAPL", "10-Q")

    def test_no_filings_of_form(self):
        self.sec.routes[SUBMISSIONS_URL] = submissions()
        with self.assertRaisesRegex(ValueError, "10-Q"):
            sec_mdna.fetch_mdna("AAPL", "10-Q")


if __name__ == "__main__":
    unittest.main()
