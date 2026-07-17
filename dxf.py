"""Genera un plano DXF por pieza a partir del resultado de
analisis.analizar_solido() — v2, estilo plano de taller (Solid Edge):

  - Vista principal (alzado, plano X·ancho): contorno real con ingletes,
    taladros de la cara frontal (círculo + ejes de centro) y de la trasera
    (trazo discontinuo).
  - Vista planta (encima, alineada en X): silueta L×grosor con los taladros
    de las caras superior (continuo) e inferior (discontinuo).
  - Vistas de extremo (izquierda/derecha, alineadas): sección grosor×ancho
    con los taladros axiales de cada testero.
  - Cotas DXF reales (DIMENSION): longitud, ancho y grosor.
  - Tabla de taladros (como la "hole table" de Solid Edge): cada taladro
    lleva etiqueta T1, T2… y la tabla lista cara, X, Y, Ø, profundidad
    (o PASANTE) y avellanado. Escala a piezas con decenas de taladros donde
    acotar cada uno sería ilegible.
  - Cajetín con nombre, medidas, ángulos de corte y fecha.

Convención de coordenadas del análisis (marco local consistente):
  X = a lo largo de la barra desde el extremo izquierdo (extremo_a).
  En caras frontal/trasera: y = distancia desde el borde inferior (Y).
  En caras superior/inferior: y = distancia desde la cara frontal (Z).
  En extremos: x = desde borde inferior (Y), y = desde cara frontal (Z).

La pieza se normaliza para que la vista principal empiece en (0,0), 1:1.
"""
import math
from datetime import date

import ezdxf

# Capas del plano
CAPAS = [
    ('CONTORNO', 7, 'CONTINUOUS'),
    ('TALADROS', 1, 'CONTINUOUS'),
    ('TALADROS_OCULTOS', 8, 'DASHED'),
    ('EJES', 4, 'DASHDOT'),
    ('COTAS', 3, 'CONTINUOUS'),
    ('TEXTO', 7, 'CONTINUOUS'),
    ('CAJETIN', 7, 'CONTINUOUS'),
]

UNIT_LABELS = {0: 'unidades', 1: 'in', 2: 'ft', 4: 'mm', 5: 'cm', 6: 'm'}


def _texto(msp, contenido, punto, altura, capa='TEXTO'):
    msp.add_text(contenido, dxfattribs={'layer': capa, 'height': altura, 'insert': punto})


def _ejes_centro(msp, cx, cy, radio):
    """Marcas de centro de taladro (cruz que sobresale del círculo)."""
    e = radio * 1.4
    msp.add_line((cx - e, cy), (cx + e, cy), dxfattribs={'layer': 'EJES'})
    msp.add_line((cx, cy - e), (cx, cy + e), dxfattribs={'layer': 'EJES'})


def _circulo_taladro(msp, cx, cy, diametro, oculto=False):
    capa = 'TALADROS_OCULTOS' if oculto else 'TALADROS'
    msp.add_circle((cx, cy), diametro / 2, dxfattribs={'layer': capa})
    _ejes_centro(msp, cx, cy, diametro / 2)


def _cota_lineal(msp, p1, p2, base, alto_texto, angulo=0):
    dim = msp.add_linear_dim(
        base=base, p1=p1, p2=p2, angle=angulo, dimstyle='EZDXF',
        override={'dimtxt': alto_texto, 'dimasz': alto_texto * 0.8, 'dimexe': alto_texto * 0.4,
                  'dimexo': alto_texto * 0.3, 'dimdec': 1, 'dimlfac': 1, 'dimclrt': 3},
        dxfattribs={'layer': 'COTAS'},
    )
    dim.render()


