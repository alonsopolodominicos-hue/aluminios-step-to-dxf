"""POST /convertir — recibe un STEP (.step/.stp) con piezas de perfil de
aluminio y devuelve un ZIP con un DXF esquemático de corte por pieza
(longitud, ángulos de extremo y sección), más un manifest.txt.

Servicio Docker aparte (no cabe como función serverless de Vercel: el wheel
de cadquery/OCP pesa varios cientos de MB). Ver
/Users/alonsopolo/.claude/plans/snuggly-kindling-peach.md para el contexto
completo — construido sobre la Parte A ya en producción
(api/cam-separar-dxf.py), que separa DXF combinados pieza por pieza; esto
hace lo mismo pero generando el DXF desde cero a partir de un STEP 3D.

Piezas que no son una caja de 6 caras planas (herraje: tornillos, bisagras,
geometría curva) se omiten y se listan aparte en el manifest — no se genera
un DXF inventado para ellas.
"""
import hmac
import io
import os
import tempfile
import zipfile
from typing import Optional

import cadquery as cq
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from analisis import analizar_solido
from dxf import generar_dxf_pieza

app = FastAPI()

EXTENSIONES_VALIDAS = {'.step', '.stp'}


# ── Autenticación ─────────────────────────────────────────────────────────
#
# Este servicio NO verifica tokens de Firebase directamente — lo intentamos
# (require_user() con firebase-admin, igual que api/cam-separar-dxf.py) pero
# el contenedor de Render no puede completar la conexión saliente a
# googleapis.com para descargar los certificados públicos: Google devuelve
# 403 a la IP compartida de salida de Render (bloqueo de reputación de IP,
# no de nuestra credencial — DNS y TLS funcionan bien).
#
# En su lugar, src/app/api/admin/convertir-step/route.ts (Next.js/Vercel) es
# el único que verifica la sesión real del usuario — verifyIdToken() nunca ha
# fallado ahí — y llama a este servicio con un secreto compartido fijo
# (STEP_CONVERTER_SECRET, igual en ambos lados). Este servicio nunca debe
# quedar expuesto sin ese secreto: no vuelve a comprobar quién es el usuario.

def require_secreto_compartido(authorization: Optional[str]) -> None:
    secreto = os.environ.get('STEP_CONVERTER_SECRET')
    if not secreto:
        raise HTTPException(status_code=500, detail='Falta STEP_CONVERTER_SECRET en las variables de entorno del servicio')
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Falta autenticación de servicio')
    recibido = authorization[len('Bearer '):]
    if not hmac.compare_digest(recibido, secreto):
        raise HTTPException(status_code=401, detail='Autenticación de servicio inválida')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    cleaned = ''.join(c if (c.isalnum() or c in ('-', '_')) else '_' for c in name.strip().replace(' ', '_'))
    return cleaned[:48] or 'pieza'


@app.get('/')
@app.get('/salud')
async def salud():
    return JSONResponse({'estado': 'ok'})


@app.post('/')
@app.post('/convertir')
async def convertir(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    require_secreto_compartido(authorization)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail='El archivo está vacío')

    nombre_original = file.filename or 'pieza.stp'
    _, ext = os.path.splitext(nombre_original.lower())
    if ext not in EXTENSIONES_VALIDAS:
        ext = '.stp'

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        resultado = cq.importers.importStep(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'No se pudo leer el STEP: {e}')
    finally:
        if tmp_path:
            os.unlink(tmp_path)

    solidos = resultado.solids().vals()
    if not solidos:
        raise HTTPException(status_code=400, detail='El STEP no contiene ningún sólido')

    zip_buffer = io.BytesIO()
    manifest_lines = ['Pieza\tLongitud\tSección\tÁngulo A\tÁngulo B\tUnidad']
    omitidas_lines = ['Sólido\tMotivo']
    piezas_generadas = 0

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for idx, solido in enumerate(solidos):
            analisis = analizar_solido(solido)
            if not analisis['ok']:
                omitidas_lines.append(f"{idx}\t{analisis['motivo']}")
                continue

            piezas_generadas += 1
            nombre_capa = _safe_filename(f"pieza_{idx:03d}_L{analisis['longitud']:.0f}")
            doc = generar_dxf_pieza(nombre_capa, analisis)
            nombre_archivo = f'{nombre_capa}.dxf'
            buffer_pieza = io.StringIO()
            doc.write(buffer_pieza)
            zf.writestr(nombre_archivo, buffer_pieza.getvalue())

            marca = '' if analisis['seccion_regular'] else ' (aprox.)'
            manifest_lines.append(
                f"{nombre_archivo}\t{analisis['longitud']:.2f}\t"
                f"{analisis['seccion'][0]:.2f}x{analisis['seccion'][1]:.2f}{marca}\t"
                f"{analisis['angulo_corte_a']:.1f}°\t{analisis['angulo_corte_b']:.1f}°\tmm"
            )

        if piezas_generadas == 0:
            raise HTTPException(
                status_code=400,
                detail='Ningún sólido del STEP se pudo interpretar como pieza de perfil (caja de 6 caras)',
            )

        zf.writestr('manifest.txt', '\n'.join(manifest_lines))
        if len(omitidas_lines) > 1:
            zf.writestr('omitidas.txt', '\n'.join(omitidas_lines))

    zip_buffer.seek(0)
    base_nombre = nombre_original.rsplit('.', 1)[0]
    nombre_zip = f'{_safe_filename(base_nombre)}_dxf.zip'
    return Response(
        content=zip_buffer.getvalue(),
        media_type='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename="{nombre_zip}"',
            'X-Piezas-Count': str(piezas_generadas),
            'X-Piezas-Omitidas': str(len(omitidas_lines) - 1),
            'Access-Control-Expose-Headers': 'X-Piezas-Count, X-Piezas-Omitidas, Content-Disposition',
        },
    )
