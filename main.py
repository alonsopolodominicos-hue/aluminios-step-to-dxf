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
from dataclasses import dataclass
import hashlib
import hmac
import io
import json
import math
import os
import tempfile
import time
import zipfile
from typing import Optional

import cadquery as cq
from cadquery import exporters
from fastapi.concurrency import run_in_threadpool
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from analisis import analizar_solido
from analisis_panel import analizar_solido_panel
from ensamblaje import leer_componentes, solo_piezas, agrupar_iguales, tiene_nombres_utiles
from desarrollo import analizar_panel_curvado
from calco2d import calcar_solido
from medidor_dxf import medir_dxf
from dxf import generar_dxf_pieza
from dxf_panel import generar_dxf_panel

# Versión del motor de análisis/DXF. Se ve en /salud para poder comprobar
# que un despliegue de Render ha entrado de verdad.
#   2026-08-13: paneles de canto curvo, huecos pasantes, cara buena,
#   cajeados con contorno real, taladros de canto y numeración sin saltos.
VERSION_ANALISIS = '2026-08-13i'

app = FastAPI()

# El navegador sube los STEP DIRECTAMENTE aquí (Vercel rechaza cuerpos de más
# de 4.5 MB — límite de plataforma), autenticado con un token firmado de corta
# duración que emite Vercel. CORS abierto: la autorización real es el token.
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['GET', 'POST'],
    allow_headers=['authorization', 'content-type'],
    expose_headers=['content-disposition', 'x-piezas-count', 'x-piezas-omitidas'],
)

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

def _token_firmado_valido(token: str, secreto: str) -> bool:
    """Token de subida directa desde el navegador: "firmado.<exp>.<hmac>" con
    hmac = HMAC-SHA256(secreto, "subida:<exp>"). Lo emite Vercel (que sí ha
    verificado la sesión del usuario o el token público) con ~10 min de vida;
    el navegador lo usa para subir el STEP directamente aquí, evitando el
    límite de 4.5 MB de cuerpo de las funciones de Vercel."""
    try:
        prefijo, exp_s, firma = token.split('.', 2)
        if prefijo != 'firmado':
            return False
        exp = int(exp_s)
        if exp < time.time():
            return False
        esperado = hmac.new(secreto.encode(), f'subida:{exp}'.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(firma, esperado)
    except Exception:
        return False


def require_secreto_compartido(authorization: Optional[str]) -> None:
    secreto = os.environ.get('STEP_CONVERTER_SECRET')
    if not secreto:
        raise HTTPException(status_code=500, detail='Falta STEP_CONVERTER_SECRET en las variables de entorno del servicio')
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Falta autenticación de servicio')
    recibido = authorization[len('Bearer '):]
    if hmac.compare_digest(recibido, secreto):
        return  # secreto directo (proxy de Vercel)
    if _token_firmado_valido(recibido, secreto):
        return  # token firmado de subida directa (navegador)
    raise HTTPException(status_code=401, detail='Autenticación de servicio inválida')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    cleaned = ''.join(c if (c.isalnum() or c in ('-', '_')) else '_' for c in name.strip().replace(' ', '_'))
    return cleaned[:48] or 'pieza'


async def _cargar_step(file: UploadFile):
    """Lee el UploadFile, lo escribe a un temporal y lo carga con cadquery.
    Devuelve (nombre_original, resultado_cadquery, componentes). Lanza
    HTTPException 400 si el archivo está vacío o no se puede leer como STEP.

    `componentes` es la lectura del STEP como ENSAMBLAJE (nombres de producto,
    repeticiones y cuerpos TOOL) — ver ensamblaje.py. Va vacía si el STEP no
    trae esa estructura, y entonces el llamador sigue por el camino de
    siempre (un sólido = una pieza, numerada por orden)."""
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
        try:
            componentes = leer_componentes(tmp_path)
        except Exception:
            componentes = []   # sin estructura: se sigue por el camino de siempre
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'No se pudo leer el STEP: {e}')
    finally:
        if tmp_path:
            os.unlink(tmp_path)

    return nombre_original, resultado, componentes


# ── Caja negra ────────────────────────────────────────────────────────────
#
# Cuando alguien del taller dice "no responde a tiempo" no hay forma de saber
# qué pasó: si la petición ni siquiera llegó (se quedó subiendo en la oficina),
# si llegó y tardó mucho, o si el contenedor se quedó sin memoria y Render lo
# reinició — un reinicio mata la petición en curso y desde el navegador se ve
# EXACTAMENTE igual que un cuelgue. Esto lo distingue.

