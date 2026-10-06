import io
import json
import math
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import quant

KEY = "SECRET-FMP-KEY"


class PureFunctionsTest(unittest.TestCase):
    def test_num(self):
        self.assertEqual(quant.num(3), 3.0)
        self.assertEqual(quant.num(-1.5), -1.5)
        for bad in (None, "12", True, math.nan, math.inf, [], {}):
            self.assertIsNone(quant.num(bad), bad)

    def test_positive(self):
        self.assertEqual(quant.positive(12.5), 12.5)
        for bad in (0, -3, None, "x"):
            self.assertIsNone(quant.positive(bad), bad)

    def test_median(self):
        self.assertEqual(quant.median([3, None, 1, 2]), 2)
        self.assertEqual(quant.median([4, 1, 3, 2]), 2.5)
        self.assertIsNone(quant.median([]))
        self.assertIsNone(quant.median([None, None]))
        self.assertIsNone(quant.median([1, 2], min_count=3))
        self.assertEqual(quant.median([1, 2, None, 3], min_count=3), 2)

    def test_relative(self):
        self.assertAlmostEqual(quant.relative(30, 24), 0.25)
        self.assertAlmostEqual(quant.relative(20, 25), -0.2)
        for args in ((None, 10), (10, None), (10, 0), (10, -5)):
            self.assertIsNone(quant.relative(*args), args)

    def test_yoy(self):
        self.assertAlmostEqual(quant.yoy(120, 100), 0.2)
        self.assertAlmostEqual(quant.yoy(80, 100), -0.2)
        self.assertIsNone(quant.yoy(120, 0))
        self.assertIsNone(quant.yoy(120, None))
        self.assertIsNone(quant.yoy(None, 100))

    def test_avg_volume_excludes_last_session(self):
        self.assertEqual(quant.avg_volume([999] * 5 + [100] * 30 + [5000]), 100)

    def test_avg_volume_needs_31_sessions(self):
        self.assertIsNone(quant.avg_volume([100] * 30))
        self.assertIsNone(quant.avg_volume([100] * 29 + [None] + [100, 100]))

    def test_cost_of_equity_capm(self):
        self.assertAlmostEqual(quant.cost_of_equity(0.045, 1.2), 0.105)    # 4.5 % + 1.2 × 5 %
        for anomalous in (None, 0, -0.3):
            self.assertIsNone(quant.cost_of_equity(0.045, anomalous), anomalous)  # sin beta neutra asumida

    def test_effective_tax_rate(self):
        self.assertAlmostEqual(quant.effective_tax_rate(20, 100), 0.2)
        self.assertEqual(quant.effective_tax_rate(50, 100), 0.35)         # tope
        self.assertEqual(quant.effective_tax_rate(-5, 100), 0.0)          # crédito fiscal extraordinario
        self.assertEqual(quant.effective_tax_rate(-5859, -36273), 0.0)    # MSTR: EBT < 0, sin escudo fiscal
        self.assertEqual(quant.effective_tax_rate(10, 0), 0.0)
        self.assertEqual(quant.effective_tax_rate(20, 100, exempt=True), 0.0)
        self.assertEqual(quant.effective_tax_rate(None, None, exempt=True), 0.0)
        self.assertIsNone(quant.effective_tax_rate(None, 100))
        self.assertIsNone(quant.effective_tax_rate(20, None))

    def test_cost_of_debt(self):
        self.assertAlmostEqual(quant.cost_of_debt(10, 200), 0.05)
        self.assertEqual(quant.cost_of_debt(0, 200), 0)
        for args in ((None, 200), (10, None), (-1, 200)):
            self.assertIsNone(quant.cost_of_debt(*args), args)

    def test_cash_cycle(self):
        self.assertEqual(quant.cash_cycle(40, 30, 50, 999), 20)       # se calcula, no se copia el de FMP
        self.assertEqual(quant.cash_cycle(90, None, 80, None), 10)    # sin inventario: DSO - DPO
        self.assertEqual(quant.cash_cycle(90, 0, 80, None), 10)
        self.assertEqual(quant.cash_cycle(None, 5, 80, 12.5), 12.5)   # sin DSO: el de FMP
        self.assertIsNone(quant.cash_cycle(90, 5, None, None))
        self.assertIsNone(quant.cash_cycle(0, 0, 233.26, -233.26))    # BABA: DSO 0 = no reportado, ni el de FMP


