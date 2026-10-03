# Spec: quant

Estado: **APROBADO** (2026-10-03)
Módulo hoja, sin dependencias. Convenciones comunes en `CAPABILITY_MAP.md`.

## Objetivo

Dar a `radar` (y al resumen periódico) las métricas de cada ticker **en contexto**: un múltiplo sólo dice algo comparado con el histórico de la propia empresa y con la mediana de sus competidores. `quant` devuelve números ya calculados (fracciones, sin formatear); presentar y redactar es trabajo de `radar`/`analyst`.

## Interfaz pública

```python
def analyze(symbol: str, peers: list[str] | None = None) -> Report:
    """Métricas de symbol con contexto histórico y frente a su grupo de pares."""
```

- `peers=None` → los pide a FMP (`stock-peers`). Lista explícita (vendrá de `portfolio.toml` vía `radar`) → se usa tal cual y no se llama a `stock-peers`.
- Configuración: `FMP_API_KEY` (entorno / `.env`).
- CLI de verificación: `python -m quant AAPL [PEER ...]` imprime el `Report` como JSON.

```python
@dataclass(frozen=True)
class Metrics:
    symbol: str
    price: float | None                 # último cierre
    pe_ttm: float | None
    ev_ebitda_ttm: float | None
    operating_margin_last: float | None # último periodo reportado (trimestre, o FY si hubo fallback)
    operating_margin_ttm: float | None  # base de comparación con peers
    revenue_growth_yoy: float | None    # último periodo vs mismo periodo del año anterior
    last_period: str | None             # "Q3 2026", o "FY 2025" si hubo fallback
    fcf_ttm: float | None
    fcf_yield: float | None
    volume: float | None                # última sesión
    volume_avg_30d: float | None        # media de las 30 sesiones ANTERIORES
    # Balance y rentabilidad del capital (añadido 2026-10-03; mismos payloads TTM, 0 llamadas extra)
    net_debt_to_ebitda: float | None    # None si EBITDA <= 0 o si hay caja neta
    net_cash: bool                      # True sólo si el ratio es < 0 Y el EBITDA es > 0
    debt_to_equity: float | None        # apalancamiento visible aunque el EBITDA sea negativo (MSTR)
    interest_coverage: float | None     # EBIT / intereses; el negativo se conserva (no cubre los intereses)
    roic: float | None                  # el negativo se conserva (destruye valor)
    roe: float | None
    current_ratio: float | None

@dataclass(frozen=True)
class Report:
    metrics: Metrics
    pe_hist_median: float | None
    ev_ebitda_hist_median: float | None
    pe_vs_hist: float | None            # +0.20 = cotiza con 20 % de prima sobre su mediana histórica
    ev_ebitda_vs_hist: float | None
    volume_divergence: float | None     # +1.5 = volumen 2,5× la media de 30 sesiones
    peers: tuple[str, ...]              # peers que aportaron dato
    peer_median_pe: float | None
    peer_median_operating_margin: float | None
    pe_vs_peers: float | None           # prima/descuento relativo frente a la mediana de pares
    margin_vs_peers: float | None       # diferencia en puntos: 0.30 - 0.25 = +0.05
```

Unidades: márgenes, crecimientos, yields, primas y divergencias como **fracción** (0.25 = 25 %). `radar` formatea.

### Balance y rentabilidad del capital

| Campo | Fuente (TTM) | Regla |
|---|---|---|
| `net_debt_to_ebitda`, `net_cash` | `key-metrics-ttm.netDebtToEBITDATTM`; signo del EBITDA a partir de `evToEBITDATTM` | EBITDA ≤ 0 → `None` y `net_cash=False`: el ratio no significa nada, y su signo negativo **no** es caja neta (MSTR: −0,23 con EBITDA negativo). EBITDA > 0 y ratio < 0 → `None` y `net_cash=True`. |
| `debt_to_equity` | `ratios-ttm.debtToEquityRatioTTM` | `positive()`: con patrimonio negativo no es interpretable. Mantiene visible el apalancamiento cuando el ratio sobre EBITDA es `None`. |
| `interest_coverage` | `ratios-ttm.interestCoverageRatioTTM` | `num()`: **el negativo se conserva**, porque significa que el EBIT no cubre los intereses (MSTR: −262,9) y es la señal de riesgo que se busca. |
| `roic`, `roe` | `key-metrics-ttm.returnOnInvestedCapitalTTM` / `returnOnEquityTTM` | `num()`: el negativo se conserva, porque un ROIC que cae o es negativo es justo lo que el LLM debe cruzar con el MD&A. |
| `current_ratio` | `key-metrics-ttm.currentRatioTTM` | `positive()` |

