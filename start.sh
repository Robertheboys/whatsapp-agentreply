#!/bin/bash
# AgentKit WhatsApp (Zernio + OpenRouter) — Verificación de entorno
# El usuario ejecuta: bash start.sh

set -e

echo ""
echo "==========================================================="
echo "   AgentKit WhatsApp — Zernio + OpenRouter"
echo "==========================================================="
echo ""
echo "  Preparando tu entorno..."
echo ""

# ── Verificar Python 3.11+ ────────────────────────────────────
echo "  [1/4] Verificando Python..."
if ! command -v python3 &> /dev/null; then
    echo "  ERROR: Python 3 no encontrado. Descárgalo en https://python.org/downloads"
    exit 1
fi
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo "  ERROR: Necesitas Python 3.11+. Versión actual: $(python3 --version)"
    exit 1
fi
echo "  OK — $(python3 --version)"

# ── Carpetas base ─────────────────────────────────────────────
echo "  [2/4] Preparando carpetas..."
mkdir -p config/knowledge data
touch config/knowledge/.gitkeep
echo "  OK"

# ── .env ──────────────────────────────────────────────────────
echo "  [3/4] Archivo .env..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  Creado .env desde la plantilla (recuerda llenar tus claves)."
else
    echo "  Ya existe .env"
fi

# ── Claude Code (opcional, para onboarding por agente) ────────
echo "  [4/4] Claude Code (opcional)..."
if command -v claude &> /dev/null; then
    echo "  OK — Claude Code instalado"
else
    echo "  (No instalado) Para el setup guiado: npm install -g @anthropic-ai/claude-code"
fi

echo ""
echo "==========================================================="
echo ""
echo "  Siguiente paso — setup guiado por agente:"
echo ""
echo "    claude"
echo "    /setup-agente"
echo ""
echo "  O manual: edita .env y config/businesses.yaml, luego:"
echo "    pip install -r requirements.txt"
echo "    python tests/test_local.py"
echo ""
echo "==========================================================="
echo ""
