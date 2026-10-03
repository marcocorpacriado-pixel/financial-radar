import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

import analyst
from analyst import Analysis, Finding

KEY = "sk-ant-SECRET"
HEADING = "Item 2. Management’s Discussion and Analysis of Financial Condition and Results of Operations"
FLS = ("This Item contains forward-looking statements, within the meaning of the Private Securities "
       "Litigation Reform Act of 1995, that involve risks and uncertainties.")
GUIDANCE = "The Company now expects gross margin to be between 47% and 48% for the fourth quarter."
COSTS = "Tariffs and higher memory prices increased product costs during the quarter."
CURRENT = "\n".join([HEADING, FLS, GUIDANCE, COSTS, "Products | $78,678 | $66,613"])
PREVIOUS = "\n".join([HEADING, "The Company expects gross margin to be between 46% and 47% for the third quarter."])


# --- Poda -----------------------------------------------------------------

class PruneTest(unittest.TestCase):
    def test_removes_page_noise_and_keeps_content(self):
        noise = ["PART I", "Item 2", "45", "•"]
        footer = "Apple Inc. | Q3 2026 Form 10-Q | {}"
        lines = [HEADING]
        for page in range(5):
            lines += ["Párrafo %d con contenido real." % page, *noise, footer.format(13 + page), "Products | $1 | $2"]
        pruned = analyst.prune_mdna("\n".join(lines)).split("\n")
        self.assertEqual(pruned[0], HEADING)
        self.assertEqual([l for l in pruned if l.startswith("Párrafo")], ["Párrafo %d con contenido real." % p for p in range(5)])
        self.assertEqual(pruned.count("Products | $1 | $2"), 5)  # filas con $ nunca se tocan
        for gone in ("PART I", "Item 2", "45", "•"):
            self.assertNotIn(gone, pruned)
        self.assertFalse([l for l in pruned if l.startswith("Apple Inc.")])

    def test_repeated_table_headers_and_subheadings_are_kept(self):
        # Medido en MSFT/NVDA: se repiten mucho, pero dan sentido a las cifras.
        content = ["Three Months Ended | Six Months Ended", "2026 | 2025 | 2026 | 2025", "(In millions)",
                   "Intelligent Cloud", "Year Ended", "Fiscal Year 2026 Compared with Fiscal Year 2025"]
        text = "\n".join([HEADING] + content * 5)
        self.assertEqual(analyst.prune_mdna(text), text)

    def test_rare_repetition_is_kept(self):
        text = "\n".join([HEADING, "Intelligent Cloud", "texto", "Intelligent Cloud", "más texto"])
        self.assertEqual(analyst.prune_mdna(text), text)

    def test_forward_looking_boilerplate(self):
        keep = "Management believes its forward-looking statements on demand are prudent."
        text = "\n".join([HEADING, "Forward-Looking Statements", FLS, keep, GUIDANCE])
        self.assertEqual(analyst.prune_mdna(text), "\n".join([HEADING, keep, GUIDANCE]))

    def test_first_line_always_kept(self):
        self.assertEqual(analyst.prune_mdna("•\ntexto"), "•\ntexto")


# --- Validador de citas ---------------------------------------------------

class QuoteTest(unittest.TestCase):
    def test_exact_quote(self):
        self.assertTrue(analyst.quote_found(GUIDANCE, CURRENT))

    def test_typography_spacing_and_case_tolerated(self):
        text = "Management’s outlook — revised:  the Company now  expects\nhigher “services” growth."
        self.assertTrue(analyst.quote_found("management's outlook - revised: the company now expects higher \"services\" growth", text))
        self.assertTrue(analyst.quote_found('..."The Company now expects gross margin to be between 47%"...', CURRENT))

    def test_invented_or_paraphrased_rejected(self):
        self.assertFalse(analyst.quote_found("The Company expects gross margin to expand significantly next year.", CURRENT))
        self.assertFalse(analyst.quote_found("The Company now expects gross margins between 47% and 48%", CURRENT))

    def test_short_quote_rejected(self):
        self.assertFalse(analyst.quote_found("gross margin", CURRENT))

    def test_missing_text_rejected(self):
        self.assertFalse(analyst.quote_found(GUIDANCE, None))


# --- API simulada ---------------------------------------------------------

def finding(quote, source="current", summary="Resumen"):
    return {"summary": summary, "quote": quote, "source": source}


def api_body(data, stop="end_turn", usage=(30000, 1500)):
    return {
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": json.dumps(data)}],
        "stop_reason": stop,
        "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
    }