ARRANQUE = time.time()
ULTIMAS_PETICIONES: list[dict] = []
MAX_PETICIONES = 15


def _memoria_mb() -> dict:
    """Memoria del proceso. El plan gratuito de Render corta a 512 MB."""
    datos = {}
    try:
        import resource
        pico = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux da KB; macOS da bytes.
        datos['pico_mb'] = round(pico / (1024 * 1024 if pico > 10 ** 7 else 1024), 1)
    except Exception:
        pass
    try:
        # RSS actual, solo en Linux (que es donde corre de verdad).
        with open('/proc/self/status') as f:
            for linea in f:
                if linea.startswith('VmRSS:'):
                    datos['actual_mb'] = round(int(linea.split()[1]) / 1024, 1)
                    break
    except Exception:
        pass
    return datos


@app.middleware('http')
async def _caja_negra(request, call_next):
    """Anota cada petición: cuánto pesaba, cuánto tardó y cómo acabó."""
    if request.url.path in ('/', '/salud'):
        return await call_next(request)
    t0 = time.time()
    estado = 'error'
    try:
        respuesta = await call_next(request)
        estado = str(respuesta.status_code)
        return respuesta
    finally:
        try:
            ULTIMAS_PETICIONES.append({
                'ruta': request.url.path,
                'mb': round(int(request.headers.get('content-length', 0)) / 1e6, 2),
                'segundos': round(time.time() - t0, 1),
                'estado': estado,
                'hace_s': 0,   # se recalcula al leerlo
                '_t': time.time(),
            })
            del ULTIMAS_PETICIONES[:-MAX_PETICIONES]
        except Exception:
            pass


def _banco_cpu() -> float:
    """Milisegundos que tarda esta máquina en una cuenta fija.

    Sirve para comparar peras con peras: el plan gratuito de Render da una
    fracción de CPU compartida, así que el mismo STEP puede tardar el triple
    aquí que en un portátil. Sin un número medido no hay forma de saber si
    una conversión que "no responde a tiempo" es por el arranque en frío, por
    el tamaño del archivo o porque la máquina va lenta ese rato.

    La cuenta es aritmética pura a propósito: no toca disco ni red, y no
    depende de OpenCASCADE, así que el número es comparable entre máquinas y
    entre versiones del servicio.
    """
    t0 = time.perf_counter()
    x = 0.0
    for i in range(400_000):
        x += math.sqrt(i % 1000 + 1)
    return round((time.perf_counter() - t0) * 1000, 1)


@app.get('/')
@app.get('/salud')
async def salud(banco: int = 0):
    # Además del "estoy vivo", la VERSIÓN del análisis: sin esto no había
    # forma de saber desde fuera si un despliegue de Render había entrado
    # (el repo espejo no auto-despliega y el servicio contestaba lo mismo
    # con el código viejo que con el nuevo). Subir VERSION_ANALISIS cada vez
    # que cambien las reglas de análisis o el DXF.
    ahora = time.time()
    cuerpo = {
        'estado': 'ok',
        'version': VERSION_ANALISIS,
        'commit': (os.environ.get('RENDER_GIT_COMMIT') or '')[:7] or None,
        # Si esto se reinicia solo, es que al contenedor lo han matado (casi
        # siempre por memoria) y con él la conversión que hubiera en marcha.
        'lleva_encendido_s': round(ahora - ARRANQUE),
        'memoria': _memoria_mb(),
        'ultimas_peticiones': [
            {**{k: v for k, v in p.items() if not k.startswith('_')},
             'hace_s': round(ahora - p['_t'])}
            for p in reversed(ULTIMAS_PETICIONES)
        ],
    }
    # Solo bajo petición expresa (/salud?banco=1): la comprobación de salud
    # normal la llaman el navegador y un cron cada pocos minutos y tiene que
    # seguir siendo instantánea.
    if banco:
        cuerpo['banco_cpu_ms'] = await run_in_threadpool(_banco_cpu)
    return JSONResponse(cuerpo)


