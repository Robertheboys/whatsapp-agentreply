# tests/test_smoke.py — Pruebas mínimas sin red (firma HMAC + parseo de webhook)
"""
Verifica las dos piezas críticas que no necesitan llamadas externas:
1. La verificación de firma HMAC-SHA256 del webhook de Zernio.
2. El parseo del payload `message.received` (enrutado por account.id + CTWA).

Uso:
    python tests/test_smoke.py        # corre sin pytest
    pytest tests/test_smoke.py        # o con pytest
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Forzar un secreto conocido ANTES de importar el módulo (se lee en import).
os.environ["ZERNIO_WEBHOOK_SECRET"] = "secreto-de-prueba-123"

from agent import zernio  # noqa: E402
from agent.models import parsear_message_received  # noqa: E402


def test_firma_valida_pasa():
    body = b'{"event":"message.received","id":"evt_1"}'
    firma = hmac.new(b"secreto-de-prueba-123", body, hashlib.sha256).hexdigest()
    assert zernio.verificar_firma(body, firma) is True


def test_firma_alterada_se_rechaza():
    body = b'{"event":"message.received","id":"evt_1"}'
    firma_mala = "0" * 64
    assert zernio.verificar_firma(body, firma_mala) is False
    # Body manipulado con firma del original tampoco pasa
    firma_ok = hmac.new(b"secreto-de-prueba-123", body, hashlib.sha256).hexdigest()
    assert zernio.verificar_firma(body + b"x", firma_ok) is False


def test_parseo_message_received_basico():
    payload = {
        "id": "evt_abc",
        "event": "message.received",
        "message": {"id": "m1", "text": "Hola", "direction": "inbound", "from": "51999000111"},
        "conversation": {"id": "conv_abc123"},
        "account": {"id": "acct_dontape"},
        "timestamp": "2026-06-26T21:30:00Z",
    }
    msg = parsear_message_received(payload)
    assert msg is not None
    assert msg.account_id == "acct_dontape"
    assert msg.conversation_id == "conv_abc123"
    assert msg.texto == "Hola"
    assert msg.es_propio is False
    assert msg.contacto == "51999000111"


def test_parseo_ignora_salientes_y_no_message():
    saliente = {
        "event": "message.received",
        "message": {"id": "m2", "text": "eco", "isOutbound": True},
        "conversation": {"id": "c1"},
        "account": {"id": "a1"},
    }
    msg = parsear_message_received(saliente)
    assert msg is not None and msg.es_propio is True

    otro_evento = {"event": "message.sent", "message": {}, "conversation": {}, "account": {}}
    assert parsear_message_received(otro_evento) is None


def test_parseo_captura_ctwa():
    payload = {
        "event": "message.received",
        "message": {"id": "m3", "text": "Vi su anuncio", "from": "51999000222"},
        "conversation": {
            "id": "conv_xyz",
            "metadata": {
                "ctwa_source_id": "1200000000",
                "ctwa_headline": "Oferta de hoy",
                "ctwa_clid": "AbCd123",
                "otro_campo": "ignorar",
            },
        },
        "account": {"id": "acct_battery"},
    }
    msg = parsear_message_received(payload)
    assert msg is not None
    assert msg.metadata_ctwa.get("ctwa_source_id") == "1200000000"
    assert msg.metadata_ctwa.get("ctwa_headline") == "Oferta de hoy"
    assert "otro_campo" not in msg.metadata_ctwa  # solo se quedan los ctwa_*


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fallos = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            fallos += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns) - fallos}/{len(fns)} pruebas OK")
    return fallos


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
