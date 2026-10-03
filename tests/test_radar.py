import contextlib
import io
import tempfile
import unittest
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import analyst
import quant
import radar
import sec_mdna

NOW = datetime(2026, 10, 3, 21, 30, tzinfo=timezone.utc)
MSFT = radar.Position("MSFT", "MSFT", "MSFT", ("GOOGL", "AMZN", "AAPL"), True)
ESEA = radar.Position("ESEA", "ESEA", "ESEA", ("DAC", "GSL", "ZIM"), False)


def report(symbol="MSFT", **values):
    metric_names = {f.name for f in fields(quant.Metrics)}
    metrics = {**dict.fromkeys(metric_names), "symbol": symbol, "net_cash": False}
    rep = {**dict.fromkeys(f.name for f in fields(quant.Report)), "peers": ()}
    for k, v in values.items():
        (metrics if k in metric_names else rep)[k] = v
    return quant.Report(**{**rep, "metrics": quant.Metrics(**metrics)})


FULL = dict(price=512.3, pe_ttm=34.1, ev_ebitda_ttm=22.0, operating_margin_ttm=0.452, revenue_growth_yoy=0.15,
            last_period="Q4 2026", fcf_yield=0.021, net_debt_to_ebitda=0.52, debt_to_equity=0.29,
            interest_coverage=50.88, roic=0.2056, roe=0.3321, current_ratio=1.23, pe_vs_hist=0.12, pe_vs_peers=0.08,
            ev_ebitda_vs_hist=0.05, margin_vs_peers=0.061, volume_divergence=-0.12)


def filing(accession, form="10-Q", filed="2026-07-29", period="2026-06-30"):
    return sec_mdna.Filing("MSFT", "0000789019", form, accession, filed, period, f"https://sec.example/{accession}.htm")


def analysis(catalyst="Lanzamiento de Copilot para empresas."):
    finding = lambda s: analyst.Finding(s, "quote", "current")
    return analyst.Analysis((finding("Eleva la previsión de ingresos de Azure."),), (),
                            (finding(catalyst),) if catalyst else (), (), 1000, 100)


# --- Cartera --------------------------------------------------------------

