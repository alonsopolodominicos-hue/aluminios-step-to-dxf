"""Análisis geométrico de sólidos STEP: extrae longitud, ángulos de corte,
sección y taladros de piezas de perfil (cajas de 6 direcciones de cara,
posiblemente fragmentadas por agujeros, con extremos rectos o a inglete).

v3 — correcciones de correctitud validadas con tests sintéticos (ver
test_analisis.py, geometrías generadas con cadquery con ground truth
conocido):
  - Marco local CONSISTENTE por pieza (X=eje, Y=ancho, Z=grosor) con caras
    semánticas (frontal/trasera/superior/inferior/extremo_a/extremo_b) —
    antes la `y` de caras opuestas se medía desde bordes contrarios.
  - Taladros deduplicados por línea de eje + SOLAPE de rango axial — antes
    dos taladros axiales del mismo Ø en extremos opuestos se fusionaban.
  - Cara del taladro por proximidad de la BOCA al plano de la cara — antes
    abs(dot) de normales no distinguía entre caras opuestas.
  - Profundidad y pasante/ciego calculados del rango axial del cilindro.
  - Filtro de no-taladros por barrido angular (~360°) — antes los fillets
    y fresados parciales generaban taladros falsos.
  - Avellanados (cilindros concéntricos solapados de Ø distinto) → un solo
    taladro con su avellanado anotado.
  - Longitud SIEMPRE medida a lo largo del eje de la barra — antes las
    barras paralelogramo (ambos ingletes paralelos) salían mal.

Sólidos que no son una caja (con o sin taladros) — herraje: tornillos,
bisagras, piezas con sección real demasiado compleja — se reportan como no
soportados, no se fuerzan.
"""
import math

from cadquery import Vector
from OCP.BRepAdaptor import BRepAdaptor_Surface

UMBRAL_PARALELO = -0.99        # normales opuestas (caras enfrentadas de la caja)
UMBRAL_MISMA_DIRECCION = 0.99  # normales casi idénticas (misma cara física, fragmentada)
UMBRAL_EJE_TALADRO = 0.95      # eje del taladro ~paralelo a la normal de la cara que lo aloja
UMBRAL_CARA_SEMANTICA = 0.7    # dot mínimo para nombrar una cara frontal/superior/etc.
TOL_MISMO_EJE = 0.05           # mm — distancia perpendicular máxima entre ejes coincidentes
TOL_RADIO = 0.05               # mm — diferencia de radio para ser el mismo cilindro
TOL_SOLAPE_AXIAL = 0.1         # mm — solape mínimo de rangos para fusionar caras del mismo taladro
TOL_ADYACENTE_AVELLANADO = 0.5 # mm — hueco máximo entre piloto y caja de avellanado
TOL_PASANTE = 0.6              # mm — distancia boca↔plano de cara para "toca la cara"
BARRIDO_MIN_TALADRO = 5.2      # rad (~300°) — barrido angular mínimo para ser un taladro


def _es_plana(face):
    try:
        return face.geomType() == 'PLANE'
    except Exception:
        return False


def _normal_unitaria(face):
    n = face.normalAt()
    largo = math.sqrt(n.x ** 2 + n.y ** 2 + n.z ** 2)
    return Vector(n.x / largo, n.y / largo, n.z / largo)


def _dot(a, b):
    return a.x * b.x + a.y * b.y + a.z * b.z


def _cruz(a, b):
    return Vector(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)


def _resta(a, b):
    return Vector(a.x - b.x, a.y - b.y, a.z - b.z)


def _suma(a, b):
    return Vector(a.x + b.x, a.y + b.y, a.z + b.z)


def _escala(a, s):
    return Vector(a.x * s, a.y * s, a.z * s)


def _norma(v):
    return math.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2)


def _normalizar(v):
    largo = _norma(v)
    return Vector(v.x / largo, v.y / largo, v.z / largo)


# ── Envolvente por clustering de normales ────────────────────────────────────
# Se agrupan las caras PLANE por dirección de normal: una cara física puede
# venir partida en varios fragmentos — por un taladro que la atraviesa, o por
# cómo exportó el CAD la geometría — y todos esos fragmentos comparten normal.

