# Spec: radar

Estado: **APROBADO** (2026-10-03)
Orquestador: depende de `notify`, `quant`, `sec-mdna` y `analyst`. Convenciones comunes en `CAPABILITY_MAP.md`.

## Objetivo

Un único punto de entrada que, ejecutado bajo demanda o por el Programador de tareas, hace dos cosas:
1. **Alerta inmediata** (`silent=False`) cuando un ticker monitorizado en la SEC publica un 10-K/10-Q nuevo. Incluye el MD&A comparado con el filing anterior y sintetizado por `analyst`, cruzado con las métricas de balance de `quant`, y el enlace al documento.
2. **Resumen periódico consolidado** (`silent=True`) cada `summary_every_days`. Incluye las 5 posiciones con números clave, primas/descuentos frente a su histórico y sus pares, salud del balance y el último catalizador detectado.

Cada posición se procesa por separado: si una falla, el resto sigue adelante, y el fallo aparece en el mensaje y en el código de salida.

## Cartera (verificada con datos reales el 2026-10-03)

| Ticker | FMP (plan gratuito) | SEC EDGAR | Flujo |
|---|---|---|---|
| MSFT, AMZN | ✓ | 10-K/10-Q | Completo |
| MSTR | ✓; P/E −1,7, EBITDA negativo, cobertura de intereses −262,9 | 10-K/10-Q ("Strategy Inc") | Completo. El P/E sale `n/d` (≤ 0); el balance (D/E, cobertura, ROIC) es lo que informa del riesgo |
| ESEA | ✓ | Sólo 20-F/6-K | `sec_enabled = false`: sólo cuantitativo |
| BABA | ✓; estados financieros en CNY | Sólo 20-F/6-K | `sec_enabled = false`: sólo cuantitativo. Ratios sin unidad válidos |

REPLY (`REY.MI`) queda **eliminada**: el plan gratuito de FMP devuelve HTTP 402 para la Bolsa de Milán.

## `portfolio.toml`

```toml
summary_every_days = 12

[[positions]]
ticker = "MSTR"           # obligatorio: nombre en mensajes y clave en state.json
ticker_fmp = "MSTR"       # opcional, por defecto = ticker
ticker_sec = "MSTR"       # opcional, por defecto = ticker
peers = ["COIN", "PLTR", "MARA"]   # opcional, por defecto []
sec_enabled = true        # opcional, por defecto true
```

- Se lee con `tomllib`.
- **Validación estricta**, con `ValueError` que indica la posición:
  - claves desconocidas (atrapa erratas como `sec_enable`);
  - `ticker` ausente o duplicado;
  - tipos incorrectos;
  - `summary_every_days` < 1;
  - cartera vacía.
- Siempre se pasa la lista de `peers` (aunque sea vacía) a `quant.analyze`: nunca se usa `stock-peers` de FMP (el problema de NXT en AAPL).

## `state.json`

```json
{
  "last_check": "2026-10-03T21:30:00+00:00",
  "last_summary": "2026-10-03T21:30:00+00:00",
  "tickers": {
    "MSFT": {
      "accessions": {"10-K": "0000950170-26-000123", "10-Q": "0000950170-26-000045"},
      "last_analysis": {"form": "10-K", "accession": "…", "filing_date": "2026-07-30", "url": "…",
                        "lines": ["Guidance: …"], "catalyst": "…"}
    }
  },
  "sent": {"<sha256 del mensaje>": "2026-10-03T21:30:00+00:00"}
}
```

- **Escritura atómica**: se escribe `state.json.tmp` y se renombra con `os.replace`.
- **Se guarda tras cada paso completado.** Una alerta sólo marca su accession como procesado después de un envío correcto; si Telegram falla, se reintenta en la siguiente ejecución.
- **Duplicados:** se comprueba el `sha256` del mensaje en `sent` antes de enviar, y no se reenvía un mensaje idéntico. Las entradas de más de 180 días se purgan. `--force-summary` ignora esta comprobación sólo para el resumen.
- **Primera vez que se ve un ticker:** sus accessions actuales se guardan como línea base **sin alerta**.

## Flujo

### `--check` (por defecto)

1. **Filings.** Para cada posición con `sec_enabled`, y para cada form (`10-K`, `10-Q`), se compara `sec_mdna.filings(ticker_sec, form)[0]` con `state`:
   - **Sin línea base** → se registra y no se alerta.
   - **Accession nuevo** →
     - se ejecuta `quant.analyze`, que aporta las métricas (si falla, se sigue sin ellas);
     - después `sec_mdna.fetch_mdna` → `analyst.summarize_mdna(cur, prev, metrics)` → `analyst.render`;
     - la alerta lleva la cabecera `Nuevo 10-Q · MSFT · periodo 2026-09-30 (presentado 2026-10-28)`, el render y la URL;
     - se envía con `notify.send(..., silent=False)` y se guardan `accessions` y `last_analysis`.
   - **Falla `sec_mdna` o `analyst`** → se envía igualmente una alerta corta: cabecera, `Análisis no disponible: <error>` y la URL. El accession se marca como procesado y la salida es 1.
   - **Ningún filing de ese form** → se omite sin error.
2. **Resumen.** Se genera si han pasado ≥ `summary_every_days` desde `last_summary`, si nunca hubo resumen o si se pasa `--force-summary`.

### Resumen consolidado

