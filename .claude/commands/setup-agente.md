Lee el archivo CLAUDE.md completo de este repositorio. Contiene todas las instrucciones del
sistema de onboarding de AgentKit WhatsApp (Zernio + OpenRouter).

Ejecuta el flujo de configuración siguiendo las fases EN ORDEN, en español, una pregunta a la vez:

FASE 1 — Verificar entorno (Python 3.11+, instalar dependencias, crear .env desde .env.example).
FASE 2 — Entrevistar al usuario por CADA número/negocio (nombre, rubro, nombre del agente, tono,
         horario, archivos de conocimiento en config/knowledge/<negocio>/).
FASE 3 — Pedir credenciales y escribirlas SOLO en .env (nunca al repo): OPENROUTER_API_KEY,
         ZERNIO_API_KEY, y el account_id de cada número (del dashboard de Zernio). Generar
         ZERNIO_WEBHOOK_SECRET y REPORT_TOKEN aleatorios. Preguntar si quiere anuncios/ROAS:
         si sí, ENABLE_ADS=true + META_ACCESS_TOKEN + meta_ad_account_id por negocio.
FASE 4 — Generar config/businesses.yaml con un bloque por negocio y su system prompt.
FASE 5 — Probar en local con `python tests/test_local.py` hasta que el usuario apruebe.
FASE 6 — Guiar el deploy y el alta del webhook en Zernio. Recomienda la ruta más fácil:
         Railway vía su MCP (`claude mcp add railway --transport http https://mcp.railway.com`).
         Si el MCP de Railway está disponible, ofrece desplegar por el usuario (crear proyecto,
         cargar variables, volumen en /app/data, devolver la URL). Alternativas: Coolify o Docker.

REGLAS:
- Habla siempre en español. Una pregunta a la vez. Nunca avances de fase sin confirmar.
- NUNCA escribas claves en archivos versionados; solo en .env (que está en .gitignore).
- No inventes credenciales: si el usuario no las tiene, guíalo paso a paso para obtenerlas.
