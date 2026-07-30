"""Tests de la numeración de piezas de main.py (`/convertir`, `/analizar-panel`,
`/convertir-panel`) con STEP sintéticos de 2 sólidos.

Bug reportado por el taller: a veces sale una pieza de nombre "0" — algo que
Yudigar (el proceso de referencia) nunca produce, porque numera sus piezas
empezando en 1. Causa en este servicio: `nombre_capa` se construía con
`enumerate(solidos)`, que en Python empieza en 0 — la primera pieza salía como
"pieza_000_..." en vez de "pieza_001_...". Estos tests comprueban que la
numeración ahora es 1-based en los tres endpoints.

Mismo patrón que test_analisis.py/test_analisis_panel.py: construye con
cadquery sólidos de geometría conocida, los exporta a un STEP sintético (aquí
con 2 sólidos, no 1) y llama al endpoint real vía TestClient. Ejecutar con el
venv local:

    .venv-step/bin/python services/step-to-dxf/test_main.py
"""
import json
import os
import re
import sys
import tempfile
import zipfile
from io import BytesIO

import cadquery as cq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SECRETO = 'test-secret-step-to-dxf'
os.environ.setdefault('STEP_CONVERTER_SECRET', SECRETO)

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

client = TestClient(main.app)
HEADERS = {'Authorization': f'Bearer {SECRETO}'}

FALLOS = []


def check(nombre, cond, detalle=''):
    if cond:
        print(f'  ✓ {nombre}')
    else:
        print(f'  ✗ {nombre} — {detalle}')
        FALLOS.append(f'{nombre}: {detalle}')


def _step_dos_perfiles() -> bytes:
    """STEP con 2 sólidos-caja (perfil regular de 6 caras), tamaños distintos
    para poder distinguirlos si hiciera falta."""
    b1 = cq.Workplane('XY').box(300, 60, 40, centered=(False, False, False))
    b2 = cq.Workplane('XY').transformed(offset=(0, 200, 0)).box(500, 60, 40, centered=(False, False, False))
    compound = cq.Compound.makeCompound([b1.val(), b2.val()])
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
        path = tmp.name
    try:
        cq.exporters.export(compound, path, exportType='STEP')
        with open(path, 'rb') as f:
            return f.read()
    finally:
        os.unlink(path)


def _step_dos_paneles() -> bytes:
    """STEP con 2 sólidos-panel (tablero regular), tamaños distintos."""
    p1 = cq.Workplane('XY').box(600, 400, 18, centered=(False, False, False))
    p2 = cq.Workplane('XY').transformed(offset=(0, 0, 500)).box(700, 350, 18, centered=(False, False, False))
    compound = cq.Compound.makeCompound([p1.val(), p2.val()])
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
        path = tmp.name
    try:
        cq.exporters.export(compound, path, exportType='STEP')
        with open(path, 'rb') as f:
            return f.read()
    finally:
        os.unlink(path)


def _indices(nombres):
    """Extrae el número de "pieza_NNN_..." de cada nombre_capa."""
    out = []
    for n in nombres:
        m = re.match(r'pieza_(\d+)_', n)
        assert m, f'nombre inesperado: {n}'
        out.append(int(m.group(1)))
    return out


def caso_convertir_perfiles_numera_desde_1():
    print('Caso 1: /convertir (perfiles de aluminio) numera pieza_001, pieza_002... nunca pieza_000')
    contenido = _step_dos_perfiles()
    res = client.post('/convertir', headers=HEADERS,
                       files={'file': ('perfiles.step', contenido, 'application/step')})
    check('200 OK', res.status_code == 200, f'{res.status_code}: {res.text[:300]}')
    if res.status_code != 200:
        return
    zf = zipfile.ZipFile(BytesIO(res.content))
    piezas = json.loads(zf.read('analisis.json'))
    nombres = [p['nombre_capa'] for p in piezas]
    check('2 piezas', len(nombres) == 2, str(nombres))
    indices = _indices(nombres)
    check('numeración 1-based, nunca empieza en 0', min(indices) == 1, str(indices))
    check('ninguna pieza se llama "pieza_000_..."', all('pieza_000_' not in n for n in nombres), str(nombres))


def caso_analizar_panel_numera_desde_1():
    print('Caso 2: /analizar-panel (paneles de madera) numera pieza_001, pieza_002... nunca pieza_000')
    contenido = _step_dos_paneles()
    res = client.post('/analizar-panel', headers=HEADERS,
                       files={'file': ('paneles.step', contenido, 'application/step')})
    check('200 OK', res.status_code == 200, f'{res.status_code}: {res.text[:300]}')
    if res.status_code != 200:
        return
    data = res.json()
    nombres = [p['nombre_capa'] for p in data['piezas']]
    check('2 piezas', len(nombres) == 2, str(nombres))
    indices = _indices(nombres)
    check('numeración 1-based, nunca empieza en 0', min(indices) == 1, str(indices))
    check('ninguna pieza se llama "pieza_000_..."', all('pieza_000_' not in n for n in nombres), str(nombres))


def caso_convertir_panel_numera_desde_1():
    print('Caso 3: /convertir-panel (ZIP con DXF de paneles) numera pieza_001, pieza_002... nunca pieza_000')
    contenido = _step_dos_paneles()
    res = client.post('/convertir-panel', headers=HEADERS,
                       files={'file': ('paneles.step', contenido, 'application/step')})
    check('200 OK', res.status_code == 200, f'{res.status_code}: {res.text[:300]}')
    if res.status_code != 200:
        return
    zf = zipfile.ZipFile(BytesIO(res.content))
    piezas = json.loads(zf.read('analisis_panel.json'))['piezas']
    nombres = [p['nombre_capa'] for p in piezas]
    dxfs = [n for n in zf.namelist() if n.endswith('.dxf')]
    check('2 piezas en el JSON', len(nombres) == 2, str(nombres))
    check('2 DXF en el ZIP, ninguno "0.dxf" ni "pieza_000...dxf"',
          len(dxfs) == 2 and all(not d.startswith('pieza_000') and d != '0.dxf' for d in dxfs), str(dxfs))
    indices = _indices(nombres)
    check('numeración 1-based, nunca empieza en 0', min(indices) == 1, str(indices))


if __name__ == '__main__':
    for caso in (caso_convertir_perfiles_numera_desde_1, caso_analizar_panel_numera_desde_1,
                 caso_convertir_panel_numera_desde_1):
        caso()
        print()
    if FALLOS:
        print(f'✗ {len(FALLOS)} comprobaciones fallidas:')
        for f in FALLOS:
            print(f'  - {f}')
        sys.exit(1)
    print('✓ Todos los casos pasan')
