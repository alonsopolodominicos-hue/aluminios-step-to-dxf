"""Genera un plano DXF por pieza a partir del resultado de
analisis.analizar_solido() — solo geometría, sin texto ni cotas:

  - Vista principal (alzado, plano X·ancho): contorno real con ingletes,
    taladros de la cara frontal (círculo + ejes de centro) y de la trasera
    (trazo discontinuo).
  - Vista planta (encima, alineada en X): silueta L×grosor con los taladros
    de las caras superior (continuo) e inferior (discontinuo).
  - Vistas de extremo (izquierda/derecha, alineadas): sección grosor×ancho
    con los taladros axiales de cada testero.

Convención de coordenadas del análisis (marco local consistente):
  X = a lo largo de la barra desde el extremo izquierdo (extremo_a).
  En caras frontal/trasera: y = distancia desde el borde inferior (Y).
  En caras superior/inferior: y = distancia desde la cara frontal (Z).
  En extremos: x = desde borde inferior (Y), y = desde cara frontal (Z).

La pieza se normaliza para que la vista principal empiece en (0,0), 1:1.
"""
import math

import ezdxf

# Capas del plano
CAPAS = [
    ('CONTORNO', 7, 'CONTINUOUS'),
    ('TALADROS', 1, 'CONTINUOUS'),
    ('TALADROS_OCULTOS', 8, 'DASHED'),
    ('EJES', 4, 'DASHDOT'),
]


def _ejes_centro(msp, cx, cy, radio):
    """Marcas de centro de taladro (cruz que sobresale del círculo)."""
    e = radio * 1.4
    msp.add_line((cx - e, cy), (cx + e, cy), dxfattribs={'layer': 'EJES'})
    msp.add_line((cx, cy - e), (cx, cy + e), dxfattribs={'layer': 'EJES'})


def _circulo_taladro(msp, cx, cy, diametro, oculto=False):
    capa = 'TALADROS_OCULTOS' if oculto else 'TALADROS'
    msp.add_circle((cx, cy), diametro / 2, dxfattribs={'layer': capa})
    _ejes_centro(msp, cx, cy, diametro / 2)


def generar_dxf_pieza(nombre_capa, analisis, insunits=4, measurement=1):
    """analisis = dict de analisis.analizar_solido() con ok=True.
    Devuelve un ezdxf.Document con el plano completo de la pieza (solo
    geometría: sin textos, cotas, tabla de taladros ni cajetín)."""
    del nombre_capa  # ya no se usa: era solo para el cajetín, que se ha quitado.
    L = analisis['longitud']
    ancho = analisis.get('ancho') or (analisis['seccion'][1] if len(analisis['seccion']) > 1 else 20)
    grosor = analisis.get('grosor') or analisis['seccion'][0]
    ang_a = math.radians(analisis['angulo_corte_a'])
    ang_b = math.radians(analisis['angulo_corte_b'])
    taladros = analisis.get('taladros', [])

    doc = ezdxf.new(dxfversion='R2010', setup=True)
    doc.header['$INSUNITS'] = insunits
    doc.header['$MEASUREMENT'] = measurement
    for nombre, color, tipo in CAPAS:
        doc.layers.add(name=nombre, color=color, linetype=tipo)
    msp = doc.modelspace()

    # Separación entre vistas, proporcional a la pieza.
    sep = max(ancho * 0.45, 18.0)

    # ── Vista principal (alzado): contorno con ingletes ──────────────────────
    # off = desplazamiento horizontal del borde inclinado a lo alto de la
    # pieza: ancho·tan(ángulo) → el borde dibujado forma el ángulo REAL.
    # Convención: el borde superior es el largo punta a punta (la longitud L
    # del análisis); el inferior queda recortado por los ingletes. El sentido
    # real del inglete se verifica en pieza; el ángulo exacto va acotado.
    off_a = ancho * math.tan(ang_a) if ang_a else 0.0
    off_b = ancho * math.tan(ang_b) if ang_b else 0.0
    contorno = [(off_a, 0), (L - off_b, 0), (L, ancho), (0, ancho)]
    msp.add_lwpolyline(contorno, close=True, dxfattribs={'layer': 'CONTORNO'})

    # ── Vista planta (L × grosor), encima ────────────────────────────────────
    y_planta = ancho + sep
    msp.add_lwpolyline(
        [(0, y_planta), (L, y_planta), (L, y_planta + grosor), (0, y_planta + grosor)],
        close=True, dxfattribs={'layer': 'CONTORNO'},
    )

    # ── Vistas de extremo (grosor × ancho), a izquierda y derecha ───────────
    x_ext_a = -sep - grosor
    x_ext_b = L + sep
    for x0 in (x_ext_a, x_ext_b):
        msp.add_lwpolyline(
            [(x0, 0), (x0 + grosor, 0), (x0 + grosor, ancho), (x0, ancho)],
            close=True, dxfattribs={'layer': 'CONTORNO'},
        )

    # ── Taladros por vista ──────────────────────────────────────────────────
    for tal in taladros:
        cara = tal.get('cara')
        x, y = tal.get('x'), tal.get('y')
        d = tal['diametro']

        if cara in ('frontal', 'trasera') and x is not None:
            _circulo_taladro(msp, x, y, d, oculto=(cara == 'trasera'))
        elif cara in ('superior', 'inferior') and x is not None:
            cy = y_planta + y
            _circulo_taladro(msp, x, cy, d, oculto=(cara == 'inferior'))
        elif cara == 'extremo_a' and x is not None:
            cx = x_ext_a + y  # y del análisis = desde cara frontal (eje Z, horizontal aquí)
            _circulo_taladro(msp, cx, x, d)
        elif cara == 'extremo_b' and x is not None:
            cx = x_ext_b + y
            _circulo_taladro(msp, cx, x, d)

    return doc
