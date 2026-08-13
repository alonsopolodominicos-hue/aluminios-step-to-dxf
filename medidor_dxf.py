"""Mide piezas 2D dentro de un DXF: cuánto miden de largo y de ancho.

El problema: en un DXF de nesting o de taller una pieza no viene como "una
pieza". Viene como un montón de líneas y arcos sueltos, en cualquier orden, y
además girada en un ángulo cualquiera para aprovechar el tablero. Medir la
caja envolvente tal cual (mín/máx de x e y) daría una medida MAYOR que la
pieza en cuanto esté girada un solo grado.

Cómo se resuelve, en cuatro pasos:

  1. EXTRAER: leer las entidades y convertir todo (arcos, círculos, splines,
     polilíneas) a segmentos rectos. A partir de aquí solo hay puntos.
  2. RECONSTRUIR: unir los segmentos cuyos extremos se tocan, hasta cerrar
     bucles. Eso devuelve los polígonos que de verdad hay dibujados.
  3. JERARQUÍA: un polígono contenido dentro de otro es un agujero o un
     mecanizado, no una pieza. Se descarta como pieza y se cuenta aparte.
  4. CALIBRE ROTATORIO: sobre la envolvente convexa de cada contorno, se
     prueba a apoyar la pieza sobre cada uno de sus lados y se mide la caja
     resultante. La de MENOR ÁREA es la caja real de la pieza. Este es el
     truco que hace que la medida no dependa del giro con el que esté puesta
     en el dibujo.

Todo con matemáticas de Python puro: no hace falta shapely ni scipy, que
engordarían el contenedor sin aportar nada aquí.
"""
from __future__ import annotations

import math

import ezdxf

# Dos extremos más cerca que esto se consideran el mismo punto al unir
# segmentos. Los DXF reales no cierran perfecto: un CAD que exporta arcos
# deja huecos de centésimas entre el final de uno y el principio del otro.
TOL_UNION = 0.1
# Paso al convertir una curva en segmentos rectos. 0,1 mm es más fino que
# cualquier tolerancia de taller y no dispara el número de puntos.
TOL_CURVA = 0.1
# Por debajo de esto un "polígono" es ruido de dibujo, no una pieza.
AREA_MIN_PIEZA = 1.0        # mm²
PERIMETRO_MIN_PIEZA = 4.0   # mm


# ── Fase 1: extracción ───────────────────────────────────────────────────────

def _segmentos_de_arco(cx, cy, radio, ang_ini, ang_fin):
    """Un arco → lista de puntos. Los ángulos vienen en grados, como en DXF.

    El número de tramos sale de la flecha máxima admitida (TOL_CURVA): la
    separación entre la cuerda y el arco. Así una curva de radio grande se
    trocea poco y una de radio pequeño, mucho — que es lo que hace falta.
    """
    barrido = math.radians((ang_fin - ang_ini) % 360.0 or 360.0)
    if radio <= 0 or barrido <= 0:
        return []
    # flecha = R (1 − cos(Δ/2)) → despejando el paso angular para flecha = TOL
    try:
        paso = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - TOL_CURVA / radio)))
    except ValueError:
        paso = barrido
    tramos = max(4, int(math.ceil(barrido / paso)) if paso > 0 else 4)
    puntos = []
    for i in range(tramos + 1):
        a = math.radians(ang_ini) + barrido * (i / tramos)
        puntos.append((cx + radio * math.cos(a), cy + radio * math.sin(a)))
    return puntos


def _puntos_a_segmentos(puntos, cerrado=False):
    """Cadena de puntos → lista de segmentos [(p0, p1), ...]."""
    segmentos = []
    for i in range(len(puntos) - 1):
        if math.dist(puntos[i], puntos[i + 1]) > 1e-9:
            segmentos.append((puntos[i], puntos[i + 1]))
    if cerrado and len(puntos) > 2 and math.dist(puntos[-1], puntos[0]) > 1e-9:
        segmentos.append((puntos[-1], puntos[0]))
    return segmentos


