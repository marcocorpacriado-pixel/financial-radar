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
summary_every_days = 14                    # resumen quincenal (antes 12)
analysis_model = "claude-sonnet-5-5"       # opcional: análisis de 10-K/10-Q (alertas y análisis inicial)
summary_model = "claude-haiku-4-5"         # opcional: selección y síntesis de noticias del resumen

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
- **Formato (revisado el 2026-10-03): HTML de Telegram** (`notify.send(..., html=True)`). Todo el texto dinámico (cifras, síntesis del LLM, titulares) pasa por `html.escape`; `notify` lo trocea si supera 4096 caracteres. Así se ve en el móvil:
```
📊 <b>Radar de cartera</b> · 2026-10-03 (cada 14 días)

🔹 <b>MSFT</b> 517.53 · volumen -18% vs media 30 sesiones
• <b>Val:</b> P/E 28.8 (hist -18%, pares +37%) · EV/EBITDA 19.0 (hist -19%)
• <b>Op:</b> margen 46.8% (pares +14.0 pp) · ingresos +18% YoY (FY2026 Q4) · FCF yield 1.7%
• <b>Balance:</b> deuda neta/EBITDA 0.5x · D/E 0.29 · cobertura int. 50.9x · ROIC 20.6% · ROE 33.2% · liquidez 1.23
• <b>Filing:</b> 10-K 2026-07-29 · Riesgo/Cat.: …   (sin ⚠️: a menudo es un catalizador positivo)
💡 <b>Noticias:</b> Microsoft lanzó Copilot rediseñado…
   ◦ CNBC, 25-09: Microsoft gives Copilot a much-needed overhaul, and the stock deservedly soars
```
- `format_quant` devuelve las mismas líneas **en texto plano**, con las etiquetas `Val:`, `Op:` y `Balance:`. Ese texto es el que recibe `analyst` como `<financial_metrics>`; las negritas y los emojis se añaden sólo al componer el mensaje.
- **El bloque de noticias cierra cada posición**, también las que tienen `sec_enabled = false` (ESEA, BABA), que no tienen línea de filing.
- **Métricas operativas distorsionadas:** si `|margen operativo TTM| > 500 %`, la línea `Op:` muestra `⚠️ margen -1676.7%: métricas operativas distorsionadas (típico del mark-to-market de activos digitales); sin comparación con pares`.
  - Se omite la diferencia frente a pares, porque no significa nada en ese caso.
  - Crecimiento y FCF yield se mantienen.
  - El umbral es genérico (`DISTORTED_MARGIN = 5.0`), no exclusivo de MSTR.
- **Etiqueta de periodo:** `FY2026 Q4` (trimestre del **ejercicio fiscal** de la empresa: MSFT cierra en junio y BABA en marzo) o `FY2025` si hubo fallback anual.
- **Alertas de filings** siguen en texto plano: su contenido es el render de `analyst`.
- Valor ausente → `n/d`. Caja neta → `deuda neta/EBITDA caja neta`. El precio va sin símbolo de divisa.
- Si `quant` falla en una posición → `TICKER: sin datos FMP (<error>)` y el resto sigue (salida 1).
- Coste: ~45 llamadas FMP por resumen (los pares compartidos se piden una vez), dentro del límite de 250/día.
- **Noticias (añadido el 2026-10-03, `SPEC-news.md`):**
  - para **todas** las posiciones, también ESEA y BABA, se ejecuta `news.fetch_news(ticker, limit=10)`;
  - después, una sola llamada a `analyst.summarize_news(..., model=summary_model)`;
  - por posición se muestran `Noticias: <síntesis>` y los ≤ 3 titulares elegidos (`· fuente, dd-mm: título`, cortado a 120 caracteres);
  - si falla Haiku, se muestran los 3 primeros candidatos sin síntesis;
  - si falla el feed de un ticker, `Noticias: no disponibles`;
  - si Haiku no elige nada, `Noticias: sin novedades materiales`;
  - los fallos de noticias **no** cambian el código de salida: son un complemento y Google News puede fallar de forma puntual.
- Los modelos salen de `portfolio.toml`:
  - `analysis_model` va a `summarize_mdna` (alertas y análisis inicial);
  - `summary_model` va a `summarize_news`;
  - por defecto, `claude-sonnet-5-5` y `claude-haiku-4-5`.

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
- **Sin consola (`pythonw.exe`):** `sys.stdout` y `sys.stderr` son `None`. En ese caso `main` los redirige en modo *append* a `radar.log`, en la carpeta del proyecto e ignorado por git, para no perder ni los avisos ni las trazas de error.

