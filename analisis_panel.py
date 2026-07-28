"""Análisis geométrico de paneles STEP: extrae largo, ancho, grosor y taladros
de piezas de tablero (madera/melamina) — paneles planos, no barras de perfil.

Reutiliza el algoritmo de envolvente de `analisis.py` (clustering de caras por
normal + emparejamiento de caras opuestas): el criterio "par lateral de MENOR
separación = cara frontal/trasera" ya identifica correctamente el grosor tanto
en una barra como en un panel — un panel 600×400×18 da longitud=600 (el par de
mayor separación, tratado como "extremos"), ancho_y=400 y grosor_z=18 sin
ningún cambio en `_analizar_envolvente`. Igual ocurre con la detección de
taladros (`_caras_cilindricas`/`_agrupar_cilindros`/`_fusionar_avellanados`/
`_asignar_taladros`): sirve igual para una cazoleta de bisagra que para un
taladro de perfil de aluminio.

Lo que SÍ cambia respecto a una barra de perfil:
  - No se aplica `_fusionar_pasantes_perfil_hueco` (específico de perfiles
    huecos de pared fina; un panel de tablero es macizo, no hueco).
  - Las caras 'extremo_a'/'extremo_b' (el par de MAYOR separación — en una
    barra son los dos cortes de los extremos) se renombran a 'lateral_iz'/
    'lateral_de' (en un panel son los dos cantos cortos laterales).
  - Se añaden avisos (nunca un bloqueo) cuando la proporción de dimensiones no
    parece la de un panel de tablero — p. ej. una pletina de aluminio larga y
    estrecha podría colarse por el mismo algoritmo; el aviso deja la decisión
    al usuario en vez de rechazar piezas límite legítimas.

Cajeados y ranuras (bolsillos rectangulares, no cilíndricos): se detectan en
las caras ANCHAS del panel (frontal/trasera/superior/inferior) como caras
PLANE recesadas de contorno no circular (un contorno circular ya lo cubre la
detección de taladros ciegos). El caso fiable es el BOLSILLO CERRADO (hueco
para bisagra de cazoleta con caja, cuerpo de cerradura, paso de cableado): su
fondo nunca toca directamente ninguna cara exterior — siempre hay una pared
de por medio — mientras que las PAREDES del propio bolsillo, si por casualidad
comparten normal con otro grupo (p. ej. una pared que mira en la dirección
'superior'), sí tocan una cara exterior con una arista compartida (donde la
pared se une a la superficie original). Ese contacto es la señal que distingue
fondo de pared, sin necesitar explorar la topología BREP completa.

Limitación conocida y DELIBERADA: una RANURA ABIERTA a un canto del panel (p.
ej. el rebaje para encajar la trasera, que llega hasta el borde) tiene su
propio fondo tocando la cara exterior de ese canto — con este mismo criterio
se descarta como si fuera una pared. Resultado: NO se detecta (no se inventa
con datos equivocados). Ver test_ranura_abierta_a_canto_no_se_detecta en
test_analisis_panel.py — es un fallo silencioso y seguro (el usuario la añade
a mano), no un dato falso que pueda acabar en un corte real equivocado.
"""
import math

from cadquery import Vector
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GeomAbs import GeomAbs_CurveType
from OCP.TopAbs import TopAbs_Orientation, TopAbs_IN
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.gp import gp_Pnt

from analisis import (
    _es_plana,
    _clusterizar_caras_planas,
    _emparejar_grupos_opuestos,
    _analizar_envolvente,
    _caras_cilindricas,
    _agrupar_cilindros,
    _fusionar_avellanados,
    _asignar_taladros,
    _plano_exterior,
    _dot,
    _cruz,
    _resta,
    _norma,
    _normalizar,
    _escala,
    _separacion_entre_planos,
    BARRIDO_MIN_TALADRO,
)

RENOMBRE_CARA = {'extremo_a': 'lateral_iz', 'extremo_b': 'lateral_de'}

# _detectar_cajeados solo explora las caras ANCHAS. Los cantos los lleva
# _detectar_canales_perimetro, que va por geometría y no por nombre de cara:
# ir por nombre dejaba sin mecanizar todos los paneles NO rectangulares (sus
# cantos no son ninguno de los cuatro de la caja envolvente, así que no tenían
# nombre y se saltaban enteros). En un mueble real eso es la mayoría de las
# piezas — 12 de 16 en el montaje 2876_6426, todas con canal perimetral.
GRUPOS_CAJEADO = ('frontal', 'trasera')
PROFUNDIDAD_MIN_CAJEADO = 0.5   # mm — por debajo es ruido numérico, no un cajeado real
AREA_MIN_CAJEADO = 20.0         # mm² — descarta fragmentos/virutas de geometría
TOL_ARISTA_COMPARTIDA = 0.1     # mm — distancia entre extremos para considerar la misma arista
UMBRAL_RANURA_ANCHO = 15.0      # mm — dimensión menor por debajo de esto se reporta como 'ranura'

