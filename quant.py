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
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

BASE = "https://financialmodelingprep.com/stable/"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
# Sin un User-Agent de navegador Yahoo responde 403/429.
YAHOO_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
MAX_PEERS, MIN_PEERS, HIST_YEARS = 5, 3, 5
calls_made = 0
RF_FALLBACK = 0.04  # sin FMP: bono a 10 años aproximado
ERP = 0.05          # prima de riesgo de mercado, constante conservadora
MAX_TAX_RATE = 0.35  # tope de t: neutraliza créditos fiscales extraordinarios y anomalías contables
RF_CACHE, RF_TTL = Path("cache/fmp-treasury.json"), datetime.timedelta(hours=24)


@dataclass(frozen=True)
class Metrics:
    symbol: str
    price: float | None
    change_1d: float | None
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
    beta: float | None
    asset_turnover: float | None
    dso: float | None
    dio: float | None
    dpo: float | None
    cash_conversion_cycle: float | None


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
    risk_free_rate: float | None  # None sólo en informes de Yahoo (sin FMP)
    cost_of_equity: float | None
    roe_spread: float | None
    cost_of_debt: float | None
    tax_rate: float | None
    cost_of_debt_after_tax: float | None


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


def cost_of_equity(rf: float, beta: float | None) -> float | None:
    """CAPM: rf + beta × ERP; None si no hay beta o es <= 0 (anómala): no se asume una beta neutra."""
    return rf + beta * ERP if beta is not None and beta > 0 else None


def cost_of_debt(interest: float | None, debt: float | None) -> float | None:
    """Intereses / deuda total; None sin deuda o con intereses ausentes o negativos."""
    return interest / debt if interest is not None and interest >= 0 and debt else None


def effective_tax_rate(tax: float | None, ebt: float | None, exempt: bool = False) -> float | None:
    """t para el escudo fiscal: 0 si exenta o con EBT <= 0 (sin beneficio no hay escudo); si no, impuesto/EBT en [0, 0.35]."""
    if exempt or (ebt is not None and ebt <= 0):
        return 0.0
    return min(max(tax / ebt, 0.0), MAX_TAX_RATE) if tax is not None and ebt is not None else None


def cash_cycle(dso: float | None, dio: float | None, dpo: float | None, fmp_ccc: float | None) -> float | None:
    """DSO + DIO - DPO; sin inventario (DIO None o 0) = DSO - DPO. Sin DSO o DPO, el CCC de FMP.
    DSO = 0 es un dato no reportado (BABA), no cobro al contado: CCC None."""
    if dso == 0:
        return None
    return dso + (dio or 0) - dpo if dso is not None and dpo is not None else fmp_ccc


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


def _statement(path: str, symbol: str, limit: int) -> tuple[list[dict], bool]:
    """(filas, trimestral): trimestral si el plan lo da (no 402/403) y no viene vacío; si no, anual."""
    try:
        if rows := _rows(fetch(path, symbol=symbol, period="quarter", limit=limit)):
            return rows, True
    except FMPError as e:
        if e.status not in (402, 403):
            raise
    return _rows(fetch(path, symbol=symbol, period="annual", limit=min(limit, 2))), False


TTM_FIELDS = ("interestExpense", "incomeTaxExpense", "incomeBeforeTax")


def _last_period(symbol: str) -> tuple[float | None, float | None, str | None, dict[str, float | None]]:
    """(margen operativo, crecimiento YoY, etiqueta) del último trimestre y sumas TTM de TTM_FIELDS; FY si no hay trimestral."""
    rows, quarterly = _statement("income-statement", symbol, 5)
    lag = 4 if quarterly else 1
    if not rows:
        return None, None, None, dict.fromkeys(TTM_FIELDS)
    ttm = {}
    for field in TTM_FIELDS:
        values = [num(r.get(field)) for r in rows[:lag]]
        ttm[field] = sum(values) if len(values) == lag and None not in values else None
    revenue, op_income = positive(rows[0].get("revenue")), num(rows[0].get("operatingIncome"))
    margin = op_income / revenue if revenue and op_income is not None else None
    growth = yoy(revenue, num(rows[lag].get("revenue"))) if len(rows) > lag else None
    # Ejercicio fiscal de la empresa (MSFT cierra en junio, BABA en marzo): "FY2026 Q4" o "FY2025".
    period, fiscal_year = rows[0].get("period"), rows[0].get("fiscalYear")
    label = (f"FY{fiscal_year}" if period == "FY" else f"FY{fiscal_year} {period}") if period and fiscal_year else period
    return margin, growth, label, ttm


@functools.cache
def risk_free_rate() -> float:
    """Rendimiento del bono a 10 años (fracción), cacheado 24 h en disco; RF_FALLBACK si FMP falla."""
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        cached = json.loads(RF_CACHE.read_text(encoding="utf-8"))
        if now - datetime.datetime.fromisoformat(cached["fetched"]) < RF_TTL and positive(cached["rf"]):
            return cached["rf"]
    except (OSError, ValueError, LookupError, TypeError):
        pass  # caché ausente, caducada o corrupta: se pide a FMP
    since = (now.date() - datetime.timedelta(days=10)).isoformat()
    try:
        rows = _rows(fetch("treasury-rates", **{"from": since}))
        rf = positive(max(rows, key=lambda r: str(r.get("date"))).get("year10")) if rows else None
    except RuntimeError as e:
        print(f"rf: {e}", file=sys.stderr)
        rf = None
    if rf is None:
        print(f"rf no disponible: se usa {RF_FALLBACK:.1%}", file=sys.stderr)
        return RF_FALLBACK
    rf /= 100  # FMP lo da en %
    try:
        RF_CACHE.parent.mkdir(parents=True, exist_ok=True)
        RF_CACHE.write_text(json.dumps({"fetched": now.isoformat(), "rf": rf}), encoding="utf-8")
    except OSError:
        pass  # sin caché se vuelve a pedir mañana; no es motivo para abortar
    return rf


