"""Tests de regresión geométrica de analisis.py con STEP sintéticos.

Cada caso construye con cadquery una pieza de GEOMETRÍA CONOCIDA (ground
truth), la exporta a STEP en un temporal, la reimporta (mismo camino que en
producción) y comprueba que analizar_solido() devuelve exactamente lo
esperado. Ejecutar con el venv local:

    .venv-step/bin/python services/step-to-dxf/test_analisis.py
"""
import math
import sys
import tempfile
import os

import cadquery as cq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analisis import analizar_solido  # noqa: E402

FALLOS = []


def check(nombre, cond, detalle=''):
    if cond:
        print(f'  ✓ {nombre}')
    else:
        print(f'  ✗ {nombre} — {detalle}')
        FALLOS.append(f'{nombre}: {detalle}')


def analizar(workplane_o_solid):
    """Exporta a STEP y reimporta — mismo pipeline que producción."""
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
        path = tmp.name
    try:
        cq.exporters.export(workplane_o_solid, path, exportType='STEP')
        result = cq.importers.importStep(path)
        solids = result.solids().vals()
        assert len(solids) == 1, f'esperaba 1 sólido, hay {len(solids)}'
        return analizar_solido(solids[0])
    finally:
        os.unlink(path)


def aprox(a, b, tol=0.15):
    return abs(a - b) <= tol


# ── Caso 1: barra recta con taladros laterales conocidos ─────────────────────
# Caja 500 (X) × 60 (Y) × 40 (Z). Dos taladros pasantes Ø8 en la cara frontal
# (Z+) en x=100 e y=30, x=400 e y=30; un taladro ciego Ø5 prof. 15 en x=250.
def caso_barra_taladros():
    print('Caso 1: barra recta con taladros laterales (2 pasantes + 1 ciego)')
    barra = (
        cq.Workplane('XY').box(500, 60, 40, centered=(False, False, False))
        .faces('>Z').workplane(origin=(0, 0, 0))
        .pushPoints([(100, 30), (400, 30)]).hole(8)          # pasantes Ø8
        .faces('>Z').workplane(origin=(0, 0, 0))
        .pushPoints([(250, 30)]).hole(5, depth=15)           # ciego Ø5 prof 15
    )
    r = analizar(barra)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('longitud 500', aprox(r['longitud'], 500), f"{r['longitud']}")
    check('ángulos rectos', aprox(r['angulo_corte_a'], 0, 0.5) and aprox(r['angulo_corte_b'], 0, 0.5),
          f"{r['angulo_corte_a']}/{r['angulo_corte_b']}")
    check('sección 40×60', aprox(r['seccion'][0], 40) and aprox(r['seccion'][1], 60), f"{r['seccion']}")
    tal = r['taladros']
    check('3 taladros', len(tal) == 3, f'{len(tal)}: {tal}')
    pas = sorted([t for t in tal if t['diametro'] == 8], key=lambda t: t['x'])
    ciegos = [t for t in tal if t['diametro'] == 5]
    check('2 pasantes Ø8 + 1 ciego Ø5', len(pas) == 2 and len(ciegos) == 1, f'{tal}')
    if len(pas) == 2 and len(ciegos) == 1:
        check('x de los pasantes = 100 y 400', aprox(pas[0]['x'], 100) and aprox(pas[1]['x'], 400),
              f"{pas[0]['x']}, {pas[1]['x']}")
        check('y de los pasantes = 30 (misma referencia ambos)', aprox(pas[0]['y'], 30) and aprox(pas[1]['y'], 30),
              f"{pas[0]['y']}, {pas[1]['y']}")
        check('pasantes marcados pasante', pas[0]['pasante'] and pas[1]['pasante'], f'{pas}')
        check('pasantes prof ≈ 40 (el grosor)', aprox(pas[0]['profundidad'], 40, 0.3), f"{pas[0]['profundidad']}")
        c = ciegos[0]
        check('ciego no pasante', not c['pasante'], f'{c}')
        check('ciego prof ≈ 15', aprox(c['profundidad'], 15, 0.5), f"{c['profundidad']}")
        check('ciego x=250', aprox(c['x'], 250), f"{c['x']}")
        # El ciego se taladra desde Z+ (la cara de mayor Z). Todas las caras
        # laterales usan la MISMA referencia: la clave es que sea consistente.
        check('ciego en cara frontal o trasera', c['cara'] in ('frontal', 'trasera'), f"{c['cara']}")