def _clusterizar_caras_planas(faces_planas):
    grupos = []  # [{'normal': Vector, 'indices': [...]}]
    for i, f in enumerate(faces_planas):
        n = _normal_unitaria(f)
        grupo = next((g for g in grupos if _dot(n, g['normal']) > UMBRAL_MISMA_DIRECCION), None)
        if grupo:
            grupo['indices'].append(i)
        else:
            grupos.append({'normal': n, 'indices': [i]})
    return grupos


def _emparejar_grupos_opuestos(grupos):
    n = len(grupos)
    candidatos = []
    for i in range(n):
        for j in range(i + 1, n):
            d = _dot(grupos[i]['normal'], grupos[j]['normal'])
            if d < UMBRAL_PARALELO:
                candidatos.append((d, i, j))
    candidatos.sort(key=lambda t: t[0])
    usados = set()
    pares = []
    for d, i, j in candidatos:
        if i in usados or j in usados:
            continue
        pares.append((i, j))
        usados.add(i)
        usados.add(j)
    sin_emparejar = [i for i in range(n) if i not in usados]
    return pares, sin_emparejar


def _extension_en_direccion(vertices, direccion):
    proys = [_dot(v, direccion) for v in vertices]
    return min(proys), max(proys)


def _plano_exterior(vertices, normal):
    """Cota del plano exterior del envolvente en la dirección `normal`: la
    proyección máxima de los vértices del sólido. Robusto frente a caras
    interiores con la misma normal (el FONDO plano de un taladro ciego se
    clusteriza con la cara exterior y desplazaría un centro promedio)."""
    return max(_dot(v, normal) for v in vertices)


def _separacion_entre_planos(vertices, grupos, gi, gj):
    # Con normales opuestas n y ~-n: max·n + max·(-n) = extensión total.
    return _plano_exterior(vertices, grupos[gi]['normal']) + _plano_exterior(vertices, grupos[gj]['normal'])


def _centro_grupo_exterior(faces_planas, grupo, vertices):
    """Centro promedio de SOLO las caras del grupo que están en el plano
    exterior (excluye fondos de taladros ciegos con la misma normal)."""
    normal = grupo['normal']
    plano = _plano_exterior(vertices, normal)
    exteriores = [i for i in grupo['indices']
                  if abs(_dot(Vector(*faces_planas[i].Center().toTuple()), normal) - plano) < 0.5]
    indices = exteriores or grupo['indices']
    cx = sum(faces_planas[i].Center().x for i in indices) / len(indices)
    cy = sum(faces_planas[i].Center().y for i in indices) / len(indices)
    cz = sum(faces_planas[i].Center().z for i in indices) / len(indices)
    return Vector(cx, cy, cz)


