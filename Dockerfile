FROM python:3.11-slim

WORKDIR /app

# Dependencias primero (mejor cache de capas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código de la app
COPY . .

# Carpeta para la base de datos SQLite (montar como volumen en producción)
RUN mkdir -p /app/data

EXPOSE 8000

# 1 worker: el agente es liviano (I/O-bound) y SQLite no soporta
# múltiples escritores concurrentes bien.
# Forma shell para respetar $PORT cuando el host lo inyecta (Railway, etc.);
# si no está definido, usa 8000.
CMD uvicorn agent.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
