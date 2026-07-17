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
  - Taladros: la pieza puede tener mecanizados en varias caras (4 laterales
    + 2 de extremo), pero este dibujo es una única vista 2D — no un plano
    multivista. Se dibujan como círculo los taladros de la cara lateral con
    más mecanizados (la "cara principal"); el resto se anota como texto
    (cara, posición, diámetro) bajo el dibujo, no se descarta nada.

La pieza se normaliza a origen (0,0), igual que en la Parte A
(api/cam-separar-dxf.py), reutilizando ezdxf.bbox.extents().
"""
import math

import ezdxf
from ezdxf import bbox

UNIT_LABELS = {0: 'unidades', 1: 'in', 2: 'ft', 4: 'mm', 5: 'cm', 6: 'm'}


def _dibujar_taladros(msp, nombre_capa, taladros, alto_texto):
    """Dibuja como círculo los taladros de la cara lateral con más mecanizados
    (la "cara principal" de este dibujo 2D); el resto se anota como texto —
    no hay una única vista 2D que muestre las 6 caras de la pieza a la vez."""
    if not taladros:
        return

    laterales = [t for t in taladros if not t.get('es_extremo') and t.get('cara')]
    cara_dibujada = None
    dibujados = []
    if laterales:
        conteo = {}
        for t in laterales:
            conteo[t['cara']] = conteo.get(t['cara'], 0) + 1
        cara_dibujada = max(conteo, key=conteo.get)
        dibujados = [t for t in laterales if t['cara'] == cara_dibujada]

    for t in dibujados:
        radio = t['diametro'] / 2
        msp.add_circle((t['x'], t['y']), radio, dxfattribs={'layer': nombre_capa})
        msp.add_text(
            f"Ø{t['diametro']:.1f}",
            dxfattribs={'layer': nombre_capa, 'height': max(alto_texto * 0.6, 1.5), 'insert': (t['x'] + radio + 1, t['y'])},
        )

    no_dibujados = [t for t in taladros if t not in dibujados]
    if no_dibujados:
        y_base = -alto_texto * 5.2
        cabecera = f"Otros mecanizados (cara dibujada arriba: {cara_dibujada or 'ninguna'}):" if cara_dibujada else 'Mecanizados (posición sin determinar la cara dibujada):'
        msp.add_text(cabecera, dxfattribs={'layer': nombre_capa, 'height': alto_texto * 0.6, 'insert': (0, y_base)})
        for i, t in enumerate(no_dibujados, start=1):
            if t['x'] is not None:
                linea = f"  {t['cara']}: x={t['x']:.1f} y={t['y']:.1f} Ø{t['diametro']:.1f}"
            else:
                linea = f"  cara sin determinar: Ø{t['diametro']:.1f} (revisar a mano)"
            msp.add_text(linea, dxfattribs={'layer': nombre_capa, 'height': alto_texto * 0.55, 'insert': (0, y_base - i * alto_texto * 0.75)})


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

    _dibujar_taladros(msp, nombre_capa, analisis.get('taladros', []), alto_texto)

    entidades = list(msp)
    caja = bbox.extents(entidades, fast=True)
    if caja is not None and caja.has_data:
        dx, dy = -caja.extmin.x, -caja.extmin.y
        if dx != 0 or dy != 0:
            for entidad in entidades:
                entidad.translate(dx, dy, 0)

    return doc
