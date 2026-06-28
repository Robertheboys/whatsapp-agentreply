# agent/memory.py — Memoria y persistencia (SQLite async)
"""
Guarda el historial de conversaciones, las conversaciones (con su origen de anuncio
CTWA), un registro de eventos procesados (idempotencia del webhook), las conversiones
enviadas a Meta y las ventas registradas.

Usa SQLAlchemy async sobre SQLite por defecto (DATABASE_URL). Funciona también con
PostgreSQL si se cambia DATABASE_URL.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/agente.db")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Mensaje(Base):
    """Un turno de conversación (cliente o agente)."""
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(80), index=True)
    contacto: Mapped[str] = mapped_column(String(80), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_ahora)


class Conversacion(Base):
    """Una conversación de Zernio, con su origen de anuncio (ad_source) si aplica."""
    __tablename__ = "conversaciones"

    conversation_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(80), index=True)
    contacto: Mapped[str] = mapped_column(String(80), index=True, default="")
    ad_source: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON serializado
    creado: Mapped[datetime] = mapped_column(DateTime, default=_ahora)


class EventoProcesado(Base):
    """Idempotencia: IDs de eventos de webhook ya manejados."""
    __tablename__ = "eventos_procesados"

    event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_ahora)


class Conversion(Base):
    """Conversión enviada a Meta (vía Zernio)."""
    __tablename__ = "conversiones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(120), index=True)
    evento: Mapped[str] = mapped_column(String(40))  # "LeadSubmitted" | "Purchase" | ...
    valor: Mapped[float] = mapped_column(Float, default=0.0)
    estado: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|error
    ts: Mapped[datetime] = mapped_column(DateTime, default=_ahora)
    __table_args__ = (UniqueConstraint("conversation_id", "evento", name="uq_conv_evento"),)


class Venta(Base):
    """Venta registrada, atribuible a un anuncio."""
    __tablename__ = "ventas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(120), index=True)
    ad_id: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_ahora)


# ── Inicialización ────────────────────────────────────────────
async def inicializar_db() -> None:
    """Crea las tablas si no existen."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ── Idempotencia ──────────────────────────────────────────────
async def evento_ya_procesado(event_id: str) -> bool:
    """True si el evento ya fue manejado. Si no, lo marca como procesado."""
    if not event_id:
        return False
    async with async_session() as session:
        existe = await session.get(EventoProcesado, event_id)
        if existe:
            return True
        session.add(EventoProcesado(event_id=event_id))
        try:
            await session.commit()
            return False
        except IntegrityError:
            # Otra petición concurrente (reintento de Zernio) ya insertó este evento.
            await session.rollback()
            return True


# ── Historial ─────────────────────────────────────────────────
async def guardar_mensaje(account_id: str, contacto: str, role: str, content: str) -> None:
    async with async_session() as session:
        session.add(Mensaje(account_id=account_id, contacto=contacto, role=role, content=content))
        await session.commit()


async def obtener_historial(account_id: str, contacto: str, limite: int = 20) -> list[dict]:
    """Últimos N turnos de un contacto en un negocio, en orden cronológico."""
    async with async_session() as session:
        q = (
            select(Mensaje)
            .where(Mensaje.account_id == account_id, Mensaje.contacto == contacto)
            .order_by(Mensaje.ts.desc())
            .limit(limite)
        )
        filas = (await session.execute(q)).scalars().all()
        filas.reverse()
        return [{"role": m.role, "content": m.content} for m in filas]


async def limpiar_historial(account_id: str, contacto: str) -> None:
    async with async_session() as session:
        q = select(Mensaje).where(Mensaje.account_id == account_id, Mensaje.contacto == contacto)
        for m in (await session.execute(q)).scalars().all():
            await session.delete(m)
        await session.commit()


# ── Conversaciones / ad_source ────────────────────────────────
async def obtener_conversacion(conversation_id: str) -> Conversacion | None:
    async with async_session() as session:
        return await session.get(Conversacion, conversation_id)


async def upsert_conversacion(
    conversation_id: str, account_id: str, contacto: str = ""
) -> Conversacion:
    """Crea la conversación si no existe; devuelve la fila."""
    async with async_session() as session:
        conv = await session.get(Conversacion, conversation_id)
        if conv is None:
            conv = Conversacion(
                conversation_id=conversation_id, account_id=account_id, contacto=contacto
            )
            session.add(conv)
            try:
                await session.commit()
                await session.refresh(conv)
            except IntegrityError:
                # Carrera con otro mensaje de la misma conversación: re-leer la fila existente.
                await session.rollback()
                conv = await session.get(Conversacion, conversation_id)
        return conv


async def guardar_ad_source(conversation_id: str, ad_source: dict) -> None:
    """Persiste (one-shot) el origen de anuncio en la conversación."""
    async with async_session() as session:
        conv = await session.get(Conversacion, conversation_id)
        if conv is None:
            return
        conv.ad_source = json.dumps(ad_source, ensure_ascii=False)
        await session.commit()


async def tiene_ad_source(conversation_id: str) -> bool:
    conv = await obtener_conversacion(conversation_id)
    return bool(conv and conv.ad_source)


def leer_ad_source(conv: Conversacion | None) -> dict | None:
    if conv and conv.ad_source:
        try:
            return json.loads(conv.ad_source)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


# ── Conversiones ──────────────────────────────────────────────
async def registrar_conversion(
    conversation_id: str, evento: str, valor: float, estado: str = "pending"
) -> bool:
    """
    Registra una conversión. Devuelve True si es nueva (no existía antes),
    False si ya estaba registrada (idempotencia por conversación+evento).
    """
    async with async_session() as session:
        q = select(Conversion).where(
            Conversion.conversation_id == conversation_id, Conversion.evento == evento
        )
        if (await session.execute(q)).scalar_one_or_none():
            return False
        session.add(
            Conversion(conversation_id=conversation_id, evento=evento, valor=valor, estado=estado)
        )
        await session.commit()
        return True


async def actualizar_estado_conversion(conversation_id: str, evento: str, estado: str) -> None:
    async with async_session() as session:
        q = select(Conversion).where(
            Conversion.conversation_id == conversation_id, Conversion.evento == evento
        )
        conv = (await session.execute(q)).scalar_one_or_none()
        if conv:
            conv.estado = estado
            await session.commit()


# ── Ventas ────────────────────────────────────────────────────
async def registrar_venta(conversation_id: str, amount: float, ad_id: str | None = None) -> None:
    async with async_session() as session:
        session.add(Venta(conversation_id=conversation_id, amount=amount, ad_id=ad_id))
        await session.commit()


async def ingresos_por_anuncio(dias: int = 30) -> dict[str, float]:
    """Suma de ventas (de los últimos `dias`) agrupada por ad_id (para el ROAS)."""
    corte = datetime.now(timezone.utc) - timedelta(days=dias)
    async with async_session() as session:
        q = select(Venta).where(Venta.ts >= corte)
        filas = (await session.execute(q)).scalars().all()
    agregado: dict[str, float] = {}
    for v in filas:
        if not v.ad_id:
            continue
        agregado[v.ad_id] = agregado.get(v.ad_id, 0.0) + (v.amount or 0.0)
    return agregado