def _analizar_envolvente(solid, faces_planas, grupos):
    pares, sin_emparejar = _emparejar_grupos_opuestos(grupos)
    # Una caja necesita 3 ejes: o los 3 emparejados (caja completa), o 2
    # emparejados + 2 sueltos (los extremos, no paralelos entre sí por un
    # corte recto+inglete o ingletes distintos).
    if not ((len(pares) == 3 and len(sin_emparejar) == 0) or (len(pares) == 2 and len(sin_emparejar) == 2)):
        return None, f'{len(pares)} pares y {len(sin_emparejar)} caras sueltas (no define una caja completa)'

    vertices = [Vector(*v.toTuple()) for v in solid.Vertices()]

    # Candidato a "par de extremos": el que da la mayor extensión de la pieza.
    candidatos = []
    for i, j in pares:
        vmin, vmax = _extension_en_direccion(vertices, grupos[i]['normal'])
        candidatos.append({'grupos': (i, j), 'extension': vmax - vmin})
    if len(sin_emparejar) == 2:
        i, j = sin_emparejar
        ca = _centro_grupo_exterior(faces_planas, grupos[i], vertices)
        cb = _centro_grupo_exterior(faces_planas, grupos[j], vertices)
        direccion = _normalizar(_resta(cb, ca))
        vmin, vmax = _extension_en_direccion(vertices, direccion)
        candidatos.append({'grupos': (i, j), 'extension': vmax - vmin})

    elegido = max(candidatos, key=lambda c: c['extension'])
    gi_ext, gj_ext = elegido['grupos']

    # Eje de la barra: dirección centro-a-centro entre los dos extremos. La
    # LONGITUD se mide SIEMPRE como extensión de vértices sobre este eje —
    # para barras paralelogramo (ingletes paralelos) la extensión sobre la
    # normal del corte daría un valor equivocado.
    ca = _centro_grupo_exterior(faces_planas, grupos[gi_ext], vertices)
    cb = _centro_grupo_exterior(faces_planas, grupos[gj_ext], vertices)
    eje = _normalizar(_resta(cb, ca))
    eje_min, eje_max = _extension_en_direccion(vertices, eje)
    longitud = eje_max - eje_min

    # extremo_a = el de menor proyección sobre el eje (el "izquierdo").
    if _dot(ca, eje) <= _dot(cb, eje):
        g_ext_a, g_ext_b = gi_ext, gj_ext
    else:
        g_ext_a, g_ext_b = gj_ext, gi_ext

    def angulo_corte(g):
        d = max(-1.0, min(1.0, abs(_dot(grupos[g]['normal'], eje))))
        return math.degrees(math.acos(d))

    # Laterales: el resto de grupos. La sección sale de sus pares.
    laterales = [k for k in range(len(grupos)) if k not in (g_ext_a, g_ext_b)]
    pares_laterales = [p for p in pares if p != elegido['grupos'] and p != (gj_ext, gi_ext)]
    sueltas_laterales = [k for k in laterales if not any(k in p for p in pares_laterales)]

    seccion_medidas = []
    for gi, gj in pares_laterales:
        seccion_medidas.append({'par': (gi, gj), 'sep': _separacion_entre_planos(vertices, grupos, gi, gj)})
    seccion_regular = len(sueltas_laterales) == 0
    if not seccion_regular and len(sueltas_laterales) == 2:
        i, j = sueltas_laterales
        ca2 = _centro_grupo_exterior(faces_planas, grupos[i], vertices)
        cb2 = _centro_grupo_exterior(faces_planas, grupos[j], vertices)
        seccion_medidas.append({'par': (i, j), 'sep': _norma(_resta(cb2, ca2))})

    seccion = sorted(m['sep'] for m in seccion_medidas)

    # ── Marco local consistente ────────────────────────────────────────────
    # X = eje (extremo_a → extremo_b). Z = normal de la cara "frontal": la del
    # par lateral de MENOR separación (las dos caras grandes de la barra están
    # separadas por el grosor, la dimensión pequeña). Signo determinista para
    # que piezas iguales den siempre el mismo marco. Y = Z × X (mano derecha).
    if pares_laterales:
        par_frontal = min(seccion_medidas[:len(pares_laterales)], key=lambda m: m['sep'])['par']
        z0 = grupos[par_frontal[0]]['normal']
    else:
        z0 = grupos[laterales[0]]['normal']
    # Ortogonalizar contra X (numéricamente ya casi lo es) y fijar signo.
    z0 = _resta(z0, _escala(eje, _dot(z0, eje)))
    z0 = _normalizar(z0)
    puntuacion = 4 * z0.z + 2 * z0.y + z0.x
    Z = z0 if puntuacion >= 0 else _escala(z0, -1)
    Y = _cruz(Z, eje)  # X × Y = Z con X=eje

    # Nombres semánticos de las caras laterales según su normal en el marco.
    nombres = {}
    nombres[g_ext_a] = 'extremo_a'
    nombres[g_ext_b] = 'extremo_b'
    for k in laterales:
        n = grupos[k]['normal']
        if _dot(n, Z) > UMBRAL_CARA_SEMANTICA:
            nombres[k] = 'frontal'
        elif _dot(n, Z) < -UMBRAL_CARA_SEMANTICA:
            nombres[k] = 'trasera'
        elif _dot(n, Y) > UMBRAL_CARA_SEMANTICA:
            nombres[k] = 'superior'
        elif _dot(n, Y) < -UMBRAL_CARA_SEMANTICA:
            nombres[k] = 'inferior'
        else:
            nombres[k] = f'lateral_{k}'  # cara inclinada (sección no rectangular)

    y_min, y_max = _extension_en_direccion(vertices, Y)
    z_min, z_max = _extension_en_direccion(vertices, Z)

    return {
        'longitud': longitud,
        'angulo_corte_a': angulo_corte(g_ext_a),
        'angulo_corte_b': angulo_corte(g_ext_b),
        'seccion': seccion,
        'seccion_regular': seccion_regular,
        'grupos_extremo': (g_ext_a, g_ext_b),
        'nombres': nombres,
        'planos': {k: _plano_exterior(vertices, grupos[k]['normal']) for k in range(len(grupos))},
        'eje': eje, 'Y': Y, 'Z': Z,
        'eje_min': eje_min, 'y_min': y_min, 'z_min': z_min,
        'ancho_y': y_max - y_min, 'grosor_z': z_max - z_min,
        'vertices': vertices,
    }, None