# ── Caso 2: taladros axiales en AMBOS extremos (el bug de la fusión) ─────────
# Caja 300 × 50 × 50 con un taladro axial Ø6 prof. 20 en el centro de CADA
# testero. La v2 los fusionaba en uno.
def caso_axiales_extremos():
    print('Caso 2: taladros axiales del mismo Ø en ambos extremos (anti-fusión)')
    barra = (
        cq.Workplane('YZ').box(50, 50, 300, centered=(True, True, False))
        .faces('>X').workplane().pushPoints([(0, 0)]).hole(6, depth=20)
        .faces('<X').workplane().pushPoints([(0, 0)]).hole(6, depth=20)
    )
    r = analizar(barra)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('longitud 300', aprox(r['longitud'], 300), f"{r['longitud']}")
    ext = [t for t in r['taladros'] if t['es_extremo']]
    check('DOS taladros de extremo (no fusionados)', len(ext) == 2, f"{len(ext)}: {r['taladros']}")
    if len(ext) == 2:
        caras = sorted(t['cara'] for t in ext)
        check('uno en extremo_a y otro en extremo_b', caras == ['extremo_a', 'extremo_b'], f'{caras}')
        check('profundidad ≈ 20 ambos', all(aprox(t['profundidad'], 20, 0.5) for t in ext),
              f"{[t['profundidad'] for t in ext]}")
        check('ninguno pasante', all(not t['pasante'] for t in ext), f'{ext}')


# ── Caso 3: caras opuestas — un taladro ciego por cada cara ancha ────────────
# El bug del abs(dot): ciego desde Z+ en x=80, ciego desde Z− en x=220.
# Deben salir en caras DISTINTAS (frontal y trasera).
def caso_caras_opuestas():
    print('Caso 3: taladros ciegos en caras opuestas (anti-abs(dot))')
    # Geometría explícita para evitar la ambigüedad de coordenadas de los
    # workplanes de cara de cadquery: cilindros construidos en coordenadas
    # GLOBALES y restados de la caja.
    base = cq.Workplane('XY').box(300, 60, 40, centered=(False, False, False)).val()
    ciego_arriba = cq.Solid.makeCylinder(3, 12, cq.Vector(80, 30, 40 - 12), cq.Vector(0, 0, 1))
    ciego_abajo = cq.Solid.makeCylinder(3, 12, cq.Vector(220, 30, 0), cq.Vector(0, 0, 1))
    barra = base.cut(ciego_arriba).cut(ciego_abajo)
    r = analizar(barra)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    tal = sorted(r['taladros'], key=lambda t: t['x'])
    check('2 taladros', len(tal) == 2, f'{tal}')
    if len(tal) == 2:
        check('caras distintas (frontal vs trasera)',
              {tal[0]['cara'], tal[1]['cara']} == {'frontal', 'trasera'},
              f"{tal[0]['cara']}, {tal[1]['cara']}")
        check('x correctos 80 y 220', aprox(tal[0]['x'], 80) and aprox(tal[1]['x'], 220),
              f"{tal[0]['x']}, {tal[1]['x']}")
        check('misma y=30 en ambas caras (marco consistente, sin espejo)',
              aprox(tal[0]['y'], 30) and aprox(tal[1]['y'], 30),
              f"{tal[0]['y']}, {tal[1]['y']}")


# ── Caso 4: fillets NO son taladros ──────────────────────────────────────────
def caso_fillets():
    print('Caso 4: aristas redondeadas (fillets) no generan taladros falsos')
    barra = (
        cq.Workplane('XY').box(400, 50, 30, centered=(False, False, False))
        .edges('|X').fillet(4)
        .faces('>Z').workplane(origin=(0, 0, 0)).pushPoints([(200, 25)]).hole(8)
    )
    r = analizar(barra)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('exactamente 1 taladro (los 4 fillets descartados)', len(r['taladros']) == 1,
          f"{len(r['taladros'])}: {r['taladros']}")
    check('descartados ≥ 4', r['taladros_descartados'] >= 4, f"{r['taladros_descartados']}")
    if len(r['taladros']) == 1:
        check('el taladro es el Ø8 en x=200', aprox(r['taladros'][0]['x'], 200) and r['taladros'][0]['diametro'] == 8,
              f"{r['taladros'][0]}")


# ── Caso 5: avellanado (piloto + caja concéntricos) ──────────────────────────
def caso_avellanado():
    print('Caso 5: taladro con caja (piloto Ø4 pasante + caja Ø9 prof. 6) → un taladro')
    barra = (
        cq.Workplane('XY').box(200, 50, 20, centered=(False, False, False))
        .faces('>Z').workplane(origin=(0, 0, 0)).pushPoints([(100, 25)]).cboreHole(4, 9, 6)
    )
    r = analizar(barra)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('UN solo taladro', len(r['taladros']) == 1, f"{len(r['taladros'])}: {r['taladros']}")
    if len(r['taladros']) == 1:
        t = r['taladros'][0]
        check('principal Ø4', t['diametro'] == 4, f"{t['diametro']}")
        check('lleva avellanado Ø9', 'avellanado' in t and t['avellanado']['diametro'] == 9, f'{t}')
        check('avellanado prof ≈ 6', 'avellanado' in t and aprox(t['avellanado']['profundidad'], 6, 0.5), f'{t}')
        check('x=100', aprox(t['x'], 100), f"{t['x']}")


