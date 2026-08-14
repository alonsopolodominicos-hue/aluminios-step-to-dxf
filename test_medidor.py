"""Tests del medidor de DXF. Se ejecutan con el venv local:

    .venv-step/bin/python services/step-to-dxf/test_medidor.py
"""
import base64
import io
import math
import os
import sys
import tempfile

import ezdxf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from medidor_dxf import (  # noqa: E402
    caja_minima, envolvente_convexa, punto_dentro, area_con_signo,
    reconstruir_bucles, medir_dxf, detectar_nombre,
)

FALLOS = []


def check(nombre, cond, detalle=''):
    if cond:
        print(f'  ✓ {nombre}')
    else:
        print(f'  ✗ {nombre} — {detalle}')
        FALLOS.append(nombre)


def girar(puntos, grados, cx=0.0, cy=0.0):
    a = math.radians(grados)
    return [((x - cx) * math.cos(a) - (y - cy) * math.sin(a) + cx,
             (x - cx) * math.sin(a) + (y - cy) * math.cos(a) + cy) for x, y in puntos]


def caso_calibre_rotatorio():
    """La medida NO puede depender de cómo esté girada la pieza."""
    print('CASO: calibre rotatorio')
    base = [(0, 0), (300, 0), (300, 150), (0, 150)]
    for grados in (0, 17, 37, 45, 90, 123, 179):
        L, A, _ = caja_minima(girar(base, grados))
        check(f'[calibre] 300x150 girado {grados}° mide 300x150',
              abs(L - 300) < 0.01 and abs(A - 150) < 0.01, f'{L:.2f}x{A:.2f}')
    # La caja RECTA de la misma pieza girada sí cambia: es justo lo que se evita.
    g = girar(base, 37)
    xs = [p[0] for p in g]
    check('[calibre] la caja recta girada SÍ mide de más (por eso hace falta)',
          (max(xs) - min(xs)) > 320, f'{max(xs)-min(xs):.1f}')


def caso_envolvente_y_dentro():
    print('CASO: envolvente convexa y punto dentro')
    cuadrado = [(0, 0), (10, 0), (10, 10), (0, 10)]
    conRuido = cuadrado + [(5, 5), (3, 4)]      # puntos interiores
    env = envolvente_convexa(conRuido)
    check('[envolvente] ignora los puntos de dentro', len(env) == 4, str(env))
    check('[dentro] un punto interior está dentro', punto_dentro((5, 5), cuadrado))
    check('[dentro] un punto exterior está fuera', not punto_dentro((15, 5), cuadrado))
    check('[área] el cuadrado de 10 mide 100', abs(abs(area_con_signo(cuadrado)) - 100) < 1e-6)


def caso_reconstruccion():
    print('CASO: reconstrucción de bucles')
    # Cuadrado dado como 4 segmentos SUELTOS y desordenados, con un hueco de
    # 0,05 mm en una esquina — como sale de un CAD real.
    segs = [((10, 0), (10, 10)), ((0, 10), (0, 0)),
            ((0, 0), (10, 0)), ((10, 10), (0.05, 10))]
    bucles = reconstruir_bucles(segs)
    check('[unir] cierra el cuadrado pese al hueco de 0,05', len(bucles) == 1, f'{len(bucles)} bucles')
    # Con segmentos que no cierran, no se inventa un bucle.
    check('[unir] no cierra lo que no cierra',
          reconstruir_bucles([((0, 0), (10, 0)), ((20, 0), (30, 0))]) == [])


def caso_dxf_completo():
    print('CASO: DXF completo con pieza y agujero')
    doc = ezdxf.new()
    msp = doc.modelspace()
    # Pieza de 400x200 girada 30°, con un agujero circular dentro.
    pieza = girar([(0, 0), (400, 0), (400, 200), (0, 200)], 30)
    msp.add_lwpolyline(pieza, close=True)
    centro = girar([(200, 100)], 30)[0]
    msp.add_circle(centro, 25)
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'p.dxf')
        doc.saveas(ruta)
        r = medir_dxf(ruta)
    piezas = r['piezas']
    check('[dxf] encuentra 1 pieza (el agujero no cuenta)', len(piezas) == 1, str(len(piezas)))
    if piezas:
        p = piezas[0]
        check('[dxf] mide 400x200 pese al giro de 30°',
              abs(p['largo'] - 400) < 0.5 and abs(p['ancho'] - 200) < 0.5,
              f"{p['largo']}x{p['ancho']}")
        check('[dxf] detecta el agujero de dentro', p['agujeros'] == 1, str(p['agujeros']))
        check('[dxf] dice a cuántos grados está girada',
              abs(p['angulo'] - 30) < 0.5 or abs(p['angulo'] - 120) < 0.5, str(p['angulo']))


