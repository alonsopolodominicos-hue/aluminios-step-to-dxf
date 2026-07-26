"""Genera el plano DXF de un PANEL de tablero (madera/melamina) a partir del
resultado de analisis_panel.analizar_solido_panel() — escala 1:1 y pieza en el
origen (0,0), como los planos de barra de dxf.py:

  - Vista principal (la cara ancha, largo × ancho): contorno, taladros de la
    cara frontal (círculo + ejes de centro) y de la trasera (discontinuo), y
    cajeados/ranuras como rectángulos (FRESADO / discontinuo si van por la
    cara trasera).
  - Taladros de canto (lateral_iz/lateral_de/superior/inferior): marca corta
    perpendicular al borde correspondiente en su posición, con etiqueta — el
    detalle exacto va en la tabla.
  - Cotas DXF reales de largo y ancho; el grosor va en el cajetín.
  - Tabla de mecanizados (taladros y cajeados) con cara, X, Y, medida y
    profundidad — mismo formato de "hole table" que los planos de barra.
  - Cajetín con nombre, medidas, avisos y fecha.

Convención de coordenadas del análisis (marco local consistente):
  En caras frontal/trasera: x = a lo largo del LARGO, y = desde el borde
  inferior (el ANCHO) — se dibujan tal cual en la vista principal.
  En superior/inferior: y = distancia desde la cara frontal (el GROSOR).
  En lateral_iz/lateral_de: x = desde el borde inferior (ANCHO), y = desde la
  cara frontal (GROSOR).
"""
from datetime import date

import ezdxf

from dxf import _texto, _circulo_taladro, _cota_lineal, UNIT_LABELS

CAPAS_PANEL = [
    ('CONTORNO', 7, 'CONTINUOUS'),
    ('TALADROS', 1, 'CONTINUOUS'),
    ('TALADROS_OCULTOS', 8, 'DASHED'),
    ('FRESADO', 2, 'CONTINUOUS'),
    ('FRESADO_OCULTO', 30, 'DASHED'),
    ('EJES', 4, 'DASHDOT'),
    ('COTAS', 3, 'CONTINUOUS'),
    ('TEXTO', 7, 'CONTINUOUS'),
    ('CAJETIN', 7, 'CONTINUOUS'),
]


def _rect(msp, x0, y0, x1, y1, capa):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                       dxfattribs={'layer': capa})


