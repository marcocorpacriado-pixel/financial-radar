import contextlib
import io
import os
import sys
import tempfile
import unittest
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import analyst
import news
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
            last_period="FY2026 Q4", fcf_yield=0.021, net_debt_to_ebitda=0.52, debt_to_equity=0.29,
            interest_coverage=50.88, roic=0.2056, roe=0.3321, current_ratio=1.23, pe_vs_hist=0.12, pe_vs_peers=0.08,
            ev_ebitda_vs_hist=0.05, margin_vs_peers=0.061, volume_divergence=-0.12,
            asset_turnover=0.4376, dso=88.96, dio=4.79, dpo=145.54, cash_conversion_cycle=-51.79,
            cost_of_equity=0.1077, roe_spread=0.2244, cost_of_debt=0.0237, tax_rate=0.194, cost_of_debt_after_tax=0.0191)
EFFICIENCY = ("Eficiencia: ROE 33.2% (spread +22.4 pp vs k=10.8%, crea valor) · ROIC 20.6% · Rot. 0.44x · "
              "CCC -52d (DSO 89d | DIO 5d | DPO 146d)")
SOLVENCY = "Solvencia: Deuda Neta/EBITDA 0.5x · D/E 0.29 · rd 2.4% (neto 1.9%, t=19.4%) · Cobertura 50.9x · Liq. 1.23"


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
        cfg = self.load('[[positions]]\nticker = "MSFT"\n')
        self.assertEqual((cfg.summary_every_days, cfg.analysis_model, cfg.summary_model),
                         (14, "claude-sonnet-5-5", "claude-haiku-4-5"))
        self.assertEqual(cfg.positions, [radar.Position("MSFT", "MSFT", "MSFT", (), True)])

    def test_invalid(self):
        for text in ('[[positions]]\nticker = "MSFT"\nsec_enable = false\n',            # errata
                     '[[positions]]\npeers = ["A"]\n',                                   # sin ticker
                     '[[positions]]\nticker = "A"\n[[positions]]\nticker = "A"\n',       # duplicado
                     '[[positions]]\nticker = "A"\npeers = "B"\n',                       # tipo
                     '[[positions]]\nticker = "A"\nsec_enabled = "no"\n',                # tipo
                     '[[positions]]\nticker = "A"\ndistorted_metrics = 1\n',            # tipo
                     '[[positions]]\nticker = "A"\ntax_exempt = "yes"\n',               # tipo
                     'summary_every_days = 0\n[[positions]]\nticker = "A"\n',
                     'summary_every_days = 12\n',                                        # sin posiciones
                     'otra = 1\n[[positions]]\nticker = "A"\n',
                     'summary_model = 5\n[[positions]]\nticker = "A"\n',
                     'analysis_model = ""\n[[positions]]\nticker = "A"\n'):
            with self.assertRaises(ValueError, msg=text):
                self.load(text)

    def test_real_portfolio(self):
        cfg = radar.load_portfolio(radar.ROOT / "portfolio.toml")
        self.assertEqual((cfg.summary_every_days, cfg.analysis_model, cfg.summary_model),
                         (14, "claude-sonnet-5-5", "claude-haiku-4-5"))
        self.assertEqual({p.ticker: (p.peers, p.sec_enabled) for p in cfg.positions}, {
            "MSFT": (("GOOGL", "AMZN", "AAPL", "ORCL"), True),
            "AMZN": (("MSFT", "WMT", "GOOGL"), True),
            "MSTR": (("COIN", "PLTR", "MARA"), True),
            "ESEA": (("DAC", "GSL", "ZIM"), False),
            "BABA": (("JD", "PDD", "BIDU", "TCEHY"), False),
        })
        self.assertEqual({p.ticker for p in cfg.positions if p.distorted_metrics}, {"MSTR"})
        self.assertEqual({p.ticker for p in cfg.positions if p.tax_exempt}, {"ESEA"})

    def test_flags(self):
        cfg = self.load('[[positions]]\nticker = "X"\ndistorted_metrics = true\ntax_exempt = true\nsec_enabled = false\n')
        self.assertEqual(cfg.positions, [radar.Position("X", "X", "X", (), False, True, True)])


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
            "Val: P/E 34.1 (hist +12%, pares +8%) · EV/EBITDA 22.0 (hist +5%)",
            "Op: margen 45.2% (pares +6.1 pp) · ingresos +15% YoY (FY2026 Q4) · FCF yield 2.1%",
            EFFICIENCY,
            SOLVENCY,
        ])

    def test_missing_values(self):
        self.assertEqual(radar.format_quant(report(), "MSTR"), [
            "MSTR n/d · volumen n/d vs media 30 sesiones",
            "Val: P/E n/d (hist n/d, pares n/d) · EV/EBITDA n/d (hist n/d)",
            "Op: margen n/d (pares n/d) · ingresos n/d · FCF yield n/d",
            "Eficiencia: ROE n/d (spread n/d vs k=n/d) · ROIC n/d · Rot. n/d · CCC n/d (DSO n/d | DIO n/d | DPO n/d)",
            "Solvencia: Deuda Neta/EBITDA n/d · D/E n/d · rd n/d (neto n/d, t=n/d) · Cobertura n/d · Liq. n/d",
        ])

    def test_net_cash_negative_coverage_and_fy(self):
        lines = radar.format_quant(report(net_cash=True, interest_coverage=-262.854, roic=-0.134,
                                          revenue_growth_yoy=-0.05, last_period="FY2025"), "X")
        self.assertIn("ingresos -5% YoY (FY2025)", lines[2])
        self.assertIn("ROIC -13.4%", lines[3])
        self.assertTrue(lines[4].startswith("Solvencia: Deuda Neta/EBITDA caja neta · D/E n/d · rd n/d (neto n/d, t=n/d) · Cobertura -262.9x"))

    def test_value_destruction_and_no_inventory(self):
        lines = radar.format_quant(report(roe=0.06, cost_of_equity=0.105, roe_spread=-0.045,
                                          dso=90.66, dio=0.0, dpo=79.89, cash_conversion_cycle=10.77), "X")
        self.assertTrue(lines[3].startswith("Eficiencia: ROE 6.0% (spread -4.5 pp vs k=10.5%, destruye valor)"))
        self.assertTrue(lines[3].endswith("CCC 11d (DSO 91d | DIO 0d | DPO 80d)"))

    def test_unreported_dso(self):
        # BABA real: FMP da DSO 0 y un CCC de -233d; quant deja el CCC en None.
        lines = radar.format_quant(report(dso=0.0, dio=0.0, dpo=233.26), "BABA")
        self.assertTrue(lines[3].endswith("CCC n/d (DSO no reportado | DIO 0d | DPO 233d)"))

    def test_distorted_operating_margin(self):
        # MSTR real: margen -16.77 (-1677 %) por el mark-to-market del bitcoin.
        lines = radar.format_quant(report(operating_margin_ttm=-16.767, margin_vs_peers=-16.877, revenue_growth_yoy=0.07,
                                          last_period="FY2026 Q2", fcf_yield=-0.411), "MSTR")
        self.assertEqual(lines[2], "Op: ⚠️ margen -1676.7%: distorsionado (|margen| > 500 %); sin comparación con pares · "
                                   "ingresos +7% YoY (FY2026 Q2) · FCF yield -41.1%")
        self.assertIn("pares +500.0 pp", radar.format_quant(report(operating_margin_ttm=5.0, margin_vs_peers=5.0), "X")[2])

    def test_distorted_metrics_flag(self):
        # Con el flag de portfolio.toml se marca aunque el margen no pase del 500 %.
        lines = radar.format_quant(report(operating_margin_ttm=0.12, margin_vs_peers=-0.05, roe=-0.608,
                                          cost_of_equity=0.2328, roe_spread=-0.8408), "MSTR", distorted_metrics=True)
        self.assertTrue(lines[2].startswith("Op: ⚠️ margen 12.0%: no representativo por tesorería en activos digitales; "
                                            "sin comparación con pares · "))
        self.assertTrue(lines[3].startswith("Eficiencia: ROE -60.8% (spread -84.1 pp vs k=23.3% ⚠️ no representativo "
                                            "por tesorería en activos digitales) · "))
        self.assertNotIn("destruye valor", lines[3])
        self.assertNotIn("⚠️", "".join(radar.format_quant(report(operating_margin_ttm=0.12, roe_spread=-0.1), "X")))

    def test_summary_block_html(self):
        lines = radar.format_quant(report(**FULL), "MSFT") + ["Filing: 10-K 2026-07-29 · Riesgo/Cat.: Costes <altos> & deuda"]
        self.assertEqual(radar._html_block("MSFT", lines), [
            "🔹 <b>MSFT</b> 512.30 · volumen -12% vs media 30 sesiones",
            "• <b>Val:</b> P/E 34.1 (hist +12%, pares +8%) · EV/EBITDA 22.0 (hist +5%)",
            "• <b>Op:</b> margen 45.2% (pares +6.1 pp) · ingresos +15% YoY (FY2026 Q4) · FCF yield 2.1%",
            "• <b>Eficiencia:</b> " + EFFICIENCY.removeprefix("Eficiencia: "),
            "• <b>Solvencia:</b> " + SOLVENCY.removeprefix("Solvencia: "),
            "• <b>Filing:</b> 10-K 2026-07-29 · Riesgo/Cat.: Costes &lt;altos&gt; &amp; deuda",
        ])


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
        self.metrics_seen, self.models_seen, self.tax_exempt_seen = [], [], {}
        self.news = {}              # ticker -> lista de Headline o excepción
        self.digests = {}           # resultado de summarize_news o excepción
        self.news_calls = []
        self.send = Mock()
        self.sec_filings = Mock(side_effect=lambda t, form: self.filings.get((t, form), []))
        for target, fake in (("quant.analyze", self.fake_quant),
                             ("sec_mdna.filings", self.sec_filings),
                             ("sec_mdna.fetch_mdna", self.fake_fetch),
                             ("analyst.summarize_mdna", self.fake_summarize),
                             ("analyst.summarize_news", self.fake_summarize_news),
                             ("news.fetch_news", self.fake_fetch_news),
                             ("notify.send", self.send)):
            p = patch(target, fake)
            p.start()
            self.addCleanup(p.stop)

    def fake_quant(self, symbol, peers, tax_exempt=False):
        self.tax_exempt_seen[symbol] = tax_exempt
        r = self.reports[symbol]
        if isinstance(r, Exception):
            raise r
        return r

    def fake_fetch(self, ticker, form):
        f = self.filings[(ticker, form)][0]
        return sec_mdna.MDNAContext(f, "texto actual", "h1"), sec_mdna.MDNAContext(filing("Q1"), "texto anterior", "h0")

    def fake_summarize(self, current, previous, metrics, model):
        self.metrics_seen.append(metrics)
        self.models_seen.append(model)
        if isinstance(self.analysis, Exception):
            raise self.analysis
        return self.analysis

    def fake_fetch_news(self, ticker, limit):
        self.assertEqual(limit, 10)
        r = self.news.get(ticker, [])
        if isinstance(r, Exception):
            raise r
        return r

    def fake_summarize_news(self, headlines, model):
        self.news_calls.append((headlines, model))
        if isinstance(self.digests, Exception):
            raise self.digests
        return self.digests

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
        self.assertIn(SOLVENCY, self.metrics_seen[0])
        self.assertIn(EFFICIENCY, self.metrics_seen[0])
        self.assertNotIn("<b>", self.metrics_seen[0])  # Claude recibe las métricas en texto plano
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
        self.assertTrue(self.send.call_args.kwargs["html"])
        self.assertTrue(text.startswith("📊 <b>Radar de cartera</b> · 2026-10-03 (cada 12 días)\n\n🔹 <b>MSFT</b> 512.30"))
        self.assertIn("\n• <b>Filing:</b> 10-Q 2026-07-29 · Riesgo/Cat.: Lanzamiento de Copilot para empresas.", text)
        self.assertIn("\n\n🔹 <b>ESEA</b> 512.30", text)
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
        self.assertIn("🔹 <b>MSFT</b> 512.30", text)
        self.assertIn("🔹 <b>ESEA</b>: sin datos FMP (FMP ratios-ttm: HTTP 402)", text)

    def test_position_flags_reach_quant_and_format(self):
        positions = (replace(MSFT, distorted_metrics=True), replace(ESEA, tax_exempt=True))
        self.run_radar(self.known_state(q="Q2"), positions=positions, force_summary=True)
        self.assertEqual(self.tax_exempt_seen, {"MSFT": False, "ESEA": True})
        text = self.send.call_args.args[0]
        self.assertEqual(text.count("no representativo por tesorería en activos digitales"), 2)  # sólo MSFT: Op y Eficiencia

    def test_catalyst_fallback_text(self):
        self.analysis = analysis(catalyst=None)
        self.run_radar({"tickers": {}, "sent": {}})
        self.assertIn("Riesgo/Cat.: sin catalizadores verificados", self.send.call_args.args[0])