# Detector geométrico de mecanizado de CANTO.
TOL_NORMAL_CANTO = 1e-3      # |n·Z| — por debajo, la cara es de canto (vertical)
COS_MISMO_CANTO = 0.999      # coseno entre normales para considerarlas el mismo canto
SOLAPE_MIN_CANTO = 1.0       # mm que deben solaparse dos caras para ser el mismo canto
PROFUNDIDAD_MIN_CANAL = 0.5  # mm — por debajo es ruido de geometría
TOL_TOCA_CARA = 0.05         # mm — margen para decidir si llega a una cara ancha
MUESTRAS_RAYO = 8            # puntos de sondeo entre la cara y el contorno


def _proyectar_en_polilinea(px, py, puntos):
    """Punto más próximo de una polilínea cerrada y su distancia."""
    mejor, dmin = (px, py), float('inf')
    for i in range(len(puntos)):
        ax, ay = puntos[i]
        bx, by = puntos[(i + 1) % len(puntos)]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        qx, qy = ax + t * dx, ay + t * dy
        d = math.hypot(px - qx, py - qy)
        if d < dmin:
            mejor, dmin = (qx, qy), d
    return mejor, dmin


def _dist_a_polilinea(px, py, puntos):
    """Distancia mínima de un punto a una polilínea cerrada."""
    return _proyectar_en_polilinea(px, py, puntos)[1]


def _salida_del_contorno(px, py, dx, dy, puntos):
    """Distancia desde (px,py) hasta que el rayo (dx,dy) cruza el contorno.
    None si no lo cruza."""
    mejor = None
    for i in range(len(puntos)):
        ax, ay = puntos[i]
        bx, by = puntos[(i + 1) % len(puntos)]
        ex, ey = bx - ax, by - ay
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = ((ax - px) * ey - (ay - py) * ex) / den      # a lo largo del rayo
        u = ((ax - px) * dy - (ay - py) * dx) / den      # a lo largo del segmento
        if t > 1e-6 and -1e-9 <= u <= 1 + 1e-9 and (mejor is None or t < mejor):
            mejor = t
    return mejor