def generar_dxf_panel(nombre_capa, analisis, insunits=4, measurement=1):
    """analisis = dict de analizar_solido_panel() con ok=True.
    Devuelve un ezdxf.Document con el plano completo del panel."""
    L = analisis['largo']
    A = analisis['ancho']
    G = analisis['grosor']
    taladros = analisis.get('taladros', [])
    cajeados = analisis.get('cajeados', [])
    avisos = analisis.get('advertencias', [])

    doc = ezdxf.new(dxfversion='R2010', setup=True)
    doc.header['$INSUNITS'] = insunits
    doc.header['$MEASUREMENT'] = measurement
    for nombre, color, tipo in CAPAS_PANEL:
        doc.layers.add(name=nombre, color=color, linetype=tipo)
    msp = doc.modelspace()

    # Módulo de tamaño para textos/separaciones, proporcional a la pieza.
    t = max(min(L, A) * 0.03, 6.0)
    sep = 8 * t

    # ── Vista principal: contorno del panel 1:1 en el origen ────────────────
    _rect(msp, 0, 0, L, A, 'CONTORNO')

    filas_tabla = []
    n_tag = 0

    # ── Taladros ────────────────────────────────────────────────────────────
    for tal in taladros:
        n_tag += 1
        tag = f'T{n_tag}'
        cara = tal.get('cara')
        x, y = tal.get('x'), tal.get('y')
        d = tal['diametro']
        pos_etiqueta = None

        if cara in ('frontal', 'trasera') and x is not None:
            _circulo_taladro(msp, x, y, d, oculto=(cara == 'trasera'))
            pos_etiqueta = (x + d / 2 + t * 0.3, y + d / 2 + t * 0.3)
        elif cara in ('superior', 'inferior') and x is not None:
            # Canto largo: marca corta perpendicular al borde en su x.
            borde_y = A if cara == 'superior' else 0
            signo = 1 if cara == 'superior' else -1
            msp.add_line((x, borde_y), (x, borde_y + signo * t * 1.2), dxfattribs={'layer': 'TALADROS'})
            pos_etiqueta = (x + t * 0.3, borde_y + signo * t * 1.4)
        elif cara in ('lateral_iz', 'lateral_de') and x is not None:
            # Canto corto: la x local del análisis recorre el ANCHO del panel.
            borde_x = 0 if cara == 'lateral_iz' else L
            signo = -1 if cara == 'lateral_iz' else 1
            msp.add_line((borde_x, x), (borde_x + signo * t * 1.2, x), dxfattribs={'layer': 'TALADROS'})
            pos_etiqueta = (borde_x + signo * t * 1.4, x + t * 0.3)

        if pos_etiqueta:
            _texto(msp, tag, pos_etiqueta, t * 0.7, capa='TALADROS')

        prof = 'PASANTE' if tal.get('pasante') else f"prof. {tal.get('profundidad', '?')}"
        extra = ''
        if tal.get('avellanado'):
            av = tal['avellanado']
            extra = f" + caja Ø{av['diametro']}x{av['profundidad']}"
        filas_tabla.append((tag, cara or 'sin cara', x if x is not None else '—',
                            y if y is not None else '—', f'Ø{d}', prof + extra))

    # ── Cajeados / ranuras ──────────────────────────────────────────────────
    for caj in cajeados:
        n_tag += 1
        tag = f'T{n_tag}'
        capa = 'FRESADO_OCULTO' if caj['cara'] == 'trasera' else 'FRESADO'
        x0, y0 = caj['x'], caj['y']
        _rect(msp, x0, y0, x0 + caj['largo'], y0 + caj['ancho'], capa)
        _texto(msp, tag, (x0 + caj['largo'] + t * 0.3, y0), t * 0.7, capa=capa)
        filas_tabla.append((tag, caj['cara'], x0, y0,
                            f"{caj['forma']} {caj['largo']:.0f}x{caj['ancho']:.0f}",
                            f"prof. {caj['profundidad']}"))

    # ── Cotas generales ─────────────────────────────────────────────────────
    _cota_lineal(msp, (0, 0), (L, 0), (L / 2, -sep * 0.35), t)
    _cota_lineal(msp, (0, 0), (0, A), (-sep * 0.35, A / 2), t, angulo=90)

    # ── Tabla de mecanizados ────────────────────────────────────────────────
    y_tabla = -sep * 0.6
    if filas_tabla:
        _texto(msp, 'TABLA DE MECANIZADOS', (0, y_tabla), t * 0.85)
        cab = f"{'TAG':<5}{'CARA':<12}{'X':>9}{'Y':>9}  {'MEDIDA':<18}{'PROFUNDIDAD'}"
        _texto(msp, cab, (0, y_tabla - t * 1.4), t * 0.7)
        for j, (tag, cara, x, y, medida, prof) in enumerate(filas_tabla):
            fila = f"{tag:<5}{cara:<12}{str(x):>9}{str(y):>9}  {medida:<18}{prof}"
            _texto(msp, fila, (0, y_tabla - t * 1.4 * (j + 2)), t * 0.7)
        y_fin_tabla = y_tabla - t * 1.4 * (len(filas_tabla) + 2)
    else:
        y_fin_tabla = y_tabla

    for aviso in avisos:
        _texto(msp, f'⚠ {aviso}', (0, y_fin_tabla - t * 1.4), t * 0.6)
        y_fin_tabla -= t * 1.4

    # ── Cajetín ─────────────────────────────────────────────────────────────
    alto_caj = t * 5.6
    ancho_caj = max(L * 0.55, t * 40)
    y0 = y_fin_tabla - t * 1.5 - alto_caj
    _rect(msp, 0, y0, ancho_caj, y0 + alto_caj, 'CAJETIN')
    msp.add_line((0, y0 + alto_caj - t * 1.8), (ancho_caj, y0 + alto_caj - t * 1.8),
                 dxfattribs={'layer': 'CAJETIN'})
    _texto(msp, nombre_capa, (t * 0.5, y0 + alto_caj - t * 1.4), t, capa='CAJETIN')
    unidad = UNIT_LABELS.get(insunits, 'mm')
    _texto(msp, f'Panel {L:.1f} x {A:.1f} x {G:.1f} {unidad}', (t * 0.5, y0 + alto_caj - t * 3.2),
           t * 0.75, capa='CAJETIN')
    _texto(msp, f'Taladros: {len(taladros)}   Cajeados: {len(cajeados)}   '
                f'Aluminios Cariñena   {date.today().strftime("%d/%m/%Y")}',
           (t * 0.5, y0 + t * 0.7), t * 0.75, capa='CAJETIN')

    return doc
