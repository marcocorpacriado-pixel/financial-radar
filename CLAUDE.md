# Portfolio Financial Radar & SEC Analyzer

## Arquitectura y Principios de Desarrollo
- Lenguaje: Python 3.10+.
- Reglas de código: Aplicar estrictamente la escalera de `ponytail` (priorizar biblioteca estándar de Python, código mínimo que funcione, evitar frameworks inflados).
- Metodología: Guiado por especificaciones (`spec-driven-development` de `agent-skills`).
- Cada función de cálculo numérico o parseo debe contar con su correspondiente test unitario simple antes de darse por completada.

## Componentes del Sistema
1. **Módulo Cuantitativo:** Conexión con FMP API (Financial Modeling Prep) para métricas clave, múltiplos de valoración, precio y volumen.
2. **Módulo Cualitativo:** Extracción quirúrgica de SEC EDGAR para Item 7 (10-K) e Item 2 (10-Q) centrada en variaciones de MD&A.
3. **Módulo de Notificación:** Bot de Telegram / servicio push para envío de alertas periódicas al móvil.