GOOD = {
    "guidance_changes": [finding(GUIDANCE, summary="Eleva la previsión de margen bruto a 47-48 %."),
                         finding("between 46% and 47% for the third quarter", "previous", "Previsión anterior 46-47 %.")],
    "operational_pressures": [finding(COSTS, summary="Aranceles y memoria encarecen el coste.")],
    "key_risks_and_catalysts": [finding("The Company faces a new antitrust lawsuit in Europe.", summary="Inventada.")],
}


def http_error(code, headers=None, body=b'{"type":"error","error":{"type":"x","message":"msg"}}'):
    return urllib.error.HTTPError(analyst.API_URL, code, "err", headers or {}, io.BytesIO(body))


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.responses = [api_body(GOOD)]
        self.requests = []
        for p in (patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}),
                  patch("analyst.urllib.request.urlopen", self.fake_urlopen),
                  patch("analyst.time.sleep")):
            self.sleep = p.start()
            self.addCleanup(p.stop)

    def fake_urlopen(self, req, timeout):
        self.requests.append(req)
        self.assertEqual(timeout, 300)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return io.BytesIO(json.dumps(r).encode())


class RequestTest(ApiTestCase):
    def test_headers_and_body(self):
        analyst.summarize_mdna(CURRENT, PREVIOUS)
        req = self.requests[0]
        self.assertEqual(req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.get_header("X-api-key"), KEY)
        self.assertEqual(req.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(req.get_header("Anthropic-beta"), "server-side-fallback-2026-07-01")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        body = json.loads(req.data)
        self.assertEqual((body["model"], body["max_tokens"], body["fallbacks"]), ("claude-sonnet-5-5", 8000, "default"))
        self.assertNotIn("temperature", body)
        self.assertEqual(body["output_config"]["effort"], "medium")
        fmt = body["output_config"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertEqual(fmt["schema"]["required"], list(analyst.CATEGORIES))
        content = body["messages"][0]["content"]
        self.assertIn("<current_mdna>", content)
        self.assertIn("<previous_mdna>", content)
        self.assertIn(GUIDANCE, content)
        self.assertNotIn("Litigation Reform Act", content)  # se envía el texto podado

    def test_metrics_block_only_when_given(self):
        analyst.summarize_mdna(CURRENT, PREVIOUS)
        self.assertNotIn("<financial_metrics>", json.loads(self.requests[0].data)["messages"][0]["content"])
        metrics = "MSTR 160.01\n  Deuda neta/EBITDA n/d · D/E 0.15 · Cobertura int. -262.9x · ROIC -13.4%"
        analyst.summarize_mdna(CURRENT, PREVIOUS, metrics)
        content = json.loads(self.requests[1].data)["messages"][0]["content"]
        self.assertIn(f"<financial_metrics>\n{metrics}\n</financial_metrics>", content)

    def test_prompt_rules(self):
        prompt = analyst.SYSTEM_PROMPT
        for rule in ("year-over-year", "sequential", "interanual", "secuencial", "<financial_metrics>",
                     "interest coverage", "debt maturities", "quotes must always come from the MD&A"):
            self.assertIn(rule, prompt)

    def test_without_previous(self):
        analyst.summarize_mdna(CURRENT, None)
        self.assertNotIn("<previous_mdna>", json.loads(self.requests[0].data)["messages"][0]["content"])

    def test_input_budget_raises_before_calling(self):
        with patch("analyst.MAX_INPUT_CHARS", 100):
            with self.assertRaisesRegex(ValueError, "MAX_INPUT_CHARS"):
                analyst.summarize_mdna(CURRENT, PREVIOUS)
        self.assertEqual(self.requests, [])

    def test_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_API_KEY"):
                analyst.summarize_mdna(CURRENT, None)
        self.assertEqual(self.requests, [])


class ErrorsTest(ApiTestCase):
    def test_401_no_retry_no_key(self):
        self.responses = [http_error(401)]
        with self.assertRaises(RuntimeError) as ctx:
            analyst.summarize_mdna(CURRENT, None)
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertEqual(len(self.requests), 1)

    def test_429_waits_retry_after_then_succeeds(self):
        self.responses = [http_error(429, {"retry-after": "3"}), api_body(GOOD)]
        analyst.summarize_mdna(CURRENT, PREVIOUS)
        self.assertEqual(len(self.requests), 2)
        self.sleep.assert_called_once_with(3.0)

    def test_retry_after_capped_and_defaulted(self):
        self.responses = [http_error(429, {"retry-after": "999"}), http_error(500), api_body(GOOD)]
        analyst.summarize_mdna(CURRENT, PREVIOUS)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [60.0, 5.0])

    def test_529_exhausts_retries(self):
        self.responses = [http_error(529) for _ in range(3)] + [api_body(GOOD)]
        with self.assertRaisesRegex(RuntimeError, "529"):
            analyst.summarize_mdna(CURRENT, None)
        self.assertEqual(len(self.requests), 3)

    def test_400_reports_api_message(self):
        self.responses = [http_error(400, body=b'{"error": {"message": "max_tokens: too large"}}')]
        with self.assertRaisesRegex(RuntimeError, "400.*too large"):
            analyst.summarize_mdna(CURRENT, None)

    def test_refusal_and_max_tokens(self):
        self.responses = [{**api_body(GOOD, stop="refusal"), "stop_details": {"category": "cyber"}}]
        with self.assertRaisesRegex(RuntimeError, "cyber"):
            analyst.summarize_mdna(CURRENT, None)
        self.responses = [api_body(GOOD, stop="max_tokens")]
        with self.assertRaisesRegex(RuntimeError, "max_tokens"):
            analyst.summarize_mdna(CURRENT, None)

    def test_invalid_json_or_structure(self):
        bad_json = api_body(GOOD)
        bad_json["content"][1]["text"] = "{no es json"
        for body in (bad_json,
                     api_body({"guidance_changes": [], "operational_pressures": []}),
                     api_body({**GOOD, "guidance_changes": [{"summary": "x", "quote": "y", "source": "otro"}]})):
            self.responses = [body]
            with self.assertRaises(ValueError):
                analyst.summarize_mdna(CURRENT, PREVIOUS)


class SummarizeTest(ApiTestCase):
    def test_only_verified_quotes_survive(self):
        a = analyst.summarize_mdna(CURRENT, PREVIOUS)
        self.assertEqual([f.summary for f in a.guidance_changes],
                         ["Eleva la previsión de margen bruto a 47-48 %.", "Previsión anterior 46-47 %."])
        self.assertEqual(len(a.operational_pressures), 1)
        self.assertEqual(a.key_risks_and_catalysts, ())
        self.assertEqual([f.summary for f in a.rejected], ["Inventada."])
        self.assertEqual((a.input_tokens, a.output_tokens), (30000, 1500))

    def test_previous_source_without_previous_is_rejected(self):
        a = analyst.summarize_mdna(CURRENT, None)
        self.assertIn("Previsión anterior 46-47 %.", [f.summary for f in a.rejected])

    def test_quote_only_in_unpruned_text_is_rejected(self):
        self.responses = [api_body({**GOOD, "guidance_changes": [finding(FLS)]})]
        a = analyst.summarize_mdna(CURRENT, PREVIOUS)
        self.assertEqual(a.guidance_changes, ())

    def test_max_three_per_category(self):
        many = [finding(GUIDANCE, summary=f"s{i}") for i in range(5)]
        self.responses = [api_body({**GOOD, "guidance_changes": many})]
        a = analyst.summarize_mdna(CURRENT, PREVIOUS)
        self.assertEqual([f.summary for f in a.guidance_changes], ["s0", "s1", "s2"])


# --- Render ---------------------------------------------------------------

def analysis(g=0, p=0, r=0, rejected=0):
    mk = lambda tag, n: tuple(Finding(f"{tag}{i}", "q", "current") for i in range(n))
    return Analysis(mk("g", g), mk("p", p), mk("r", r), mk("x", rejected), 0, 0)


class RenderTest(unittest.TestCase):
    def test_max_ten_lines_alternating(self):
        lines = analyst.render(analysis(3, 3, 3, rejected=2), "AAPL 10-Q 2026-06-27").split("\n")
        self.assertEqual(len(lines), 10)
        self.assertEqual(lines[0], "AAPL 10-Q 2026-06-27")
        self.assertEqual(lines[1:4], ["Guidance: g0", "Presión: p0", "Riesgo/Cat.: r0"])
        self.assertEqual(lines[-1], "(2 citas no verificadas descartadas)")

    def test_singular_and_uneven(self):
        lines = analyst.render(analysis(3, 0, 1, rejected=1), "H").split("\n")
        self.assertEqual(lines, ["H", "Guidance: g0", "Riesgo/Cat.: r0", "Guidance: g1", "Guidance: g2",
                                 "(1 cita no verificada descartada)"])

    def test_no_findings(self):
        self.assertEqual(analyst.render(analysis(), "H"), "H\nSin cambios materiales verificables en el MD&A.")

    def test_summary_is_one_line_and_cut(self):
        a = Analysis((Finding("línea uno\nlínea dos " + "x" * 300, "q", "current"),), (), (), (), 0, 0)
        line = analyst.render(a, "H").split("\n")[1]
        self.assertTrue(line.startswith("Guidance: línea uno línea dos"))
        self.assertLessEqual(len(line), len("Guidance: ") + 200)
        self.assertEqual(len(analyst.render(a, "H").split("\n")), 2)


if __name__ == "__main__":
    unittest.main()
