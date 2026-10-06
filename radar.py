"""Orquestador: alertas de 10-K/10-Q nuevos y resumen periódico de la cartera. Spec: SPEC-radar.md."""
import argparse
import hashlib
import html
import json
import os
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import analyst
import news
import notify
import quant
import sec_mdna

ROOT = Path(__file__).resolve().parent
PORTFOLIO = Path("portfolio.toml")
STATE = Path("state.json")
FORMS = ("10-K", "10-Q")
SENT_TTL = timedelta(days=180)
DISTORTED_MARGIN = 5.0  # |margen operativo| > 500 %: red de seguridad para posiciones sin distorted_metrics
_LABELS = ("Val", "Op", "Eficiencia", "Solvencia", "Fundamentales", "Filing")
_POSITION_KEYS = {"ticker", "ticker_fmp", "ticker_sec", "peers", "sec_enabled", "distorted_metrics", "tax_exempt",
                  "fmp_enabled", "name", "ticker_yahoo", "currency", "news_query", "news_lang"}
NO_FUNDAMENTALS = "Fundamentales: n/d (sin cobertura FMP; precio y volumen de Yahoo Finance)"


@dataclass(frozen=True)
class Position:
    ticker: str
    ticker_fmp: str
    ticker_sec: str
    peers: tuple[str, ...]
    sec_enabled: bool
    distorted_metrics: bool = False  # tesorería en activos digitales (MSTR): margen, ROE y spread no representativos
    tax_exempt: bool = False         # régimen de tonelaje (navieras): t = 0 en el coste neto de la deuda
    fmp_enabled: bool = True         # false: sólo precio y volumen de Yahoo (ticker_yahoo), sin FMP
    name: str | None = None          # razón social: desambigua el ticker ante Haiku
    ticker_yahoo: str | None = None
    currency: str | None = None      # se muestra junto al precio
    news_query: str | None = None    # búsqueda de Google News; por defecto "<ticker> stock"
    news_lang: str = "en"            # edición de Google News (news.LOCALES)


@dataclass(frozen=True)
class Config:
    summary_every_days: int
    analysis_model: str
    summary_model: str
    positions: list[Position]


# --- Configuración y estado -----------------------------------------------

def load_portfolio(path: Path = PORTFOLIO) -> Config:
    """Configuración de portfolio.toml con validación estricta."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    if unknown := set(data) - {"summary_every_days", "analysis_model", "summary_model", "positions"}:
        raise ValueError(f"{path}: claves desconocidas {sorted(unknown)}")
    days = data.get("summary_every_days", 14)
    if type(days) is not int or days < 1:
        raise ValueError(f"{path}: summary_every_days debe ser un entero >= 1")
    models = data.get("analysis_model", analyst.MODEL), data.get("summary_model", analyst.NEWS_MODEL)
    if not all(isinstance(m, str) and m for m in models):
        raise ValueError(f"{path}: analysis_model y summary_model deben ser nombres de modelo")
    positions = []
    for i, raw in enumerate(data.get("positions", []), 1):
        where = f"{path}, posición {i}"
        if unknown := set(raw) - _POSITION_KEYS:
            raise ValueError(f"{where}: claves desconocidas {sorted(unknown)}")
        ticker = raw.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            raise ValueError(f"{where}: falta ticker")
        if any(p.ticker == ticker for p in positions):
            raise ValueError(f"{where}: ticker {ticker} duplicado")
        fmp, sec = raw.get("ticker_fmp", ticker), raw.get("ticker_sec", ticker)
        peers, flags = raw.get("peers", []), [raw.get(k, k == "sec_enabled") for k in ("sec_enabled", "distorted_metrics", "tax_exempt")]
        if not (isinstance(fmp, str) and isinstance(sec, str) and all(isinstance(f, bool) for f in flags)
                and isinstance(peers, list) and all(isinstance(p, str) for p in peers)):
            raise ValueError(f"{where}: ticker_fmp/ticker_sec deben ser texto, peers una lista de textos "
                             "y sec_enabled/distorted_metrics/tax_exempt true/false")
        texts = {k: raw.get(k) for k in ("name", "ticker_yahoo", "currency", "news_query")}
        fmp_enabled, lang = raw.get("fmp_enabled", True), raw.get("news_lang", "en")
        if not (isinstance(fmp_enabled, bool) and all(v is None or (isinstance(v, str) and v) for v in texts.values())):
            raise ValueError(f"{where}: name/ticker_yahoo/currency/news_query deben ser texto y fmp_enabled true/false")
        if lang not in news.LOCALES:
            raise ValueError(f"{where}: news_lang debe ser uno de {sorted(news.LOCALES)}")
        if not fmp_enabled and not texts["ticker_yahoo"]:
            raise ValueError(f"{where}: fmp_enabled = false necesita ticker_yahoo")
        positions.append(Position(ticker, fmp, sec, tuple(peers), *flags, fmp_enabled=fmp_enabled, news_lang=lang, **texts))
    if not positions:
        raise ValueError(f"{path}: no hay posiciones")
    return Config(days, *models, positions)


def load_state(path: Path = STATE) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    state.setdefault("tickers", {})
    state.setdefault("sent", {})
    return state


def save_state(state: dict, path: Path = STATE) -> None:
    """Escritura atómica: un corte a mitad no deja un state.json corrupto."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def prune_sent(state: dict, now: datetime) -> None:
    state["sent"] = {h: t for h, t in state["sent"].items() if now - datetime.fromisoformat(t) < SENT_TTL}