def generar_dxf_pieza(nombre_capa, analisis, insunits=4, measurement=1):
    """analisis = dict de analisis.analizar_solido() con ok=True.
    Devuelve un ezdxf.Document con el plano completo de la pieza."""
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

    # Módulo de tamaño para textos/separaciones, proporcional a la pieza.
    t = max(ancho * 0.08, 3.0)
    sep = max(ancho * 0.45, 6 * t)

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
    _texto(msp, 'PLANTA', (0, y_planta + grosor + t * 0.6), t * 0.8)
    _texto(msp, 'ALZADO', (0, -t * 1.6), t * 0.8)

    # ── Vistas de extremo (grosor × ancho), a izquierda y derecha ───────────
    x_ext_a = -sep - grosor
    x_ext_b = L + sep
    for x0, etiqueta in ((x_ext_a, 'EXTREMO A'), (x_ext_b, 'EXTREMO B')):
        msp.add_lwpolyline(
            [(x0, 0), (x0 + grosor, 0), (x0 + grosor, ancho), (x0, ancho)],
            close=True, dxfattribs={'layer': 'CONTORNO'},
        )
        _texto(msp, etiqueta, (x0, -t * 1.6), t * 0.7)

    # ── Taladros por vista + etiquetas T1..Tn ───────────────────────────────
    filas_tabla = []
    for i, tal in enumerate(taladros, start=1):
        tag = f'T{i}'
        cara = tal.get('cara')
        x, y = tal.get('x'), tal.get('y')
        d = tal['diametro']
        pos_etiqueta = None

        if cara in ('frontal', 'trasera') and x is not None:
            _circulo_taladro(msp, x, y, d, oculto=(cara == 'trasera'))
            pos_etiqueta = (x + d / 2 + t * 0.3, y + d / 2 + t * 0.3)
        elif cara in ('superior', 'inferior') and x is not None:
            cy = y_planta + y
            _circulo_taladro(msp, x, cy, d, oculto=(cara == 'inferior'))
            pos_etiqueta = (x + d / 2 + t * 0.3, cy + d / 2 + t * 0.3)
        elif cara == 'extremo_a' and x is not None:
            cx = x_ext_a + y  # y del análisis = desde cara frontal (eje Z, horizontal aquí)
            _circulo_taladro(msp, cx, x, d)
            pos_etiqueta = (cx + d / 2 + t * 0.3, x + d / 2 + t * 0.3)
        elif cara == 'extremo_b' and x is not None:
            cx = x_ext_b + y
            _circulo_taladro(msp, cx, x, d)
            pos_etiqueta = (cx + d / 2 + t * 0.3, x + d / 2 + t * 0.3)

        if pos_etiqueta:
            _texto(msp, tag, pos_etiqueta, t * 0.7, capa='TALADROS')

        prof = 'PASANTE' if tal.get('pasante') else f"prof. {tal.get('profundidad', '?')}"
        avell = ''
        if tal.get('avellanado'):
            av = tal['avellanado']
            avell = f" + caja Ø{av['diametro']}x{av['profundidad']}"
        filas_tabla.append((tag, cara or 'sin cara', x if x is not None else '—',
                            y if y is not None else '—', f'Ø{d}', prof + avell))

    # ── Cotas generales ─────────────────────────────────────────────────────
    y_cota_L = -sep * 0.8
    _cota_lineal(msp, (0, 0), (L, 0), (L / 2, y_cota_L), t)
    x_cota_ancho = x_ext_a - sep * 0.5
    _cota_lineal(msp, (0, 0), (0, ancho), (x_cota_ancho, ancho / 2), t, angulo=90)
    y_cota_grosor = y_planta + grosor + sep * 0.5
    _cota_lineal(msp, (0, y_planta), (0, y_planta + grosor), (x_cota_ancho, y_planta + grosor / 2), t, angulo=90)

    # ── Tabla de taladros ───────────────────────────────────────────────────
    y_tabla = y_cota_L - sep * 0.6
    if filas_tabla:
        _texto(msp, 'TABLA DE TALADROS', (0, y_tabla), t * 0.85)
        cab = f"{'TAG':<5}{'CARA':<12}{'X':>9}{'Y':>9}  {'Ø':<7}{'PROFUNDIDAD'}"
        _texto(msp, cab, (0, y_tabla - t * 1.4), t * 0.7)
        for j, (tag, cara, x, y, dia, prof) in enumerate(filas_tabla):
            fila = f"{tag:<5}{cara:<12}{str(x):>9}{str(y):>9}  {dia:<7}{prof}"
            _texto(msp, fila, (0, y_tabla - t * 1.4 * (j + 2)), t * 0.7)
        y_fin_tabla = y_tabla - t * 1.4 * (len(filas_tabla) + 2)
    else:
        y_fin_tabla = y_tabla

    descartados = analisis.get('taladros_descartados', 0)
    if descartados:
        _texto(msp, f'({descartados} geometrías cilíndricas no-taladro descartadas: fillets/fresados)',
               (0, y_fin_tabla - t * 1.4), t * 0.6)
        y_fin_tabla -= t * 1.4

    # ── Cajetín ─────────────────────────────────────────────────────────────
    alto_caj = t * 5.6
    ancho_caj = max(L * 0.55, t * 40)
    y0 = y_fin_tabla - t * 1.5 - alto_caj
    msp.add_lwpolyline(
        [(0, y0), (ancho_caj, y0), (ancho_caj, y0 + alto_caj), (0, y0 + alto_caj)],
        close=True, dxfattribs={'layer': 'CAJETIN'},
    )
    msp.add_line((0, y0 + alto_caj - t * 1.8), (ancho_caj, y0 + alto_caj - t * 1.8), dxfattribs={'layer': 'CAJETIN'})
    _texto(msp, nombre_capa, (t * 0.5, y0 + alto_caj - t * 1.4), t, capa='CAJETIN')
    unidad = UNIT_LABELS.get(insunits, 'mm')
    _texto(msp, f'L {L:.1f} x {ancho:.1f} x {grosor:.1f} {unidad}   '
                f'Cortes: {analisis["angulo_corte_a"]:.1f}° / {analisis["angulo_corte_b"]:.1f}°'
                + ('' if analisis.get('seccion_regular', True) else '   (sección aprox.)'),
           (t * 0.5, y0 + alto_caj - t * 3.2), t * 0.75, capa='CAJETIN')
    _texto(msp, f'Taladros: {len(taladros)}   Aluminios Cariñena   {date.today().strftime("%d/%m/%Y")}',
           (t * 0.5, y0 + t * 0.7), t * 0.75, capa='CAJETIN')

    return doc
