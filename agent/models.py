# agent/models.py — Modelos de datos normalizados
"""
Formato común de un mensaje entrante, independiente del payload crudo de Zernio.
El webhook de Zernio (`message.received`) trae el contexto del mensaje, la
conversación y la cuenta; aquí lo aplanamos a algo simple de usar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MensajeEntrante:
    """Mensaje entrante normalizado de WhatsApp (vía Zernio)."""

    account_id: str          # Cuenta/número de Zernio que recibió el mensaje (identifica el negocio)
    conversation_id: str     # ID de conversación de Zernio (se usa para responder)
    contacto: str            # Identificador del cliente (teléfono / id de contacto)
    texto: str               # Contenido de texto del mensaje
    mensaje_id: str          # ID del mensaje en la plataforma
    es_propio: bool = False  # True si lo envió el negocio (eco saliente, se ignora)
    # Metadatos CTWA de la conversación (ctwa_source_id, ctwa_headline, etc.) si vinieron en el payload
    metadata_ctwa: dict[str, Any] = field(default_factory=dict)


def _str(valor: Any) -> str:
    """Convierte a string seguro (None -> '')."""
    return "" if valor is None else str(valor)


def parsear_message_received(payload: dict[str, Any]) -> MensajeEntrante | None:
    """
    Extrae un MensajeEntrante del payload de un webhook `message.received` de Zernio.

    Estructura esperada (campos tolerantes a variaciones de nombre):
        {
          "id": "evt_...",
          "event": "message.received",
          "message": { "id"/"messageId", "text"/"body", "direction"/"isOutbound",
                       "from"/"contact"/"senderId", ... },
          "conversation": { "id"/"_id"/"conversationId", "metadata": { "ctwa_*": ... } },
          "account": { "id"/"_id"/"accountId" },
          "timestamp": "2026-06-26T21:30:00Z"
        }

    Devuelve None si no es un mensaje de texto entrante utilizable.
    """
    if payload.get("event") != "message.received":
        return None

    message = payload.get("message") or {}
    conversation = payload.get("conversation") or {}
    account = payload.get("account") or {}

    # ── ID de cuenta (qué número/negocio) ─────────────────────
    account_id = _str(
        account.get("id")
        or account.get("_id")
        or account.get("accountId")
        or message.get("accountId")
    )
    if not account_id:
        return None

    # ── ID de conversación (para responder) ───────────────────
    conversation_id = _str(
        conversation.get("id")
        or conversation.get("_id")
        or conversation.get("conversationId")
        or message.get("conversationId")
    )
    if not conversation_id:
        return None

    # ── Texto del mensaje ─────────────────────────────────────
    texto = message.get("text")
    if isinstance(texto, dict):  # algunos formatos anidan { "body": "..." }
        texto = texto.get("body")
    texto = _str(texto or message.get("body") or message.get("content")).strip()

    # ── ¿Es saliente (eco propio)? ────────────────────────────
    direccion = _str(message.get("direction")).lower()
    es_propio = bool(
        message.get("isOutbound")
        or message.get("fromMe")
        or direccion in ("outbound", "outgoing", "out")
    )

    # ── Contacto (cliente) ────────────────────────────────────
    # Si Zernio no trae un identificador de remitente, usamos el conversation_id
    # como clave del historial. Así NUNCA se mezclan historiales de clientes
    # distintos bajo una clave vacía.
    contacto = _str(
        message.get("from")
        or message.get("contact")
        or message.get("senderId")
        or conversation.get("contactId")
        or conversation.get("contact")
    ) or conversation_id

    # ── Metadatos CTWA de la conversación ─────────────────────
    metadata = conversation.get("metadata") or {}
    metadata_ctwa = {k: v for k, v in metadata.items() if str(k).startswith("ctwa_")}

    return MensajeEntrante(
        account_id=account_id,
        conversation_id=conversation_id,
        contacto=contacto,
        texto=texto,
        mensaje_id=_str(message.get("id") or message.get("messageId")),
        es_propio=es_propio,
        metadata_ctwa=metadata_ctwa,
    )