def headline(title, source="CNBC", day=25):
    return news.Headline(title, source, datetime(2026, 9, day, 18, 0, tzinfo=timezone.utc), "https://g/" + title)


class NewsInSummaryTest(RunTestCase):
    def setUp(self):
        super().setUp()
        self.news = {"MSFT": [headline("Why It's Time to Load Up on Microsoft Stock", "Yahoo Finance"),
                              headline("Microsoft gives Copilot a much-needed overhaul, and the stock deservedly soars")],
                     "ESEA": [headline("Euroseas Stock Price Forecast. Should You Buy ESEA?", "StockInvest.us")]}
        self.digests = {"MSFT": analyst.NewsDigest("Microsoft renueva Copilot y la acción sube.", (1,))}

    def summary(self, **kwargs):
        code = self.run_radar(self.known_state(q="Q2"), positions=(MSFT, ESEA), force_summary=True, **kwargs)
        return code, self.send.call_args.args[0]

    def test_news_for_all_positions_with_haiku_picks(self):
        code, text = self.summary()
        self.assertEqual(code, 0)
        self.assertIn("\n💡 <b>Noticias:</b> Microsoft renueva Copilot y la acción sube.\n"
                      "   ◦ CNBC, 25-09: Microsoft gives Copilot a much-needed overhaul, and the stock deservedly soars", text)
        self.assertNotIn("Load Up", text)  # relleno descartado por Haiku
        self.assertIn("🔹 <b>ESEA</b> 512.30", text)
        self.assertTrue(text.endswith("\n💡 <b>Noticias:</b> sin novedades materiales"))  # cierra el bloque de ESEA
        headlines, model = self.news_calls[0]
        self.assertEqual(model, "claude-haiku-4-5")
        self.assertEqual(headlines["ESEA"], ["Euroseas Stock Price Forecast. Should You Buy ESEA? (StockInvest.us, 25-09)"])

    def test_models_come_from_config(self):
        self.summary(summary_model="claude-x-news")
        self.assertEqual(self.news_calls[0][1], "claude-x-news")
        self.run_radar(self.known_state(), analysis_model="claude-x-filings")
        self.assertEqual(self.models_seen, ["claude-x-filings"])

    def test_haiku_failure_shows_first_headlines(self):
        self.digests = RuntimeError("Anthropic HTTP 529")
        code, text = self.summary()
        self.assertEqual(code, 0)
        self.assertIn("\n💡 <b>Noticias</b> (sin síntesis):\n   ◦ Yahoo Finance, 25-09: Why It's Time to Load Up on Microsoft Stock", text)

    def test_feed_failure_isolated(self):
        self.news["ESEA"] = RuntimeError("Google News ESEA: HTTP 503")
        code, text = self.summary()
        self.assertEqual(code, 0)
        self.assertIn("🔹 <b>ESEA</b> 512.30", text)
        self.assertIn("\n💡 <b>Noticias:</b> no disponibles", text)
        self.assertNotIn("ESEA", self.news_calls[0][0])

    def test_long_headline_cut(self):
        self.news["MSFT"] = [headline("x" * 300)]
        self.digests = {"MSFT": analyst.NewsDigest("Algo.", (0,))}
        _, text = self.summary()
        line = next(l for l in text.split("\n") if l.startswith("   ◦ CNBC"))
        self.assertLessEqual(len(line), len("   ◦ CNBC, 25-09: ") + 120)

    def test_dynamic_text_is_html_escaped(self):
        self.news["MSFT"] = [headline("AT&T <deal> with Microsoft")]
        self.digests = {"MSFT": analyst.NewsDigest("Acuerdo <AT&T>.", (0,))}
        _, text = self.summary()
        self.assertIn("💡 <b>Noticias:</b> Acuerdo &lt;AT&amp;T&gt;.\n   ◦ CNBC, 25-09: AT&amp;T &lt;deal&gt; with Microsoft", text)


