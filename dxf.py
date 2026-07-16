"""Genera un DXF esquemático (vista lateral) por pieza a partir del
resultado de analisis.analizar_solido().

Convención del dibujo (vista de perfil, X = eje largo, Y = altura de sección):
  - Ángulo 0° en un extremo → corte recto (borde vertical).
  - Ángulo >0° → el borde se inclina esa cantidad; se dibuja como un
    paralelogramo/trapecio con la punta hacia la esquina inferior en cada
    extremo mitrado. Es una CONVENCIÓN de dibujo — el ángulo exacto siempre
    se anota como texto, que es el dato que importa para la sierra; la
    dirección del inglete en el dibujo es solo referencia visual y debe
    confirmarse contra la pieza real antes de cortar.

La pieza se normaliza a origen (0,0), igual que en la Parte A
(api/cam-separar-dxf.py), reutilizando ezdxf.bbox.extents().
"""
import math

import ezdxf
from ezdxf import bbox

UNIT_LABELS = {0: 'unidades', 1: 'in', 2: 'ft', 4: 'mm', 5: 'cm', 6: 'm'}


def generar_dxf_pieza(nombre_capa, analisis, insunits=4, measurement=1):
    """analisis = dict de analisis.analizar_solido() con ok=True.
    Devuelve un ezdxf.Document ya normalizado a origen."""
    L = analisis['longitud']
    ancho_seccion, alto_seccion = analisis['seccion'][0], analisis['seccion'][1]
    H = alto_seccion
    ang_a = math.radians(analisis['angulo_corte_a'])
    ang_b = math.radians(analisis['angulo_corte_b'])

    offset_a = (H / 2) * math.tan(ang_a)
    offset_b = (H / 2) * math.tan(ang_b)

    puntos = [
        (offset_a, 0),
        (L - offset_b, 0),
        (L, H),
        (0, H),
    ]

    doc = ezdxf.new(dxfversion='R2010')
    doc.header['$INSUNITS'] = insunits
    doc.header['$MEASUREMENT'] = measurement
    doc.layers.add(name=nombre_capa, color=7)
    msp = doc.modelspace()

    msp.add_lwpolyline(puntos, close=True, dxfattribs={'layer': nombre_capa})

    alto_texto = max(H * 0.12, 3.0)
    msp.add_text(
        f"L={L:.1f}",
        dxfattribs={'layer': nombre_capa, 'height': alto_texto, 'insert': (L / 2 - L * 0.08, -alto_texto * 1.8)},
    )
    msp.add_text(
        f"{analisis['angulo_corte_a']:.1f}°",
        dxfattribs={'layer': nombre_capa, 'height': alto_texto, 'insert': (0, H + alto_texto * 0.5)},
    )
    msp.add_text(
        f"{analisis['angulo_corte_b']:.1f}°",
        dxfattribs={'layer': nombre_capa, 'height': alto_texto, 'insert': (L - alto_texto * 3, H + alto_texto * 0.5)},
    )
    msp.add_text(
        f"sección {ancho_seccion:.1f}x{alto_seccion:.1f}" + ('' if analisis['seccion_regular'] else ' (aprox.)'),
        dxfattribs={'layer': nombre_capa, 'height': alto_texto, 'insert': (0, -alto_texto * 3.4)},
    )

    entidades = list(msp)
    caja = bbox.extents(entidades, fast=True)
    if caja is not None and caja.has_data:
        dx, dy = -caja.extmin.x, -caja.extmin.y
        if dx != 0 or dy != 0:
            for entidad in entidades:
                entidad.translate(dx, dy, 0)

    return doc