def extraer_segmentos(msp):
    """Todas las entidades dibujadas → segmentos rectos.

    Lo que no se sepa leer se ignora en silencio: más vale medir la pieza con
    lo que se entiende que tumbar el proceso entero por una entidad rara.
    """
    segmentos = []
    for e in msp:
        try:
            tipo = e.dxftype()
            if tipo == 'LINE':
                segmentos += _puntos_a_segmentos(
                    [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)])
            elif tipo == 'ARC':
                c = e.dxf.center
                segmentos += _puntos_a_segmentos(_segmentos_de_arco(
                    c.x, c.y, e.dxf.radius, e.dxf.start_angle, e.dxf.end_angle))
            elif tipo == 'CIRCLE':
                c = e.dxf.center
                segmentos += _puntos_a_segmentos(
                    _segmentos_de_arco(c.x, c.y, e.dxf.radius, 0.0, 360.0), cerrado=True)
            elif tipo == 'LWPOLYLINE':
                pts = [(p[0], p[1]) for p in e.get_points('xy')]
                segmentos += _puntos_a_segmentos(pts, cerrado=bool(e.closed))
            elif tipo == 'POLYLINE':
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                segmentos += _puntos_a_segmentos(pts, cerrado=bool(e.is_closed))
            elif tipo == 'SPLINE':
                # flattening() da la spline ya troceada a la tolerancia pedida.
                pts = [(p[0], p[1]) for p in e.flattening(TOL_CURVA)]
                segmentos += _puntos_a_segmentos(pts)
            elif tipo == 'ELLIPSE':
                pts = [(p[0], p[1]) for p in e.flattening(TOL_CURVA)]
                segmentos += _puntos_a_segmentos(pts)
        except Exception:
            continue   # entidad ilegible: se ignora, no tumba la medición
    return segmentos


# ── Fase 2: reconstrucción topológica ────────────────────────────────────────

def _clave(p, tol=TOL_UNION):
    """Rejilla para agrupar puntos casi iguales sin comparar todos con todos."""
    return (round(p[0] / tol), round(p[1] / tol))


def reconstruir_bucles(segmentos, tol=TOL_UNION):
    """Une los segmentos por sus extremos hasta cerrar bucles.

    Se construye un mapa "punto → segmentos que salen de ahí" con una rejilla
    de tolerancia, y luego se va tirando del hilo: desde un segmento, se busca
    otro que empiece donde este acaba, y así hasta volver al principio.

    Los caminos que no cierran (una pieza mal dibujada, una cota suelta) se
    descartan: no son piezas y medirlos daría un número inventado.
    """
    if not segmentos:
        return []

    vecinos: dict = {}
    for idx, (a, b) in enumerate(segmentos):
        # Se indexa cada extremo en su casilla y en las ocho de alrededor: dos
        # puntos que se tocan pueden caer en casillas distintas si el borde de
        # la rejilla pasa justo entre ellos, y entonces no se encontrarían.
        for punto, invertido in ((a, False), (b, True)):
            cx, cy = _clave(punto, tol)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    vecinos.setdefault((cx + dx, cy + dy), []).append((idx, invertido))

    usados = [False] * len(segmentos)
    bucles = []

    for inicio in range(len(segmentos)):
        if usados[inicio]:
            continue
        usados[inicio] = True
        a, b = segmentos[inicio]
        camino = [a, b]
        actual = b
        cerrado = False

        # Tirar del hilo hasta cerrar o quedarse sin continuación. El tope de
        # vueltas evita quedarse colgado con geometría degenerada.
        for _ in range(len(segmentos) + 1):
            if math.dist(actual, camino[0]) <= tol and len(camino) > 2:
                cerrado = True
                break
            siguiente = None
            for idx, invertido in vecinos.get(_clave(actual, tol), []):
                if usados[idx]:
                    continue
                p0, p1 = segmentos[idx]
                origen, destino = (p1, p0) if invertido else (p0, p1)
                if math.dist(origen, actual) <= tol:
                    siguiente = (idx, destino)
                    break
            if siguiente is None:
                break
            idx, destino = siguiente
            usados[idx] = True
            camino.append(destino)
            actual = destino

        if cerrado and len(camino) >= 4:
            bucles.append(camino)

    return bucles


# ── Fase 3: jerarquía (qué es pieza y qué es agujero) ────────────────────────

def area_con_signo(poligono):
    """Área por la fórmula del cordón de zapato. El SIGNO da el sentido de
    giro; para medir solo interesa el valor absoluto."""
    s = 0.0
    for i in range(len(poligono)):
        x0, y0 = poligono[i]
        x1, y1 = poligono[(i + 1) % len(poligono)]
        s += x0 * y1 - x1 * y0
    return s / 2.0


