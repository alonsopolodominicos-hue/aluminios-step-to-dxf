"""Desarrollo (despliegue) de paneles CURVADOS a su forma plana de corte.

Un panel curvado no es un panel de caja: sus dos caras grandes son
CILÍNDRICAS, no planas, así que `analizar_solido_panel` lo rechaza ("no
define una caja completa") y desaparecía del ZIP. Medido en el techo
2876_7616 son 13 piezas: el metacrilato (PMMA, 2 mm) y el tablero flexible
(FLEX, 9-10 mm), curvados con radios de 518 a 3022 mm.

Estas piezas se cortan PLANAS y se curvan después, así que lo que hace falta
en el DXF es su desarrollo: el largo de arco de la fibra media. Para una
superficie cilíndrica el cálculo es exacto:

    grosor  = |R_exterior − R_interior|
    R_medio = (R_exterior + R_interior) / 2
    largo   = R_medio × ángulo barrido        (fibra neutra)
    ancho   = longitud del cilindro en su eje

La comprobación de que el desarrollo es correcto es el ÁREA: largo × ancho
tiene que coincidir con el área de la cara cilíndrica medida por OCCT. Si no
coincide, la pieza no es un cilindro simple y se prefiere no dar un número
inventado — se descarta con su motivo.
"""
from __future__ import annotations

import math

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_SurfaceType

# Tolerancia del cuadre de área entre el desarrollo y la cara real (5 %).
# Por encima, la superficie no es un cilindro simple (tiene recortes o
# varios radios) y el desarrollo plano no sería fiable.
TOL_AREA = 0.05
# Radios fuera de esto no son un curvado de taller.
RADIO_MIN, RADIO_MAX = 50.0, 20000.0
# Grosores de chapa/tablero flexible admitidos (mm).
GROSOR_MIN, GROSOR_MAX = 0.5, 40.0


def _cilindros(solido):
    """Caras cilíndricas con su radio, área y rango de ángulo/longitud."""
    salida = []
    for f in solido.Faces():
        ad = BRepAdaptor_Surface(f.wrapped)
        if ad.GetType() != GeomAbs_SurfaceType.GeomAbs_Cylinder:
            continue
        cil = ad.Cylinder()
        salida.append({
            'radio': cil.Radius(),
            'area': f.Area(),
            'u0': ad.FirstUParameter(), 'u1': ad.LastUParameter(),
            'v0': ad.FirstVParameter(), 'v1': ad.LastVParameter(),
            'eje': (cil.Axis().Direction().X(), cil.Axis().Direction().Y(), cil.Axis().Direction().Z()),
        })
    return salida


def analizar_panel_curvado(solido):
    """Desarrolla un panel curvado. Devuelve el mismo dict que
    `analizar_solido_panel` (largo/ancho/grosor/contorno/…) o
    {'ok': False, 'motivo'} si no es un curvado de un solo radio.
    """
    cils = _cilindros(solido)
    if len(cils) < 2:
        return {'ok': False, 'motivo': 'no es un panel curvado (menos de 2 caras cilíndricas)'}

    # Las dos caras grandes son la exterior y la interior del mismo curvado.
    cils.sort(key=lambda c: -c['area'])
    ext, inte = cils[0], cils[1]
    grosor = abs(ext['radio'] - inte['radio'])
    if not (GROSOR_MIN <= grosor <= GROSOR_MAX):
        return {'ok': False, 'motivo': f'grosor de {grosor:.1f} mm fuera de rango para un curvado'}

    radio_medio = (ext['radio'] + inte['radio']) / 2
    if not (RADIO_MIN <= radio_medio <= RADIO_MAX):
        return {'ok': False, 'motivo': f'radio de curvado de {radio_medio:.0f} mm fuera de rango'}

    barrido = abs(ext['u1'] - ext['u0'])          # ángulo, en radianes
    ancho = abs(ext['v1'] - ext['v0'])            # longitud a lo largo del eje
    if barrido <= 0 or ancho <= 0:
        return {'ok': False, 'motivo': 'no se pudo medir el barrido del curvado'}

    largo = radio_medio * barrido                 # fibra neutra

    # Comprobación de que el desarrollo es real: el área tiene que cuadrar.
    area_desarrollo = (ext['radio'] * barrido) * ancho
    if ext['area'] <= 0 or abs(area_desarrollo - ext['area']) / ext['area'] > TOL_AREA:
        return {'ok': False, 'motivo': (
            'el desarrollo no cuadra con el área real de la cara '
            f'({area_desarrollo / 100:.0f} vs {ext["area"] / 100:.0f} cm²): '
            'la pieza no es un curvado de un solo radio')}

    largo_r = round(largo, 2)
    ancho_r = round(ancho, 2)
    return {
        'ok': True,
        'largo': largo_r,
        'ancho': ancho_r,
        'grosor': round(grosor, 2),
        'seccion_regular': True,
        'curvado': True,
        'radio_curvado': round(radio_medio, 1),
        'angulo_curvado': round(math.degrees(barrido), 1),
        'taladros': [],
        'taladros_descartados': 0,
        'cajeados': [],
        'huecos': [],
        'angulo_corte_a': 0.0, 'angulo_corte_b': 0.0,
        'plano_corte_a': 'recto', 'plano_corte_b': 'recto',
        'advertencias': [
            f'Pieza CURVADA desarrollada: se corta PLANA de {largo_r:.0f}×{ancho_r:.0f} mm '
            f'y se curva a radio {radio_medio:.0f} mm ({math.degrees(barrido):.0f}°). '
            'La medida del largo es la fibra media.',
        ],
    }
