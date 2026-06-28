# agent/zernio.py — Cliente de la API de Zernio
"""
Pequeño cliente HTTP para la API de Zernio (zernio.com).

Cubre lo que necesita el agente:
- enviar_mensaje: responder en una conversación de WhatsApp.
- get_conversation: leer una conversación (incluye metadata CTWA del anuncio).
- provision_dataset: crear/obtener el dataset CTWA de un número (idempotente).
- enviar_conversion: reportar una conversión a Meta vía Zernio (LeadSubmitted/Purchase).
- verificar_firma: validar el HMAC-SHA256 del webhook.

Docs: https://docs.zernio.com
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("agente")

ZERNIO_BASE_URL = os.getenv("ZERNIO_BASE_URL", "https://zernio.com/api/v1")
ZERNIO_API_KEY = os.getenv("ZERNIO_API_KEY", "")
ZERNIO_WEBHOOK_SECRET = os.getenv("ZERNIO_WEBHOOK_SECRET", "")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

_TIMEOUT = httpx.Timeout(20.0)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {ZERNIO_API_KEY}",
        "Content-Type": "application/json",
    }


# ── Seguridad del webhook ─────────────────────────────────────
def verificar_firma(body_crudo: bytes, firma: str | None) -> bool:
    """
    Valida la firma X-Zernio-Signature (HMAC-SHA256 hex en minúsculas del body crudo).

    En producción (ENVIRONMENT=production) es OBLIGATORIO: si falta el secreto, se RECHAZA
    (fail-closed) para no dejar el webhook abierto. En desarrollo se permite con una advertencia.
    """
    if not ZERNIO_WEBHOOK_SECRET:
        if ENVIRONMENT == "production":
            logger.error("ZERNIO_WEBHOOK_SECRET no configurado en producción: webhook RECHAZADO.")
            return False
        logger.warning("ZERNIO_WEBHOOK_SECRET no configurado (modo desarrollo): firma sin verificar.")
        return True
    if not firma:
        return False
    esperado = hmac.new(
        ZERNIO_WEBHOOK_SECRET.encode("utf-8"), body_crudo, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperado, firma.strip())


# ── Envío de mensajes ─────────────────────────────────────────
async def enviar_mensaje(conversation_id: str, account_id: str, texto: str) -> bool:
    """
    Envía un mensaje de texto a una conversación.
    POST /v1/inbox/conversations/{conversationId}/messages
    """
    if not ZERNIO_API_KEY:
        logger.error("ZERNIO_API_KEY no configurada: no se puede enviar mensaje.")
        return False
    url = f"{ZERNIO_BASE_URL}/inbox/conversations/{conversation_id}/messages"
    payload = {"accountId": account_id, "message": texto}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json=payload, headers=_headers())
        if r.status_code >= 300:
            logger.error("Zernio enviar_mensaje %s: %s", r.status_code, r.text[:300])
            return False
        return True
    except httpx.HTTPError as e:
        logger.error("Zernio enviar_mensaje error de red: %s", e)
        return False


# ── Lectura de conversación (metadata CTWA) ───────────────────
async def get_conversation(conversation_id: str) -> dict | None:
    """
    GET /v1/inbox/conversations/{conversationId}
    Devuelve el objeto de conversación (incluye metadata con ctwa_*), o None.
    """
    if not ZERNIO_API_KEY:
        return None
    url = f"{ZERNIO_BASE_URL}/inbox/conversations/{conversation_id}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers=_headers())
        if r.status_code >= 300:
            logger.warning("Zernio get_conversation %s: %s", r.status_code, r.text[:200])
            return None
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Zernio get_conversation error: %s", e)
        return None


# ── Dataset CTWA (para conversiones) ──────────────────────────
async def provision_dataset(account_id: str) -> dict | None:
    """
    POST /v1/whatsapp/dataset  — crea (o devuelve) el dataset CTWA de un número.
    Idempotente: re-ejecutar es seguro.
    """
    if not ZERNIO_API_KEY:
        return None
    url = f"{ZERNIO_BASE_URL}/whatsapp/dataset"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json={"accountId": account_id}, headers=_headers())
        if r.status_code >= 300:
            logger.error("Zernio provision_dataset %s: %s", r.status_code, r.text[:300])
            return None
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.error("Zernio provision_dataset error: %s", e)
        return None


# ── Conversiones a Meta (vía Zernio) ──────────────────────────
async def enviar_conversion(
    account_id: str,
    conversation_id: str,
    evento: str,
    event_id: str,
    valor: float | None = None,
    moneda: str = "USD",
) -> bool:
    """
    POST /v1/whatsapp/conversions — reporta una conversión a Meta para atribución CTWA.
    `evento`: LeadSubmitted | Purchase | AddToCart | InitiateCheckout | ViewContent.
    """
    if not ZERNIO_API_KEY:
        return False
    url = f"{ZERNIO_BASE_URL}/whatsapp/conversions"
    payload: dict = {
        "accountId": account_id,
        "conversationId": conversation_id,
        "eventName": evento,
        "eventId": event_id,
    }
    if valor is not None:
        payload["value"] = valor
        payload["currency"] = moneda
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json=payload, headers=_headers())
        if r.status_code >= 300:
            logger.error("Zernio enviar_conversion %s: %s", r.status_code, r.text[:300])
            return False
        return True
    except httpx.HTTPError as e:
        logger.error("Zernio enviar_conversion error: %s", e)
        return False


# ── CLI: provisionar el dataset CTWA por número (para anuncios/ROAS) ──
# Uso: python -m agent.zernio provision <account_id> [<account_id> ...]
if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "provision":
        async def _main() -> None:
            for account_id in sys.argv[2:]:
                res = await provision_dataset(account_id)
                if res:
                    print(f"OK  {account_id}: dataset = {res.get('datasetId') or res}")
                else:
                    print(f"ERROR  {account_id}: no se pudo provisionar (revisa ZERNIO_API_KEY)")

        asyncio.run(_main())
    else:
        print("Uso: python -m agent.zernio provision <account_id> [<account_id> ...]")
