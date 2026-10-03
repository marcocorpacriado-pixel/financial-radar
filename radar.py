"""Orquestador: alertas de 10-K/10-Q nuevos y resumen periódico de la cartera. Spec: SPEC-radar.md."""
import argparse
import hashlib
import json
import os
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import analyst
import notify
import quant
import sec_mdna

ROOT = Path(__file__).resolve().parent
PORTFOLIO = Path("portfolio.toml")
STATE = Path("state.json")
FORMS = ("10-K", "10-Q")
SENT_TTL = timedelta(days=180)
_POSITION_KEYS = {"ticker", "ticker_fmp", "ticker_sec", "peers", "sec_enabled"}


@dataclass(frozen=True)
class Position:
    ticker: str
    ticker_fmp: str
    ticker_sec: str
    peers: tuple[str, ...]
    sec_enabled: bool


# --- Configuración y estado -----------------------------------------------

def load_portfolio(path: Path = PORTFOLIO) -> tuple[int, list[Position]]:
    """(summary_every_days, posiciones) con validación estricta de portfolio.toml."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    if unknown := set(data) - {"summary_every_days", "positions"}:
        raise ValueError(f"{path}: claves desconocidas {sorted(unknown)}")
    days = data.get("summary_every_days", 12)
    if type(days) is not int or days < 1:
        raise ValueError(f"{path}: summary_every_days debe ser un entero >= 1")
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
        peers, enabled = raw.get("peers", []), raw.get("sec_enabled", True)
        if not (isinstance(fmp, str) and isinstance(sec, str) and isinstance(enabled, bool)
                and isinstance(peers, list) and all(isinstance(p, str) for p in peers)):
            raise ValueError(f"{where}: ticker_fmp/ticker_sec deben ser texto, peers una lista de textos "
                             "y sec_enabled true/false")
        positions.append(Position(ticker, fmp, sec, tuple(peers), enabled))
    if not positions:
        raise ValueError(f"{path}: no hay posiciones")
    return days, positions


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


def format_quant(report: quant.Report, ticker: str) -> list[str]:
    """Bloque de métricas: lo muestra el resumen y lo recibe analyst como <financial_metrics>."""
    m, r = report.metrics, report
    growth = "n/d" if m.revenue_growth_yoy is None else f"{_delta(m.revenue_growth_yoy)} YoY ({m.last_period})"
    leverage = "caja neta" if m.net_cash else _n(m.net_debt_to_ebitda, suffix="x")
    return [
        f"{ticker} {_n(m.price, 2)} · volumen {_delta(r.volume_divergence)} vs media 30 sesiones",
        f"P/E {_n(m.pe_ttm)} (hist {_delta(r.pe_vs_hist)}, pares {_delta(r.pe_vs_peers)}) · "
        f"EV/EBITDA {_n(m.ev_ebitda_ttm)} (hist {_delta(r.ev_ebitda_vs_hist)})",
        f"Margen op. {_pct(m.operating_margin_ttm)} (pares {_pp(r.margin_vs_peers)}) · Ingresos {growth} · "
        f"FCF yield {_pct(m.fcf_yield)}",
        f"Deuda neta/EBITDA {leverage} · D/E {_n(m.debt_to_equity, 2)} · Cobertura int. {_n(m.interest_coverage, suffix='x')} · "
        f"ROIC {_pct(m.roic)} · ROE {_pct(m.roe)} · Liquidez {_n(m.current_ratio, 2)}",
    ]


def _header(pos: Position, f: sec_mdna.Filing) -> str:
    return f"Nuevo {f.form} · {pos.ticker} · periodo {f.report_date} (presentado {f.filing_date})"


# --- Ejecución --------------------------------------------------------------

def run(positions: list[Position], days: int, state: dict, now: datetime, *,
        force_summary: bool = False, dry_run: bool = False, state_path: Path = STATE) -> int:
    """Alertas de filings nuevos y, si toca, resumen consolidado. Devuelve 0 o 1 (hubo fallos)."""
    errors = 0
    latest: dict[str, list[sec_mdna.Filing]] = {}  # filings más recientes vistos en esta ejecución

    def log(msg):
        print(msg, file=sys.stderr)

    def persist():
        if not dry_run:
            save_state(state, state_path)

    def deliver(text, silent, dedupe=True):
        digest = hashlib.sha256(text.encode()).hexdigest()
        if dedupe and digest in state["sent"]:
            log("Mensaje idéntico ya enviado: no se reenvía")
            return
        if dry_run:
            print(f"--- [dry-run] silent={silent} ---\n{text}\n")
        else:
            notify.send(text, silent=silent)
        state["sent"][digest] = now.isoformat()

    def metrics(pos):
        return format_quant(quant.analyze(pos.ticker_fmp, list(pos.peers)), pos.ticker)

    def analyze_filing(pos, filing, metrics_text):
        current, previous = sec_mdna.fetch_mdna(pos.ticker_sec, filing.form)
        a = analyst.summarize_mdna(current.text, previous.text if previous else None, metrics_text)
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
        blocks = []
        for pos in positions:
            try:
                lines = metrics(pos)
            except Exception as e:
                errors += 1
                blocks.append(f"{pos.ticker}: sin datos FMP ({e})")
                continue
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
                lines.append(f"Último filing: {la['form']} {la['filing_date']} · "
                             f"Riesgo/Cat.: {la['catalyst'] or 'sin catalizadores verificados'}")
            blocks.append("\n  ".join(lines))
        text = f"Radar de cartera · {now:%Y-%m-%d} (cada {days} días)\n\n" + "\n\n".join(blocks)
        try:
            deliver(text, silent=True, dedupe=not force_summary)
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
    notify.load_env()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    days, positions = load_portfolio()
    return run(positions, days, load_state(), datetime.now(timezone.utc),
               force_summary=args.force_summary, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
