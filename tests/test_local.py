# tests/test_local.py — Simulador de chat en la terminal (sin WhatsApp)
"""
Prueba tu agente sin Zernio ni WhatsApp: eliges un negocio y chateas como si fueras
un cliente. Útil para afinar el system prompt antes de desplegar.

Uso:
    python tests/test_local.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import brain, memory  # noqa: E402
from agent.config import cargar_negocios  # noqa: E402


async def main() -> None:
    await memory.inicializar_db()
    negocios = cargar_negocios()

    if not negocios:
        print("\n  No hay negocios en config/businesses.yaml.")
        print("  Copia config/businesses.example.yaml → config/businesses.yaml y configúralo.\n")
        return

    lista = list(negocios.values())
    print("\n" + "=" * 55)
    print("   AgentKit WhatsApp — Test Local")
    print("=" * 55)
    for i, n in enumerate(lista, 1):
        print(f"   {i}. {n.nombre}  (agente: {n.agente}, modelo: {n.modelo})")
    print("-" * 55)

    if len(lista) == 1:
        negocio = lista[0]
    else:
        try:
            idx = int(input(f"Elige un negocio (1-{len(lista)}): ").strip() or "1")
        except (ValueError, EOFError, KeyboardInterrupt):
            idx = 1
        negocio = lista[max(1, min(idx, len(lista))) - 1]

    contacto = "test-local-001"
    print(f"\n  Chateando con: {negocio.nombre}")
    print("  Comandos: 'limpiar' borra el historial, 'salir' termina.\n")

    system_prompt = negocio.construir_system_prompt()

    while True:
        try:
            mensaje = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nFin del test.")
            break
        if not mensaje:
            continue
        if mensaje.lower() == "salir":
            print("Fin del test.")
            break
        if mensaje.lower() == "limpiar":
            await memory.limpiar_historial(negocio.zernio_account_id, contacto)
            print("[Historial borrado]\n")
            continue

        historial = await memory.obtener_historial(negocio.zernio_account_id, contacto)
        print("\nAgente: ", end="", flush=True)
        respuesta = await brain.generar_respuesta(
            mensaje=mensaje,
            historial=historial,
            system_prompt=system_prompt,
            modelo=negocio.modelo,
        )
        print(respuesta + "\n")

        await memory.guardar_mensaje(negocio.zernio_account_id, contacto, "user", mensaje)
        await memory.guardar_mensaje(negocio.zernio_account_id, contacto, "assistant", respuesta)


if __name__ == "__main__":
    asyncio.run(main())