@app.post('/previsualizar')
async def previsualizar(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    """Devuelve un ZIP con un STL por sólido del STEP para poder verlos en 3D
    antes de convertir, cada uno pintable de un color distinto en el visor —
    un único STL fusionado no distingue piezas que se solapan o quedan casi
    de canto desde el ángulo de cámara por defecto. No hace ningún análisis,
    solo tesela la geometría de cada sólido tal cual viene."""
    require_secreto_compartido(authorization)
    _, resultado, _componentes = await _cargar_step(file)

    solidos = resultado.solids().vals()
    if not solidos:
        raise HTTPException(status_code=400, detail='El STEP no contiene ningún sólido')

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # start=1: numeración 1-based (pieza_001, pieza_002...) igual que
        # Yudigar — nunca empieza en 0, así que nunca coincide con "pieza_000".
        for idx, solido in enumerate(solidos, start=1):
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

    nombre_original, resultado, componentes = await _cargar_step(file)

    t_inicio = time.monotonic()
    solidos = resultado.solids().vals()
    if not solidos:
        raise HTTPException(status_code=400, detail='El STEP no contiene ningún sólido')

    zip_buffer = io.BytesIO()
    # "Extremo" = taladros axiales de testero: aparecen en el DXF y en
    # analisis.json pero NO en el .bpp (el formato de panel no los representa).
    manifest_lines = ['Pieza\tLongitud\tSección\tÁngulo A\tÁngulo B\tTaladros\tPasantes\tExtremo (no BPP)\tDescartados\tUnidad']
    omitidas_lines = ['Sólido\tMotivo']
    piezas_json = []
    piezas_generadas = 0

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # start=1: numeración 1-based (pieza_001, pieza_002...) igual que
        # Yudigar, que nunca nombra una pieza "0" ni "pieza_000".
        for idx, solido in enumerate(solidos, start=1):
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
            tal = analisis['taladros']
            pasantes = sum(1 for x in tal if x.get('pasante'))
            extremos = sum(1 for x in tal if x.get('es_extremo'))
            manifest_lines.append(
                f"{nombre_archivo}\t{analisis['longitud']:.2f}\t"
                f"{analisis['seccion'][0]:.2f}x{analisis['seccion'][1]:.2f}{marca}\t"
                f"{analisis['angulo_corte_a']:.1f}°\t{analisis['angulo_corte_b']:.1f}°\t"
                f"{len(tal)}\t{pasantes}\t{extremos}\t{analisis['taladros_descartados']}\tmm"
            )
            piezas_json.append({
                'nombre_capa': nombre_capa,
                'longitud': round(analisis['longitud'], 2),
                'seccion': [round(s, 2) for s in analisis['seccion']],
                'seccion_regular': analisis['seccion_regular'],
                'ancho': analisis['ancho'],
                'grosor': analisis['grosor'],
                'angulo_corte_a': round(analisis['angulo_corte_a'], 1),
                'angulo_corte_b': round(analisis['angulo_corte_b'], 1),
                'taladros': analisis['taladros'],
                'taladros_descartados': analisis['taladros_descartados'],
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
            # zf.write() lee el fichero por trozos; writestr(f.read()) metía
            # el STL ENTERO en memoria (y otra copia comprimida). En un
            # conjunto grande son decenas de MB de más en un servicio con
            # 512 MB — una de las formas de quedarse sin memoria a mitad de
            # conversión y que el navegador acabe con "no respondió a tiempo".
            zf.write(tmp_stl, 'conjunto_completo.stl')
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
            # Cuánto ha tardado el SERVICIO. Si el navegador dice que no
            # respondió a tiempo y aquí pone 15 s, el tiempo se fue en la
            # subida o en la red, no en convertir.
            'X-Tiempo-Ms': str(int((time.monotonic() - t_inicio) * 1000)),
            'Access-Control-Expose-Headers': 'X-Piezas-Count, X-Piezas-Omitidas, X-Tiempo-Ms, Content-Disposition',
        },
    )


@app.post('/analizar-panel')
async def analizar_panel(
    file: UploadFile = File(...),
    forzar_2d: bool = Form(False),
    authorization: Optional[str] = Header(None),
):
    """Analiza un STEP de un mueble de madera/melamina: por cada sólido,
    intenta reconocerlo como panel de tablero (largo×ancho×grosor + taladros
    detectados). Devuelve JSON directo, sin DXF ni ZIP — a diferencia de
    /convertir, aquí el despiece y el .bpp real los construye después el
    navegador (src/lib/despiece/despieceDesdeStepPanel.ts), reutilizando el
    mismo motor BPP ya usado por el resto de la app para muebles."""
    require_secreto_compartido(authorization)

    _, resultado, componentes = await _cargar_step(file)
    return await run_in_threadpool(_analizar_panel_sync, resultado, componentes, forzar_2d)


def _analizar_panel_sync(resultado, componentes, forzar_2d=False):
    """Análisis pesado en un hilo aparte (ver _convertir_panel_sync)."""
    entradas = _entradas_a_convertir(resultado, componentes)
    if not entradas:
        raise HTTPException(status_code=400, detail='El STEP no contiene ningún sólido')

    piezas = []
    omitidas = []
    # start=1: numeración 1-based (pieza_001, pieza_002...) igual que Yudigar,
    # que nunca nombra una pieza "0" ni "pieza_000".
    for idx, ent in enumerate(entradas, start=1):
        solido = ent.solido
        analisis = _analizar_pieza(solido, forzar_2d)
        if not analisis['ok']:
            omitidas.append({'solido': ent.etiqueta or idx, 'material': ent.material,
                             'motivo': analisis['motivo']})
            continue
        piezas.append({
            'nombre_capa': _safe_filename(ent.etiqueta or f"pieza_{idx:03d}_{analisis['largo']:.0f}x{analisis['ancho']:.0f}"),
            'unidades': ent.unidades,
            'material': ent.material,
            'largo': analisis['largo'],
            'ancho': analisis['ancho'],
            'grosor': analisis['grosor'],
            'seccion_regular': analisis['seccion_regular'],
            'taladros': analisis['taladros'],
            'taladros_descartados': analisis['taladros_descartados'],
            'cajeados': analisis.get('cajeados', []),
            'contorno': analisis.get('contorno'),
            'advertencias': analisis['advertencias'],
        })

    if not piezas:
        raise HTTPException(
            status_code=400,
            detail='Ningún sólido del STEP se pudo interpretar como panel de tablero',
        )

    return JSONResponse({'piezas': piezas, 'omitidas': omitidas})


@dataclass
class _Entrada:
    """Un cuerpo a convertir, con lo que el ensamblaje sabe de él."""
    solido: object
    etiqueta: str | None = None   # nombre del STEP ('P01'); None = numerar por orden
    unidades: int = 1
    material: str | None = None


def _entradas_a_convertir(resultado, componentes):
    """Qué hay que convertir y cómo se llama.

    Con ensamblaje: una entrada por pieza DISTINTA, con su nombre del STEP,
    sus unidades y su material, y sin los cuerpos TOOL. Sin ensamblaje: un
    sólido = una entrada, como siempre.
    """
    if componentes and tiene_nombres_utiles(componentes):
        piezas = solo_piezas(componentes)
        if piezas:
            return [
                # Nombre COMPLETO del STEP: ya viene con el convenio de la OF
                # (2876_7616P01), así que el ZIP sale con el nombre definitivo
                # y el renombrado del cliente (que solo toca "pieza_NNN") lo
                # respeta tal cual.
                _Entrada(solido=c.solido, etiqueta=c.nombre.strip() or None,
                         unidades=n, material=c.material)
                for c, n in agrupar_iguales(piezas)
            ]
    return [_Entrada(solido=s) for s in resultado.solids().vals()]



def _analizar_pieza(solido, forzar_2d=False):
    """Cómo se saca el plano de UNA pieza, en orden de preferencia.

    1. Análisis inteligente (medidas + taladros + cajeados + canales).
    2. Si la pieza es curvada, su desarrollo plano.
    3. CALCO 2D como salvavidas: la silueta tal cual.

    Antes, cuando 1 y 2 fallaban, la pieza acababa en omitidas.txt y el taller
    se quedaba SIN PLANO — que es peor que un plano sin mecanizados. Ahora
    siempre sale algo, y el manifiesto avisa de que esa pieza va sin BPP.

    Con forzar_2d se va directo al calco: es lo que se quiere cuando solo
    hace falta el dibujo de corte.
    """
    if forzar_2d:
        return calcar_solido(solido)
    analisis = analizar_solido_panel(solido)
    if analisis['ok']:
        return analisis
    curvado = analizar_panel_curvado(solido)
    if curvado['ok']:
        return curvado
    calco = calcar_solido(solido)
    if calco['ok']:
        # Se conserva el motivo del análisis fallido: al operario le sirve
        # saber POR QUÉ esa pieza va sin mecanizados.
        calco['advertencias'] = [
            f"Sin mecanizados reconocidos ({analisis['motivo']}).",
        ] + list(calco.get('advertencias', []))
        return calco
    return analisis   # ni el calco ha podido: se descarta con su motivo



@app.post('/medir-dxf')
async def medir_dxf_endpoint(
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
):
    """Mide las piezas de un DXF: cuánto miden de largo y de ancho de verdad.

    No convierte nada ni genera programas de máquina: solo mide. Sirve para
    un DXF de nesting o un plano de taller donde las piezas vienen giradas
    para aprovechar el tablero — la medida sale correcta igualmente porque se
    calcula con la caja envolvente mínima (calibre rotatorio), no con la caja
    recta del dibujo.
    """
    require_secreto_compartido(authorization)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail='El archivo está vacío')

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        # Medir es CPU: va al threadpool para no bloquear el servicio.
        resultado = await run_in_threadpool(medir_dxf, tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if not resultado['piezas']:
        raise HTTPException(
            status_code=400,
            detail=' '.join(resultado['avisos']) or 'No se ha podido medir el DXF',
        )
    return JSONResponse({
        'archivo': file.filename or 'plano.dxf',
        'piezas': resultado['piezas'],
        'avisos': resultado['avisos'],
    })



@app.post('/convertir-panel')
async def convertir_panel(
    file: UploadFile = File(...),
    forzar_2d: bool = Form(False),
    authorization: Optional[str] = Header(None),
):
    """Como /convertir pero para MUEBLES de madera/melamina: cada sólido se
    analiza como panel de tablero y el ZIP lleva un DXF por pieza (1:1, en el
    origen, con taladros/cajeados y tabla de mecanizados), manifest.txt,
    omitidas.txt, analisis_panel.json y conjunto_completo.stl. Los .bpp por
    pieza los añade el navegador a partir del analisis_panel.json (mismo
    reparto de trabajo que el flujo de perfiles)."""
    require_secreto_compartido(authorization)

    nombre_original, resultado, componentes = await _cargar_step(file)
    return await run_in_threadpool(
        _convertir_panel_sync, nombre_original, resultado, componentes, forzar_2d)


def _convertir_panel_sync(nombre_original, resultado, componentes, forzar_2d=False):
    """Todo el trabajo PESADO de la conversión, en un hilo aparte.

    Los endpoints son `async`: si esto corriera en el bucle de eventos,
    bloquearía el proceso entero mientras dura la conversión. Consecuencias
    reales: /salud dejaba de responder (y la app decía "se está iniciando"
    cuando en realidad estaba trabajando) y dos conversiones a la vez se
    ponían en cola sin que nadie lo supiera — la segunda se comía el tiempo
    de espera del navegador y salía "El servicio no respondió a tiempo"."""
    t_inicio = time.monotonic()
    entradas = _entradas_a_convertir(resultado, componentes)
    if not entradas:
        raise HTTPException(status_code=400, detail='El STEP no contiene ningún sólido')

    zip_buffer = io.BytesIO()
    manifest_lines = ['Pieza\tUds\tMaterial\tLargo\tAncho\tGrosor\tTaladros\tCajeados\tAvisos\tUnidad']
    omitidas_lines = ['Pieza\tMaterial\tMotivo']
    piezas_json = []
    piezas_generadas = 0

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # start=1: numeración 1-based (pieza_001, pieza_002...) igual que
        # Yudigar, que nunca nombra una pieza "0" ni "pieza_000".
        # Se desglosa el STEP en sus sólidos y se procesa uno a uno. La
        # numeración va sobre las piezas REALMENTE generadas, no sobre el
        # índice del sólido: un sólido descartado (un herraje) se comía su
        # número y la serie salía con saltos (P01, P02, P04), que luego no
        # casaba con la columna FILE de la OF y mandaba una pieza a máquina
        # con el nombre de otra. El nº de sólido queda en el manifiesto para
        # poder rastrearlo.
        for idx, ent in enumerate(entradas, start=1):
            analisis = _analizar_pieza(ent.solido, forzar_2d)
            if not analisis['ok']:
                omitidas_lines.append(
                    f"{ent.etiqueta or idx}\t{ent.material or '-'}\t{analisis['motivo']}")
                continue

            piezas_generadas += 1
            # Con ensamblaje, el nombre es el del STEP (ya trae el convenio de
            # la OF: P01, P02…); sin él, la numeración de siempre.
            nombre_capa = _safe_filename(
                ent.etiqueta or f"pieza_{piezas_generadas:03d}_{analisis['largo']:.0f}x{analisis['ancho']:.0f}")
            doc = generar_dxf_panel(nombre_capa, analisis)
            buffer_pieza = io.StringIO()
            doc.write(buffer_pieza)
            zf.writestr(f'{nombre_capa}.dxf', buffer_pieza.getvalue())

            avisos = analisis['advertencias']
            manifest_lines.append(
                f"{nombre_capa}.dxf\t{ent.unidades}\t{ent.material or '-'}\t"
                f"{analisis['largo']:.2f}\t{analisis['ancho']:.2f}\t"
                f"{analisis['grosor']:.2f}\t{len(analisis['taladros'])}\t{len(analisis['cajeados'])}\t"
                f"{' | '.join(avisos) if avisos else '-'}\tmm"
            )
            piezas_json.append({
                'nombre_capa': nombre_capa,
                'unidades': ent.unidades,
                'material': ent.material,
                'largo': analisis['largo'],
                'ancho': analisis['ancho'],
                'grosor': analisis['grosor'],
                'seccion_regular': analisis['seccion_regular'],
                'taladros': analisis['taladros'],
                'taladros_descartados': analisis['taladros_descartados'],
                'cajeados': analisis['cajeados'],
                'contorno': analisis.get('contorno'),
                'advertencias': avisos,
            })

        if piezas_generadas == 0:
            raise HTTPException(
                status_code=400,
                detail='Ningún sólido del STEP se pudo interpretar como panel de tablero',
            )

        zf.writestr('manifest.txt', '\n'.join(manifest_lines))
        if len(omitidas_lines) > 1:
            zf.writestr('omitidas.txt', '\n'.join(omitidas_lines))
        zf.writestr('analisis_panel.json', json.dumps({
            'piezas': piezas_json,
            # Las columnas son Pieza / Material / Motivo, y "Pieza" es el
            # NOMBRE del STEP ('2876_7616 - PMMA_4'), no un número: hacerle
            # int() reventaba la conversión entera de cualquier STEP con
            # nombres de pieza (fallo del 13/08, cazado al ejecutar el
            # endpoint de verdad y no solo el análisis).
            'omitidas': [
                {'pieza': partes[0], 'material': partes[1] if len(partes) > 2 else None,
                 'motivo': partes[-1]}
                for partes in (l.split('\t') for l in omitidas_lines[1:])
            ],
        }, ensure_ascii=False))

        # Conjunto completo en 3D — mismo export de referencia que /convertir.
        tmp_stl = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.stl', delete=False) as tmp:
                tmp_stl = tmp.name
            exporters.export(resultado.val(), tmp_stl, exportType='STL')
            # zf.write() lee el fichero por trozos; writestr(f.read()) metía
            # el STL ENTERO en memoria (y otra copia comprimida). En un
            # conjunto grande son decenas de MB de más en un servicio con
            # 512 MB — una de las formas de quedarse sin memoria a mitad de
            # conversión y que el navegador acabe con "no respondió a tiempo".
            zf.write(tmp_stl, 'conjunto_completo.stl')
        except Exception as e:
            print(f'[convertir-panel] no se pudo exportar el conjunto completo a STL: {type(e).__name__}: {e}')
        finally:
            if tmp_stl and os.path.exists(tmp_stl):
                os.unlink(tmp_stl)

    zip_buffer.seek(0)
    base_nombre = nombre_original.rsplit('.', 1)[0]
    nombre_zip = f'{_safe_filename(base_nombre)}_despiece.zip'
    return Response(
        content=zip_buffer.getvalue(),
        media_type='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename="{nombre_zip}"',
            'X-Piezas-Count': str(piezas_generadas),
            'X-Piezas-Omitidas': str(len(omitidas_lines) - 1),
            # Cuánto ha tardado el SERVICIO. Si el navegador dice que no
            # respondió a tiempo y aquí pone 15 s, el tiempo se fue en la
            # subida o en la red, no en convertir.
            'X-Tiempo-Ms': str(int((time.monotonic() - t_inicio) * 1000)),
            'Access-Control-Expose-Headers': 'X-Piezas-Count, X-Piezas-Omitidas, X-Tiempo-Ms, Content-Disposition',
        },
    )
