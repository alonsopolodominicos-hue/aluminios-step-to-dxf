"""Banco de pruebas contra la salida de la herramienta PROFESIONAL.

No es un test unitario: es el contraste con la realidad. Coge un STEP real del
taller y compara, pieza a pieza, las medidas que saca nuestro analizador con
las del DXF que genera la herramienta profesional para ese mismo trabajo.

Es la única forma honesta de saber si un cambio en la geometría mejora o
empeora: los tests sintéticos comprueban que el código hace lo que yo creo,
esto comprueba que hace lo que el taller necesita.

Uso:
    .venv-step/bin/python services/step-to-dxf/comparar_con_profesional.py \\
        ~/Downloads/6425\\ PROFESIONAL/002876--6425\\ STEP/2876_6425.stp \\
        ~/Downloads/6425\\ PROFESIONAL/002876--6425\\ DXF/002876--6425\\ Cara\\ Vista

El segundo argumento es la carpeta con los DXF del profesional (una cara).
"""
import os
import re
import sys

import ezdxf
from ezdxf import bbox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ensamblaje import leer_componentes, solo_piezas, agrupar_iguales  # noqa: E402
from analisis_panel import analizar_solido_panel  # noqa: E402
from desarrollo import analizar_panel_curvado  # noqa: E402
from dxf_panel import generar_dxf_panel  # noqa: E402

# Diferencia máxima admitida entre nuestra medida y la del profesional.
TOL_MM = 0.5


def medidas_dxf(ruta):
    """(largo, ancho) del dibujo, con los arcos incluidos en la envolvente."""
    try:
        caja = bbox.extents(ezdxf.readfile(ruta).modelspace(), fast=False)
        return round(caja.size.x, 2), round(caja.size.y, 2)
    except Exception:
        return None


def medidas_nuestras(solido):
    """(largo, ancho) del DXF que genera el conversor para esa pieza.

    Se mide sobre el DIBUJO, no sobre largo/ancho del análisis: el DXF del
    profesional también incluye el mecanizado que sobresale del panel, así
    que comparar el panel contra su dibujo daría diferencias falsas de 50 mm
    en casi todas las piezas.
    """
    a = analizar_solido_panel(solido)
    if not a['ok']:
        a = analizar_panel_curvado(solido)
    if not a['ok']:
        return None, a['motivo']
    try:
        doc = generar_dxf_panel('cmp', a)
        caja = bbox.extents(doc.modelspace(), fast=False)
        return (round(caja.size.x, 2), round(caja.size.y, 2)), None
    except Exception as e:
        return None, f'no se pudo dibujar: {type(e).__name__}: {e}'


def comparar(ruta_step, dir_profesional):
    piezas = solo_piezas(leer_componentes(ruta_step))
    # Se casa por el nombre COMPLETO del STEP, no por el corto: un mismo STEP
    # puede traer piezas de dos pedidos ('2876_6425P01' y '2876_7616P01') y
    # el corto ('P01') las confundiría entre sí.
    por_nombre = {c.nombre.strip(): c for c, _ in agrupar_iguales(piezas)}

    ficheros = {}
    for f in os.listdir(dir_profesional):
        m = re.match(r'(.+)\.dxf$', f, re.IGNORECASE)
        if m and re.search(r'P\d{2,3}$', m.group(1)):
            ficheros[m.group(1)] = os.path.join(dir_profesional, f)

    iguales, girada, distinta, sin_dato = [], [], [], []
    for nombre in sorted(ficheros, key=lambda p: int(re.search(r'P(\d+)$', p).group(1))):
        prof = medidas_dxf(ficheros[nombre])
        comp = por_nombre.get(nombre)
        if prof is None or comp is None:
            sin_dato.append((nombre, 'no está en el STEP' if comp is None else 'DXF ilegible'))
            continue
        nuestra, motivo = medidas_nuestras(comp.solido)
        if nuestra is None:
            sin_dato.append((nombre, motivo))
            continue
        # Girada = mismas medidas con largo y ancho intercambiados. Es la misma
        # pieza; solo cambia a qué lado llamamos largo.
        if abs(prof[0] - nuestra[0]) <= TOL_MM and abs(prof[1] - nuestra[1]) <= TOL_MM:
            iguales.append(nombre)
        elif abs(prof[0] - nuestra[1]) <= TOL_MM and abs(prof[1] - nuestra[0]) <= TOL_MM:
            girada.append((nombre, prof, nuestra))
        else:
            distinta.append((nombre, prof, nuestra))

    total = len(ficheros)
    print(f'PIEZAS DEL PROFESIONAL: {total}')
    print(f'  ✓ iguales           : {len(iguales)}')
    print(f'  ↻ giradas (mismas medidas, ejes cambiados): {len(girada)}')
    print(f'  ✗ distintas         : {len(distinta)}')
    print(f'  ? sin dato          : {len(sin_dato)}')
    if girada:
        print('\nGIRADAS:')
        for n, p, q in girada:
            print(f'  {n:<6} profesional {p[0]:9.1f}x{p[1]:<9.1f} nuestro {q[0]:9.1f}x{q[1]:.1f}')
    if distinta:
        print('\nDISTINTAS:')
        for n, p, q in distinta:
            print(f'  {n:<6} profesional {p[0]:9.1f}x{p[1]:<9.1f} nuestro {q[0]:9.1f}x{q[1]:<9.1f}'
                  f'  dif {abs(p[0]-q[0]):7.1f} x {abs(p[1]-q[1]):.1f}')
    if sin_dato:
        print('\nSIN DATO:')
        for n, motivo in sin_dato:
            print(f'  {n:<6} {motivo}')
    return len(iguales), len(girada), len(distinta), len(sin_dato)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    comparar(sys.argv[1], sys.argv[2])