class MainTest(unittest.TestCase):
    def test_flags_reach_run(self):
        with patch("radar.run", return_value=0) as run, patch("radar.load_state", return_value={"tickers": {}, "sent": {}}):
            self.assertEqual(radar.main(["--dry-run", "--force-summary"]), 0)
            self.assertEqual(run.call_args.kwargs, {"force_summary": True, "dry_run": True,
                                                    "analysis_model": "claude-sonnet-5-5", "summary_model": "claude-haiku-4-5"})
            radar.main([])
            self.assertEqual((run.call_args.kwargs["force_summary"], run.call_args.kwargs["dry_run"]), (False, False))
            self.assertEqual(len(run.call_args.args[0]), 5)
            self.assertEqual(run.call_args.args[1], 14)

    def test_pythonw_without_console_logs_to_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        cfg = radar.Config(14, "a", "s", [MSFT])

        def fake_run(*args, **kwargs):
            print("aviso de prueba", file=sys.stderr)
            return 0

        with patch("radar.ROOT", Path(tmp.name)), patch("radar.load_portfolio", return_value=cfg), \
                patch("radar.load_state", return_value={"tickers": {}, "sent": {}}), patch("radar.run", fake_run), \
                patch("radar.notify.load_env"), patch("sys.stdout", None), patch("sys.stderr", None):
            self.assertEqual(radar.main(["--check"]), 0)
            sys.stderr.close()
        log = (Path(tmp.name) / "radar.log").read_text(encoding="utf-8")
        self.assertIn("radar.py --check", log)
        self.assertIn("aviso de prueba", log)


if __name__ == "__main__":
    unittest.main()