# ── Detección de taladros ────────────────────────────────────────────────────

def _caras_cilindricas(solid):
    """Propiedades de cada cara CYLINDER: radio, línea de eje canónica
    (dirección con signo fijo + punto de la línea más cercano al origen),
    rango axial (proyección de sus vértices sobre la línea) y barrido angular."""
    out = []
    for f in solid.Faces():
        if f.geomType() != 'CYLINDER':
            continue
        try:
            adaptor = BRepAdaptor_Surface(f.wrapped, True)
            cyl = adaptor.Cylinder()
            ax = cyl.Axis()
            loc, dirv = ax.Location(), ax.Direction()
            d = _normalizar(Vector(dirv.X(), dirv.Y(), dirv.Z()))
            # Dirección canónica (signo fijo) para que caras del mismo taladro
            # con orientaciones opuestas compartan línea y coordenadas.
            puntuacion = 4 * d.z + 2 * d.y + d.x
            if puntuacion < 0 or (abs(puntuacion) < 1e-12 and d.x < 0):
                d = _escala(d, -1)
            p = Vector(loc.X(), loc.Y(), loc.Z())
            p0 = _resta(p, _escala(d, _dot(p, d)))  # punto de la línea más cercano al origen global

            vs = [Vector(*v.toTuple()) for v in f.Vertices()]
            if not vs:
                bb = f.BoundingBox()
                vs = [Vector(x, y, z) for x in (bb.xmin, bb.xmax) for y in (bb.ymin, bb.ymax) for z in (bb.zmin, bb.zmax)]
            ts = [_dot(v, d) for v in vs]

            barrido = abs(adaptor.LastUParameter() - adaptor.FirstUParameter())
            out.append({
                'radio': cyl.Radius(), 'd': d, 'p0': p0,
                't_min': min(ts), 't_max': max(ts), 'barrido': barrido,
            })
        except Exception:
            continue
    return out


def _misma_linea(a, b):
    if abs(_dot(a['d'], b['d'])) < UMBRAL_MISMA_DIRECCION:
        return False
    return _norma(_resta(a['p0'], b['p0'])) < TOL_MISMO_EJE


def _agrupar_cilindros(caras):
    """Caras del mismo cilindro físico: misma línea de eje, mismo radio y
    rangos axiales que SOLAPAN. Dos taladros del mismo Ø alineados en el mismo
    eje (uno en cada extremo de la barra) tienen rangos disjuntos y quedan
    como taladros separados."""
    cilindros = []
    for c in caras:
        destino = None
        for cil in cilindros:
            if abs(cil['radio'] - c['radio']) > TOL_RADIO:
                continue
            if not _misma_linea(cil, c):
                continue
            if c['t_min'] > cil['t_max'] + TOL_SOLAPE_AXIAL or cil['t_min'] > c['t_max'] + TOL_SOLAPE_AXIAL:
                continue
            destino = cil
            break
        if destino:
            destino['t_min'] = min(destino['t_min'], c['t_min'])
            destino['t_max'] = max(destino['t_max'], c['t_max'])
            destino['barrido'] += c['barrido']
        else:
            cilindros.append(dict(c))
    return cilindros


