"""Genera el plano DXF de un PANEL de tablero (madera/melamina) a partir del
resultado de analisis_panel.analizar_solido_panel() — escala 1:1 y pieza en el
origen (0,0), como los planos de barra de dxf.py. Solo geometría, sin texto:

  - Vista principal (la cara ancha, largo × ancho): contorno, taladros de la
    cara frontal (círculo + ejes de centro) y de la trasera (discontinuo), y
    cajeados/ranuras como rectángulos (FRESADO / discontinuo si van por la
    cara trasera).
  - Taladros de canto (lateral_iz/lateral_de/superior/inferior): marca corta
    perpendicular al borde correspondiente en su posición.

Convención de coordenadas del análisis (marco local consistente):
  En caras frontal/trasera: x = a lo largo del LARGO, y = desde el borde
  inferior (el ANCHO) — se dibujan tal cual en la vista principal.
  En superior/inferior: y = distancia desde la cara frontal (el GROSOR).
  En lateral_iz/lateral_de: x = desde el borde inferior (ANCHO), y = desde la
  cara frontal (GROSOR).
"""
import ezdxf

from dxf import _circulo_taladro

# Colores ACI. Se evita el 2 (amarillo) para el mecanizado: sobre el fondo
# blanco que usa el taller en el CAD es casi invisible y daba la sensación de
# que el DXF salía "sin mecanizar". Los elegidos (rojo, azul, magenta) se ven
# tanto en fondo blanco como en negro; el 7 se adapta solo.
CAPAS_PANEL = [
    ('CONTORNO', 7, 'CONTINUOUS'),
    ('HUECOS', 2, 'CONTINUOUS'),            # amarillo — hueco PASANTE interior
    ('TALADROS', 1, 'CONTINUOUS'),          # rojo
    ('TALADROS_OCULTOS', 8, 'DASHED'),      # gris — taladro de la cara opuesta
    ('TALADROS_CANTO', 3, 'CONTINUOUS'),    # verde — taladro en un CANTO (a mano)
    ('FRESADO', 5, 'CONTINUOUS'),           # azul — cajeado en la cara vista
    ('FRESADO_OCULTO', 30, 'DASHED'),       # naranja — cajeado en la trasera
    ('CANALES', 6, 'DASHED'),               # magenta — ranuras en los CANTOS
    ('GALCES', 30, 'CONTINUOUS'),           # naranja — galce/rebaje de canto abierto a la cara vista
    ('GALCES_OCULTO', 30, 'DASHED'),        # ... y abierto a la cara de atrás
    ('EJES', 4, 'DASHDOT'),
]


def _mas_cercano(punto, polilinea):
    """Punto de la polilínea cerrada más próximo a `punto`. Se usa para llevar
    los extremos de una canal hasta la línea de corte sin suponer que el canto
    es recto."""
    px, py = punto
    mejor, dmin = polilinea[0], float('inf')
    for i in range(len(polilinea)):
        ax, ay = polilinea[i]
        bx, by = polilinea[(i + 1) % len(polilinea)]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        qx, qy = ax + t * dx, ay + t * dy
        d = (px - qx) ** 2 + (py - qy) ** 2
        if d < dmin:
            mejor, dmin = (qx, qy), d
    return mejor


def _rect(msp, x0, y0, x1, y1, capa):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                       dxfattribs={'layer': capa})