# --- FMP simulado ---------------------------------------------------------

def http_error(code, body=b'{"Error Message": "Premium endpoint"}'):
    return lambda: urllib.error.HTTPError(f"https://x/?apikey={KEY}", code, "err", {}, io.BytesIO(body))


def eod(last_volume, prev_volume, n_prev=30, last_price=50.0):
    """Serie EOD como la da FMP: más reciente primero."""
    rows = [{"symbol": "AAA", "date": "2026-10-02", "price": last_price, "volume": last_volume}]
    rows += [{"symbol": "AAA", "date": f"2026-08-{d:02d}", "price": 40.0, "volume": prev_volume} for d in range(n_prev, 0, -1)]
    return rows


def ratios_ttm(pe, margin):
    return [{"priceToEarningsRatioTTM": pe, "operatingProfitMarginTTM": margin}]


ROUTES = {
    ("ratios-ttm", "AAA", None): [{"priceToEarningsRatioTTM": 30, "operatingProfitMarginTTM": 0.25,
                                   "interestCoverageRatioTTM": 12.5, "debtToEquityRatioTTM": 0.8,
                                   "assetTurnoverTTM": 0.75}],
    ("key-metrics-ttm", "AAA", None): [{"evToEBITDATTM": 20, "freeCashFlowYieldTTM": 0.04, "marketCap": 1e12,
                                        "netDebtToEBITDATTM": 1.5, "returnOnInvestedCapitalTTM": 0.18,
                                        "returnOnEquityTTM": 0.3, "currentRatioTTM": 1.4,
                                        "daysOfSalesOutstandingTTM": 40, "daysOfInventoryOutstandingTTM": 30,
                                        "daysOfPayablesOutstandingTTM": 50, "cashConversionCycleTTM": 20}],
    ("ratios", "AAA", "annual"): [{"priceToEarningsRatio": v} for v in (20, 25, -5, 24, None)],
    ("key-metrics", "AAA", "annual"): [{"evToEBITDA": v} for v in (25, 25, 30, 0, 20)],
    ("income-statement", "AAA", "quarter"): [
        {"revenue": r, "operatingIncome": oi, "period": p, "fiscalYear": y, "interestExpense": i,
         "incomeTaxExpense": 2, "incomeBeforeTax": 10}
        for r, oi, p, y, i in [(120, 30, "Q3", "2026", 3), (110, 25, "Q2", "2026", 3), (105, 20, "Q1", "2026", 2),
                               (100, 20, "Q4", "2025", 2), (100, 22, "Q3", "2025", 1)]
    ],
    ("income-statement", "AAA", "annual"): [
        {"revenue": 500, "operatingIncome": 100, "period": "FY", "fiscalYear": "2025", "interestExpense": 8,
         "incomeTaxExpense": 6, "incomeBeforeTax": 40},
        {"revenue": 400, "operatingIncome": 70, "period": "FY", "fiscalYear": "2024", "interestExpense": 6,
         "incomeTaxExpense": 0, "incomeBeforeTax": 30},
    ],
    ("balance-sheet-statement", "AAA", "quarter"): [{"totalDebt": 200}],
    ("balance-sheet-statement", "AAA", "annual"): [{"totalDebt": 160}],
    ("profile", "AAA", None): [{"beta": 1.2}],
    ("treasury-rates", None, None): [{"date": "2026-10-01", "year10": 5.24}, {"date": "2026-10-02", "year10": 5.28}],
    ("historical-price-eod/light", "AAA", None): eod(last_volume=250, prev_volume=100),
    ("stock-peers", "AAA", None): [{"symbol": s} for s in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG")],
    ("ratios-ttm", "BBB", None): ratios_ttm(20, 0.20),
    ("ratios-ttm", "CCC", None): ratios_ttm(25, 0.30),
    ("ratios-ttm", "DDD", None): ratios_ttm(-10, 0.10),
    ("ratios-ttm", "EEE", None): http_error(500, b"<html>"),
    ("ratios-ttm", "FFF", None): ratios_ttm(40, 0.15),
    ("ratios-ttm", "GGG", None): ratios_ttm(10, 0.50),
}


class FakeFMP:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, timeout):
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        assert q["apikey"] == KEY and timeout == 10
        key = (u.path.removeprefix("/stable/"), q.get("symbol"), q.get("period"))
        self.calls.append(key)
        payload = self.routes[key]
        if callable(payload):
            raise payload()
        return io.BytesIO(json.dumps(payload).encode())