## Ejecución programada (Windows, configurada el 2026-10-03)

```
schtasks /Create /F /TN "financial-radar" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 23:30 /TR "C:\Users\User\Desktop\financial-radar\.venv\Scripts\pythonw.exe C:\Users\User\Desktop\financial-radar\radar.py --check"
ADAR.PY --CHECK"
```
- **De lunes a viernes a las 23:30, hora local:** la zona horaria de la máquina es *Romance Standard Time* (Madrid). Es después del cierre de EE. UU. (22:00, o 21:00 en las semanas en que no coinciden los cambios de hora), así que los 10-K/10-Q publicados tras el cierre ya están en EDGAR.
- **`pythonw.exe`:** el intérprete sin consola, para que no aparezca ninguna ventana.
- **Script por ruta (`radar.py`) en vez de `-m radar`:** `schtasks` no permite fijar el directorio de trabajo, y con `-m radar` Python buscaría el módulo en `C:\Windows\System32`. `radar.py` hace `os.chdir` a su carpeta.
- **Ejecución:** con el usuario actual y sólo con la sesión iniciada, así que no hace falta guardar contraseña. Si el PC está apagado o suspendido a esa hora, la ejecución se pierde, pero no se pierden filings: la siguiente compara el último accession con `state.json`.
- **Comprobación:** `schtasks /Query /TN financial-radar /V /FO LIST` (columna "Last Result" = 0) y `radar.log`.

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
- **Noticias y modelos (2026-10-03):**
  - `analysis_model`/`summary_model` por defecto, leídos y validados como texto;
  - el resumen incluye noticias de todas las posiciones (también `sec_enabled = false`);
  - `summary_model` llega a `summarize_news` y `analysis_model` a `summarize_mdna`;
  - si falla Haiku, se muestran los 3 primeros titulares;
  - si falla el feed, "no disponibles" sin cambiar el código de salida;
  - con `pythonw` (stdout/stderr `None`), la salida va a `radar.log`.

## Criterios de éxito

- [x] `.venv\Scripts\python -m unittest discover -s tests -v` en verde (todo el proyecto).
- [x] `python radar.py --dry-run --force-summary` imprime el resumen real de las 5 posiciones sin abortar.
- [x] `python radar.py --force-summary` envía el resumen a Telegram en silencio y crea `state.json` con las líneas base y los análisis iniciales.
- [x] Una segunda ejecución de `python radar.py --check` el mismo día no envía nada.
- [x] Noticias: `python radar.py --dry-run --force-summary` muestra la síntesis y los titulares de las 5 posiciones.
- [x] Tarea `financial-radar` creada. Una ejecución manual (`schtasks /Run`) termina con "Last Result" = 0, sin ventana y dejando traza en `radar.log`.

## Fuera de alcance

REPLY y otras bolsas no cubiertas por el plan gratuito de FMP, análisis de 20-F/6-K, métrica específica para MSTR (mNAV), conversión de divisas, recuperar ejecuciones perdidas con el PC apagado, ejecución concurrente y rotación de `radar.log`.

## Decisiones

1. Noticias RSS: módulo `news` independiente (`SPEC-news.md`), integrado en el resumen el 2026-10-03.
2. REPLY eliminada de la cartera. *Fase A (2026-10-03)*: se intentó reincorporarla con pares `["CAP.PA", "ALMY.PA", "SOP.PA", "ACN"]`, pero la clave FMP actual sigue devolviendo **402** para REY.MI, CAP.PA, ALMY.PA y SOP.PA (sólo ACN responde). Queda pendiente de una clave con cobertura europea; añadirla ahora haría que cada resumen terminara con código 1. Cuando llegue: `ticker = "REPLY"`, `ticker_fmp = "REY.MI"`, `sec_enabled = false`, y volver a verificar los pares.
3. MSTR con pares `["COIN", "PLTR", "MARA"]`, que alcanzan `MIN_PEERS = 3`.
4. BABA con pares `["JD", "PDD", "BIDU", "TCEHY"]`: TCEHY está cubierto por el plan gratuito, y BIDU tiene P/E negativo (−41,3), así que sin TCEHY sólo quedaban 2 P/E válidos. ESEA y BABA con `sec_enabled = false`.
5. Programación de lunes a viernes a las 23:30 con `pythonw.exe` (sustituye a "no ejecutar `schtasks` por ahora").
6. La frecuencia del resumen es la clave existente `summary_every_days = 14`. No se crea una clave `frequency_days`: harían lo mismo.
