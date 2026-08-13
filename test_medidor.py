"""Tests del medidor de DXF. Se ejecutan con el venv local:

    .venv-step/bin/python services/step-to-dxf/test_medidor.py
"""
import math
import os
import sys
import tempfile

import ezdxf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from medidor_dxf import (  # noqa: E402
    caja_minima, envolvente_convexa, punto_dentro, area_con_signo,
    reconstruir_bucles, medir_dxf,
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


if __name__ == '__main__':
    for caso in (caso_calibre_rotatorio, caso_envolvente_y_dentro,
                 caso_reconstruccion, caso_dxf_completo, caso_robusto):
        caso()
        print()
    if FALLOS:
        print(f'✗ {len(FALLOS)} comprobaciones fallidas')
        sys.exit(1)
    print('✓ Todos los casos pasan')
