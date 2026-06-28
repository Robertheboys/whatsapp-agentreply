# agent/main.py — Servidor FastAPI: webhook de Zernio + auto-respuesta con IA
"""
Recibe los webhooks de Zernio (message.received), enruta cada mensaje al negocio
correcto según el account.id, y responde con IA (OpenRouter) en segundo plano.

Puntos clave:
- Verifica la firma HMAC del webhook (X-Zernio-Signature).
- Idempotencia por X-Zernio-Event-Id (Zernio reintenta; no duplicamos respuestas).
- Responde 2xx en < 5 s SIEMPRE: la llamada a la IA va en BackgroundTasks
  (Zernio desactiva el webhook tras fallos/timeout).
- Atribución de anuncios CTWA + ROAS opcional (ENABLE_ADS).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from agent import ads, brain, memory, zernio
from agent.config import cargar_negocios
from agent.models import parsear_message_received

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
REPORT_TOKEN = os.getenv("REPORT_TOKEN", "")

# URLs de imágenes de producto (deben ser públicas; alojar en GitHub raw o similar)
FLYER_IMAGE_URL = os.getenv("FLYER_IMAGE_URL", "")   # flyer de lista de precios
CELDA_IMAGE_URL = os.getenv("CELDA_IMAGE_URL", "")   # foto de la celda individual
CAJA_IMAGE_URL = os.getenv("CAJA_IMAGE_URL", "")     # foto de la caja/etiqueta

# Texto de la respuesta rápida de precios (sin imagen: Pedro lo genera también por IA;
# este texto se manda junto al flyer como "caption" del segundo mensaje)
PRECIO_RAPIDO = """🔋 CELDAS DE LITIO 18650 EVE
Uso Profesional · 100% Originales

📋 LISTA DE PRECIOS:
▫️ 50 unidades → S/ 8.00 c/u
▫️ 100 unidades → S/ 7.00 c/u
▫️ 500 unidades → S/ 6.80 c/u
▫️ 1,000 unidades → S/ 6.50 c/u

⚡ Especificaciones:
• Modelo: ICR 18650/26V
• Capacidad: 2.55Ah / 2550mAh
• Marca EVE original
• No genéricas, no recicladas

