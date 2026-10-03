# Financial Radar

Radar personal de cartera que vigila un puñado de posiciones y avisa por Telegram:

- **Alerta inmediata** cuando una empresa publica un 10-K o 10-Q nuevo en la SEC: Claude compara el MD&A con el del filing anterior y lo cruza con las métricas de balance. Cada conclusión va respaldada por una cita literal del filing, y Python verifica que esa cita existe.
- **Resumen quincenal** de toda la cartera:
  - valoración frente a su histórico y frente a sus pares;
  - márgenes, crecimiento, caja y salud del balance;
  - último riesgo o catalizador detectado en los filings;
  - noticias relevantes de las dos últimas semanas, filtradas y sintetizadas por Claude Haiku.

Sólo usa la biblioteca estándar de Python: nada de `requests`, `pandas` ni SDKs.

```
📊 Radar de cartera · 2026-10-03 (cada 14 días)

🔹 MSFT 517.53 · volumen -18% vs media 30 sesiones
• Val: P/E 28.8 (hist -18%, pares +37%) · EV/EBITDA 19.0 (hist -19%)
• Op: margen 46.8% (pares +14.0 pp) · ingresos +18% YoY (FY2026 Q4) · FCF yield 1.7%
• Balance: deuda neta/EBITDA 0.5x · D/E 0.29 · cobertura int. 50.9x · ROIC 20.6% · ROE 33.2% · liquidez 1.23
• Filing: 10-K 2026-07-29 · Riesgo/Cat.: Obligaciones contractuales se disparan a $743.8 mil millones…
💡 Noticias: Microsoft lanzó Copilot rediseñado, generando rally accionario…
   ◦ CNBC, 25-09: Microsoft gives Copilot a much-needed overhaul, and the stock deservedly soars
```

## Arquitectura

Seis módulos pequeños. Los cinco primeros son independientes entre sí; sólo `radar` los conecta.

| Módulo | Qué hace | Fuente |
|---|---|---|
| `notify.py` | Envía mensajes a Telegram (texto plano o HTML) y los trocea si superan 4096 caracteres | Telegram Bot API |
| `quant.py` | P/E y EV/EBITDA frente a su mediana de 5 años y a la de sus pares; margen, crecimiento YoY, FCF, volumen; deuda neta/EBITDA, D/E, cobertura de intereses, ROIC, ROE, liquidez | Financial Modeling Prep (plan gratuito) |
| `sec_mdna.py` | Localiza el último 10-K/10-Q y el anterior del mismo tipo, y extrae el MD&A (Item 7 / Item 2) como texto limpio, con caché en disco | SEC EDGAR |
| `analyst.py` | Síntesis del MD&A con citas verificadas (Sonnet) y selección y síntesis de noticias (Haiku) | API de Anthropic |
| `news.py` | Titulares de los últimos 14 días por ticker, deduplicados | Google News RSS |
| `radar.py` | Orquestador: estado, detección de filings nuevos, resumen periódico y CLI | — |

Cada módulo tiene su especificación (`SPEC-*.md`) con las decisiones de diseño y los datos reales con los que se verificó; `CAPABILITY_MAP.md` es el índice.

## Instalación

Requiere Python 3.11 o superior (por `tomllib`); desarrollado con 3.14.

```powershell
python -m venv .venv
.venv\Scripts\python -m unittest discover -s tests -v   # 136 tests, sin red
```

Crea un archivo `.env` en la raíz (está en `.gitignore`):

```
TELEGRAM_BOT_TOKEN=...      # bot creado con @BotFather
TELEGRAM_CHAT_ID=...        # tu chat con el bot
FMP_API_KEY=...             # https://site.financialmodelingprep.com (plan gratuito)
ANTHROPIC_API_KEY=...       # clave asociada a un workspace de la Console de Anthropic
```

## Configuración: `portfolio.toml`

```toml
summary_every_days = 14
analysis_model = "claude-sonnet-5-5"   # análisis de filings
summary_model = "claude-haiku-4-5"     # noticias del resumen

[[positions]]
ticker = "MSFT"
peers = ["GOOGL", "AMZN", "AAPL", "ORCL"]

[[positions]]
ticker = "ESEA"
peers = ["DAC", "GSL", "ZIM"]
sec_enabled = false   # emisor extranjero (20-F): sólo análisis cuantitativo y noticias
```