def analyze(symbol: str, peers: list[str] | None = None, tax_exempt: bool = False) -> Report:
    """Métricas de symbol con contexto histórico y frente a su grupo de pares. tax_exempt: t = 0 (régimen de tonelaje)."""
    symbol = symbol.upper()
    ttm = _first(fetch("ratios-ttm", symbol=symbol))
    km = _first(fetch("key-metrics-ttm", symbol=symbol))
    hist_pe = median(positive(r.get("priceToEarningsRatio"))
                     for r in _rows(fetch("ratios", symbol=symbol, period="annual", limit=HIST_YEARS)))
    hist_ev = median(positive(r.get("evToEBITDA"))
                     for r in _rows(fetch("key-metrics", symbol=symbol, period="annual", limit=HIST_YEARS)))
    margin_last, growth, last_period, pnl_ttm = _last_period(symbol)
    balance, _ = _statement("balance-sheet-statement", symbol, 1)
    # totalDebt incluye arrendamientos, igual que interestExpense incluye los intereses del leasing financiero.
    debt = positive(balance[0].get("totalDebt")) if balance else None
    beta = num(_first(fetch("profile", symbol=symbol)).get("beta"))
    rf = risk_free_rate()
    k = cost_of_equity(rf, beta)
    rd = cost_of_debt(pnl_ttm["interestExpense"], debt)
    t = effective_tax_rate(pnl_ttm["incomeTaxExpense"], pnl_ttm["incomeBeforeTax"], tax_exempt)
    roe = num(km.get("returnOnEquityTTM"))
    dso, dio, dpo = (num(km.get(f"daysOf{x}OutstandingTTM")) for x in ("Sales", "Inventory", "Payables"))
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
            change_1d=relative(num(prices[-1].get("price")), num(prices[-2].get("price"))) if len(prices) > 1 else None,
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
            roe=roe,
            current_ratio=positive(km.get("currentRatioTTM")),
            beta=beta,
            asset_turnover=num(ttm.get("assetTurnoverTTM")),
            dso=dso,
            dio=dio,
            dpo=dpo,
            cash_conversion_cycle=cash_cycle(dso, dio, dpo, num(km.get("cashConversionCycleTTM"))),
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
        risk_free_rate=rf,
        cost_of_equity=k,
        roe_spread=roe - k if roe is not None and k is not None else None,
        cost_of_debt=rd,
        tax_rate=t,
        cost_of_debt_after_tax=rd * (1 - t) if rd is not None and t is not None else None,
    )


# --- Yahoo Finance (posiciones sin cobertura FMP) ---------------------------

def yahoo_report(symbol: str, data, now: float) -> Report:
    """Report con precio, variación diaria y volumen del chart de Yahoo; múltiplos y balance quedan None."""
    try:
        result = data["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        rows = list(zip(result["timestamp"], quote["close"], quote["volume"]))
        regular = result["meta"].get("currentTradingPeriod", {}).get("regular", {})
    except (KeyError, IndexError, TypeError, AttributeError):
        raise RuntimeError(f"Yahoo {symbol}: respuesta sin serie de precios") from None
    start, end = num(regular.get("start")), num(regular.get("end"))
    # La sesión en curso lleva volumen parcial: sólo cuentan sesiones cerradas, como en el EOD de FMP.
    if rows and start is not None and end is not None and num(rows[-1][0]) is not None and start <= rows[-1][0] and now < end:
        rows.pop()
    sessions = [(num(c), num(v)) for _, c, v in rows if num(c) is not None]
    closes, volumes = [c for c, _ in sessions], [v for _, v in sessions]
    volume, avg30 = (volumes[-1] if volumes else None), avg_volume(volumes)
    metrics = dict.fromkeys(f.name for f in fields(Metrics))
    report = dict.fromkeys(f.name for f in fields(Report))
    return Report(**{**report, "peers": (), "volume_divergence": relative(volume, avg30), "metrics": Metrics(**{
        **metrics, "symbol": symbol, "net_cash": False, "price": closes[-1] if closes else None,
        "change_1d": relative(closes[-1], closes[-2]) if len(closes) > 1 else None,
        "volume": volume, "volume_avg_30d": avg30})})


def yahoo(symbol: str) -> Report:
    """Precio y volumen de los últimos 3 meses (1mo sólo trae ~21 sesiones: no llega para la media de 30)."""
    url = YAHOO_CHART + urllib.parse.quote(symbol) + "?interval=1d&range=3mo"
    req = urllib.request.Request(url, headers={"User-Agent": YAHOO_UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        e.close()
        raise RuntimeError(f"Yahoo {symbol}: HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Yahoo {symbol}: sin conexión ({e.reason})") from None
    return yahoo_report(symbol, data, time.time())


if __name__ == "__main__":
    from notify import load_env  # sólo el CLI; el módulo no depende de notify

    load_env()
    report = analyze(sys.argv[1], sys.argv[2:] or None)
    print(json.dumps(asdict(report), indent=2))
    print(f"llamadas FMP: {calls_made}", file=sys.stderr)