- Si una posición con SEC aún no tiene `last_analysis` (por ejemplo, la primera ejecución), se analiza su filing más reciente con sus métricas y se guarda **sin alerta**. Ocurre una sola vez por ticker (~$0,04–0,12).
- Formato, texto plano; `notify` lo trocea si supera 4096 caracteres:
```
Radar de cartera · 2026-10-03 (cada 12 días)

MSFT 512.30 · volumen -12% vs media 30 sesiones
  P/E 34.1 (hist +12%, pares +8%) · EV/EBITDA 22.0 (hist +5%)
  Margen op. 45.2% (pares +6.1 pp) · Ingresos +15% YoY (Q4 2026) · FCF yield 2.1%
  Deuda neta/EBITDA 0.5x · D/E 0.29 · Cobertura int. 50.9x · ROIC 20.6% · ROE 33.2% · Liquidez 1.23
  Último filing: 10-K 2026-07-30 · Riesgo/Cat.: …
```
- Valor ausente → `n/d`. Caja neta → `Deuda neta/EBITDA caja neta`. El precio va sin símbolo de divisa.
- Si `quant` falla en una posición → `TICKER: sin datos FMP (<error>)` y el resto sigue (salida 1).
- Coste: ~45 llamadas FMP por resumen (los pares compartidos se piden una vez), dentro del límite de 250/día.

### Cambios en módulos existentes (tareas de esta spec)

- **`quant`:** balance y rentabilidad del capital. Ver `SPEC-quant.md`, sección *Balance y rentabilidad del capital*. Sin llamadas extra.
- **`analyst`:**
  - `summarize_mdna(current, previous, metrics=None)`;
  - etiqueta `<financial_metrics>`;
  - reglas del prompt: interanual/secuencial, y cruce del balance con el MD&A.

  Ver `SPEC-analyst.md`.
- El texto de métricas que recibe `analyst` es el mismo bloque que `radar` muestra en el resumen (`format_quant`), una sola función de formato.

## CLI

```
python radar.py [--check] [--force-summary] [--dry-run]
```

| Flag | Efecto |
|---|---|
| (ninguno) / `--check` | Filings nuevos → alertas; resumen si toca |
| `--force-summary` | Lo mismo que `--check`, más el resumen ahora |
| `--dry-run` | Imprime los mensajes en lugar de enviarlos y **no escribe `state.json`**, así que es repetible. Sí llama a FMP, SEC y Claude (con coste real) |

- `argparse` y `sys` de la stdlib.
- Al arrancar, `os.chdir` a la carpeta del proyecto (para `.env`, `cache/`, `state.json` y `portfolio.toml`), y después `notify.load_env()`.
- Código de salida: `0` si todo fue bien; `1` si falló alguna posición o algún envío. Detalle en stderr.
- **El Programador de tareas no se configura por ahora** (decisión del 2026-10-03). Comando de referencia para cuando se active:
  ```
  schtasks /Create /SC DAILY /ST 23:30 /TN "financial-radar" /TR "C:\Users\User\Desktop\financial-radar\.venv\Scripts\python.exe C:\Users\User\Desktop\financial-radar\radar.py --check"
  ```

## Tests (`tests/test_radar.py`, sin red: `quant`, `sec_mdna`, `analyst` y `notify` parcheados; estado en un directorio temporal)

- **Cartera:**
  - valores por defecto;
  - clave desconocida, `ticker` ausente o duplicado, tipos incorrectos y `summary_every_days` < 1 → `ValueError`;
  - el `portfolio.toml` real carga 5 posiciones con los pares acordados.
- **Estado:** fichero ausente → estado vacío; ida y vuelta de la escritura atómica; purga de `sent` > 180 días.
- **Periodicidad:** sin `last_summary` → toca; 11 días con un periodo de 12 → no toca; 12 días → toca.
- **Formato:**
  - bloque de `quant` completo (signos, `pp`, `x`);
  - `None` → `n/d`;
  - caja neta;
  - FY frente a trimestre.
- **Flujo:**
  - primera vista → línea base sin alerta;
  - accession nuevo → alerta `silent=False` con métricas pasadas a `analyst` y estado actualizado;
  - `sec_enabled = false` → ninguna llamada a `sec_mdna`;
  - fallo de `analyst` → alerta corta con URL;
  - fallo de `notify` → accession sin marcar y salida 1;
  - duplicado → no se reenvía;
  - fallo de `quant` en una posición → línea "sin datos" y el resto sigue;
  - resumen con `silent=True`;
  - análisis inicial sin alerta cuando falta `last_analysis`;
  - `--dry-run` → ni `send` ni escritura de estado;
  - `--force-summary` → resumen aunque no toque.

## Criterios de éxito

- [x] `.venv\Scripts\python -m unittest discover -s tests -v` en verde (todo el proyecto).
- [x] `python radar.py --dry-run --force-summary` imprime el resumen real de las 5 posiciones sin abortar.
- [x] `python radar.py --force-summary` envía el resumen a Telegram en silencio y crea `state.json` con las líneas base y los análisis iniciales.
- [x] Una segunda ejecución de `python radar.py --check` el mismo día no envía nada.

## Fuera de alcance

Noticias RSS (fase posterior independiente: `SPEC-news.md`), REPLY y otras bolsas no cubiertas por el plan gratuito de FMP, análisis de 20-F/6-K, métrica específica para MSTR (mNAV), conversión de divisas, configuración del Programador de tareas, ejecución concurrente y logging a fichero.

## Decisiones

1. Noticias RSS: fase posterior, como módulo independiente.
2. REPLY eliminada de la cartera.
3. MSTR con pares `["COIN", "PLTR", "MARA"]`, que alcanzan `MIN_PEERS = 3`.
4. BABA con pares `["JD", "PDD", "BIDU"]`; ESEA y BABA con `sec_enabled = false`.
5. No ejecutar `schtasks` por ahora.
