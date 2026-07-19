# Servicio STEP → DXF — Aluminios Cariñena

Microservicio FastAPI que convierte modelos STEP en planos DXF de corte
(con taladros y mecanizaciones) y vistas previas STL. Es la "Parte B" del
conversor 3D→DXF de la aplicación de Aluminios Cariñena.

- `POST /previsualizar` — ZIP con un STL por pieza (visor 3D)
- `POST /convertir` — ZIP con un DXF multivista por pieza + manifest + STL del conjunto
- `GET /salud` — comprobación de vida

Autenticación: cabecera `Authorization: Bearer <secreto|token firmado>`.
Requiere la variable de entorno `STEP_CONVERTER_SECRET` (no hay ningún
secreto en el código).

Este repo es un espejo de despliegue: el desarrollo vive en el repo
principal de la aplicación y esta copia se actualiza desde allí.