def _detectar_canales_perimetro(solid, env, contorno):
    """Mecanizado hecho ENTRANDO por un canto, detectado por geometría pura.

    Un canto liso deja una superficie que llega al contorno de corte. Un canto
    mecanizado deja además superficies RETRANQUEADAS hacia dentro: son el fondo
    de lo que se ha arrancado, y lo que las delata es su distancia al contorno.

    Se distinguen dos casos, que en la máquina son cosas distintas:
      - 'ranura' (canal): no toca ninguna cara ancha — va enterrada en el
        grosor. Es el encaje de trasera/fondo.
      - 'cajeado' (galce/rebaje): sí toca una cara ancha — es un escalón.

    Dos decisiones que importan, ambas por piezas reales del taller (2876_6426):

    1. Se mide contra el CONTORNO, no por nombre de cara. Ir por nombre dejaba
       sin mecanizar todos los paneles no rectangulares: sus cantos no son
       ninguno de los cuatro de la caja envolvente.

    2. Se aceptan cantos CURVOS, no solo planos. Los cantos "en diagonal" de
       estas piezas son en realidad cilindros de radio grande (859..4590 mm), y
       llevan un galce de 35 mm hecho con dos cilindros coaxiales cuyos radios
       se diferencian en exactamente eso. Filtrando por caras planas se perdía
       entero.

    `cara` se aproxima al canto recto más parecido para no romper el contrato
    del lado TypeScript (que solo entiende las seis caras y, ante un nombre
    desconocido, cae a 'frontal' — lo que metería una canal de canto en el BPP
    como fresado de CARA y estropearía la pieza). La geometría de verdad va en
    `puntos`, y `oblicuo` avisa de que el nombre es una aproximación."""
    Z, X, Y = env['Z'], env['eje'], env['Y']
    z_min, x_min, y_min = env['z_min'], env['eje_min'], env['y_min']
    grosor = env['grosor_z']
    clasificador = BRepClass3d_SolidClassifier(solid.wrapped)

    resultado = []
    for f in solid.Faces():
        # Canto = superficie cuya generatriz va con el grosor. Vale para planos
        # y para cilindros de eje paralelo a Z (los cantos curvos).
        try:
            n = Vector(*f.normalAt().toTuple())
        except Exception:
            continue
        if abs(_dot(n, Z)) > TOL_NORMAL_CANTO:
            continue
        if f.Area() < AREA_MIN_CAJEADO:
            continue
        vs = [Vector(*v.toTuple()) for v in f.Vertices()]
        if not vs:
            continue

        zs = [_dot(v, Z) - z_min for v in vs]
        z0, z1 = min(zs), max(zs)
        if z1 - z0 <= 0:
            continue

        # Traza en planta: la superficie es vertical, así que su contorno
        # proyectado ES la curva retranqueada (ida y vuelta sobre sí misma).
        puntos = []
        for p in _discretizar_wire(f.outerWire()) or []:
            q = (round(_dot(p, X) - x_min, 2), round(_dot(p, Y) - y_min, 2))
            if not puntos or abs(q[0] - puntos[-1][0]) > 0.01 or abs(q[1] - puntos[-1][1]) > 0.01:
                puntos.append(q)
        if len(puntos) < 2:
            continue

        # Retranqueo = MEDIANA de la distancia de sus puntos al contorno. No
        # vale el centro de la cara: en un canto curvo el centro de masas cae
        # por dentro de la propia curva (23 mm en un radio de 4555), y daba por
        # mecanizada la banda que en realidad ES el contorno de corte.
        dists = sorted(_dist_a_polilinea(px, py, contorno) for px, py in puntos)
        profundidad = dists[len(dists) // 2]
        if profundidad < PROFUNDIDAD_MIN_CANAL:
            continue  # llega al contorno: es canto liso, no mecanizado

        # ¿Hay MATERIAL entre esta cara y la línea de corte? Si lo hay, no es un
        # mecanizado de canto: es la PARED de un bolsillo interior, que también
        # es una cara vertical retranqueada y por geometría se le parece.
        #
        # Se lanza un RAYO hacia fuera por la normal, hasta donde cruza el
        # contorno, y se mira si por el camino queda tablero. Una canal está
        # abierta al exterior: todo el trayecto es aire. Una pared de bolsillo
        # no: el rayo cruza el hueco y se mete en el tablero.
        #
        # Se probó a sondear "hacia el punto más cercano del contorno" y no
        # vale: cerca de una esquina, el punto más cercano cae en el canto de al
        # lado y el rayo atraviesa la pieza, tirando canales buenas (3 reales de
        # 2876_6426). Y sondear solo la boca tampoco: en el testero de una
        # ranura estrecha la normal apunta a lo LARGO de la ranura y el sondeo
        # se quedaba dentro de ella, dándola por buena.
        medio = puntos[len(puntos) // 2]
        dx, dy = _dot(n, X), _dot(n, Y)
        norma = math.hypot(dx, dy)
        if norma < 1e-9:
            continue
        dx, dy = dx / norma, dy / norma
        salida = _salida_del_contorno(medio[0], medio[1], dx, dy, contorno)
        if salida is None:
            salida = profundidad
        z_medio = (z0 + z1) / 2 + z_min
        hay_material = False
        for k in range(1, MUESTRAS_RAYO + 1):
            t = salida * k / (MUESTRAS_RAYO + 1)
            sonda = (_escala(X, medio[0] + dx * t + x_min)
                     + _escala(Y, medio[1] + dy * t + y_min)
                     + _escala(Z, z_medio))
            clasificador.Perform(gp_Pnt(sonda.x, sonda.y, sonda.z), 1e-6)
            if clasificador.State() == TopAbs_IN:
                hay_material = True
                break
        if hay_material:
            continue

        abierto = ('trasera' if z0 <= TOL_TOCA_CARA
                   else 'frontal' if z1 >= grosor - TOL_TOCA_CARA else None)
        nx, ny = _dot(n, X), _dot(n, Y)
        # La traza recorre la curva de ida y de vuelta: su longitud es el doble.
        largo = sum(math.hypot(puntos[i + 1][0] - puntos[i][0], puntos[i + 1][1] - puntos[i][1])
                    for i in range(len(puntos) - 1)) / 2.0

        resultado.append({
            'cara': _canto_mas_parecido(nx, ny),
            'forma': 'cajeado' if abierto else 'ranura',
            'en_canto': True,
            'curvo': f.geomType() != 'PLANE',
            'oblicuo': abs(abs(nx) - 1) > 1e-3 and abs(abs(ny) - 1) > 1e-3,
            'abierto_a': abierto,
            'x': round(min(p[0] for p in puntos), 2),
            'y': round(z0, 2),
            'largo': round(largo, 2),
            'ancho': round(z1 - z0, 2),
            'profundidad': round(profundidad, 2),
            'puntos': puntos,
            'normal': [round(nx, 4), round(ny, 4)],
        })
    return resultado


def _canto_mas_parecido(nx, ny):
    """Canto recto cuya normal se parece más a (nx, ny). Ver docstring de
    _detectar_canales_perimetro: hace falta un nombre VÁLIDO aunque el canto
    sea oblicuo."""
    if abs(nx) >= abs(ny):
        return 'lateral_de' if nx > 0 else 'lateral_iz'
    return 'superior' if ny > 0 else 'inferior'


def _es_circular(face):
    """Contorno circular = fondo de un taladro ciego (o bolsillo redondo,
    fuera del alcance de 'cajeado' — ya cubierto por la detección de taladros)."""
    try:
        edges = face.Edges()
        return len(edges) > 0 and all(e.geomType() == 'CIRCLE' for e in edges)
    except Exception:
        return False


def _segmentos_cara(face):
    """Extremos (p1, p2) de cada arista de la cara, en coordenadas globales."""
    segmentos = []
    try:
        for e in face.Edges():
            vs = e.Vertices()
            if len(vs) >= 2:
                segmentos.append((Vector(*vs[0].toTuple()), Vector(*vs[-1].toTuple())))
    except Exception:
        pass
    return segmentos


def _mismo_segmento(a, b, tol=TOL_ARISTA_COMPARTIDA):
    (p1, p2), (q1, q2) = a, b
    def cerca(u, v):
        return _norma(_resta(u, v)) < tol
    return (cerca(p1, q1) and cerca(p2, q2)) or (cerca(p1, q2) and cerca(p2, q1))


def _toca_alguna_cara_exterior(face, segmentos_exteriores):
    segs = _segmentos_cara(face)
    return any(_mismo_segmento(s, o) for s in segs for o in segmentos_exteriores)


def _detectar_cajeados(faces_planas, grupos, env, contorno=None):
    """Bolsillos/ranuras rectangulares (no cilíndricos) en cualquier cara del
    panel: las anchas (frontal/trasera) y TAMBIÉN los cuatro cantos — las
    "canales" de encaje de trasera/fondo son ranuras de canto y son de las
    más habituales en un mueble real (confirmado con STEP del taller: un mismo
    panel llevaba canal en los cuatro cantos).

    Devuelve dicts {cara, forma, en_canto, x, y, largo, ancho, profundidad}.
    x,y = esquina inferior-izquierda en el marco local de SU cara (misma
    convención que los taladros):
      - frontal/trasera : x a lo largo del largo, y desde el borde inferior.
      - superior/inferior: x a lo largo del largo, y desde la cara frontal
        (o sea, a través del GROSOR).
      - lateral_iz/de   : x desde el borde inferior (a lo ancho), y desde la
        cara frontal (a través del grosor).
    `en_canto` marca las tres últimas: NO son mecanizables como bolsillo de
    cara — quien consuma esto debe tratarlas aparte (ver dxf_panel.py y
    despieceDesdeStepPanel.ts)."""
    # Segmentos de TODAS las caras exteriores (de todos los grupos): una
    # pared de bolsillo comparte una arista con la superficie original de
    # ALGÚN grupo (no necesariamente el suyo propio, ver docstring del módulo).
    segmentos_exteriores = []
    for k, grupo in enumerate(grupos):
        plano = env['planos'][k]
        for idx in grupo['indices']:
            f = faces_planas[idx]
            centro = Vector(*f.Center().toTuple())
            if plano - _dot(centro, grupo['normal']) < PROFUNDIDAD_MIN_CAJEADO:
                segmentos_exteriores.extend(_segmentos_cara(f))

    resultado = []
    for k, grupo in enumerate(grupos):
        # env['nombres'] viene de _analizar_envolvente con la nomenclatura de
        # barra (extremo_a/b) — aquí ya se usa la de panel (lateral_iz/de).
        nombre = RENOMBRE_CARA.get(env['nombres'].get(k), env['nombres'].get(k))
        if nombre not in GRUPOS_CAJEADO:
            continue
        normal = grupo['normal']
        plano = env['planos'][k]
        for idx in grupo['indices']:
            f = faces_planas[idx]
            centro = Vector(*f.Center().toTuple())
            profundidad = plano - _dot(centro, normal)
            if profundidad < PROFUNDIDAD_MIN_CAJEADO:
                continue  # está en el plano exterior: no es un cajeado
            if _es_circular(f):
                continue  # fondo de taladro ciego redondo, ya cubierto aparte
            if f.Area() < AREA_MIN_CAJEADO:
                continue  # fragmento/ruido de geometría
            if _toca_alguna_cara_exterior(f, segmentos_exteriores):
                continue  # es la PARED de un bolsillo (o una ranura abierta a canto), no su fondo

            vs = [Vector(*v.toTuple()) for v in f.Vertices()]
            if not vs:
                continue
            # Un fondo que LLEGA a la línea de corte no es un bolsillo cerrado:
            # es el suelo de un mecanizado de canto (galce), y lo describe
            # _detectar_canales_perimetro con su forma real. Aquí saldría como
            # un rectángulo que no existe — en 2876_6426, un galce curvo de
            # 35 mm se reportaba como un bolsillo de 875x1127 en la trasera.
            if contorno and nombre in ('frontal', 'trasera'):
                cerca = min(_dist_a_polilinea(_dot(v, env['eje']) - env['eje_min'],
                                              _dot(v, env['Y']) - env['y_min'], contorno)
                            for v in vs)
                if cerca < PROFUNDIDAD_MIN_CAJEADO:
                    continue
            # Ejes locales de ESTA cara (ver docstring).
            if nombre in ('frontal', 'trasera'):
                dir_x, min_x = env['eje'], env['eje_min']
                dir_y, min_y = env['Y'], env['y_min']
            elif nombre in ('superior', 'inferior'):
                dir_x, min_x = env['eje'], env['eje_min']
                dir_y, min_y = env['Z'], env['z_min']
            else:  # lateral_iz / lateral_de
                dir_x, min_x = env['Y'], env['y_min']
                dir_y, min_y = env['Z'], env['z_min']
            xs = [_dot(v, dir_x) - min_x for v in vs]
            ys = [_dot(v, dir_y) - min_y for v in vs]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            largo, ancho = x1 - x0, y1 - y0
            if largo <= 0 or ancho <= 0:
                continue
            forma = 'ranura' if min(largo, ancho) < UMBRAL_RANURA_ANCHO else 'cajeado'
            resultado.append({
                'cara': nombre, 'forma': forma,
                'en_canto': nombre != 'frontal' and nombre != 'trasera',
                'x': round(x0, 2), 'y': round(y0, 2),
                'largo': round(largo, 2), 'ancho': round(ancho, 2),
                'profundidad': round(profundidad, 2),
            })
    return resultado

# grosor/ancho: calibrado contra 7 STEP reales de muebles de madera (jul 2026,
# ~1200 sólidos entre todos). Separa con hueco limpio dos categorías reales
# que aparecen MEZCLADAS en un mismo montaje:
#   - Paneles estrechos legítimos (listones/tapajuntas/puertas angostas):
#     grosor/ancho real observado 0.11-0.27 (ej. 19x100mm, 30x150mm).
#   - Piezas de barra/listón de sección casi cuadrada (travesaños, patas,
#     marcos): grosor/ancho real observado 0.38-0.80 (ej. 30x40mm, 19x32mm).
# 0.3 cae justo en el hueco entre ambos grupos — validado con datos reales,
# no solo sintéticos (ver test_analisis_panel.py).
UMBRAL_PANEL_GROSOR_ANCHO = 0.3

# NOTA — se probó y se descartó un segundo umbral por ancho/largo (para
# detectar "barras estrechas" tipo pletina de aluminio): en los mismos STEP
# reales, paneles de madera legítimos y estrechos (100mm de ancho × 2000-3000mm
# de largo — listones, tapajuntas, puertas altas y angostas) dieron ratios
# ancho/largo tan bajos como 0.033 — geométricamente INDISTINGUIBLES de una
# pletina fina de aluminio con la misma proporción. Cualquier umbral que no
# diera falsos positivos sobre esos paneles reales tampoco habría cazado una
# pletina real. Limitación aceptada: una pletina de aluminio subida por error
# a este analizador de paneles NO se detecta solo por proporción; el usuario
# la notará por el material/contexto, no por un aviso automático.


# Piezas reales del taller (jul 2026) confirmaron paneles con un borde
# angulado (no rectangular — un filler/remate en ángulo, p. ej. un mueble en
# esquina): 3 de 13 piezas válidas de un mismo montaje real traían 3.6°-11.1°.
# `largo`/`ancho` siguen siendo la caja envolvente (necesaria para el
# despiece/BPP, que solo maneja piezas rectangulares) — este aviso deja claro
# que la forma REAL no es un rectángulo, para que se revise a mano antes de
# cortar en vez de asumir silenciosamente un rectángulo que no es.
UMBRAL_ANGULO_RECTO = 1.0  # grados — por debajo es ruido numérico de un corte recto


def _advertencias_panel(largo, ancho, grosor, angulo_a=0.0, angulo_b=0.0):
    avisos = []
    if largo <= 0 or ancho <= 0:
        return avisos
    if grosor / ancho > UMBRAL_PANEL_GROSOR_ANCHO:
        avisos.append(
            f'Grosor {grosor:.1f}mm parece grande para un panel de {ancho:.1f}mm de ancho '
            '(¿es en realidad una barra/listón de sección maciza, no un panel de tablero?)'
        )
    if angulo_a > UMBRAL_ANGULO_RECTO or angulo_b > UMBRAL_ANGULO_RECTO:
        avisos.append(
            f'Borde no rectangular detectado (ángulos {angulo_a:.1f}°/{angulo_b:.1f}° en los cantos cortos) — '
            f'largo/ancho son la caja envolvente, NO la forma real de la pieza; revisar y ajustar a mano antes de cortar.'
        )
    return avisos


# ── Paneles con forma (contorno no rectangular) ─────────────────────────────
# Un mueble real trae paneles que NO son cajas de 6 caras: bordes inclinados
# (armario bajo techo abuhardillado → pentágonos), esquinas redondeadas o
# laterales curvos. Confirmado con STEP reales del taller (2876_6423: 13 de 26
# sólidos eran paneles de 30mm con estas formas, no herraje). Para ellos, el
# criterio de panel no es la caja completa sino el PAR DE GROSOR: dos caras
# planas paralelas, grandes y muy próximas (el canto de tablero, 5-80mm). El
# contorno real sale del wire exterior de la cara ancha, discretizado (líneas
# exactas por sus vértices; arcos/splines muestreados a 0.05mm de flecha).
# El BPP usa el panel envolvente rectangular; el DXF lleva la forma real.

GROSOR_MIN_PANEL_FORMA = 5.0    # mm — por debajo no es tablero (chapa/lámina)
GROSOR_MAX_PANEL_FORMA = 80.0   # mm — por encima no es tablero (bloque/macizo)
DEFLECT_CONTORNO = 0.05         # mm — flecha máxima al discretizar curvas
TOL_PUNTO_DUP = 1e-3            # mm — puntos consecutivos idénticos del wire


def _discretizar_wire(wire):
    """Puntos 3D ordenados del wire (cerrado, sin duplicar el último). Las
    aristas rectas aportan sus vértices exactos; las curvas se muestrean con
    flecha máxima DEFLECT_CONTORNO. Devuelve None si OCP falla."""
    try:
        puntos = []
        exp = BRepTools_WireExplorer(wire.wrapped)
        while exp.More():
            edge = exp.Current()
            adaptor = BRepAdaptor_Curve(edge)
            u1, u2 = adaptor.FirstParameter(), adaptor.LastParameter()
            if adaptor.GetType() == GeomAbs_CurveType.GeomAbs_Line:
                pts = [adaptor.Value(u1), adaptor.Value(u2)]
            else:
                algo = GCPnts_QuasiUniformDeflection(adaptor, DEFLECT_CONTORNO, u1, u2)
                if not algo.IsDone():
                    return None
                pts = [adaptor.Value(algo.Parameter(i)) for i in range(1, algo.NbPoints() + 1)]
            if edge.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
                pts = list(reversed(pts))
            for p in pts:
                v = Vector(p.X(), p.Y(), p.Z())
                if puntos and _norma(_resta(v, puntos[-1])) < TOL_PUNTO_DUP:
                    continue
                puntos.append(v)
            exp.Next()
        if len(puntos) >= 2 and _norma(_resta(puntos[0], puntos[-1])) < TOL_PUNTO_DUP:
            puntos.pop()
        return puntos if len(puntos) >= 3 else None
    except Exception:
        return None


def _direccion_eje_contorno(wire, puntos, Z):
    """Dirección X del marco local: entre las direcciones de las aristas
    rectas del contorno, la que da la envolvente de MENOR ÁREA (criterio de
    calibre giratorio — elegir la de máxima extensión escogía la diagonal en
    piezas con esquinas cortadas). Con el eje elegido, X siempre es el lado
    LARGO de la envolvente. Determinista (signo fijado como en analisis.py)."""
    candidatas = []
    try:
        exp = BRepTools_WireExplorer(wire.wrapped)
        while exp.More():
            adaptor = BRepAdaptor_Curve(exp.Current())
            if adaptor.GetType() == GeomAbs_CurveType.GeomAbs_Line:
                a = adaptor.Value(adaptor.FirstParameter())
                b = adaptor.Value(adaptor.LastParameter())
                d = _resta(Vector(b.X(), b.Y(), b.Z()), Vector(a.X(), a.Y(), a.Z()))
                if _norma(d) > TOL_PUNTO_DUP:
                    candidatas.append(_normalizar(d))
            exp.Next()
    except Exception:
        pass
    if not candidatas:
        # Contorno todo curvo: proyecciones de los ejes globales en el plano.
        for eje_global in (Vector(1, 0, 0), Vector(0, 1, 0), Vector(0, 0, 1)):
            d = _resta(eje_global, _escala(Z, _dot(eje_global, Z)))
            if _norma(d) > 0.1:
                candidatas.append(_normalizar(d))

    def extension(d):
        proys = [_dot(p, d) for p in puntos]
        return max(proys) - min(proys)

    def area_envolvente(d):
        return extension(d) * extension(_cruz(Z, d))

    mejor = min(candidatas, key=lambda d: (round(area_envolvente(d), 6), -extension(d)))
    # X = el lado largo de la envolvente elegida.
    perpendicular = _normalizar(_cruz(Z, mejor))
    if extension(perpendicular) > extension(mejor):
        mejor = perpendicular
    puntuacion = 4 * mejor.z + 2 * mejor.y + mejor.x
    return mejor if puntuacion >= 0 else _escala(mejor, -1)


def _simplificar_colineales(puntos_2d):
    """Elimina puntos intermedios de tramos rectos (|seno| < ~0.6°)."""
    if len(puntos_2d) < 4:
        return puntos_2d
    resultado = []
    n = len(puntos_2d)
    for i in range(n):
        x0, y0 = puntos_2d[i - 1]
        x1, y1 = puntos_2d[i]
        x2, y2 = puntos_2d[(i + 1) % n]
        ax, ay = x1 - x0, y1 - y0
        bx, by = x2 - x1, y2 - y1
        na = (ax * ax + ay * ay) ** 0.5
        nb = (bx * bx + by * by) ** 0.5
        if na < TOL_PUNTO_DUP or nb < TOL_PUNTO_DUP:
            continue
        if abs(ax * by - ay * bx) / (na * nb) > 0.01:
            resultado.append(puntos_2d[i])
    return resultado


def _es_contorno_rectangular(puntos_2d):
    """True si el contorno (ya en 2D) es un rectángulo: 4 vértices tras
    simplificar colineales y las 4 esquinas a ~90° (|cos| < 0.02)."""
    pts = _simplificar_colineales(puntos_2d)
    if len(pts) != 4:
        return False
    for i in range(4):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        ax, ay = x1 - x0, y1 - y0
        bx, by = x2 - x1, y2 - y1
        na = (ax * ax + ay * ay) ** 0.5
        nb = (bx * bx + by * by) ** 0.5
        if na == 0 or nb == 0 or abs(ax * bx + ay * by) / (na * nb) > 0.02:
            return False
    return True


def _analizar_panel_forma(solid, faces_planas, grupos):
    """Fallback para paneles NO rectangulares. Devuelve el mismo dict que el
    camino rectangular más 'contorno' (lista [x,y] en mm, cerrada, en el marco
    local de la cara ancha), o None si el sólido no tiene par de grosor claro.
    Los cajeados de las caras anchas SÍ se detectan (el filtro fondo-vs-pared
    recibe las caras exteriores de TODOS los grupos, aunque solo se busque en
    frontal/trasera)."""
    pares, _ = _emparejar_grupos_opuestos(grupos)
    if not pares:
        return None
    vertices = [Vector(*v.toTuple()) for v in solid.Vertices()]
    bb = solid.BoundingBox()
    dims = sorted([bb.xmax - bb.xmin, bb.ymax - bb.ymin, bb.zmax - bb.zmin], reverse=True)

    candidatos = []
    for gi, gj in pares:
        sep = _separacion_entre_planos(vertices, grupos, gi, gj)
        if not (GROSOR_MIN_PANEL_FORMA <= sep <= GROSOR_MAX_PANEL_FORMA):
            continue
        if sep > dims[1] * 0.5:
            continue  # el grosor debe ser mucho menor que las otras dos dimensiones
        area = sum(faces_planas[i].Area() for g in (gi, gj) for i in grupos[g]['indices'])
        candidatos.append({'par': (gi, gj), 'sep': sep, 'area': area})
    if not candidatos:
        return None
    elegido = max(candidatos, key=lambda c: c['area'])
    gi, gj = elegido['par']

    # Z = normal de la cara frontal, con signo determinista (como analisis.py).
    z0 = grupos[gi]['normal']
    puntuacion = 4 * z0.z + 2 * z0.y + z0.x
    Z = z0 if puntuacion >= 0 else _escala(z0, -1)
    g_frontal = gi if _dot(grupos[gi]['normal'], Z) > 0 else gj
    g_trasera = gj if g_frontal == gi else gi

    cara_frontal = max((faces_planas[i] for i in grupos[g_frontal]['indices']), key=lambda f: f.Area())
    contorno3d = _discretizar_wire(cara_frontal.outerWire())
    if contorno3d is None:
        return None

    X = _direccion_eje_contorno(cara_frontal.outerWire(), contorno3d, Z)
    X = _normalizar(_resta(X, _escala(Z, _dot(X, Z))))
    Y = _cruz(Z, X)

    xs = [_dot(p, X) for p in contorno3d]
    ys = [_dot(p, Y) for p in contorno3d]
    x_min, y_min = min(xs), min(ys)
    largo = round(max(xs) - x_min, 2)
    ancho = round(max(ys) - y_min, 2)
    grosor = round(elegido['sep'], 2)
    contorno = [[round(x - x_min, 2), round(y - y_min, 2)] for x, y in zip(xs, ys)]

    # Taladros: mismo pipeline, con un "env" reducido a las dos caras anchas —
    # los taladros de los cantos con forma salen con cara None (solo tabla).
    grupos_sub = [grupos[g_frontal], grupos[g_trasera]]
    z_min = min(_dot(v, Z) for v in vertices)
    env_sub = {
        'nombres': {0: 'frontal', 1: 'trasera'},
        'planos': {0: _plano_exterior(vertices, grupos_sub[0]['normal']),
                   1: _plano_exterior(vertices, grupos_sub[1]['normal'])},
        'eje': X, 'Y': Y, 'Z': Z,
        'eje_min': x_min, 'y_min': y_min, 'z_min': z_min,
        'ancho_y': ancho, 'grosor_z': grosor,
    }
    caras_cil = _caras_cilindricas(solid)
    cilindros = _fusionar_avellanados(_agrupar_cilindros(caras_cil))
    mayor_dim = max(ancho, grosor, 1.0)
    validos = [c for c in cilindros
               if c['barrido'] >= BARRIDO_MIN_TALADRO and c['radio'] * 2 <= mayor_dim * 1.5]
    taladros = _asignar_taladros(validos, grupos_sub, env_sub)

    # Cajeados en las caras anchas: env con TODOS los grupos (el filtro
    # fondo-vs-pared necesita las caras exteriores de todos ellos), pero solo
    # frontal/trasera llevan nombre de GRUPOS_CAJEADO — el resto ('lateral_k')
    # no se explora, solo aporta sus aristas exteriores al filtro.
    nombres_todos = {}
    planos_todos = {}
    for k, g in enumerate(grupos):
        if k == g_frontal:
            nombres_todos[k] = 'frontal'
        elif k == g_trasera:
            nombres_todos[k] = 'trasera'
        else:
            nombres_todos[k] = f'lateral_{k}'
        planos_todos[k] = _plano_exterior(vertices, g['normal'])
    env_cajeados = {**env_sub, 'nombres': nombres_todos, 'planos': planos_todos}
    cajeados = (_detectar_cajeados(faces_planas, grupos, env_cajeados, contorno)
                + _detectar_canales_perimetro(solid, env_sub, contorno))

    avisos = _advertencias_panel(largo, ancho, grosor) + [
        f'Panel con contorno NO rectangular ({len(contorno)} puntos) — el DXF lleva la forma real; '
        f'largo/ancho ({largo:.0f}x{ancho:.0f}) y el BPP son el panel envolvente.'
    ]
    return {
        'ok': True,
        'largo': largo,
        'ancho': ancho,
        'grosor': grosor,
        'seccion_regular': False,
        'contorno': contorno,
        'taladros': taladros,
        'taladros_descartados': len(cilindros) - len(validos),
        'cajeados': cajeados,
        'advertencias': avisos,
    }


def analizar_solido_panel(solid):
    """Analiza un sólido como panel de tablero. Devuelve:
      {'ok': True, 'largo', 'ancho', 'grosor', 'seccion_regular',
       'taladros', 'taladros_descartados', 'cajeados', 'advertencias'}
    (+ 'contorno' si el panel no es rectangular), o {'ok': False, 'motivo'}
    si el sólido no tiene forma de panel.
    """
    faces_planas = [f for f in solid.Faces() if _es_plana(f)]
    if len(faces_planas) < 4:
        return {'ok': False, 'motivo': f'{len(faces_planas)} caras planas (insuficiente para un panel)'}

    grupos = _clusterizar_caras_planas(faces_planas)

    if len(grupos) > 6:
        forma = _analizar_panel_forma(solid, faces_planas, grupos)
        if forma is not None:
            return forma
        return {'ok': False, 'motivo': f'{len(grupos)} direcciones de normal distintas (forma no soportada)'}

    env, motivo = _analizar_envolvente(solid, faces_planas, grupos)
    if env is None:
        forma = _analizar_panel_forma(solid, faces_planas, grupos)
        if forma is not None:
            return forma
        return {'ok': False, 'motivo': motivo}

    # ¿La cara ancha es DE VERDAD un rectángulo? La envolvente acepta formas
    # que no lo son — paralelogramos (ambos cortes inclinados paralelos) o
    # esquinas redondeadas (el eje sale ligeramente girado y largo/ancho dan
    # MAL: 616×422 medidos para una pieza real de 600×400). La comprobación es
    # directa sobre el wire exterior de la cara ancha exterior: 4 vértices
    # tras simplificar colineales y esquinas a 90°. Si no lo es, mejor el
    # contorno real; si la ruta de forma tampoco puede, se continúa con la
    # envolvente y sus avisos (comportamiento anterior).
    g_frontal = next((k for k, n in env['nombres'].items() if n == 'frontal'), None)
    es_rectangular = True
    if g_frontal is not None:
        normal_f = grupos[g_frontal]['normal']
        plano_f = env['planos'][g_frontal]
        exteriores = [faces_planas[i] for i in grupos[g_frontal]['indices']
                      if abs(_dot(Vector(*faces_planas[i].Center().toTuple()), normal_f) - plano_f) < 0.5]
        if exteriores:
            cara_ext = max(exteriores, key=lambda f: f.Area())
            pts3d = _discretizar_wire(cara_ext.outerWire())
            if pts3d is not None:
                ejes_2d = (env['eje'], env['Y'])
                pts2d = [(_dot(p, ejes_2d[0]), _dot(p, ejes_2d[1])) for p in pts3d]
                es_rectangular = _es_contorno_rectangular(pts2d)
    if not es_rectangular:
        forma = _analizar_panel_forma(solid, faces_planas, grupos)
        if forma is not None:
            return forma

    caras_cil = _caras_cilindricas(solid)
    cilindros = _fusionar_avellanados(_agrupar_cilindros(caras_cil))

    mayor_dim = max(env['ancho_y'], env['grosor_z'], 1.0)
    validos = [c for c in cilindros
               if c['barrido'] >= BARRIDO_MIN_TALADRO and c['radio'] * 2 <= mayor_dim * 1.5]
    descartados = len(cilindros) - len(validos)

    taladros = _asignar_taladros(validos, grupos, env)
    for t in taladros:
        if t['cara'] in RENOMBRE_CARA:
            t['cara'] = RENOMBRE_CARA[t['cara']]

    # En la ruta rectangular no hay 'contorno': el de referencia es la propia
    # caja envolvente, que es exactamente la línea de corte de la pieza.
    rect = [[0.0, 0.0], [env['longitud'], 0.0],
            [env['longitud'], env['ancho_y']], [0.0, env['ancho_y']]]
    cajeados = (_detectar_cajeados(faces_planas, grupos, env, rect)
                + _detectar_canales_perimetro(solid, env, rect))

    largo = round(env['longitud'], 2)
    ancho = round(env['ancho_y'], 2)
    grosor = round(env['grosor_z'], 2)

    return {
        'ok': True,
        'largo': largo,
        'ancho': ancho,
        'grosor': grosor,
        'seccion_regular': env['seccion_regular'],
        'taladros': taladros,
        'taladros_descartados': descartados,
        'cajeados': cajeados,
        'advertencias': _advertencias_panel(largo, ancho, grosor, env['angulo_corte_a'], env['angulo_corte_b']),
    }