def punto_dentro(punto, poligono):
    """¿El punto está dentro del polígono? Lanzamiento de rayo: se cuenta
    cuántas veces un rayo horizontal cruza el contorno. Impar = dentro."""
    x, y = punto
    dentro = False
    n = len(poligono)
    for i in range(n):
        x0, y0 = poligono[i]
        x1, y1 = poligono[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            if y1 != y0:
                corte = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                if corte > x:
                    dentro = not dentro
    return dentro


def separar_piezas_y_agujeros(bucles):
    """Devuelve (contornos_exteriores, agujeros).

    Un bucle contenido dentro de otro es un agujero o un mecanizado, no una
    pieza. Se comprueba metiendo un vértice suyo en el test de punto dentro
    del otro; se mira contra el de MAYOR área para no confundirse cuando hay
    agujeros dentro de agujeros.
    """
    conArea = [(abs(area_con_signo(b)), b) for b in bucles]
    conArea = [(a, b) for a, b in conArea if a >= AREA_MIN_PIEZA]
    conArea.sort(key=lambda x: -x[0])

    exteriores, agujeros = [], []
    for area, bucle in conArea:
        contenido = False
        for area_mayor, mayor in exteriores:
            if area < area_mayor and punto_dentro(bucle[0], mayor):
                contenido = True
                break
        if contenido:
            agujeros.append(bucle)
        else:
            exteriores.append((area, bucle))
    return [b for _, b in exteriores], agujeros


# ── Fase 4: caja envolvente mínima (calibre rotatorio) ───────────────────────

def envolvente_convexa(puntos):
    """Envolvente convexa por cadena monótona de Andrew.

    Ordena los puntos y construye la mitad de abajo y la de arriba dejando
    solo los giros a la izquierda. Sale la envolvente en sentido antihorario.
    """
    pts = sorted(set((round(x, 6), round(y, 6)) for x, y in puntos))
    if len(pts) <= 2:
        return pts

    def cruz(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    abajo = []
    for p in pts:
        while len(abajo) >= 2 and cruz(abajo[-2], abajo[-1], p) <= 0:
            abajo.pop()
        abajo.append(p)
    arriba = []
    for p in reversed(pts):
        while len(arriba) >= 2 and cruz(arriba[-2], arriba[-1], p) <= 0:
            arriba.pop()
        arriba.append(p)
    return abajo[:-1] + arriba[:-1]


def caja_recta(puntos):
    """Caja alineada con los ejes del dibujo: mín/máx de x e y.

    Es la medida "como está puesta en el plano". Se da junto a la mínima
    porque no siempre coinciden: en una pieza con un lado en diagonal, el
    calibre rotatorio encuentra una caja MÁS PEQUEÑA girada, que es la buena
    para aprovechar tablero, pero el plano se suele cotar por la recta.
    """
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    return round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)


def caja_minima(puntos):
    """Caja envolvente de ÁREA MÍNIMA (calibre rotatorio).

    Teorema en el que se apoya: la caja de área mínima de un conjunto de
    puntos tiene SIEMPRE uno de sus lados apoyado en un lado de la envolvente
    convexa. Así que no hay que probar infinitos ángulos: basta con probar
    tantos como lados tenga la envolvente.

    Para cada lado se gira el conjunto hasta ponerlo horizontal, se mide la
    caja recta (que ahí sí es trivial: mín/máx de x e y) y se guarda la de
    menor área. Devuelve (largo, ancho, ángulo en grados), con el largo
    siempre como la dimensión mayor.
    """
    envolvente = envolvente_convexa(puntos)
    if len(envolvente) < 3:
        xs = [p[0] for p in puntos]
        ys = [p[1] for p in puntos]
        lado_a, lado_b = max(xs) - min(xs), max(ys) - min(ys)
        return max(lado_a, lado_b), min(lado_a, lado_b), 0.0

    mejor = None
    n = len(envolvente)
    for i in range(n):
        x0, y0 = envolvente[i]
        x1, y1 = envolvente[(i + 1) % n]
        borde = math.hypot(x1 - x0, y1 - y0)
        if borde < 1e-9:
            continue
        # Vector unitario del lado: girar el conjunto es proyectarlo sobre
        # este vector y sobre su perpendicular.
        ux, uy = (x1 - x0) / borde, (y1 - y0) / borde
        proy_u = [p[0] * ux + p[1] * uy for p in envolvente]
        proy_v = [-p[0] * uy + p[1] * ux for p in envolvente]
        ancho_u = max(proy_u) - min(proy_u)
        ancho_v = max(proy_v) - min(proy_v)
        area = ancho_u * ancho_v
        if mejor is None or area < mejor[0]:
            mejor = (area, ancho_u, ancho_v, math.degrees(math.atan2(uy, ux)))

    _, a, b, angulo = mejor
    return max(a, b), min(a, b), angulo


# ── Entrada principal ────────────────────────────────────────────────────────

def medir_dxf(ruta_o_stream):
    """Mide todas las piezas de un DXF.

    Devuelve {'piezas': [{'id', 'largo', 'ancho', 'angulo', 'area',
    'agujeros'}], 'avisos': [...]}. Las medidas son las de la caja mínima, o
    sea las reales de la pieza aunque esté girada en el dibujo.
    """
    avisos = []
    try:
        doc = ezdxf.readfile(ruta_o_stream)
    except Exception as e:
        return {'piezas': [], 'avisos': [f'No se pudo leer el DXF: {e}']}

    segmentos = extraer_segmentos(doc.modelspace())
    if not segmentos:
        return {'piezas': [], 'avisos': ['El DXF no tiene geometría que medir.']}

    # Se intenta cerrar con tolerancia creciente: un DXF de CAD real deja
    # huecos de centésimas entre arcos, y otro exportado a lo bruto puede
    # dejar décimas. Se empieza fino para no pegar cosas que no van juntas.
    bucles = []
    for tol in (TOL_UNION, 0.5, 1.0):
        bucles = reconstruir_bucles(segmentos, tol)
        if bucles:
            if tol > TOL_UNION:
                avisos.append(f'Los contornos no cerraban: se han unido con {tol} mm '
                              f'de tolerancia en vez de {TOL_UNION}.')
            break

    exteriores, agujeros = separar_piezas_y_agujeros(bucles) if bucles else ([], [])

    if not exteriores:
        # Nada cierra: es un dibujo de trazos sueltos (recorridos de
        # mecanizado, cotas). Medir igualmente lo que hay es MUCHO más útil
        # que no dar medida, y el calibre rotatorio sigue dando la medida
        # real aunque la pieza esté girada. Se avisa de que es una medida
        # sobre todo lo dibujado, no sobre un contorno reconocido.
        puntos = [p for seg in segmentos for p in seg]
        largo, ancho, angulo = caja_minima(puntos)
        recto_x, recto_y = caja_recta(puntos)
        avisos.append('Ningún contorno cierra: la medida abarca TODO lo dibujado '
                      '(incluidos trazos de mecanizado que sobresalgan de la pieza).')
        return {'piezas': [{
            'id': 1, 'largo': round(largo, 2), 'ancho': round(ancho, 2),
            'largo_recto': max(recto_x, recto_y), 'ancho_recto': min(recto_x, recto_y),
            'angulo': round(angulo % 180.0, 2),
            'area': None, 'agujeros': 0, 'contorno_cerrado': False,
        }], 'avisos': avisos}

    piezas = []
    for i, contorno in enumerate(exteriores, start=1):
        perimetro = sum(math.dist(contorno[j], contorno[(j + 1) % len(contorno)])
                        for j in range(len(contorno)))
        if perimetro < PERIMETRO_MIN_PIEZA:
            continue
        largo, ancho, angulo = caja_minima(contorno)
        recto_x, recto_y = caja_recta(contorno)
        dentro = sum(1 for a in agujeros if punto_dentro(a[0], contorno))
        piezas.append({
            'id': i,
            'largo': round(largo, 2),
            'ancho': round(ancho, 2),
            # Medida tal como está puesta en el plano (sin girar).
            'largo_recto': max(recto_x, recto_y),
            'ancho_recto': min(recto_x, recto_y),
            # Giro con el que está puesta en el dibujo (informativo).
            'angulo': round(angulo % 180.0, 2),
            'area': round(abs(area_con_signo(contorno)), 2),
            'agujeros': dentro,
            'contorno_cerrado': True,
        })

    if agujeros:
        avisos.append(f'{len(agujeros)} contorno(s) interior(es) tratados como '
                      f'agujeros o mecanizados, no como piezas.')
    return {'piezas': piezas, 'avisos': avisos}
