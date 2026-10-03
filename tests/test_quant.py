import io
import json
import math
import os
import unittest
import urllib.error
import urllib.parse
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
                                   "interestCoverageRatioTTM": 12.5, "debtToEquityRatioTTM": 0.8}],
    ("key-metrics-ttm", "AAA", None): [{"evToEBITDATTM": 20, "freeCashFlowYieldTTM": 0.04, "marketCap": 1e12,
                                        "netDebtToEBITDATTM": 1.5, "returnOnInvestedCapitalTTM": 0.18,
                                        "returnOnEquityTTM": 0.3, "currentRatioTTM": 1.4}],
    ("ratios", "AAA", "annual"): [{"priceToEarningsRatio": v} for v in (20, 25, -5, 24, None)],
    ("key-metrics", "AAA", "annual"): [{"evToEBITDA": v} for v in (25, 25, 30, 0, 20)],
    ("income-statement", "AAA", "quarter"): [
        {"revenue": r, "operatingIncome": oi, "period": p, "fiscalYear": y}
        for r, oi, p, y in [(120, 30, "Q3", "2026"), (110, 25, "Q2", "2026"), (105, 20, "Q1", "2026"),
                            (100, 20, "Q4", "2025"), (100, 22, "Q3", "2025")]
    ],
    ("income-statement", "AAA", "annual"): [
        {"revenue": 500, "operatingIncome": 100, "period": "FY", "fiscalYear": "2025"},
        {"revenue": 400, "operatingIncome": 70, "period": "FY", "fiscalYear": "2024"},
    ],
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
        key = (u.path.removeprefix("/stable/"), q["symbol"], q.get("period"))
        self.calls.append(key)
        payload = self.routes[key]
        if callable(payload):
            raise payload()
        return io.BytesIO(json.dumps(payload).encode())


class FMPTestCase(unittest.TestCase):
    routes = ROUTES

    def setUp(self):
        quant.fetch.cache_clear()
        quant.calls_made = 0
        self.fmp = FakeFMP(dict(self.routes))
        for p in (patch.dict(os.environ, {"FMP_API_KEY": KEY}), patch("quant.urllib.request.urlopen", self.fmp)):
            p.start()
            self.addCleanup(p.stop)


class AnalyzeTest(FMPTestCase):
    def test_full_report(self):
        r = quant.analyze("aaa")
        m = r.metrics
        self.assertEqual(m.symbol, "AAA")
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
        self.assertLessEqual(quant.calls_made, 12)

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
        self.assertLessEqual(quant.calls_made, 6)  # mismos payloads: ninguna llamada extra

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
        m = quant.analyze("AAA", []).metrics
        self.assertAlmostEqual(m.operating_margin_last, 0.2)   # 100 / 500
        self.assertAlmostEqual(m.revenue_growth_yoy, 0.25)     # 500 / 400 - 1
        self.assertEqual(m.last_period, "FY2025")

    def test_quarter_403_falls_back_to_fy(self):
        self.fmp.routes[("income-statement", "AAA", "quarter")] = http_error(403)
        self.assert_fy_fallback()

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


if __name__ == "__main__":
    unittest.main()