# ── Caso 6: barra paralelogramo (ambos ingletes paralelos, el bug de longitud)
# Perfil 2D paralelogramo extruido: base 400 de largo punta a punta, altura 60,
# ambos extremos cortados a 45° en el MISMO sentido. Longitud punta a punta=400.
def caso_paralelogramo():
    print('Caso 6: barra paralelogramo 45°/45° paralelos (anti bug de longitud)')
    # Paralelogramo en XY extruido 30 en Z: vértices (0,0) (340,0) (400,60) (60,60)
    barra = (
        cq.Workplane('XY')
        .polyline([(0, 0), (340, 0), (400, 60), (60, 60)]).close()
        .extrude(30)
    )
    r = analizar(barra)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('longitud punta a punta 400', aprox(r['longitud'], 400, 0.5), f"{r['longitud']}")
    check('ángulos 45° ambos', aprox(r['angulo_corte_a'], 45, 0.5) and aprox(r['angulo_corte_b'], 45, 0.5),
          f"{r['angulo_corte_a']}/{r['angulo_corte_b']}")
    check('sección 30×60', aprox(r['seccion'][0], 30) and aprox(r['seccion'][1], 60), f"{r['seccion']}")


# ── Caso 7: trapecio (ingletes no paralelos) sigue funcionando ───────────────
def caso_trapecio():
    print('Caso 7: barra trapecio (recto + inglete 45°) — regresión')
    barra = (
        cq.Workplane('XY')
        .polyline([(0, 0), (340, 0), (400, 60), (0, 60)]).close()
        .extrude(30)
    )
    r = analizar(barra)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('longitud 400', aprox(r['longitud'], 400, 0.5), f"{r['longitud']}")
    angs = sorted([r['angulo_corte_a'], r['angulo_corte_b']])
    check('un corte recto y uno a 45°', aprox(angs[0], 0, 0.5) and aprox(angs[1], 45, 0.5), f'{angs}')


# ── Caso 8: perfil HUECO con taladro pasante (dos paredes → un taladro) ──────
# Tubo 400×60×40 con pared de 2mm y un taladro Ø8 que atraviesa las dos
# paredes frontales (perfil de aluminio real). Debe salir UN taladro pasante
# con profundidad = 40 (el perfil entero), no dos perforaciones de 2mm.
def caso_perfil_hueco():
    print('Caso 8: perfil hueco — taladro a través de dos paredes = UNO pasante')
    exterior = cq.Workplane('XY').box(400, 60, 40, centered=(False, False, False)).val()
    interior = cq.Workplane('XY').transformed(offset=(0, 2, 2)).box(400, 56, 36, centered=(False, False, False)).val()
    tubo = exterior.cut(interior)
    taladro = cq.Solid.makeCylinder(4, 40, cq.Vector(150, 30, 0), cq.Vector(0, 0, 1))
    pieza = tubo.cut(taladro)
    r = analizar(pieza)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('longitud 400', aprox(r['longitud'], 400), f"{r['longitud']}")
    tal = r['taladros']
    check('UN taladro (paredes fusionadas)', len(tal) == 1, f'{len(tal)}: {tal}')
    if len(tal) == 1:
        t = tal[0]
        check('pasante con prof = 40 (perfil completo)', t['pasante'] and aprox(t['profundidad'], 40, 0.5), f'{t}')
        check('x=150 y=30', aprox(t['x'], 150) and aprox(t['y'], 30), f"{t['x']}, {t['y']}")
        check('cara frontal (convención positiva)', t['cara'] == 'frontal', f"{t['cara']}")



# ── Caso 9: el plano de barra no lleva texto ni cotas ───────────────────────
# El taller no necesita explicaciones en el DXF, solo la geometría de corte.
def caso_plano_de_barra_sin_texto():
    print('Caso 9: el plano de barra (dxf.py) no lleva texto ni cotas')
    from dxf import generar_dxf_pieza
    r = {
        'longitud': 400.0, 'ancho': 60.0, 'grosor': 30.0,
        'angulo_corte_a': 0.0, 'angulo_corte_b': 45.0,
        'taladros': [
            {'cara': 'frontal', 'x': 100.0, 'y': 30.0, 'diametro': 8.0, 'profundidad': 12.0, 'pasante': False},
            {'cara': 'extremo_a', 'x': 20.0, 'y': 15.0, 'diametro': 5.0, 'profundidad': 20.0, 'pasante': True},
        ],
    }
    doc = generar_dxf_pieza('prueba', r)
    msp = doc.modelspace()
    check('no genera ninguna entidad TEXT', len(list(msp.query('TEXT'))) == 0)
    check('no genera ninguna entidad MTEXT', len(list(msp.query('MTEXT'))) == 0)
    check('no genera ninguna cota (DIMENSION)', len(list(msp.query('DIMENSION'))) == 0)
    check('sigue dibujando los taladros como círculos', len(list(msp.query('CIRCLE'))) == 2)


if __name__ == '__main__':
    for caso in (caso_barra_taladros, caso_axiales_extremos, caso_caras_opuestas,
                 caso_fillets, caso_avellanado, caso_paralelogramo, caso_trapecio,
                 caso_perfil_hueco, caso_plano_de_barra_sin_texto):
        caso()
        print()
    if FALLOS:
        print(f'✗ {len(FALLOS)} comprobaciones fallidas:')
        for f in FALLOS:
            print(f'  - {f}')
        sys.exit(1)
    print('✓ Todos los casos pasan')
