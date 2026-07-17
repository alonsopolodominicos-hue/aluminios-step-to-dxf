"""POST /convertir — recibe un STEP (.step/.stp) con piezas de perfil de
aluminio y devuelve un ZIP con un DXF esquemático de corte por pieza
(longitud, ángulos de extremo, sección y taladros), un .bpp por pieza con
taladros de canto (lo añade src/app/api/admin/convertir-step/route.ts a
partir de analisis.json), manifest.txt, y conjunto_completo.stl — el
ensamblaje entero en 3D, de referencia, sin separar en piezas.

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
import json
import os
import tempfile
import zipfile
from typing import Optional

import cadquery as cq
from cadquery import exporters
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


async def _cargar_step(file: UploadFile):
    """Lee el UploadFile, lo escribe a un temporal y lo carga con cadquery.
    Devuelve (nombre_original, resultado_cadquery). Lanza HTTPException 400
    si el archivo está vacío o no se puede leer como STEP."""
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

    return nombre_original, resultado


@app.get('/')
@app.get('/salud')
async def salud():
    return JSONResponse({'estado': 'ok'})


@app.post('/previsualizar')
async def previsualizar(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    """Devuelve un ZIP con un STL por sólido del STEP para poder verlos en 3D
    antes de convertir, cada uno pintable de un color distinto en el visor —
    un único STL fusionado no distingue piezas que se solapan o quedan casi
    de canto desde el ángulo de cámara por defecto. No hace ningún análisis,
    solo tesela la geometría de cada sólido tal cual viene."""
    require_secreto_compartido(authorization)
    _, resultado = await _cargar_step(file)

    solidos = resultado.solids().vals()
    if not solidos:
        raise HTTPException(status_code=400, detail='El STEP no contiene ningún sólido')

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for idx, solido in enumerate(solidos):
            tmp_stl = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.stl', delete=False) as tmp:
                    tmp_stl = tmp.name
                exporters.export(solido, tmp_stl, exportType='STL')
                with open(tmp_stl, 'rb') as f:
                    zf.writestr(f'pieza_{idx:03d}.stl', f.read())
            except Exception as e:
                print(f'[previsualizar] sólido {idx} no se pudo teselar: {type(e).__name__}: {e}')
            finally:
                if tmp_stl and os.path.exists(tmp_stl):
                    os.unlink(tmp_stl)

    zip_buffer.seek(0)
    return Response(content=zip_buffer.getvalue(), media_type='application/zip')


@app.post('/')
@app.post('/convertir')
async def convertir(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    require_secreto_compartido(authorization)

    nombre_original, resultado = await _cargar_step(file)

    solidos = resultado.solids().vals()
    if not solidos:
        raise HTTPException(status_code=400, detail='El STEP no contiene ningún sólido')

    zip_buffer = io.BytesIO()
    manifest_lines = ['Pieza\tLongitud\tSección\tÁngulo A\tÁngulo B\tTaladros\tUnidad']
    omitidas_lines = ['Sólido\tMotivo']
    piezas_json = []
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
                f"{analisis['angulo_corte_a']:.1f}°\t{analisis['angulo_corte_b']:.1f}°\t"
                f"{len(analisis['taladros'])}\tmm"
            )
            piezas_json.append({
                'nombre_capa': nombre_capa,
                'longitud': round(analisis['longitud'], 2),
                'seccion': [round(s, 2) for s in analisis['seccion']],
                'seccion_regular': analisis['seccion_regular'],
                'angulo_corte_a': round(analisis['angulo_corte_a'], 1),
                'angulo_corte_b': round(analisis['angulo_corte_b'], 1),
                'taladros': analisis['taladros'],
            })

        if piezas_generadas == 0:
            raise HTTPException(
                status_code=400,
                detail='Ningún sólido del STEP se pudo interpretar como pieza de perfil (caja de 6 caras)',
            )

        zf.writestr('manifest.txt', '\n'.join(manifest_lines))
        if len(omitidas_lines) > 1:
            zf.writestr('omitidas.txt', '\n'.join(omitidas_lines))
        zf.writestr('analisis.json', json.dumps(piezas_json, ensure_ascii=False))

        # Conjunto completo en 3D (todas las piezas montadas, sin separar) —
        # de referencia, no es un plano acotado. Mismo export que /previsualizar.
        tmp_stl = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.stl', delete=False) as tmp:
                tmp_stl = tmp.name
            exporters.export(resultado.val(), tmp_stl, exportType='STL')
            with open(tmp_stl, 'rb') as f:
                zf.writestr('conjunto_completo.stl', f.read())
        except Exception as e:
            print(f'[convertir] no se pudo exportar el conjunto completo a STL: {type(e).__name__}: {e}')
        finally:
            if tmp_stl and os.path.exists(tmp_stl):
                os.unlink(tmp_stl)

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