def _fusionar_avellanados(cilindros):
    """Cilindros concéntricos (misma línea) con rangos solapados o adyacentes
    y Ø distinto = un taladro avellanado/con caja. El principal es el de rango
    más largo (el piloto/pasante); el otro queda anotado como avellanado."""
    usados = set()
    resultado = []
    for i, a in enumerate(cilindros):
        if i in usados:
            continue
        companero = None
        for j in range(i + 1, len(cilindros)):
            if j in usados:
                continue
            b = cilindros[j]
            if abs(a['radio'] - b['radio']) <= TOL_RADIO:
                continue
            if not _misma_linea(a, b):
                continue
            if b['t_min'] > a['t_max'] + TOL_ADYACENTE_AVELLANADO or a['t_min'] > b['t_max'] + TOL_ADYACENTE_AVELLANADO:
                continue
            companero = j
            break
        if companero is None:
            resultado.append(a)
            continue
        b = cilindros[companero]
        usados.add(companero)
        largo_a, largo_b = a['t_max'] - a['t_min'], b['t_max'] - b['t_min']
        principal, caja = (a, b) if largo_a >= largo_b else (b, a)
        fusionado = dict(principal)
        fusionado['t_min'] = min(a['t_min'], b['t_min'])
        fusionado['t_max'] = max(a['t_max'], b['t_max'])
        fusionado['profundidad_principal'] = principal['t_max'] - principal['t_min']
        fusionado['avellanado'] = {
            'diametro': round(caja['radio'] * 2, 2),
            'profundidad': round(caja['t_max'] - caja['t_min'], 2),
        }
        resultado.append(fusionado)
    return resultado


def _asignar_taladros(cilindros, grupos, env):
    """Convierte cada cilindro (ya filtrado como taladro real) en un taladro
    con cara semántica, posición 2D en el marco local, profundidad y pasante.
    La cara se decide por la BOCA: el extremo del rango axial más cercano al
    plano de una cara compatible. Devuelve una lista 1:1 con `cilindros`."""
    taladros = []

    for cil in cilindros:
        # Caras candidatas: normal ~paralela al eje del cilindro.
        candidatas = [k for k in range(len(grupos))
                      if abs(_dot(cil['d'], grupos[k]['normal'])) >= UMBRAL_EJE_TALADRO]
        if not candidatas:
            taladros.append({
                'cara': None, 'es_extremo': False, 'x': None, 'y': None,
                'diametro': round(cil['radio'] * 2, 2),
                'profundidad': round(cil['t_max'] - cil['t_min'], 2),
                'pasante': False,
                **({'avellanado': cil['avellanado']} if 'avellanado' in cil else {}),
            })
            continue

        extremos_rango = []
        for t in (cil['t_min'], cil['t_max']):
            extremos_rango.append(_suma(cil['p0'], _escala(cil['d'], t)))

        mejor = None  # (distancia, k_cara, punto_boca, punto_fondo)
        for k in candidatas:
            normal_cara = grupos[k]['normal']
            for idx_p, punto in enumerate(extremos_rango):
                # Distancia al plano EXTERIOR de la cara (no a un centro
                # promedio, que estaría contaminado por fondos de taladros).
                dist = abs(_dot(punto, normal_cara) - env['planos'][k])
                if mejor is None or dist < mejor[0]:
                    mejor = (dist, k, punto, extremos_rango[1 - idx_p])

        _, k_cara, boca, fondo = mejor
        nombre = env['nombres'][k_cara]
        es_extremo = nombre in ('extremo_a', 'extremo_b')

        # Pasante: el fondo también toca el plano de la cara opuesta.
        pasante = False
        opuesta = next((k for k in candidatas if k != k_cara
                        and _dot(grupos[k]['normal'], grupos[k_cara]['normal']) < UMBRAL_PARALELO), None)
        if opuesta is not None:
            pasante = abs(_dot(fondo, grupos[opuesta]['normal']) - env['planos'][opuesta]) < TOL_PASANTE

        # Posición de la boca en el marco local consistente.
        cx = _dot(boca, env['eje']) - env['eje_min']
        cy = _dot(boca, env['Y']) - env['y_min']
        cz = _dot(boca, env['Z']) - env['z_min']
        if nombre in ('frontal', 'trasera'):
            x_local, y_local = cx, cy      # x a lo largo, y desde el borde inferior
        elif nombre in ('superior', 'inferior'):
            x_local, y_local = cx, cz      # x a lo largo, y desde la cara frontal
        elif es_extremo:
            x_local, y_local = cy, cz      # y desde borde inferior, z desde cara frontal
        else:
            x_local, y_local = cx, cy      # cara inclinada: aproximado

        profundidad = cil.get('profundidad_principal', cil['t_max'] - cil['t_min'])

        taladro = {
            'cara': nombre, 'es_extremo': es_extremo,
            'x': round(x_local, 2), 'y': round(y_local, 2),
            'diametro': round(cil['radio'] * 2, 2),
            'profundidad': round(profundidad, 2),
            'pasante': pasante,
        }
        if 'avellanado' in cil:
            taladro['avellanado'] = cil['avellanado']
        taladros.append(taladro)

    return taladros