def generar_dxf_panel(nombre_capa, analisis, insunits=4, measurement=1):
    """analisis = dict de analizar_solido_panel() con ok=True.
    Devuelve un ezdxf.Document con el plano completo del panel (solo
    geometría: sin textos, cotas, tabla de mecanizados ni cajetín)."""
    del nombre_capa  # ya no se usa: era solo para el cajetín, que se ha quitado.
    L = analisis['largo']
    A = analisis['ancho']
    taladros = analisis.get('taladros', [])
    cajeados = analisis.get('cajeados', [])

    doc = ezdxf.new(dxfversion='R2010', setup=True)
    doc.header['$INSUNITS'] = insunits
    doc.header['$MEASUREMENT'] = measurement
    for nombre, color, tipo in CAPAS_PANEL:
        doc.layers.add(name=nombre, color=color, linetype=tipo)
    msp = doc.modelspace()

    # Módulo de tamaño para separaciones, proporcional a la pieza.
    t = max(min(L, A) * 0.03, 6.0)

    # Escala de los tipos de línea, PROPORCIONAL A LA PIEZA. El patrón DASHED
    # mide 1,27 mm: con la escala por defecto (1.0) sobre un panel de 2450 mm
    # los trazos son 1/1900 del dibujo y el CAD los pinta como una línea
    # fantasma — o no los pinta. Como el mecanizado vive precisamente en las
    # capas discontinuas (CANALES y los fresados de la cara oculta), el plano
    # parecía salir SIN mecanizar. Con esto los trazos miden ~2 cm y se ven.
    doc.header['$LTSCALE'] = round(t / 2, 2)

    # ── Vista principal: contorno del panel 1:1 en el origen ────────────────
    # Con forma no rectangular, el contorno REAL (polilínea cerrada, curvas
    # discretizadas a 0.05mm) más la envolvente en EJES como referencia.
    contorno = analisis.get('contorno')
    if contorno:
        msp.add_lwpolyline([(p[0], p[1]) for p in contorno], close=True,
                           dxfattribs={'layer': 'CONTORNO'})
        _rect(msp, 0, 0, L, A, 'EJES')
    else:
        _rect(msp, 0, 0, L, A, 'CONTORNO')

    # ── Huecos pasantes interiores (paso de cables, caja de enchufe…) ────────
    # Son wires interiores de la cara: hay que cortarlos igual que el contorno,
    # y hasta ahora no salían en ninguna parte del DXF.
    for hueco in analisis.get('huecos') or []:
        if len(hueco) >= 3:
            msp.add_lwpolyline([(p[0], p[1]) for p in hueco], close=True,
                               dxfattribs={'layer': 'HUECOS'})

    # ── Taladros ────────────────────────────────────────────────────────────
    for tal in taladros:
        cara = tal.get('cara')
        x, y = tal.get('x'), tal.get('y')
        d = tal['diametro']

        if cara in ('frontal', 'trasera') and x is not None:
            _circulo_taladro(msp, x, y, d, oculto=(cara == 'trasera'))
        elif cara in ('superior', 'inferior') and x is not None:
            # Canto largo: marca corta perpendicular al borde en su x.
            borde_y = A if cara == 'superior' else 0
            signo = 1 if cara == 'superior' else -1
            msp.add_line((x, borde_y), (x, borde_y + signo * t * 1.2), dxfattribs={'layer': 'TALADROS'})
        elif cara in ('lateral_iz', 'lateral_de') and x is not None:
            # Canto corto: la x local del análisis recorre el ANCHO del panel.
            borde_x = 0 if cara == 'lateral_iz' else L
            signo = -1 if cara == 'lateral_iz' else 1
            msp.add_line((borde_x, x), (borde_x + signo * t * 1.2, x), dxfattribs={'layer': 'TALADROS'})
        elif str(cara or '').startswith('canto_') and x is not None:
            # Canto de un panel con FORMA (no es ninguno de los 4 de la caja):
            # antes desaparecía del plano. Se marca en su sitio con una cruz y
            # un círculo a diámetro, en capa propia: el BPP no lo mecaniza, se
            # hace a mano y el operario necesita verlo.
            r = d / 2 if d else 2.0
            msp.add_circle((x, y), r, dxfattribs={'layer': 'TALADROS_CANTO'})
            msp.add_line((x - r * 1.6, y), (x + r * 1.6, y), dxfattribs={'layer': 'TALADROS_CANTO'})
            msp.add_line((x, y - r * 1.6), (x, y + r * 1.6), dxfattribs={'layer': 'TALADROS_CANTO'})

    # ── Cajeados / ranuras ──────────────────────────────────────────────────
    # En CARA (frontal/trasera): rectángulo a cota, en su posición real.
    # En CANTO (superior/inferior/lateral_*): la canal NO se apoya sobre la
    # línea del canto — entra HACIA DENTRO del panel su profundidad real. En
    # planta se ve como la banda de material que el disco se ha llevado: se
    # dibuja abierta al borde (solo los dos topes y la línea de fondo), en
    # trazo discontinuo porque queda enterrada dentro del grosor. Así se puede
    # medir en el CAD.
    # Línea de corte contra la que se apoyan las canales: el contorno real si
    # la pieza tiene forma, y si no la propia caja envolvente.
    contorno_ref = ([(pt[0], pt[1]) for pt in contorno] if contorno
                    else [(0.0, 0.0), (L, 0.0), (L, A), (0.0, A)])
    for caj in cajeados:
        x0, y0 = caj['x'], caj['y']
        cara = caj['cara']

        if not caj.get('en_canto'):
            capa = 'FRESADO_OCULTO' if cara == 'trasera' else 'FRESADO'
            # Con contorno real (bolsillo en L, redondeado, con arcos) se
            # dibuja la forma que es; sin él, la caja envolvente de siempre.
            forma_real = caj.get('puntos')
            if forma_real and len(forma_real) >= 3:
                msp.add_lwpolyline([(q[0], q[1]) for q in forma_real], close=True,
                                   dxfattribs={'layer': capa})
            else:
                _rect(msp, x0, y0, x0 + caj['largo'], y0 + caj['ancho'], capa)
            continue

        # Traza REAL del fondo, tal cual sale de la geometría del sólido: sirve
        # igual para un canto recto que para uno curvo (estas piezas llevan
        # cantos de radio 859-4590 mm con galce). Sin ella no se puede dibujar.
        pts = [tuple(q) for q in caj.get('puntos', [])]
        if len(pts) < 2:
            continue
        capa = ('CANALES' if caj['forma'] == 'ranura'
                else 'GALCES_OCULTO' if caj.get('abierto_a') == 'trasera' else 'GALCES')
        # Polilínea ABIERTA y dos conectores hasta la línea de corte: el lado
        # que da al canto no se cierra — ahí no hay material, es la boca. Los
        # conectores van al punto MÁS CERCANO del contorno, no en la dirección
        # de la normal, que en un canto curvo no es constante.
        msp.add_lwpolyline(pts, close=False, dxfattribs={'layer': capa})
        for extremo in (pts[0], pts[-1]):
            msp.add_line(extremo, _mas_cercano(extremo, contorno_ref), dxfattribs={'layer': capa})

    return doc
