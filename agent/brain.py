# agent/brain.py — Cerebro de IA (OpenRouter)
"""
Genera respuestas usando OpenRouter, que es compatible con la API de OpenAI.
Cada negocio define su propio system prompt y modelo (config.BusinessConfig).
"""

from __future__ import annotations

import logging
import os
import re

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

logger = logging.getLogger("agente")

# Estilo WhatsApp: respuestas naturales y, cuando aplique, repartidas en varios
# mensajes cortos (globos). El modelo separa cada globo con una línea de solo "---".
ESTILO_WHATSAPP = (
    "\n\n## Estilo de respuesta (WhatsApp)\n"
    "- Escribe como en un chat real de WhatsApp: natural, cercano y al grano.\n"
    "- Si tu respuesta tiene varias ideas, repártela en VARIOS mensajes cortos en vez de un "
    "bloque largo. Separa cada mensaje con una línea que contenga solo: ---\n"
    "- Si una sola frase basta, responde en un único mensaje (sin ---).\n"
    "- No uses más de 4 mensajes. Nada de listas numeradas largas ni párrafos enormes."
)

OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1024"))

# Encabezados opcionales que OpenRouter usa para rankings/atribución.
_EXTRA_HEADERS = {
    "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://github.com/"),
    "X-Title": os.getenv("OPENROUTER_APP_NAME", "WhatsApp Agentreply"),
}

# Cliente perezoso: se crea al primer uso (permite importar sin la key en tests).
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY no configurada en .env")
        _client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)
    return _client


MENSAJE_ERROR = (
    "Disculpa, estoy teniendo un problema técnico en este momento. "
    "Por favor intenta de nuevo en unos minutos."
)


async def generar_respuesta(
    mensaje: str,
    historial: list[dict],
    system_prompt: str,
    modelo: str,
) -> str:
    """
    Genera la respuesta del agente.

    Args:
        mensaje: texto nuevo del cliente.
        historial: turnos previos [{"role": "user"|"assistant", "content": str}].
        system_prompt: personalidad + info del negocio.
        modelo: id de modelo de OpenRouter (ej. "openai/gpt-4o-mini").
    """
    if not mensaje or len(mensaje.strip()) < 1:
        return "¿Podrías escribir tu consulta? Estoy para ayudarte."

    mensajes = [{"role": "system", "content": system_prompt + ESTILO_WHATSAPP}]
    mensajes.extend(historial)
    mensajes.append({"role": "user", "content": mensaje})

    try:
        client = _get_client()
        resp = await client.chat.completions.create(
            model=modelo,
            messages=mensajes,
            max_tokens=MAX_TOKENS,
            extra_headers=_EXTRA_HEADERS,
        )
        texto = (resp.choices[0].message.content or "").strip()
        if not texto:
            return MENSAJE_ERROR
        if resp.usage:
            logger.info(
                "Respuesta OpenRouter (%s in / %s out, modelo %s)",
                resp.usage.prompt_tokens,
                resp.usage.completion_tokens,
                modelo,
            )
        return texto
    except Exception as e:  # noqa: BLE001 — degradar con mensaje al cliente
        logger.error("Error OpenRouter: %s", e)
        return MENSAJE_ERROR


# Línea separadora de globos: solo guiones o pipes (--- , |||).
_SEP_GLOBOS = re.compile(r"(?m)^\s*(?:-{3,}|\|{2,})\s*$")


def dividir_en_globos(texto: str, max_globos: int = 4) -> list[str]:
    """
    Divide la respuesta del modelo en 1 o más "globos" (mensajes de WhatsApp).

    1) Si el modelo usó el separador (línea con solo --- o |||), corta por ahí.
    2) Si no, corta por párrafos (líneas en blanco).
    3) Si tampoco, devuelve un único globo.
    Limita a `max_globos`: el exceso se une al último para no spamear.
    """
    texto = (texto or "").strip()
    if not texto:
        return [""]

    if _SEP_GLOBOS.search(texto):
        partes = _SEP_GLOBOS.split(texto)
    else:
        partes = re.split(r"\n\s*\n", texto)

    globos = [p.strip() for p in partes if p.strip()]
    if not globos:
        return [texto]

    if len(globos) > max_globos:
        globos = globos[: max_globos - 1] + [" ".join(globos[max_globos - 1 :])]
    return globos


# Directiva de imagen que el modelo puede emitir: [IMG:clave] o [IMG:https://...]
_IMG_RE = re.compile(r"\[IMG:\s*([^\]]+?)\s*\]", re.IGNORECASE)


def _resolver_media(clave: str, media: dict) -> str | None:
    clave = clave.strip()
    if clave in media:
        return (media.get(clave) or {}).get("url")
    if clave.lower().startswith("http"):
        return clave  # permite URL directa
    return None


def parsear_respuesta(respuesta: str, media: dict | None = None, max_globos: int = 4) -> list[tuple]:
    """
    Convierte la respuesta del modelo en una lista ordenada de partes:
      ("texto", texto, None)         → mensaje de texto
      ("imagen", url, caption)       → imagen (con texto opcional como caption)

    Detecta directivas [IMG:clave] (clave de la galería del negocio) o [IMG:https://...].
    Las claves desconocidas se ignoran (se queda solo el texto).
    """
    media = media or {}
    partes: list[tuple] = []
    for globo in dividir_en_globos(respuesta, max_globos):
        urls = [
            url for m in _IMG_RE.finditer(globo) if (url := _resolver_media(m.group(1), media))
        ]
        texto = _IMG_RE.sub("", globo).strip()
        if urls:
            for i, url in enumerate(urls):
                partes.append(("imagen", url, texto if i == 0 else ""))
        elif texto:
            partes.append(("texto", texto, None))
    if not partes:
        partes = [("texto", (respuesta or "").strip(), None)]
    return partes