class FMPTestCase(unittest.TestCase):
    routes = ROUTES

    def setUp(self):
        quant.fetch.cache_clear()
        quant.risk_free_rate.cache_clear()
        quant.calls_made = 0
        self.fmp = FakeFMP(dict(self.routes))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.rf_cache = Path(tmp.name, "cache", "treasury.json")
        for p in (patch.dict(os.environ, {"FMP_API_KEY": KEY}), patch("quant.urllib.request.urlopen", self.fmp),
                  patch("quant.RF_CACHE", self.rf_cache)):
            p.start()
            self.addCleanup(p.stop)


class AnalyzeTest(FMPTestCase):
    def test_full_report(self):
        r = quant.analyze("aaa")
        m = r.metrics
        self.assertEqual(m.symbol, "AAA")
        self.assertAlmostEqual(m.change_1d, 0.25)                      # 50 / 40 - 1 (última sesión vs anterior)
        self.assertEqual((m.price, m.pe_ttm, m.ev_ebitda_ttm, m.operating_margin_ttm), (50.0, 30.0, 20.0, 0.25))
        self.assertAlmostEqual(m.operating_margin_last, 0.25)          # 30 / 120
        self.assertAlmostEqual(m.revenue_growth_yoy, 0.2)              # 120 / 100 - 1 (Q3 vs Q3)
        self.assertEqual(m.last_period, "FY2026 Q3")
        self.assertAlmostEqual(m.fcf_yield, 0.04)
        self.assertAlmostEqual(m.fcf_ttm, 4e10)
        self.assertEqual((m.volume, m.volume_avg_30d), (250.0, 100.0))
        self.assertEqual(r.pe_hist_median, 24)                         # [20, 25, 24]
        self.assertAlmostEqual(r.pe_vs_hist, 0.25)
        self.assertEqual(r.ev_ebitda_hist_median, 25)                  # [25, 25, 30, 20]
        self.assertAlmostEqual(r.ev_ebitda_vs_hist, -0.2)
        self.assertAlmostEqual(r.volume_divergence, 1.5)
        # Peers: sin AAA, máx. 5 (GGG fuera), EEE falla, DDD sólo aporta margen.
        self.assertEqual(r.peers, ("BBB", "CCC", "DDD", "FFF"))
        self.assertEqual(r.peer_median_pe, 25)                         # [20, 25, 40]
        self.assertAlmostEqual(r.pe_vs_peers, 0.2)
        self.assertAlmostEqual(r.peer_median_operating_margin, 0.175)  # [0.20, 0.30, 0.10, 0.15]
        self.assertAlmostEqual(r.margin_vs_peers, 0.075)
        self.assertNotIn(("ratios-ttm", "GGG", None), self.fmp.calls)
        self.assertEqual(quant.calls_made, len(self.fmp.calls))
        self.assertLessEqual(quant.calls_made, 15)  # 9 propias (incl. treasury, 1 por ejecución) + stock-peers + 5 peers

    def test_capital_cost_and_cash_cycle(self):
        r = quant.analyze("AAA", [])
        m = r.metrics
        self.assertEqual((m.beta, m.asset_turnover, m.dso, m.dio, m.dpo, m.cash_conversion_cycle), (1.2, 0.75, 40, 30, 50, 20))
        self.assertAlmostEqual(r.risk_free_rate, 0.0528)          # year10 de la fecha más reciente, en fracción
        self.assertAlmostEqual(r.cost_of_equity, 0.1128)          # 5.28 % + 1.2 × 5 %
        self.assertAlmostEqual(r.roe_spread, 0.1872)              # 30 % - 11.28 %
        self.assertAlmostEqual(r.cost_of_debt, 0.05)              # (3 + 3 + 2 + 2) / 200
        self.assertAlmostEqual(r.tax_rate, 0.2)                   # (2 × 4) / (10 × 4)
        self.assertAlmostEqual(r.cost_of_debt_after_tax, 0.04)    # 5 % × (1 - 20 %)

    def test_tax_exempt_keeps_gross_rd(self):
        r = quant.analyze("AAA", [], tax_exempt=True)
        self.assertEqual(r.tax_rate, 0.0)
        self.assertAlmostEqual(r.cost_of_debt_after_tax, 0.05)

    def test_loss_before_tax_has_no_shield(self):
        key = ("income-statement", "AAA", "quarter")
        self.fmp.routes[key] = [{**row, "incomeTaxExpense": -1, "incomeBeforeTax": -10} for row in ROUTES[key]]
        r = quant.analyze("AAA", [])
        self.assertEqual(r.tax_rate, 0.0)
        self.assertAlmostEqual(r.cost_of_debt_after_tax, r.cost_of_debt)

    def test_no_inventory_null_beta_and_missing_interest(self):
        key = ("key-metrics-ttm", "AAA", None)
        self.fmp.routes[key] = [{**ROUTES[key][0], "daysOfInventoryOutstandingTTM": None,
                                 "cashConversionCycleTTM": None, "returnOnEquityTTM": None}]
        self.fmp.routes[("profile", "AAA", None)] = [{"beta": None}]
        key = ("income-statement", "AAA", "quarter")
        self.fmp.routes[key] = [{**row, "interestExpense": None} for row in ROUTES[key]]
        r = quant.analyze("AAA", [])
        self.assertEqual((r.metrics.dio, r.metrics.cash_conversion_cycle), (None, -10))  # 40 - 50
        self.assertEqual((r.metrics.beta, r.cost_of_equity), (None, None))              # sin beta: k n/d
        self.assertEqual((r.roe_spread, r.cost_of_debt, r.cost_of_debt_after_tax), (None, None, None))
        self.assertAlmostEqual(r.tax_rate, 0.2)                                          # t no depende de los intereses


