# agent/brain.py — Cerebro de IA (OpenRouter)
"""
Genera respuestas usando OpenRouter, que es compatible con la API de OpenAI.
Cada negocio define su propio system prompt y modelo (config.BusinessConfig).
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

logger = logging.getLogger("agente")

OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1024"))

# Encabezados opcionales que OpenRouter usa para rankings/atribución.
_EXTRA_HEADERS = {
    "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://github.com/"),
    "X-Title": os.getenv("OPENROUTER_APP_NAME", "AgentKit WhatsApp"),
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

    mensajes = [{"role": "system", "content": system_prompt}]
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