def caso_robusto():
    print('CASO: no se tumba con basura')
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_text('esto es un texto, no geometría')
    msp.add_point((5, 5))
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'v.dxf')
        doc.saveas(ruta)
        r = medir_dxf(ruta)
    check('[robusto] un DXF sin piezas devuelve aviso, no excepción',
          r['piezas'] == [] and len(r['avisos']) > 0, str(r))
    r2 = medir_dxf('/no/existe/de/verdad.dxf')
    check('[robusto] un fichero que no existe devuelve aviso', r2['piezas'] == [] and r2['avisos'])


def caso_deteccion_nombre():
    """Qué pieza es cada una: el texto que cae dentro (o pegado a) su contorno."""
    print('CASO: detección del nombre de la pieza por texto')
    cuadrado = [(0, 0), (10, 0), (10, 10), (0, 10)]

    check('[nombre] texto dentro del contorno se detecta',
          detectar_nombre(cuadrado, [((5, 5), 'P01')]) == 'P01')
    check('[nombre] texto pegado pero fuera del contorno se detecta',
          detectar_nombre(cuadrado, [((15, 5), 'P02')]) == 'P02')
    check('[nombre] texto lejos del contorno no se detecta',
          detectar_nombre(cuadrado, [((1000, 1000), 'LEJOS')]) is None)
    check('[nombre] sin ningún texto no se detecta',
          detectar_nombre(cuadrado, []) is None)
    check('[nombre] con varios textos elige el más cercano',
          detectar_nombre(cuadrado, [((1000, 1000), 'LEJOS'), ((5, 5), 'CERCA')]) == 'CERCA')


def caso_dxf_con_nombres_y_cotas():
    """Extremo a extremo: medir_dxf pone nombre a la pieza con texto y genera
    un DXF acotado con cotas nativas y etiqueta en la que no tenía texto."""
    print('CASO: DXF completo con nombres de pieza y DXF acotado')
    doc = ezdxf.new()
    msp = doc.modelspace()
    # Pieza 1: 100x50 con su nombre escrito dentro.
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True)
    msp.add_text('P01', height=5).set_placement((50, 25))
    # Pieza 2: 100x50 girada 20°, sin ningún texto cerca.
    pieza2 = girar([(0, 300), (100, 300), (100, 350), (0, 350)], 20, cx=0, cy=325)
    msp.add_lwpolyline(pieza2, close=True)

    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'p.dxf')
        doc.saveas(ruta)
        r = medir_dxf(ruta)

    piezas = r['piezas']
    check('[dxf] detecta las 2 piezas', len(piezas) == 2, str(len(piezas)))
    check('[dxf] la pieza con texto dentro lleva su nombre',
          any(p['nombre'] == 'P01' for p in piezas), str(piezas))
    check('[dxf] la pieza sin texto cerca queda sin nombre',
          any(p['nombre'] is None for p in piezas), str(piezas))

    b64 = r.get('dxf_acotado_base64')
    check('[acotado] la respuesta trae un DXF acotado', bool(b64))
    if b64:
        doc2 = ezdxf.read(io.StringIO(base64.b64decode(b64).decode('utf-8')))
        msp2 = doc2.modelspace()
        dims = list(msp2.query('DIMENSION'))
        check('[acotado] hay 2 cotas por pieza (largo + ancho) × 2 piezas',
              len(dims) == 4, str(len(dims)))
        medidas = sorted(round(d.get_measurement(), 1) for d in dims)
        check('[acotado] las cotas miden 50 y 100, como las piezas',
              medidas == [50.0, 50.0, 100.0, 100.0], str(medidas))
        textos = [t.plain_text() for t in msp2.query('TEXT')]
        check('[acotado] la pieza sin nombre lleva una etiqueta "Pieza N" en el plano',
              any('Pieza' in t for t in textos), str(textos))


if __name__ == '__main__':
    for caso in (caso_calibre_rotatorio, caso_envolvente_y_dentro,
                 caso_reconstruccion, caso_dxf_completo, caso_robusto,
                 caso_deteccion_nombre, caso_dxf_con_nombres_y_cotas):
        caso()
        print()
    if FALLOS:
        print(f'✗ {len(FALLOS)} comprobaciones fallidas')
        sys.exit(1)
    print('✓ Todos los casos pasan')
