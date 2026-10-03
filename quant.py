"""Métricas FMP en contexto: histórico propio y mediana de pares. Spec: SPEC-quant.md."""
import datetime
import functools
import json
import math
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

BASE = "https://financialmodelingprep.com/stable/"
MAX_PEERS, MIN_PEERS, HIST_YEARS = 5, 3, 5
calls_made = 0


@dataclass(frozen=True)
class Metrics:
    symbol: str
    price: float | None
    pe_ttm: float | None
    ev_ebitda_ttm: float | None
    operating_margin_last: float | None
    operating_margin_ttm: float | None
    revenue_growth_yoy: float | None
    last_period: str | None
    fcf_ttm: float | None
    fcf_yield: float | None
    volume: float | None
    volume_avg_30d: float | None
    net_debt_to_ebitda: float | None
    net_cash: bool
    debt_to_equity: float | None
    interest_coverage: float | None
    roic: float | None
    roe: float | None
    current_ratio: float | None


@dataclass(frozen=True)
class Report:
    metrics: Metrics
    pe_hist_median: float | None
    ev_ebitda_hist_median: float | None
    pe_vs_hist: float | None
    ev_ebitda_vs_hist: float | None
    volume_divergence: float | None
    peers: tuple[str, ...]
    peer_median_pe: float | None
    peer_median_operating_margin: float | None
    pe_vs_peers: float | None
    margin_vs_peers: float | None


class FMPError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --- Cálculo --------------------------------------------------------------

