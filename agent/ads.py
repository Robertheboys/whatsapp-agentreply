# agent/ads.py — Atribución de anuncios Click-to-WhatsApp (CTWA) + ROAS
"""
Módulo OPCIONAL (se activa con ENABLE_ADS=true).

Hace 3 cosas, imitando en pequeño el patrón de simplemas-suite:
1. CAPTURAR: guarda el origen del anuncio (ctwa_*) en cada conversación que viene de un
   anuncio de Meta. Zernio ya lo persiste en conversation.metadata; aquí lo copiamos a
   nuestra base para reportes y atribución.
2. ENRIQUECER: con el ad_id (ctwa_source_id) consulta la Meta Graph API para añadir
   nombre de anuncio / ad set / campaña.
3. ROAS: ingresos (ventas registradas) ÷ gasto del anuncio (Meta Insights), por anuncio.

Las conversiones de vuelta a Meta (LeadSubmitted/Purchase) se envían vía agent/zernio.py.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

from agent import memory

load_dotenv()

logger = logging.getLogger("agente")

ENABLE_ADS = os.getenv("ENABLE_ADS", "false").lower() in ("1", "true", "yes", "si", "sí")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v21.0")
META_GRAPH_BASE = f"https://graph.facebook.com/{META_GRAPH_VERSION}"

_TIMEOUT = httpx.Timeout(20.0)


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 1. Capturar origen del anuncio ────────────────────────────
def _extraer_ad_source(metadata_ctwa: dict) -> dict | None:
    """Construye el objeto ad_source desde los campos ctwa_* de la conversación."""
    source_id = metadata_ctwa.get("ctwa_source_id")
    clid = metadata_ctwa.get("ctwa_clid")
    if not source_id and not clid:
        return None
    return {
        "ctwa_source_id": source_id,
        "ctwa_clid": clid,
        "ctwa_headline": metadata_ctwa.get("ctwa_headline"),
        "ctwa_source_url": metadata_ctwa.get("ctwa_source_url"),
        "ctwa_source_type": metadata_ctwa.get("ctwa_source_type", "ad"),
        "ad_name": None,
        "campaign_name": None,
        "captured_at": _ahora_iso(),
        "enriched_at": None,
    }


async def capturar_ad_source(conversation_id: str, metadata_ctwa: dict) -> dict | None:
    """
    Guarda (one-shot) el origen del anuncio en la conversación. Devuelve el ad_source
    capturado, o None si la conversación no vino de un anuncio o ya tenía origen.
    """
    if not ENABLE_ADS:
        return None
    if await memory.tiene_ad_source(conversation_id):
        return None
    ad_source = _extraer_ad_source(metadata_ctwa or {})
    if not ad_source:
        return None
    await memory.guardar_ad_source(conversation_id, ad_source)
    logger.info(
        "Conversación %s vino del anuncio %s (%s)",
        conversation_id,
        ad_source.get("ctwa_source_id"),
        ad_source.get("ctwa_headline"),
    )
    return ad_source


# ── 2. Enriquecer con Meta Graph API ──────────────────────────
async def enriquecer_ad_source(conversation_id: str) -> None:
    """
    Añade nombre de anuncio/ad set/campaña usando el ad_id. Requiere META_ACCESS_TOKEN.
    Patrón equivalente a enrichAdSource() de simplemas-suite.
    """
    if not ENABLE_ADS or not META_ACCESS_TOKEN:
        return
    conv = await memory.obtener_conversacion(conversation_id)
    ad_source = memory.leer_ad_source(conv)
    if not ad_source or ad_source.get("enriched_at"):
        return
    ad_id = ad_source.get("ctwa_source_id")
    if not ad_id:
        return

    url = f"{META_GRAPH_BASE}/{ad_id}"
    params = {
        "fields": "name,adset{id,name},campaign{id,name,objective}",
        "access_token": META_ACCESS_TOKEN,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, params=params)
        if r.status_code >= 300:
            logger.warning("Meta enrich %s: %s", r.status_code, r.text[:200])
            ad_source["enrich_error"] = f"{r.status_code}"
        else:
            ad = r.json()
            ad_source["ad_name"] = ad.get("name")
            ad_source["adset_id"] = (ad.get("adset") or {}).get("id")
            ad_source["adset_name"] = (ad.get("adset") or {}).get("name")
            ad_source["campaign_id"] = (ad.get("campaign") or {}).get("id")
            ad_source["campaign_name"] = (ad.get("campaign") or {}).get("name")
            ad_source["campaign_objective"] = (ad.get("campaign") or {}).get("objective")
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Meta enrich error: %s", e)
        ad_source["enrich_error"] = str(e)

    ad_source["enriched_at"] = _ahora_iso()
    await memory.guardar_ad_source(conversation_id, ad_source)


# ── 3. ROAS ───────────────────────────────────────────────────
async def _gasto_por_anuncio(ad_account_id: str, dias: int) -> dict[str, float]:
    """Gasto por anuncio desde Meta Insights. {} si no hay token o ad_account."""
    if not META_ACCESS_TOKEN or not ad_account_id:
        return {}
    # time_range acepta cualquier rango (date_preset solo admite valores fijos como last_30d).
    hasta = datetime.now(timezone.utc).date()
    desde = hasta - timedelta(days=dias)
    url = f"{META_GRAPH_BASE}/{ad_account_id}/insights"
    params = {
        "level": "ad",
        "fields": "ad_id,spend",
        "time_range": json.dumps({"since": desde.isoformat(), "until": hasta.isoformat()}),
        "limit": "500",
        "access_token": META_ACCESS_TOKEN,
    }
    gasto: dict[str, float] = {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, params=params)
        if r.status_code >= 300:
            logger.warning("Meta insights %s: %s", r.status_code, r.text[:200])
            return {}
        for fila in r.json().get("data", []):
            ad_id = fila.get("ad_id")
            if ad_id:
                gasto[ad_id] = float(fila.get("spend") or 0.0)
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Meta insights error: %s", e)
    return gasto


async def reporte_roas(ad_account_id: str | None = None, dias: int = 30) -> dict:
    """
    Reporte simple de ROAS por anuncio:
        ingresos (ventas registradas en SQLite) ÷ gasto (Meta Insights).
    Si no hay token/ad_account, devuelve solo ingresos por anuncio.
    """
    ingresos = await memory.ingresos_por_anuncio(dias)
    gasto = await _gasto_por_anuncio(ad_account_id or "", dias) if ad_account_id else {}

    anuncios = []
    for ad_id in sorted(set(ingresos) | set(gasto)):
        rev = round(ingresos.get(ad_id, 0.0), 2)
        spend = round(gasto.get(ad_id, 0.0), 2)
        roas = round(rev / spend, 2) if spend > 0 else None
        anuncios.append({"ad_id": ad_id, "ingresos": rev, "gasto": spend, "roas": roas})

    return {"dias": dias, "anuncios": anuncios}