"Negativo → None" se aplica sólo donde el negativo no tiene sentido económico (múltiplos, apalancamiento sobre EBITDA negativo, D/E con patrimonio negativo). En cobertura y rentabilidades, el negativo es información.

## Reglas de cálculo (funciones puras, todas con test)

| Función | Regla |
|---|---|
| `num(x)` | `float` si es `int`/`float` finito (no `bool`); si no, `None`. Todo valor de FMP pasa por aquí. |
| `positive(x)` | `num(x)` si > 0, si no `None`. Se aplica a P/E y EV/EBITDA: un múltiplo ≤ 0 (pérdidas, EBITDA negativo) no es significativo. |
| `median(values)` | `statistics.median` de los valores no `None`; `None` si no queda ninguno. |
| `relative(current, ref)` | `current / ref - 1`; `None` si alguno es `None` o `ref ≤ 0`. Prima/descuento y divergencia de volumen. |
| `yoy(current, year_ago)` | `current / year_ago - 1`; `None` si `year_ago` es `None` o ≤ 0. |
| `avg_volume(volumes)` | Serie en orden cronológico. Media de las 30 sesiones previas a la última (sin incluirla, para no sesgar la media con el propio pico). `None` si hay < 31 sesiones. |

- **Mediana histórica**: mediana de los últimos 5 ejercicios fiscales (P/E y EV/EBITDA anuales, filtrados con `positive`). Mediana y no media: un año con beneficios deprimidos dispara el P/E y distorsiona la media.
- **Mediana de pares**: P/E TTM y margen operativo TTM de cada peer. Se excluye el propio ticker; P/E ≤ 0 se excluye. Con menos de **3 peers con dato** (`MIN_PEERS`) la mediana es `None`: una "mediana" de 1–2 empresas no es un benchmark.
- **Margen vs pares**: se compara `operating_margin_ttm` (TTM contra TTM), nunca el trimestral, para no mezclar estacionalidad.
- **Margen y crecimiento del último periodo**: `income-statement` trimestral (fila 0 vs fila 4 = mismo trimestre del año anterior). Si FMP responde 402/403 o lista vacía → fallback automático a `period=annual&limit=2` (fila 0 vs fila 1), sin abortar; `last_period` indica qué base se usó.
- **FCF**: `fcf_ttm = fcf_yield × market_cap` a partir de `key-metrics-ttm` (evita una llamada extra al cash-flow statement).

## Fuente de datos: FMP stable API

Base `https://financialmodelingprep.com/stable/`. `/v4/stock_peers` y el resto de v3/v4 son **legacy**; se usa su equivalente `stable`.

| Endpoint | Uso | Campos (verificados con la key real el 2026-10-03) |
|---|---|---|
| `ratios-ttm?symbol=` | P/E TTM, margen op. TTM (ticker y peers) | `priceToEarningsRatioTTM`, `operatingProfitMarginTTM` |
| `key-metrics-ttm?symbol=` | EV/EBITDA TTM, FCF yield, market cap | `evToEBITDATTM`, `freeCashFlowYieldTTM`, `marketCap` |
| `ratios?symbol=&period=annual&limit=5` | P/E histórico | `priceToEarningsRatio` |
| `key-metrics?symbol=&period=annual&limit=5` | EV/EBITDA histórico | `evToEBITDA` |
| `income-statement?symbol=&period=quarter&limit=5` | margen op. y crecimiento YoY del último trimestre | `revenue`, `operatingIncome`, `period`, `fiscalYear` (más reciente primero) |
| `historical-price-eod/light?symbol=&from=` | precio y volumen (últimos ~60 días naturales) | `date`, `price`, `volume` (más reciente primero: se ordena por `date`) |
| `stock-peers?symbol=` | lista de pares (sólo si no vienen en config) | `symbol` |