📦 Stock disponible · Envíos a todo Perú
¿Cuántas unidades necesitas? 👇"""

_PALABRAS_PRECIO = {
    "precio", "precios", "costo", "costos", "cuánto", "cuanto", "vale",
    "lista", "cotización", "cotizacion", "tarifa", "tarifas", "cuántos",
    "cuantos", "cuestan", "cuesta", "cobran", "cobras", "valen",
    "presupuesto", "oferta", "descuento",
}


def _es_pregunta_de_precios(texto: str) -> bool:
    """Devuelve True si el mensaje parece una consulta de precios."""
    palabras = set(texto.lower().split())
    return bool(palabras & _PALABRAS_PRECIO)


# Respuestas en varios "globos" (mensajes) para verse más humano.
MULTI_BUBBLE = os.getenv("MULTI_BUBBLE", "true").lower() in ("1", "true", "yes", "si", "sí")
BUBBLE_MAX = int(os.getenv("BUBBLE_MAX", "4"))
BUBBLE_DELAY_MIN = float(os.getenv("BUBBLE_DELAY_MIN", "0.6"))
BUBBLE_DELAY_MAX = float(os.getenv("BUBBLE_DELAY_MAX", "4.0"))


def _delay_globo(texto: str) -> float:
    """Pausa (s) antes de un globo, proporcional a su largo (simula tipeo)."""
    return max(BUBBLE_DELAY_MIN, min(len(texto) / 45.0, BUBBLE_DELAY_MAX))
logging.basicConfig(level=logging.DEBUG if ENVIRONMENT == "development" else logging.INFO)
logger = logging.getLogger("agente")

# Índice de negocios { zernio_account_id: BusinessConfig }
NEGOCIOS = cargar_negocios()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await memory.inicializar_db()
    logger.info("Base de datos lista. Negocios: %d. Anuncios: %s", len(NEGOCIOS), ads.ENABLE_ADS)
    yield


app = FastAPI(title="WhatsApp Agentreply (Zernio + OpenRouter)", version="1.0.0", lifespan=lifespan)


# ── Salud ─────────────────────────────────────────────────────
@app.get("/")
async def health():
    return {"status": "ok", "negocios": len(NEGOCIOS), "ads": ads.ENABLE_ADS}


# ── Webhook de Zernio ─────────────────────────────────────────
@app.post("/webhook")
async def webhook(
    request: Request,
    background: BackgroundTasks,
    x_zernio_signature: str | None = Header(default=None),
    x_zernio_event_id: str | None = Header(default=None),
):
    body = await request.body()

    # 1) Verificar firma HMAC
    if not zernio.verificar_firma(body, x_zernio_signature):
        logger.warning("Webhook con firma inválida — rechazado.")
        raise HTTPException(status_code=401, detail="Firma inválida")

    # 2) Parsear JSON
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON inválido")

    # 3) Idempotencia. Si Zernio no manda un id de evento, derivamos una clave
    #    estable del cuerpo para que un reintento no se procese dos veces.
    event_id = x_zernio_event_id or payload.get("id") or hashlib.sha256(body).hexdigest()
    if await memory.evento_ya_procesado(event_id):
        return {"status": "duplicate"}

    # 4) Solo nos interesa message.received
    msg = parsear_message_received(payload)
    if msg is None or msg.es_propio or not msg.texto:
        return {"status": "ignored"}

    # 5) Resolver negocio por account.id
    negocio = NEGOCIOS.get(msg.account_id)
    if negocio is None:
        logger.warning("Mensaje de cuenta no configurada: %s", msg.account_id)
        return {"status": "unknown_account"}

    # 6) Procesar en segundo plano (respondemos 2xx YA para no pasar el límite de 5 s)
    background.add_task(_procesar_mensaje, msg, negocio)
    return {"status": "accepted"}


async def _procesar_mensaje(msg, negocio) -> None:
    """Trabajo pesado: atribución de anuncio + IA + respuesta. Corre fuera del request."""
    try:
        # Asegurar fila de conversación
        await memory.upsert_conversacion(msg.conversation_id, msg.account_id, msg.contacto)

        # Respuesta rápida de precios: si el mensaje pregunta por precios y tenemos el flyer,
        # lo enviamos antes de la respuesta de IA.
        if FLYER_IMAGE_URL and _es_pregunta_de_precios(msg.texto):
            await zernio.enviar_typing(msg.conversation_id, msg.account_id)
            await asyncio.sleep(0.8)
            ok = await zernio.enviar_imagen(
                msg.conversation_id,
                msg.account_id,
                FLYER_IMAGE_URL,
            )
            if ok:
                await asyncio.sleep(1.0)
                await zernio.enviar_mensaje(msg.conversation_id, msg.account_id, PRECIO_RAPIDO)
                logger.info("Flyer de precios enviado a %s", msg.contacto)

        # Atribución de anuncio CTWA (opcional, one-shot)
        if ads.ENABLE_ADS:
            await _atribuir_anuncio(msg, negocio)

        # IA
        historial = await memory.obtener_historial(msg.account_id, msg.contacto)
        respuesta = await brain.generar_respuesta(
            mensaje=msg.texto,
            historial=historial,
            system_prompt=negocio.construir_system_prompt(),
            modelo=negocio.modelo,
        )

        # Partir en globos (1 o más mensajes) para verse más humano
        globos = brain.dividir_en_globos(respuesta, BUBBLE_MAX) if MULTI_BUBBLE else [respuesta]

        # Guardar el turno (texto unido y limpio, sin separadores)
        await memory.guardar_mensaje(msg.account_id, msg.contacto, "user", msg.texto)
        await memory.guardar_mensaje(msg.account_id, msg.contacto, "assistant", "\n\n".join(globos))

        # Enviar cada globo; si son varios, "escribiendo…" + pausa breve entre ellos
        multi = len(globos) > 1
        for globo in globos:
            if multi:
                await zernio.enviar_typing(msg.conversation_id, msg.account_id)
                await asyncio.sleep(_delay_globo(globo))
            await zernio.enviar_mensaje(msg.conversation_id, msg.account_id, globo)
        logger.info("Respuesta (%d globo/s) a %s (%s)", len(globos), msg.contacto, negocio.nombre)
    except Exception as e:  # noqa: BLE001 — no romper el worker de background
        logger.error("Error procesando mensaje: %s", e)


async def _atribuir_anuncio(msg, negocio) -> None:
    """Captura el origen del anuncio y dispara enriquecimiento + LeadSubmitted (one-shot)."""
    if await memory.tiene_ad_source(msg.conversation_id):
        return

    metadata = msg.metadata_ctwa
    # Si el webhook no trajo los ctwa_*, los leemos de la conversación en Zernio.
    if not metadata.get("ctwa_source_id") and not metadata.get("ctwa_clid"):
        conv = await zernio.get_conversation(msg.conversation_id)
        if conv:
            md = conv.get("metadata") or {}
            metadata = {k: v for k, v in md.items() if str(k).startswith("ctwa_")}

    ad_source = await ads.capturar_ad_source(msg.conversation_id, metadata)
    if not ad_source:
        return

    # Enriquecer nombres de campaña (si hay token de Meta)
    await ads.enriquecer_ad_source(msg.conversation_id)

    # Reportar el lead a Meta (idempotente por conversación+evento)
    if await memory.registrar_conversion(msg.conversation_id, "LeadSubmitted", 0.0):
        ok = await zernio.enviar_conversion(
            account_id=msg.account_id,
            conversation_id=msg.conversation_id,
            evento="LeadSubmitted",
            event_id=f"lead_{msg.conversation_id}",
        )
        await memory.actualizar_estado_conversion(
            msg.conversation_id, "LeadSubmitted", "sent" if ok else "error"
        )


# ── Endpoints de anuncios/ROAS (solo si ENABLE_ADS) ───────────
def _check_token(token: str | None) -> None:
    if not REPORT_TOKEN or token != REPORT_TOKEN:
        raise HTTPException(status_code=401, detail="No autorizado")


@app.post("/sale")
async def registrar_venta(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """
    Registra una venta y dispara la conversión Purchase a Meta.
    Body: { 