Campos por posición:

| Campo | Obligatorio | Uso |
|---|---|---|
| `ticker` | sí | Nombre en los mensajes |
| `ticker_fmp` | no | Símbolo en FMP, si difiere |
| `ticker_sec` | no | Símbolo en EDGAR, si difiere |
| `peers` | no | Pares fijados a mano (nunca se usa la lista automática de FMP) |
| `sec_enabled` | no (por defecto `true`) | `false` para emisores sin 10-K/10-Q |

## Uso

```powershell
.venv\Scripts\python radar.py --check                     # filings nuevos → alerta; resumen si toca
.venv\Scripts\python radar.py --force-summary             # envía el resumen ahora
.venv\Scripts\python radar.py --dry-run --force-summary   # imprime sin enviar ni guardar estado
```

Cada módulo tiene además su propio CLI de comprobación:

```powershell
.venv\Scripts\python -m notify "hola"
.venv\Scripts\python -m quant MSFT GOOGL AMZN
.venv\Scripts\python -m sec_mdna MSFT 10-Q
.venv\Scripts\python -m news MSFT
.venv\Scripts\python -m analyst cache\sec\MSFT_10-Q_2026-03-31.txt cache\sec\MSFT_10-Q_2025-12-31.txt
```

### Ejecución automática en Windows

De lunes a viernes a las 23:30, después del cierre de EE. UU. Usa `pythonw.exe` para que no aparezca ninguna ventana, y la salida va a `radar.log`:

```powershell
schtasks /Create /F /TN "financial-radar" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 23:30 /TR "C:\ruta\financial-radar\.venv\Scripts\pythonw.exe C:\ruta\financial-radar\radar.py --check"
```

## Cómo se evitan los errores típicos

- **Alucinaciones:** cada hallazgo del MD&A trae una cita literal, y se descarta si la cita no aparece en el texto enviado al modelo. Las noticias sólo pueden apoyarse en titulares reales de la lista.
- **Falsos positivos del índice del filing:** de todos los encabezados "Item 7 / Item 2 – Management's Discussion" se elige la sección más larga; la entrada del índice sólo ocupa una línea.
- **Métricas engañosas:**
  - un P/E o EV/EBITDA ≤ 0 se muestra como `n/d`;
  - un ratio deuda neta/EBITDA negativo sólo cuenta como caja neta si el EBITDA es positivo;
  - un margen operativo de más de ±500 % se marca como distorsionado (típico del mark-to-market de activos digitales);
  - una mediana de pares con menos de 3 valores no se calcula.
- **Alertas duplicadas o perdidas:**
  - `state.json` se escribe de forma atómica y sólo avanza tras un envío correcto;
  - un mensaje idéntico no se reenvía;
  - la primera ejecución registra los filings actuales sin alertar.
- **Fallos parciales:** si falla una posición, una fuente de datos o el LLM, el resto del mensaje sale igual y el código de salida es 1.
- **Secretos:** las claves nunca aparecen en mensajes de error (van en URLs y cabeceras que no se registran).

## Costes aproximados

| Concepto | Coste |
|---|---|
| FMP, SEC EDGAR, Google News, Telegram | 0 € (unas 45 llamadas FMP por resumen, de 250/día gratuitas) |
| Análisis de un filing nuevo (Sonnet 5.5) | ~$0,04–0,12 |
| Síntesis de noticias del resumen (Haiku 4.5) | < $0,01 |

## Limitaciones conocidas

- El plan gratuito de FMP no cubre bolsas fuera de EE. UU. (p. ej. Borsa Italiana devuelve 402).
- No analiza 20-F/6-K de emisores extranjeros: para ellos sólo hay cuantitativo y noticias.
- La deduplicación de noticias es léxica: no detecta paráfrasis sin palabras en común.
- La tarea programada sólo corre con la sesión de Windows iniciada. Una ejecución perdida retrasa la alerta un día, pero no pierde el filing.

## Aviso

Herramienta personal de seguimiento. No es asesoramiento financiero: las cifras proceden de terceros y las síntesis las genera un LLM.