def summary_due(state: dict, now: datetime, days: int) -> bool:
    last = state.get("last_summary")
    return last is None or now - datetime.fromisoformat(last) >= timedelta(days=days)


# --- Formato ----------------------------------------------------------------

def _n(x: float | None, digits: int = 1, suffix: str = "") -> str:
    return "n/d" if x is None else f"{x:.{digits}f}{suffix}"


def _pct(x: float | None) -> str:
    return "n/d" if x is None else f"{x * 100:.1f}%"


def _delta(x: float | None) -> str:
    return "n/d" if x is None else f"{x * 100:+.0f}%"


def _pp(x: float | None) -> str:
    return "n/d" if x is None else f"{x * 100:+.1f} pp"


def format_quant(report: quant.Report, ticker: str, distorted_metrics: bool = False,
                 currency: str | None = None) -> list[str]:
    """Bloque de métricas: lo muestra el resumen y lo recibe analyst como <financial_metrics>."""
    m, r = report.metrics, report
    growth = "n/d" if m.revenue_growth_yoy is None else f"{_delta(m.revenue_growth_yoy)} YoY ({m.last_period})"
    leverage = "caja neta" if m.net_cash else _n(m.net_debt_to_ebitda, suffix="x")
    if distorted_metrics:
        note = "no representativo por tesorería en activos digitales"
    elif m.operating_margin_ttm is not None and abs(m.operating_margin_ttm) > DISTORTED_MARGIN:
        note = "distorsionado (|margen| > 500 %)"
    else:
        note = None
    if note:
        value = f" ⚠️ {note}"
    elif r.roe_spread is None:
        value = ""
    else:
        value = ", crea valor" if r.roe_spread > 0 else ", destruye valor"
    dso = "DSO no reportado" if m.dso == 0 else f"DSO {_n(m.dso, 0, 'd')}"  # quant deja el CCC en None
    if note:
        margin = f"⚠️ margen {_pct(m.operating_margin_ttm)}: {note}; sin comparación con pares"
    else:
        margin = f"margen {_pct(m.operating_margin_ttm)} (pares {_pp(r.margin_vs_peers)})"
    return [
        f"{ticker} {_n(m.price, 2)}{' ' + currency if currency else ''}"
        f"{'' if m.change_1d is None else f' ({round(m.change_1d * 100, 1) or 0.0:+.1f}% día)'} · "  # sin '-0.0%'
        f"volumen {_delta(r.volume_divergence)} vs media 30 sesiones",
        f"Val: P/E {_n(m.pe_ttm)} (hist {_delta(r.pe_vs_hist)}, pares {_delta(r.pe_vs_peers)}) · "
        f"EV/EBITDA {_n(m.ev_ebitda_ttm)} (hist {_delta(r.ev_ebitda_vs_hist)})",
        f"Op: {margin} · ingresos {growth} · FCF yield {_pct(m.fcf_yield)}",
        f"Eficiencia: ROE {_pct(m.roe)} (spread {_pp(r.roe_spread)} vs k={_pct(r.cost_of_equity)}{value}) · "
        f"ROIC {_pct(m.roic)} · Rot. {_n(m.asset_turnover, 2, 'x')} · CCC {_n(m.cash_conversion_cycle, 0, 'd')} "
        f"({dso} | DIO {_n(m.dio, 0, 'd')} | DPO {_n(m.dpo, 0, 'd')})",
        f"Solvencia: Deuda Neta/EBITDA {leverage} · D/E {_n(m.debt_to_equity, 2)} · "
        f"rd {_pct(r.cost_of_debt)} (neto {_pct(r.cost_of_debt_after_tax)}, t={_pct(r.tax_rate)}) · Cobertura {_n(m.interest_coverage, suffix='x')} · "
        f"Liq. {_n(m.current_ratio, 2)}",
    ]


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _html_block(ticker: str, lines: list[str]) -> list[str]:
    """Líneas planas de una posición → HTML de Telegram: ticker en negrita y etiquetas como viñetas."""
    out = [f"🔹 <b>{_esc(ticker)}</b>{_esc(lines[0].removeprefix(ticker))}"]
    for line in lines[1:]:
        label, sep, body = line.partition(": ")
        out.append(f"• <b>{label}:</b> {_esc(body)}" if sep and label in _LABELS else f"• {_esc(line)}")
    return out


