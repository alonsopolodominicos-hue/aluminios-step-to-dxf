FROM python:3.12-slim

# cadquery (vía cadquery-ocp, bindings de OpenCASCADE) enlaza contra
# librerías gráficas del sistema aunque el uso aquí sea 100% headless —
# sin ellas, "import cadquery" falla con undefined symbol / libGL.so.1.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglu1-mesa \
    libgomp1 \
    libxrender1 \
    libxi6 \
    libsm6 \
    libxext6 \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