class RiskFreeRateTest(FMPTestCase):
    def write_cache(self, rf, age):
        self.rf_cache.parent.mkdir(parents=True)
        self.rf_cache.write_text(json.dumps({"fetched": (datetime.now(timezone.utc) - age).isoformat(), "rf": rf}))

    def test_fetches_and_caches(self):
        self.assertAlmostEqual(quant.risk_free_rate(), 0.0528)
        self.assertAlmostEqual(json.loads(self.rf_cache.read_text())["rf"], 0.0528)
        self.assertEqual(self.fmp.calls, [("treasury-rates", None, None)])

    def test_fresh_cache_skips_fmp(self):
        self.write_cache(0.045, timedelta(hours=23))
        self.assertEqual(quant.risk_free_rate(), 0.045)
        self.assertEqual(self.fmp.calls, [])

    def test_stale_or_corrupt_cache_refetches(self):
        self.write_cache(0.045, timedelta(hours=25))
        self.assertAlmostEqual(quant.risk_free_rate(), 0.0528)
        quant.risk_free_rate.cache_clear()
        self.rf_cache.write_text("{no json")
        self.assertAlmostEqual(quant.risk_free_rate(), 0.0528)

    def test_failure_falls_back_without_caching(self):
        for payload in (http_error(500, b"<html>"), [], [{"date": "2026-10-02", "year10": None}]):
            quant.fetch.cache_clear()
            quant.risk_free_rate.cache_clear()
            self.fmp.routes[("treasury-rates", None, None)] = payload
            with patch("sys.stderr", io.StringIO()):
                self.assertEqual(quant.risk_free_rate(), 0.04, payload)
            self.assertFalse(self.rf_cache.exists())

    def test_no_api_key_falls_back(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.stderr", io.StringIO()):
            self.assertEqual(quant.risk_free_rate(), 0.04)

    def test_config_peers_skip_stock_peers(self):
        r = quant.analyze("AAA", ["bbb", "AAA", "ccc", "GGG"])
        self.assertNotIn(("stock-peers", "AAA", None), self.fmp.calls)
        self.assertEqual(r.peers, ("BBB", "CCC", "GGG"))
        self.assertEqual(r.peer_median_pe, 20)

    def test_fewer_than_min_peers_gives_none(self):
        r = quant.analyze("AAA", ["BBB", "DDD", "EEE"])
        self.assertEqual(r.peers, ("BBB", "DDD"))
        self.assertIsNone(r.peer_median_pe)
        self.assertIsNone(r.pe_vs_peers)
        self.assertIsNone(r.peer_median_operating_margin)
        self.assertIsNone(r.margin_vs_peers)

    def test_cache_shares_calls_between_analyses(self):
        quant.analyze("AAA", ["BBB", "CCC", "FFF"])
        n = len(self.fmp.calls)
        quant.analyze("AAA", ["BBB", "CCC", "FFF"])
        self.assertEqual(len(self.fmp.calls), n)
        self.assertEqual(self.fmp.calls.count(("ratios-ttm", "BBB", None)), 1)


class BalanceTest(FMPTestCase):
    def set_ttm(self, ratios=None, metrics=None):
        for key, extra in ((("ratios-ttm", "AAA", None), ratios), (("key-metrics-ttm", "AAA", None), metrics)):
            if extra:
                self.fmp.routes[key] = [{**self.fmp.routes[key][0], **extra}]

    def test_balance_fields(self):
        m = quant.analyze("AAA", []).metrics
        self.assertEqual((m.net_debt_to_ebitda, m.net_cash, m.debt_to_equity), (1.5, False, 0.8))
        self.assertEqual((m.interest_coverage, m.roic, m.roe, m.current_ratio), (12.5, 0.18, 0.3, 1.4))
        self.assertLessEqual(quant.calls_made, 9)  # 6 + balance + profile + treasury

    def test_net_cash(self):
        self.set_ttm(metrics={"netDebtToEBITDATTM": -0.8})  # EBITDA > 0 (EV/EBITDA = 20)
        m = quant.analyze("AAA", []).metrics
        self.assertIsNone(m.net_debt_to_ebitda)
        self.assertTrue(m.net_cash)

    def test_mstr_negative_ebitda_is_not_net_cash(self):
        # Valores reales de MSTR (2026-10-03): ratio negativo porque el EBITDA es negativo, no por caja neta.
        self.set_ttm(ratios={"priceToEarningsRatioTTM": -1.67, "interestCoverageRatioTTM": -262.85, "debtToEquityRatioTTM": 0.149},
                     metrics={"evToEBITDATTM": -2.66, "netDebtToEBITDATTM": -0.232,
                              "returnOnInvestedCapitalTTM": -0.134, "returnOnEquityTTM": -0.608})
        m = quant.analyze("AAA", []).metrics
        self.assertIsNone(m.net_debt_to_ebitda)
        self.assertFalse(m.net_cash)
        self.assertIsNone(m.pe_ttm)
        self.assertEqual(m.debt_to_equity, 0.149)
        self.assertEqual((m.interest_coverage, m.roic, m.roe), (-262.85, -0.134, -0.608))  # el negativo es la señal

    def test_meaningless_values_are_none(self):
        self.set_ttm(ratios={"debtToEquityRatioTTM": -2.0, "interestCoverageRatioTTM": None},
                     metrics={"currentRatioTTM": 0, "returnOnEquityTTM": "n/a", "netDebtToEBITDATTM": None})
        m = quant.analyze("AAA", []).metrics
        self.assertEqual((m.debt_to_equity, m.interest_coverage, m.current_ratio, m.roe), (None, None, None, None))
        self.assertEqual((m.net_debt_to_ebitda, m.net_cash), (None, False))


class FallbackTest(FMPTestCase):
    def assert_fy_fallback(self):
        r = quant.analyze("AAA", [])
        m = r.metrics
        self.assertAlmostEqual(m.operating_margin_last, 0.2)   # 100 / 500
        self.assertAlmostEqual(m.revenue_growth_yoy, 0.25)     # 500 / 400 - 1
        self.assertEqual(m.last_period, "FY2025")
        return r

    def test_quarter_403_falls_back_to_fy(self):
        self.fmp.routes[("income-statement", "AAA", "quarter")] = http_error(403)
        self.fmp.routes[("balance-sheet-statement", "AAA", "quarter")] = http_error(403)
        r = self.assert_fy_fallback()
        self.assertAlmostEqual(r.cost_of_debt, 0.05)  # 8 (FY) / 160 (FY)
        self.assertAlmostEqual(r.tax_rate, 0.15)      # 6 / 40 (FY)

    def test_quarter_402_falls_back_to_fy(self):
        self.fmp.routes[("income-statement", "AAA", "quarter")] = http_error(402)
        self.assert_fy_fallback()

    def test_quarter_empty_falls_back_to_fy(self):
        self.fmp.routes[("income-statement", "AAA", "quarter")] = []
        self.assert_fy_fallback()

    def test_missing_values_give_none_not_zero(self):
        self.fmp.routes[("key-metrics-ttm", "AAA", None)] = []
        self.fmp.routes[("historical-price-eod/light", "AAA", None)] = eod(250, 100, n_prev=10)
        r = quant.analyze("AAA", [])
        m = r.metrics
        self.assertEqual((m.ev_ebitda_ttm, m.fcf_yield, m.fcf_ttm, m.volume_avg_30d), (None, None, None, None))
        self.assertIsNone(r.ev_ebitda_vs_hist)
        self.assertIsNone(r.volume_divergence)
        self.assertEqual(r.peers, ())


class ErrorsTest(FMPTestCase):
    def test_main_ticker_http_error_hides_key(self):
        self.fmp.routes[("ratios-ttm", "AAA", None)] = http_error(401, f'{{"Error Message": "Invalid API KEY {KEY}"}}'.encode())
        with self.assertRaises(RuntimeError) as ctx:
            quant.analyze("AAA", [])
        self.assertIn("Invalid API KEY", str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)

    def test_error_message_in_200_payload(self):
        self.fmp.routes[("ratios-ttm", "AAA", None)] = {"Error Message": "Limit Reach"}
        with self.assertRaisesRegex(RuntimeError, "Limit Reach"):
            quant.analyze("AAA", [])

    def test_missing_key_names_it(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "FMP_API_KEY"):
                quant.analyze("AAA", [])
        self.assertEqual(self.fmp.calls, [])


# --- Yahoo Finance --------------------------------------------------------

DAY = 86400
OPEN = 1_791_270_000  # apertura de la sesión de hoy (epoch)


def chart(closes, volumes, end=OPEN + 9 * 3600):
    """Respuesta de /v8/finance/chart como la da Yahoo: una barra por sesión, la última es la de hoy."""
    stamps = [OPEN - (len(closes) - 1 - i) * DAY for i in range(len(closes))]
    return {"chart": {"error": None, "result": [{
        "meta": {"currency": "EUR", "symbol": "NXT.MC", "regularMarketPrice": 1.014,
                 "currentTradingPeriod": {"regular": {"start": OPEN, "end": end}}},
        "timestamp": stamps, "indicators": {"quote": [{"close": closes, "volume": volumes}]}}]}}


# 30 sesiones a 1.0 con 100 de volumen, ayer 1.05 con 250, y la de hoy en curso (cierre null, volumen parcial).
NXT = chart([1.0] * 30 + [1.05, None], [100] * 30 + [250, 3])


class YahooTest(unittest.TestCase):
    def test_closed_sessions_only(self):
        r = quant.yahoo_report("NXT.MC", NXT, now=OPEN + 3600)  # mercado abierto
        m = r.metrics
        self.assertEqual((m.symbol, m.price, m.volume, m.volume_avg_30d), ("NXT.MC", 1.05, 250, 100))
        self.assertAlmostEqual(m.change_1d, 0.05)
        self.assertAlmostEqual(r.volume_divergence, 1.5)
        # Sin FMP: múltiplos, balance y coste de capital quedan None, nunca 0.
        self.assertEqual((m.pe_ttm, m.roe, m.dso, m.net_debt_to_ebitda, m.net_cash), (None, None, None, None, False))
        self.assertEqual((r.risk_free_rate, r.cost_of_equity, r.peers, r.pe_vs_peers), (None, None, (), None))

    def test_after_close_today_counts(self):
        data = chart([1.0] * 31 + [0.9], [100] * 31 + [400])
        r = quant.yahoo_report("NXT.MC", data, now=OPEN + 10 * 3600)  # después del cierre
        self.assertEqual((r.metrics.price, r.metrics.volume), (0.9, 400))
        self.assertAlmostEqual(r.metrics.change_1d, -0.1)
        self.assertAlmostEqual(r.volume_divergence, 3.0)

    def test_short_or_holey_series(self):
        r = quant.yahoo_report("NXT.MC", chart([1.0, None, 1.1, None], [10, None, 20, 1]), now=OPEN)
        self.assertEqual(r.metrics.price, 1.1)
        self.assertAlmostEqual(r.metrics.change_1d, 0.1)       # el festivo (null) no cuenta como sesión
        self.assertEqual((r.metrics.volume_avg_30d, r.volume_divergence), (None, None))
        empty = quant.yahoo_report("NXT.MC", chart([], []), now=OPEN)
        self.assertEqual((empty.metrics.price, empty.metrics.change_1d), (None, None))

    def test_malformed_response(self):
        for data in ({"chart": {"result": None, "error": {"code": "Not Found"}}}, {}, [], {"chart": {"result": [{}]}}):
            with self.assertRaisesRegex(RuntimeError, "Yahoo NXT.MC"):
                quant.yahoo_report("NXT.MC", data, now=OPEN)

    def test_fetch_uses_browser_user_agent_and_3mo(self):
        with patch("quant.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(NXT).encode())) as urlopen:
            r = quant.yahoo("NXT.MC")
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://query1.finance.yahoo.com/v8/finance/chart/NXT.MC?interval=1d&range=3mo")
        self.assertTrue(req.get_header("User-agent").startswith("Mozilla/5.0"))
        self.assertEqual(r.metrics.symbol, "NXT.MC")

    def test_http_error(self):
        err = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(b""))
        with patch("quant.urllib.request.urlopen", side_effect=err):
            with self.assertRaisesRegex(RuntimeError, "Yahoo NXT.MC: HTTP 429"):
                quant.yahoo("NXT.MC")


if __name__ == "__main__":
    unittest.main()