def _header(pos: Position, f: sec_mdna.Filing) -> str:
    return f"Nuevo {f.form} · {pos.ticker} · periodo {f.report_date} (presentado {f.filing_date})"


def _candidate(h: news.Headline) -> str:
    """Titular tal como lo ve Haiku."""
    return f"{h.title} ({h.source}, {h.published:%d-%m})"


def _headline_line(h: news.Headline) -> str:
    title = h.title if len(h.title) <= 120 else h.title[:119].rstrip() + "…"
    return f"   ◦ {_esc(h.source)}, {h.published:%d-%m}: {_esc(title)}"


def _news_lines(headlines: list[news.Headline] | None, digest: analyst.NewsDigest | None, synthesized: bool) -> list[str]:
    """Bloque HTML de noticias; cierra cada posición (también las que no tienen filings SEC)."""
    if headlines is None:
        return ["💡 <b>Noticias:</b> no disponibles"]
    if not synthesized and headlines:  # Haiku falló: los primeros del feed, sin filtrar
        return ["💡 <b>Noticias</b> (sin síntesis):"] + [_headline_line(h) for h in headlines[:3]]
    if digest is None:
        return ["💡 <b>Noticias:</b> sin novedades materiales"]
    return [f"💡 <b>Noticias:</b> {_esc(digest.summary)}"] + [_headline_line(headlines[i]) for i in digest.picks]


# --- Ejecución --------------------------------------------------------------