class PortfolioTest(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.toml"
            path.write_text(text, encoding="utf-8")
            return radar.load_portfolio(path)

    def test_defaults(self):
        days, positions = self.load('[[positions]]\nticker = "MSFT"\n')
        self.assertEqual(days, 12)
        self.assertEqual(positions, [radar.Position("MSFT", "MSFT", "MSFT", (), True)])

    def test_invalid(self):
        for text in ('[[positions]]\nticker = "MSFT"\nsec_enable = false\n',            # errata
                     '[[positions]]\npeers = ["A"]\n',                                   # sin ticker
                     '[[positions]]\nticker = "A"\n[[positions]]\nticker = "A"\n',       # duplicado
                     '[[positions]]\nticker = "A"\npeers = "B"\n',                       # tipo
                     '[[positions]]\nticker = "A"\nsec_enabled = "no"\n',                # tipo
                     'summary_every_days = 0\n[[positions]]\nticker = "A"\n',
                     'summary_every_days = 12\n',                                        # sin posiciones
                     'otra = 1\n[[positions]]\nticker = "A"\n'):
            with self.assertRaises(ValueError, msg=text):
                self.load(text)

    def test_real_portfolio(self):
        days, positions = radar.load_portfolio(radar.ROOT / "portfolio.toml")
        self.assertEqual(days, 12)
        self.assertEqual({p.ticker: (p.peers, p.sec_enabled) for p in positions}, {
            "MSFT": (("GOOGL", "AMZN", "AAPL", "ORCL"), True),
            "AMZN": (("MSFT", "WMT", "GOOGL"), True),
            "MSTR": (("COIN", "PLTR", "MARA"), True),
            "ESEA": (("DAC", "GSL", "ZIM"), False),
            "BABA": (("JD", "PDD", "BIDU"), False),
        })


# --- Estado, periodicidad y formato ---------------------------------------

class StateTest(unittest.TestCase):
    def test_missing_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            state = radar.load_state(path)
            self.assertEqual(state, {"tickers": {}, "sent": {}})
            state["last_summary"] = NOW.isoformat()
            radar.save_state(state, path)
            self.assertEqual(radar.load_state(path), state)
            self.assertFalse(path.with_name("state.json.tmp").exists())

    def test_prune_sent(self):
        state = {"sent": {"viejo": (NOW - timedelta(days=181)).isoformat(), "nuevo": (NOW - timedelta(days=5)).isoformat()}}
        radar.prune_sent(state, NOW)
        self.assertEqual(list(state["sent"]), ["nuevo"])

    def test_summary_due(self):
        self.assertTrue(radar.summary_due({}, NOW, 12))
        self.assertFalse(radar.summary_due({"last_summary": (NOW - timedelta(days=11)).isoformat()}, NOW, 12))
        self.assertTrue(radar.summary_due({"last_summary": (NOW - timedelta(days=12)).isoformat()}, NOW, 12))


class FormatTest(unittest.TestCase):
    def test_full(self):
        self.assertEqual(radar.format_quant(report(**FULL), "MSFT"), [
            "MSFT 512.30 · volumen -12% vs media 30 sesiones",
            "P/E 34.1 (hist +12%, pares +8%) · EV/EBITDA 22.0 (hist +5%)",
            "Margen op. 45.2% (pares +6.1 pp) · Ingresos +15% YoY (Q4 2026) · FCF yield 2.1%",
            "Deuda neta/EBITDA 0.5x · D/E 0.29 · Cobertura int. 50.9x · ROIC 20.6% · ROE 33.2% · Liquidez 1.23",
        ])

    def test_missing_values(self):
        self.assertEqual(radar.format_quant(report(), "MSTR"), [
            "MSTR n/d · volumen n/d vs media 30 sesiones",
            "P/E n/d (hist n/d, pares n/d) · EV/EBITDA n/d (hist n/d)",
            "Margen op. n/d (pares n/d) · Ingresos n/d · FCF yield n/d",
            "Deuda neta/EBITDA n/d · D/E n/d · Cobertura int. n/d · ROIC n/d · ROE n/d · Liquidez n/d",
        ])

    def test_net_cash_negative_coverage_and_fy(self):
        lines = radar.format_quant(report(net_cash=True, interest_coverage=-262.854, roic=-0.134,
                                          revenue_growth_yoy=-0.05, last_period="FY 2025"), "X")
        self.assertIn("Ingresos -5% YoY (FY 2025)", lines[2])
        self.assertTrue(lines[3].startswith("Deuda neta/EBITDA caja neta · D/E n/d · Cobertura int. -262.9x · ROIC -13.4%"))


# --- Flujo completo con dependencias simuladas ----------------------------

class RunTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_path = Path(tmp.name) / "state.json"
        self.filings = {("MSFT", "10-K"): [filing("K1", "10-K", "2025-07-30", "2025-06-30")],
                        ("MSFT", "10-Q"): [filing("Q2")]}
        self.reports = {"MSFT": report("MSFT", **FULL), "ESEA": report("ESEA", **FULL)}
        self.analysis = analysis()
        self.metrics_seen = []
        self.send = Mock()
        self.sec_filings = Mock(side_effect=lambda t, form: self.filings.get((t, form), []))
        for target, fake in (("quant.analyze", self.fake_quant),
                             ("sec_mdna.filings", self.sec_filings),
                             ("sec_mdna.fetch_mdna", self.fake_fetch),
                             ("analyst.summarize_mdna", self.fake_summarize),
                             ("notify.send", self.send)):
            p = patch(target, fake)
            p.start()
            self.addCleanup(p.stop)

    def fake_quant(self, symbol, peers):
        r = self.reports[symbol]
        if isinstance(r, Exception):
            raise r
        return r

    def fake_fetch(self, ticker, form):
        f = self.filings[(ticker, form)][0]
        return sec_mdna.MDNAContext(f, "texto actual", "h1"), sec_mdna.MDNAContext(filing("Q1"), "texto anterior", "h0")

    def fake_summarize(self, current, previous, metrics):
        self.metrics_seen.append(metrics)
        if isinstance(self.analysis, Exception):
            raise self.analysis
        return self.analysis

    def run_radar(self, state, positions=(MSFT,), **kwargs):
        return radar.run(list(positions), 12, state, NOW, state_path=self.state_path, **kwargs)

    def known_state(self, q="Q1", last_summary_days_ago=1):
        return {"tickers": {"MSFT": {"accessions": {"10-K": "K1", "10-Q": q},
                                     "last_analysis": {"form": "10-Q", "accession": "Q1", "filing_date": "2026-04-29",
                                                       "url": "u", "lines": [], "catalyst": "Anterior."}}},
                "sent": {}, "last_summary": (NOW - timedelta(days=last_summary_days_ago)).isoformat()}

    def saved(self):
        return radar.load_state(self.state_path)


class AlertTest(RunTestCase):
    def test_new_accession_sends_loud_alert_with_metrics(self):
        self.assertEqual(self.run_radar(self.known_state()), 0)
        self.send.assert_called_once()
        text, silent = self.send.call_args.args[0], self.send.call_args.kwargs["silent"]
        self.assertFalse(silent)
        lines = text.split("\n")
        self.assertEqual(lines[0], "Nuevo 10-Q · MSFT · periodo 2026-06-30 (presentado 2026-07-29)")
        self.assertIn("Guidance: Eleva la previsión de ingresos de Azure.", lines)
        self.assertEqual(lines[-1], "https://sec.example/Q2.htm")
        self.assertIn("Cobertura int. 50.9x", self.metrics_seen[0])
        state = self.saved()
        self.assertEqual(state["tickers"]["MSFT"]["accessions"]["10-Q"], "Q2")
        self.assertEqual(state["tickers"]["MSFT"]["last_analysis"]["catalyst"], "Lanzamiento de Copilot para empresas.")
        self.assertEqual(state["last_check"], NOW.isoformat())

    def test_no_change_sends_nothing(self):
        self.assertEqual(self.run_radar(self.known_state(q="Q2")), 0)
        self.send.assert_not_called()

    def test_sec_disabled_never_touches_sec(self):
        self.run_radar({"tickers": {}, "sent": {}, "last_summary": NOW.isoformat()}, positions=(ESEA,))
        self.sec_filings.assert_not_called()

    def test_analysis_failure_sends_short_alert(self):
        self.analysis = RuntimeError("Anthropic HTTP 529: overloaded")
        self.assertEqual(self.run_radar(self.known_state()), 1)
        text = self.send.call_args.args[0]
        self.assertEqual(text, "Nuevo 10-Q · MSFT · periodo 2026-06-30 (presentado 2026-07-29)\n"
                               "Análisis no disponible: Anthropic HTTP 529: overloaded\nhttps://sec.example/Q2.htm")
        self.assertEqual(self.saved()["tickers"]["MSFT"]["accessions"]["10-Q"], "Q2")

    def test_send_failure_keeps_filing_pending(self):
        self.send.side_effect = RuntimeError("Telegram: Bad Gateway")
        self.assertEqual(self.run_radar(self.known_state()), 1)
        self.assertEqual(self.saved()["tickers"]["MSFT"]["accessions"]["10-Q"], "Q1")

    def test_duplicate_message_not_resent(self):
        state = self.known_state()
        self.run_radar(state)
        state["tickers"]["MSFT"]["accessions"]["10-Q"] = "Q1"  # p. ej. estado restaurado de una copia
        self.run_radar(state)
        self.send.assert_called_once()

    def test_dry_run_neither_sends_nor_writes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.run_radar(self.known_state(), dry_run=True), 0)
        self.send.assert_not_called()
        self.assertFalse(self.state_path.exists())
        self.assertIn("Nuevo 10-Q · MSFT", out.getvalue())