def num(x) -> float | None:
    """float si x es un número finito (no bool); si no, None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return float(x)


def positive(x) -> float | None:
    """num(x) si es > 0; un múltiplo <= 0 no es significativo."""
    x = num(x)
    return x if x is not None and x > 0 else None


def median(values, min_count: int = 1) -> float | None:
    """Mediana de los valores no None; None si hay menos de min_count."""
    values = [v for v in values if v is not None]
    return statistics.median(values) if values and len(values) >= min_count else None


def relative(current: float | None, ref: float | None) -> float | None:
    """current / ref - 1: prima (+) o descuento (-); None si ref no es positivo."""
    return current / ref - 1 if current is not None and ref is not None and ref > 0 else None


def yoy(current: float | None, year_ago: float | None) -> float | None:
    """Crecimiento interanual; None si la base no es positiva."""
    return relative(current, year_ago)


def avg_volume(volumes: list[float | None]) -> float | None:
    """Media de las 30 sesiones previas a la última (serie cronológica); None si faltan datos."""
    prev = [v for v in volumes[-31:-1] if v is not None]
    return statistics.fmean(prev) if len(volumes) >= 31 and len(prev) == 30 else None


# --- FMP ------------------------------------------------------------------

@functools.cache
def fetch(path: str, **params) -> list | dict:
    """GET memoizada a la stable API de FMP. Errores sin la apikey."""
    global calls_made
    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise RuntimeError("Falta la variable de entorno FMP_API_KEY")
    url = BASE + path + "?" + urllib.parse.urlencode({**params, "apikey": key})
    calls_made += 1
    # Nunca se encadena ni se formatea la excepción original: su URL lleva la apikey.
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            with e:
                message = json.load(e).get("Error Message", f"HTTP {e.code}")
        except (ValueError, AttributeError):
            message = f"HTTP {e.code}"
        raise FMPError(e.code, f"FMP {path}: {message}".replace(key, "***")) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"FMP {path}: sin conexión ({e.reason})") from None
    if isinstance(data, dict) and "Error Message" in data:
        raise RuntimeError(f"FMP {path}: {data['Error Message']}".replace(key, "***"))
    return data


def _rows(data) -> list[dict]:
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _first(data) -> dict:
    rows = _rows(data)
    return rows[0] if rows else {}


def _last_period(symbol: str) -> tuple[float | None, float | None, str | None]:
    """(margen operativo, crecimiento YoY, etiqueta) del último trimestre; FY si no hay trimestral."""
    try:
        rows, lag = _rows(fetch("income-statement", symbol=symbol, period="quarter", limit=5)), 4
    except FMPError as e:
        if e.status not in (402, 403):
            raise
        rows = []
    if not rows:
        rows, lag = _rows(fetch("income-statement", symbol=symbol, period="annual", limit=2)), 1
    if not rows:
        return None, None, None
    revenue, op_income = positive(rows[0].get("revenue")), num(rows[0].get("operatingIncome"))
    margin = op_income / revenue if revenue and op_income is not None else None
    growth = yoy(revenue, num(rows[lag].get("revenue"))) if len(rows) > lag else None
    # Ejercicio fiscal de la empresa (MSFT cierra en junio, BABA en marzo): "FY2026 Q4" o "FY2025".
    period, fiscal_year = rows[0].get("period"), rows[0].get("fiscalYear")
    label = (f"FY{fiscal_year}" if period == "FY" else f"FY{fiscal_year} {period}") if period and fiscal_year else period
    return margin, growth, label


def analyze(symbol: str, peers: list[str] | None = None) -> Report:
    """Métricas de symbol con contexto histórico y frente a su grupo de pares."""
    symbol = symbol.upper()
    ttm = _first(fetch("ratios-ttm", symbol=symbol))
    km = _first(fetch("key-metrics-ttm", symbol=symbol))
    hist_pe = median(positive(r.get("priceToEarningsRatio"))
                     for r in _rows(fetch("ratios", symbol=symbol, period="annual", limit=HIST_YEARS)))
    hist_ev = median(positive(r.get("evToEBITDA"))
                     for r in _rows(fetch("key-metrics", symbol=symbol, period="annual", limit=HIST_YEARS)))
    margin_last, growth, last_period = _last_period(symbol)
    since = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
    prices = sorted(_rows(fetch("historical-price-eod/light", symbol=symbol, **{"from": since})),
                    key=lambda r: str(r.get("date")))
    volumes = [num(r.get("volume")) for r in prices]

    pe, ev = positive(ttm.get("priceToEarningsRatioTTM")), positive(km.get("evToEBITDATTM"))
    margin_ttm = num(ttm.get("operatingProfitMarginTTM"))
    fcf_yield, market_cap = num(km.get("freeCashFlowYieldTTM")), positive(km.get("marketCap"))
    volume, avg30 = (volumes[-1] if volumes else None), avg_volume(volumes)
    # Con EBITDA <= 0 el ratio deuda neta/EBITDA no significa nada y su signo negativo NO es caja neta (MSTR).
    raw_ev_ebitda, net_debt_ratio = num(km.get("evToEBITDATTM")), num(km.get("netDebtToEBITDATTM"))
    ebitda_positive = raw_ev_ebitda is not None and raw_ev_ebitda > 0
    net_cash = ebitda_positive and net_debt_ratio is not None and net_debt_ratio < 0

    if peers is None:
        peers = [r.get("symbol") for r in _rows(fetch("stock-peers", symbol=symbol))]
        peers = [p for p in peers if isinstance(p, str) and p.upper() != symbol][:MAX_PEERS]
    used, peer_pe, peer_margin = [], [], []
    for p in dict.fromkeys(p.upper() for p in peers if p.upper() != symbol):
        try:
            row = _first(fetch("ratios-ttm", symbol=p))
        except RuntimeError:
            continue  # un peer fallido no invalida el análisis
        p_pe, p_margin = positive(row.get("priceToEarningsRatioTTM")), num(row.get("operatingProfitMarginTTM"))
        if p_pe is None and p_margin is None:
            continue
        used.append(p)
        peer_pe.append(p_pe)
        peer_margin.append(p_margin)
    peer_median_pe = median(peer_pe, MIN_PEERS)
    peer_median_margin = median(peer_margin, MIN_PEERS)

    return Report(
        metrics=Metrics(
            symbol=symbol,
            price=num(prices[-1].get("price")) if prices else None,
            pe_ttm=pe,
            ev_ebitda_ttm=ev,
            operating_margin_last=margin_last,
            operating_margin_ttm=margin_ttm,
            revenue_growth_yoy=growth,
            last_period=last_period,
            fcf_ttm=fcf_yield * market_cap if fcf_yield is not None and market_cap else None,
            fcf_yield=fcf_yield,
            volume=volume,
            volume_avg_30d=avg30,
            net_debt_to_ebitda=net_debt_ratio if ebitda_positive and not net_cash else None,
            net_cash=net_cash,
            debt_to_equity=positive(ttm.get("debtToEquityRatioTTM")),
            interest_coverage=num(ttm.get("interestCoverageRatioTTM")),  # negativo = el EBIT no cubre intereses
            roic=num(km.get("returnOnInvestedCapitalTTM")),
            roe=num(km.get("returnOnEquityTTM")),
            current_ratio=positive(km.get("currentRatioTTM")),
        ),
        pe_hist_median=hist_pe,
        ev_ebitda_hist_median=hist_ev,
        pe_vs_hist=relative(pe, hist_pe),
        ev_ebitda_vs_hist=relative(ev, hist_ev),
        volume_divergence=relative(volume, avg30),
        peers=tuple(used),
        peer_median_pe=peer_median_pe,
        peer_median_operating_margin=peer_median_margin,
        pe_vs_peers=relative(pe, peer_median_pe),
        margin_vs_peers=margin_ttm - peer_median_margin if margin_ttm is not None and peer_median_margin is not None else None,
    )


if __name__ == "__main__":
    from notify import load_env  # sólo el CLI; el módulo no depende de notify

    load_env()
    report = analyze(sys.argv[1], sys.argv[2:] or None)
    print(json.dumps(asdict(report), indent=2))
    print(f"llamadas FMP: {calls_made}", file=sys.stderr)
