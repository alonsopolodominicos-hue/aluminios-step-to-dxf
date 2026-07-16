FROM python:3.12-slim

# cadquery (vía cadquery-ocp, bindings de OpenCASCADE) enlaza contra
# librerías gráficas del sistema aunque el uso aquí sea 100% headless —
# sin ellas, "import cadquery" falla con undefined symbol / libGL.so.1.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libgl1 \
    libglu1-mesa \
    libgomp1 \
    libxrender1 \
    libxi6 \
    libsm6 \
    libxext6 \
    libglib2.0-0 \
    && update-ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# Render inyecta $PORT (por defecto 10000) y espera que el contenedor
# escuche ahí — confiar en el autodetect de $EXPOSE es frágil. Forma shell
# (no exec) para que ${PORT:-8000} se expanda; 8000 es el valor para
# "docker run" local, donde no existe la variable PORT.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