class SummaryTest(RunTestCase):
    def test_first_run_baselines_and_analyzes_silently(self):
        self.assertEqual(self.run_radar({"tickers": {}, "sent": {}}, positions=(MSFT, ESEA)), 0)
        self.send.assert_called_once()  # sólo el resumen, ninguna alerta
        text, silent = self.send.call_args.args[0], self.send.call_args.kwargs["silent"]
        self.assertTrue(silent)
        self.assertTrue(text.startswith("Radar de cartera · 2026-10-03 (cada 12 días)\n\nMSFT 512.30"))
        self.assertIn("\n  Último filing: 10-Q 2026-07-29 · Riesgo/Cat.: Lanzamiento de Copilot para empresas.", text)
        self.assertIn("\n\nESEA 512.30", text)
        state = self.saved()
        self.assertEqual(state["tickers"]["MSFT"]["accessions"], {"10-K": "K1", "10-Q": "Q2"})
        self.assertEqual(state["tickers"]["MSFT"]["last_analysis"]["accession"], "Q2")  # el más reciente
        self.assertEqual(state["last_summary"], NOW.isoformat())

    def test_not_due_no_summary_unless_forced(self):
        self.run_radar(self.known_state(q="Q2"))
        self.send.assert_not_called()
        self.run_radar(self.known_state(q="Q2"), force_summary=True)
        self.assertTrue(self.send.call_args.kwargs["silent"])
        self.assertIn("Riesgo/Cat.: Anterior.", self.send.call_args.args[0])

    def test_quant_failure_isolated(self):
        self.reports["ESEA"] = RuntimeError("FMP ratios-ttm: HTTP 402")
        state = self.known_state(q="Q2")
        self.assertEqual(self.run_radar(state, positions=(MSFT, ESEA), force_summary=True), 1)
        text = self.send.call_args.args[0]
        self.assertIn("MSFT 512.30", text)
        self.assertIn("ESEA: sin datos FMP (FMP ratios-ttm: HTTP 402)", text)

    def test_catalyst_fallback_text(self):
        self.analysis = analysis(catalyst=None)
        self.run_radar({"tickers": {}, "sent": {}})
        self.assertIn("Riesgo/Cat.: sin catalizadores verificados", self.send.call_args.args[0])


class MainTest(unittest.TestCase):
    def test_flags_reach_run(self):
        with patch("radar.run", return_value=0) as run, patch("radar.load_state", return_value={"tickers": {}, "sent": {}}):
            self.assertEqual(radar.main(["--dry-run", "--force-summary"]), 0)
            self.assertEqual(run.call_args.kwargs, {"force_summary": True, "dry_run": True})
            radar.main([])
            self.assertEqual(run.call_args.kwargs, {"force_summary": False, "dry_run": False})
            self.assertEqual(len(run.call_args.args[0]), 5)


if __name__ == "__main__":
    unittest.main()
