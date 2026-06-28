# agent/config.py — Carga de configuración multi-negocio
"""
Lee config/businesses.yaml y construye un índice { zernio_account_id: BusinessConfig }
para que el webhook enrute cada mensaje al negocio correcto según el account.id de Zernio.

Cada negocio define su propia personalidad (system prompt), modelo de OpenRouter y,
opcionalmente, su cuenta de anuncios de Meta. El conocimiento de la carpeta
config/knowledge/<carpeta>/ se incorpora al system prompt.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger("agente")

CONFIG_PATH = os.getenv("BUSINESSES_CONFIG", "config/businesses.yaml")
KNOWLEDGE_DIR = os.getenv("KNOWLEDGE_DIR", "config/knowledge")

# Modelo por defecto si un negocio no especifica uno (configurable y barato).
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "openai/gpt-4o-mini")

# Extensiones de texto que se incorporan al prompt desde la carpeta de conocimiento.
KNOWLEDGE_EXTS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml"}
KNOWLEDGE_MAX_CHARS = 12000  # corte de seguridad por negocio


@dataclass
class BusinessConfig:
    """Configuración de un negocio / número de WhatsApp."""

    zernio_account_id: str
    nombre: str
    agente: str = "Asistente"
    modelo: str = DEFAULT_MODEL
    tono: str = "amigable y profesional"
    system_prompt: str = ""
    knowledge_dir: str | None = None
    meta_ad_account_id: str | None = None
    raw: dict = field(default_factory=dict)

    def construir_system_prompt(self) -> str:
        """System prompt final: el definido + el conocimiento del negocio."""
        base = self.system_prompt.strip() or (
            f"Eres {self.agente}, el asistente virtual de {self.nombre}. "
            f"Tu tono es {self.tono}. Responde siempre en español, de forma breve y útil. "
            f"Si no sabes algo, ofrece conectar con una persona del equipo y nunca inventes datos."
        )
        conocimiento = self._cargar_conocimiento()
        if conocimiento:
            base += "\n\n## Información del negocio\n" + conocimiento
        return base

    def _cargar_conocimiento(self) -> str:
        carpeta = self.knowledge_dir
        if not carpeta:
            return ""
        ruta = Path(KNOWLEDGE_DIR) / carpeta
        if not ruta.is_dir():
            return ""
        partes: list[str] = []
        total = 0
        for archivo in sorted(ruta.iterdir()):
            if archivo.name.startswith(".") or not archivo.is_file():
                continue
            if archivo.suffix.lower() not in KNOWLEDGE_EXTS:
                continue
            try:
                contenido = archivo.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError as e:
                logger.warning("No se pudo leer %s: %s", archivo, e)
                continue
            if not contenido:
                continue
            restante = KNOWLEDGE_MAX_CHARS - total
            if restante <= 0:
                logger.warning("Conocimiento de %s truncado en %d chars", self.nombre, KNOWLEDGE_MAX_CHARS)
                break
            recorte = contenido[:restante]
            total += len(recorte)
            partes.append(f"### {archivo.name}\n{recorte}")
        return "\n\n".join(partes)


def cargar_negocios(path: str = CONFIG_PATH) -> dict[str, BusinessConfig]:
    """
    Carga config/businesses.yaml → { zernio_account_id: BusinessConfig }.

    Formato del YAML:
        businesses:
          - zernio_account_id: "acct_xxx"
            nombre: "Mi Negocio"
            agente: "Sofía"
            modelo: "openai/gpt-4o-mini"
            tono: "amigable"
            knowledge_dir: "mi-negocio"
            meta_ad_account_id: "act_123"   # opcional
            system_prompt: |
              Eres Sofía...
    """
    ruta = Path(path)
    if not ruta.is_file():
        logger.error("No existe %s. Crea uno desde config/businesses.example.yaml.", path)
        return {}

    data = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    negocios_raw = data.get("businesses") or []

    indice: dict[str, BusinessConfig] = {}
    for item in negocios_raw:
        account_id = str(item.get("zernio_account_id") or "").strip()
        if not account_id:
            logger.warning("Negocio sin zernio_account_id, se omite: %s", item.get("nombre"))
            continue
        cfg = BusinessConfig(
            zernio_account_id=account_id,
            nombre=str(item.get("nombre") or "Negocio"),
            agente=str(item.get("agente") or "Asistente"),
            modelo=str(item.get("modelo") or DEFAULT_MODEL),
            tono=str(item.get("tono") or "amigable y profesional"),
            system_prompt=str(item.get("system_prompt") or ""),
            knowledge_dir=item.get("knowledge_dir"),
            meta_ad_account_id=item.get("meta_ad_account_id"),
            raw=item,
        )
        indice[account_id] = cfg

    if not indice:
        logger.error("config/businesses.yaml no tiene negocios válidos.")
    else:
        logger.info("Negocios cargados: %s", ", ".join(c.nombre for c in indice.values()))
    return indice