def _fusionar_pasantes_perfil_hueco(taladros, cilindros, env):
    """Los perfiles de aluminio reales son HUECOS (pared ~1.5 mm): un taladro
    pasante atraviesa dos paredes y aparece como dos perforaciones cortas en
    caras opuestas sobre el mismo eje. Para el taller eso es UN taladro
    pasante con la profundidad total del perfil. Se fusionan solo pares de
    caras LATERALES opuestas (frontal/trasera, superior/inferior) — dos
    taladros axiales independientes en los extremos NO se fusionan."""
    PARES_OPUESTOS = {('frontal', 'trasera'), ('superior', 'inferior')}
    resultado = []
    usados = set()
    for i, a in enumerate(taladros):
        if i in usados or a['cara'] is None:
            if i not in usados:
                resultado.append(a)
            continue
        fusionado = None
        for j in range(i + 1, len(taladros)):
            if j in usados:
                continue
            b = taladros[j]
            if b['cara'] is None or abs(a['diametro'] - b['diametro']) > TOL_RADIO * 2:
                continue
            par = tuple(sorted((a['cara'], b['cara'])))
            if par not in {tuple(sorted(p)) for p in PARES_OPUESTOS}:
                continue
            if not _misma_linea(cilindros[i], cilindros[j]):
                continue
            # Fusionar: se conserva la cara "positiva" (frontal/superior) y la
            # profundidad pasa a ser el espesor total del perfil en ese eje.
            principal = a if a['cara'] in ('frontal', 'superior') else b
            span = env['grosor_z'] if principal['cara'] == 'frontal' else env['ancho_y']
            fusionado = {**principal, 'pasante': True, 'profundidad': round(span, 2)}
            usados.add(i)
            usados.add(j)
            break
        resultado.append(fusionado if fusionado else a)
        usados.add(i)
    return resultado


# ── Punto de entrada ──────────────────────────────────────────────────────────

def analizar_solido(solid):
    faces_planas = [f for f in solid.Faces() if _es_plana(f)]
    if len(faces_planas) < 4:
        return {'ok': False, 'motivo': f'{len(faces_planas)} caras planas (insuficiente para una caja)'}

    grupos = _clusterizar_caras_planas(faces_planas)
    if len(grupos) > 6:
        return {'ok': False, 'motivo': f'{len(grupos)} direcciones de normal distintas (sección real, no soportada)'}

    env, motivo = _analizar_envolvente(solid, faces_planas, grupos)
    if env is None:
        return {'ok': False, 'motivo': motivo}

    caras_cil = _caras_cilindricas(solid)
    cilindros = _fusionar_avellanados(_agrupar_cilindros(caras_cil))

    # Filtro de no-taladros ANTES de asignar (fillets/fresados parciales por
    # barrido angular, radios absurdos) — mantiene el 1:1 taladro↔cilindro
    # que necesita la fusión de pasantes de perfil hueco.
    mayor_dim = max(env['ancho_y'], env['grosor_z'], 1.0)
    validos = [c for c in cilindros
               if c['barrido'] >= BARRIDO_MIN_TALADRO and c['radio'] * 2 <= mayor_dim * 1.5]
    descartados = len(cilindros) - len(validos)

    taladros = _asignar_taladros(validos, grupos, env)
    taladros = _fusionar_pasantes_perfil_hueco(taladros, validos, env)

    return {
        'ok': True,
        'longitud': env['longitud'],
        'angulo_corte_a': env['angulo_corte_a'],
        'angulo_corte_b': env['angulo_corte_b'],
        'seccion': env['seccion'],
        'seccion_regular': env['seccion_regular'],
        'ancho': round(env['ancho_y'], 2),
        'grosor': round(env['grosor_z'], 2),
        'taladros': taladros,
        'taladros_descartados': descartados,
    }
