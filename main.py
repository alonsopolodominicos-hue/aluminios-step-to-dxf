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
import base64
import io
import json
import os
import tempfile
import zipfile
from typing import Optional

import cadquery as cq
import firebase_admin
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from firebase_admin import auth as fb_auth
from firebase_admin import credentials

from analisis import analizar_solido
from dxf import generar_dxf_pieza

app = FastAPI()

EXTENSIONES_VALIDAS = {'.step', '.stp'}


# ── Autenticación (mismo patrón que api/cam-separar-dxf.py) ─────────────────

def _firebase_app() -> firebase_admin.App:
    if firebase_admin._apps:
        return firebase_admin.get_app()
    b64 = os.environ.get('FIREBASE_SERVICE_ACCOUNT_BASE64')
    if not b64:
        raise RuntimeError('Falta FIREBASE_SERVICE_ACCOUNT_BASE64 en las variables de entorno')
    service_account_info = json.loads(base64.b64decode(b64).decode('utf-8'))
    return firebase_admin.initialize_app(credentials.Certificate(service_account_info))


def require_user(authorization: Optional[str]) -> str:
    """Exige solo sesión válida (sin el claim admin) — la usan tanto el panel
    de admin como la app móvil de los trabajadores, mismo criterio que
    requireUser() en src/lib/serverAuth.ts y que api/cam-separar-dxf.py."""
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Falta token de autenticación')
    token = authorization[len('Bearer '):]
    _firebase_app()
    try:
        decoded = fb_auth.verify_id_token(token)
    except Exception as e:
        # El motivo real (proyecto de Firebase distinto, token caducado, reloj
        # desincronizado...) se queda en los logs de Render — al cliente solo
        # le llega el mensaje genérico, nunca detalles de la credencial.
        print(f'[require_user] verify_id_token falló: {type(e).__name__}: {e}')
        raise HTTPException(status_code=401, detail='Token inválido o expirado')
    return decoded.get('uid', '')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    cleaned = ''.join(c if (c.isalnum() or c in ('-', '_')) else '_' for c in name.strip().replace(' ', '_'))
    return cleaned[:48] or 'pieza'


@app.get('/')
@app.get('/salud')
async def salud():
    # project_id no es sensible (es público en la config del cliente Firebase)
    # y permite comprobar en un segundo si esta credencial es del mismo
    # proyecto que usa la app, sin tener que rebuscar en logs.
    project_id = None
    try:
        b64 = os.environ.get('FIREBASE_SERVICE_ACCOUNT_BASE64')
        if b64:
            project_id = json.loads(base64.b64decode(b64).decode('utf-8')).get('project_id')
    except Exception:
        project_id = 'ERROR_AL_LEER_CREDENCIAL'
    return JSONResponse({'estado': 'ok', 'firebase_project_id': project_id})


@app.get('/diag-red')
async def diag_red():
    """Diagnóstico temporal: reproduce la misma llamada de red que hace
    firebase-admin al verificar un token, para ver la excepción real en vez
    del mensaje genérico de CertificateFetchError. Se retira una vez
    resuelto — no expone nada sensible, solo conectividad saliente."""
    import socket
    import time
    resultado = {}

    for host in ['www.googleapis.com', 'pypi.org', '8.8.8.8']:
        try:
            t0 = time.time()
            ip = socket.gethostbyname(host) if host != '8.8.8.8' else host
            resultado[f'dns_{host}'] = {'ok': True, 'ip': ip, 'ms': round((time.time() - t0) * 1000)}
        except Exception as e:
            resultado[f'dns_{host}'] = {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    import requests
    for url in [
        'https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com',
        'https://pypi.org',
    ]:
        try:
            t0 = time.time()
            r = requests.get(url, timeout=8)
            resultado[f'http_{url}'] = {'ok': True, 'status': r.status_code, 'ms': round((time.time() - t0) * 1000)}
        except Exception as e:
            resultado[f'http_{url}'] = {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    return JSONResponse(resultado)


@app.post('/')
@app.post('/convertir')
async def convertir(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    require_user(authorization)

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