def run(positions: list[Position], days: int, state: dict, now: datetime, *,
        force_summary: bool = False, dry_run: bool = False, state_path: Path = STATE,
        analysis_model: str = analyst.MODEL, summary_model: str = analyst.NEWS_MODEL) -> int:
    """Alertas de filings nuevos y, si toca, resumen consolidado. Devuelve 0 o 1 (hubo fallos)."""
    errors = 0
    latest: dict[str, list[sec_mdna.Filing]] = {}  # filings más recientes vistos en esta ejecución

    def log(msg):
        print(msg, file=sys.stderr)

    def persist():
        if not dry_run:
            save_state(state, state_path)

    def deliver(text, silent, dedupe=True, as_html=False):
        digest = hashlib.sha256(text.encode()).hexdigest()
        if dedupe and digest in state["sent"]:
            log("Mensaje idéntico ya enviado: no se reenvía")
            return
        if dry_run:
            print(f"--- [dry-run] silent={silent} html={as_html} ---\n{text}\n")
        else:
            notify.send(text, silent=silent, html=as_html)
        state["sent"][digest] = now.isoformat()

    def metrics(pos):
        if not pos.fmp_enabled:
            return format_quant(quant.yahoo(pos.ticker_yahoo), pos.ticker, currency=pos.currency)[:1] + [NO_FUNDAMENTALS]
        return format_quant(quant.analyze(pos.ticker_fmp, list(pos.peers), tax_exempt=pos.tax_exempt),
                            pos.ticker, pos.distorted_metrics)

    def analyze_filing(pos, filing, metrics_text):
        current, previous = sec_mdna.fetch_mdna(pos.ticker_sec, filing.form)
        a = analyst.summarize_mdna(current.text, previous.text if previous else None, metrics_text, model=analysis_model)
        f = current.filing
        rendered = analyst.render(a, _header(pos, f))
        record = {"form": f.form, "accession": f.accession, "filing_date": f.filing_date, "url": f.url,
                  "lines": rendered.split("\n")[1:],
                  "catalyst": a.key_risks_and_catalysts[0].summary if a.key_risks_and_catalysts else None}
        return f"{rendered}\n{f.url}", record

    prune_sent(state, now)

    # 1. Filings nuevos → alerta inmediata.
    for pos in positions:
        if not pos.sec_enabled:
            continue
        tstate = state["tickers"].setdefault(pos.ticker, {})
        seen = tstate.setdefault("accessions", {})
        for form in FORMS:
            try:
                found = sec_mdna.filings(pos.ticker_sec, form)
            except Exception as e:
                errors += 1
                log(f"{pos.ticker} {form}: {e}")
                continue
            if not found:
                continue
            filing = found[0]
            latest.setdefault(pos.ticker, []).append(filing)
            if form not in seen:  # primera vez: línea base, sin alerta
                seen[form] = filing.accession
                persist()
                log(f"{pos.ticker} {form}: línea base {filing.accession}")
                continue
            if seen[form] == filing.accession:
                continue
            record = None
            try:
                try:
                    metrics_text = "\n".join(metrics(pos))
                except Exception as e:
                    log(f"{pos.ticker}: métricas no disponibles para el análisis ({e})")
                    metrics_text = None
                text, record = analyze_filing(pos, filing, metrics_text)
            except Exception as e:
                errors += 1
                log(f"{pos.ticker} {form}: análisis fallido ({e})")
                text = f"{_header(pos, filing)}\nAnálisis no disponible: {e}\n{filing.url}"
            try:
                deliver(text, silent=False)
            except Exception as e:
                errors += 1
                log(f"{pos.ticker} {form}: envío fallido, se reintentará ({e})")
                continue
            seen[form] = filing.accession  # sólo tras un envío correcto
            if record:
                tstate["last_analysis"] = record
            persist()

    # 2. Resumen consolidado.
    if force_summary or summary_due(state, now, days):
        blocks: list[tuple[Position, list[str]]] = []
        headlines: dict[str, list[news.Headline] | None] = {}
        for pos in positions:
            try:
                lines = metrics(pos)
            except Exception as e:
                errors += 1
                lines = [f"{pos.ticker}: sin datos de mercado ({e})"]
            else:
                tstate = state["tickers"].setdefault(pos.ticker, {})
                if pos.sec_enabled and "last_analysis" not in tstate and latest.get(pos.ticker):
                    newest = max(latest[pos.ticker], key=lambda f: f.filing_date)
                    try:  # primer análisis del ticker: se guarda sin alerta
                        _, tstate["last_analysis"] = analyze_filing(pos, newest, "\n".join(lines))
                        persist()
                    except Exception as e:
                        errors += 1
                        log(f"{pos.ticker}: análisis inicial fallido ({e})")
                if la := tstate.get("last_analysis"):
                    lines.append(f"Filing: {la['form']} {la['filing_date']} · "
                                 f"Riesgo/Cat.: {la['catalyst'] or 'sin catalizadores verificados'}")
            try:  # las noticias son un complemento: sus fallos no cambian el código de salida
                headlines[pos.ticker] = news.fetch_news(pos.ticker, limit=10, query=pos.news_query, lang=pos.news_lang)
            except Exception as e:
                headlines[pos.ticker] = None
                log(f"{pos.ticker}: noticias no disponibles ({e})")
            blocks.append((pos, lines))
        try:
            digests = analyst.summarize_news({t: [_candidate(h) for h in hs] for t, hs in headlines.items() if hs},
                                             model=summary_model, names={p.ticker: p.name for p in positions if p.name})
            synthesized = True
        except Exception as e:
            digests, synthesized = {}, False
            log(f"Síntesis de noticias no disponible ({e})")
        text = f"📊 <b>Radar de cartera</b> · {now:%Y-%m-%d} (cada {days} días)\n\n" + "\n\n".join(
            "\n".join(_html_block(pos.ticker, lines) + _news_lines(headlines[pos.ticker], digests.get(pos.ticker), synthesized))
            for pos, lines in blocks)
        try:
            deliver(text, silent=True, dedupe=not force_summary, as_html=True)
            state["last_summary"] = now.isoformat()
        except Exception as e:
            errors += 1
            log(f"Resumen: envío fallido ({e})")

    state["last_check"] = now.isoformat()
    persist()
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="radar.py", description="Radar financiero de la cartera (SPEC-radar.md).")
    parser.add_argument("--check", action="store_true", help="filings nuevos y resumen si toca (por defecto)")
    parser.add_argument("--force-summary", action="store_true", help="envía ahora el resumen consolidado")
    parser.add_argument("--dry-run", action="store_true", help="imprime en vez de enviar y no escribe state.json")
    args = parser.parse_args(argv)
    os.chdir(ROOT)  # .env, cache/, state.json y portfolio.toml funcionan desde el Programador de tareas
    if sys.stdout is None or sys.stderr is None:  # pythonw.exe (tarea programada): sin consola → radar.log
        # ponytail: radar.log crece sin rotación (pocas líneas por ejecución); rotar si llega a molestar
        log_file = open(ROOT / "radar.log", "a", encoding="utf-8")
        sys.stdout, sys.stderr = sys.stdout or log_file, sys.stderr or log_file
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    print(f"=== {datetime.now():%Y-%m-%d %H:%M} radar.py {' '.join(sys.argv[1:] if argv is None else argv)}",
          file=sys.stderr)
    notify.load_env()
    cfg = load_portfolio()
    return run(cfg.positions, cfg.summary_every_days, load_state(), datetime.now(timezone.utc),
               force_summary=args.force_summary, dry_run=args.dry_run,
               analysis_model=cfg.analysis_model, summary_model=cfg.summary_model)


if __name__ == "__main__":
    sys.exit(main())
