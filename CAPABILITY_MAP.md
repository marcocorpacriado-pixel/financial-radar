# Capability Map: Portfolio Financial Radar

Estado: **APROBADO** (2026-10-03). Índice de specs: este archivo.

| Module id | Responsabilidad | Depende de |
|---|---|---|
| `notify` | Enviar texto a un chat de Telegram (Bot API `sendMessage`). | — |
| `quant` | Cliente FMP (plan gratuito): P/E TTM, EV/EBITDA, margen operativo, crecimiento de ingresos YoY, FCF y FCF yield, precio y divergencia de volumen frente a la media de 30 sesiones; en contexto frente a su mediana histórica y a la mediana de sus pares. | — |
| `sec-mdna` | EDGAR: ticker→CIK, detectar el último 10-K/10-Q (fecha + hash), extraer Item 7 (10-K) / Item 2 (10-Q) como texto plano. | — |
| `analyst` | Síntesis con LLM: recibe el texto del MD&A (actual y anterior) y devuelve un resumen ejecutivo denso (cambios de guidance, presión de costes, riesgos, catalizadores). Una llamada HTTP directa a la API, sin frameworks. | — |
| `news` | Titulares de Google News RSS (últimos 14 días) por ticker, filtrados y deduplicados. Añadido el 2026-10-03. | — |
| `radar` | Orquestador CLI: lee `portfolio.toml`, compara con `state.json` (timestamp + hash del último filing por ticker), dispara alerta prioritaria inmediata ante un 10-K/10-Q nuevo y el resumen periódico (cada 7–15 días), y lo envía con `notify`. | notify, quant, sec-mdna, analyst, news |

Build order: `notify` → `quant`, `sec-mdna`, `analyst`, `news` (paralelizables) → `radar`

- Los módulos hoja no se importan entre sí; sólo `radar` los conecta. Sin ciclos.
- Specs: `SPEC-notify.md`, `SPEC-quant.md`, `SPEC-sec-mdna.md`, `SPEC-analyst.md`, `SPEC-news.md`, `SPEC-radar.md`.

## Decisiones cerradas

1. MD&A: el informe final lo genera `analyst` (LLM), no un diff crudo.
2. Alertas: resumen periódico como base + alerta prioritaria inmediata sólo ante un nuevo 10-K/10-Q.
3. FMP: plan gratuito/básico; métricas listadas arriba.
4. EDGAR User-Agent: `Marco Corpa marcocorpacriado@gmail.com`.
5. Ejecución: CLI bajo demanda o Programador de tareas de Windows; `state.json` guarda timestamp + hash del último filing.
6. Git: inicializado.

## Convenciones comunes (aplican a todas las specs)

**Stack:** Python 3.14 (`.venv`), sólo biblioteca estándar: `urllib.request`, `json`, `tomllib`, `hashlib`, `html.parser`, `statistics`, `dataclasses`, `unittest`. Añadir cualquier dependencia de terceros requiere aprobación.

**Comandos** (desde la raíz, PowerShell):
```
Tests:  .venv\Scripts\python -m unittest discover -s tests -v
Run:    .venv\Scripts\python -m radar
Módulo: .venv\Scripts\python -m notify "texto de prueba"
```

**Estructura:** plana, un archivo por módulo.
```
notify.py  quant.py  sec_mdna.py  analyst.py  radar.py
tests/test_<modulo>.py
portfolio.toml        → cartera (versionado)
state.json            → estado de ejecución (ignorado por git)
SPEC-<module-id>.md   → una spec por módulo
```

**Estilo:** funciones pequeñas con type hints, sin clases salvo que guarden estado, docstring de una línea, errores como excepciones de stdlib con mensaje claro.
```python
def split_message(text: str, limit: int = 4096) -> list[str]:
    """Trocea text en partes <= limit, cortando por saltos de línea si es posible."""
```

**Tests:** `unittest` + `unittest.mock`. Toda función de cálculo o parseo tiene test antes de darse por terminada. Nada de red real en los tests: se parchea `urllib.request.urlopen`.

**Límites:**
- Siempre: secretos por variables de entorno; timeout en toda llamada HTTP; tests en verde antes de cada commit.
- Preguntar antes: nuevas dependencias, cambiar el formato de `state.json` o `portfolio.toml`, añadir módulos.
- Nunca: commitear secretos ni `state.json`; imprimir o loguear tokens/API keys (tampoco dentro de URLs).