**Presupuesto de llamadas** (plan gratuito: 250/día): 6 por ticker (+1 si hay fallback a FY) + 1 (`stock-peers`) + 1 por peer (`ratios-ttm`), con `MAX_PEERS = 5` → ≤ 12 por ticker sin caché, ~20 tickers por ejecución. Suficiente para un resumen cada 7–15 días.

**Caché**: todas las GET pasan por una función memoizada (`functools.cache`) durante la ejecución. Peers compartidos entre tickers de la cartera, o un peer que también está en cartera, se piden una sola vez. Un contador de llamadas (`calls_made`) permite a `radar` registrar el consumo.

## Errores y valores nulos

- Falta `FMP_API_KEY` → `RuntimeError` que nombra la variable.
- Error HTTP o `{"Error Message": ...}` en una llamada **del ticker principal** → `RuntimeError` con el mensaje de FMP. Nunca contiene la `apikey` (va en la query string: no se formatea la URL ni se encadena la excepción original). Timeout 10 s.
- Fallo en la llamada de **un peer** → ese peer se descarta (no aborta el análisis); queda fuera de `Report.peers`.
- Campo ausente, `null`, no numérico o lista vacía → `None` en ese campo y en todo lo derivado de él. Nunca `0` como sustituto.

## Tests (`tests/test_quant.py`, sin red)

- Puras: `num` (None, str, bool, NaN, inf, int); `positive` (negativo, cero); `median` (con None, vacía, par/impar); `relative` (prima, descuento, ref 0/None); `yoy`; `avg_volume` (excluye la última sesión, < 31 sesiones → None).
- `analyze` con la capa HTTP parcheada y payloads simulados por endpoint:
  - Report completo con valores esperados calculados a mano (prima histórica, mediana de pares, margen vs pares, FCF, divergencia de volumen, crecimiento YoY).
  - Peers por config → no se llama a `stock-peers`; el propio ticker se excluye de los peers.
  - Peer con P/E negativo o sin datos → excluido de la mediana; < 3 peers válidos → medianas `None`.
  - Peer cuya llamada falla → descartado sin abortar.
  - Caché: dos `analyze` que comparten peer → una sola llamada a ese `ratios-ttm`.
  - Trimestral con 403 o lista vacía → fallback a FY (margen, crecimiento y `last_period = "FY …"`).
  - Error de FMP en el ticker principal → `RuntimeError` sin la `apikey` en el mensaje.
  - Balance:
    - campos completos;
    - caja neta (ratio < 0 y EBITDA > 0);
    - caso MSTR: ratio < 0 con EBITDA < 0 → `None` y sin caja neta;
    - cobertura, ROIC y ROE negativos conservados;
    - D/E ≤ 0 → `None`;
    - el número de llamadas no cambia.
  - Falta `FMP_API_KEY` → `RuntimeError` con su nombre.

## Criterios de éxito

- [x] `.venv\Scripts\python -m unittest discover -s tests -v` en verde (incluido `notify`).
- [x] Task 0: smoke test con la key real confirma endpoints y nombres de campo de la tabla (≈ 8 llamadas).
- [x] `.venv\Scripts\python -m quant AAPL` devuelve un Report con todos los campos no nulos y ≤ 12 llamadas.
- [x] Los valores de AAPL coinciden en orden de magnitud con los que muestra la web de FMP (P/E, márgenes).
- [x] Ninguna salida ni error contiene la `apikey`.

## Fuera de alcance

Caché en disco entre ejecuciones (las ejecuciones son cada 7–15 días; los datos ya habrán cambiado), percentiles o z-scores, ajuste sectorial de pares, divisas no USD, umbrales de alerta (decide `radar`), formateo de texto.

## Decisiones

1. `FMP_API_KEY` en `.env`; endpoints y campos verificados (todos HTTP 200, `period=quarter` disponible en esta cuenta).
2. Fallback trimestral → FY automático ante 402/403 o payload vacío, sin abortar.
3. Histórico: 5 ejercicios completos, sin exclusiones manuales.
4. Pares: `MAX_PEERS = 5` (sólo limita la lista de FMP; la lista de config se usa tal cual), `MIN_PEERS = 3` por métrica.
