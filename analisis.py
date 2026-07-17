"""Análisis geométrico de sólidos STEP: extrae longitud, ángulos de corte,
sección y taladros de piezas de perfil (cajas de 6 direcciones de cara,
posiblemente fragmentadas por agujeros, con extremos rectos o a inglete).

Ver /Users/alonsopolo/.claude/plans/snuggly-kindling-peach.md para el
razonamiento completo — validado contra archivos STEP reales del cliente
(perfiles de aluminio de la carpeta "dxf y bpp/step" en Drive), incluyendo
un archivo con taladros de canto reales modelados en 3D.

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


def _normalizar(v):
    largo = math.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2)
    return Vector(v.x / largo, v.y / largo, v.z / largo)


# ── Envolvente por clustering de normales ────────────────────────────────────
# En vez de exigir exactamente 6 caras (como la primera versión), se agrupan
# las caras PLANE por dirección de normal: una cara física puede venir
# partida en varios fragmentos — por un taladro que la atraviesa, o por cómo
# exportó Solid Edge la geometría — y todos esos fragmentos comparten normal.

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


def _centro_grupo(faces_planas, grupo):
    indices = grupo['indices']
    cx = sum(faces_planas[i].Center().x for i in indices) / len(indices)
    cy = sum(faces_planas[i].Center().y for i in indices) / len(indices)
    cz = sum(faces_planas[i].Center().z for i in indices) / len(indices)
    return Vector(cx, cy, cz)


def _extension_en_direccion(vertices, direccion):
    proys = [_dot(v, direccion) for v in vertices]
    return min(proys), max(proys)


def _analizar_envolvente(solid, faces_planas, grupos):
    pares, sin_emparejar = _emparejar_grupos_opuestos(grupos)
    # Una caja necesita 3 ejes independientes: o los 3 salen emparejados
    # (caja completa), o 2 emparejados + 2 sueltos (el 3er eje son los 2
    # extremos, no paralelos entre sí por un corte recto+inglete, o ingletes
    # distintos en cada lado). Cualquier otra combinación no define un
    # volumen 3D completo.
    if not ((len(pares) == 3 and len(sin_emparejar) == 0) or (len(pares) == 2 and len(sin_emparejar) == 2)):
        return None, f'{len(pares)} pares y {len(sin_emparejar)} caras sueltas (no define una caja completa)'

    vertices = [Vector(*v.toTuple()) for v in solid.Vertices()]

    candidatos = []
    for i, j in pares:
        vmin, vmax = _extension_en_direccion(vertices, grupos[i]['normal'])
        candidatos.append({
            'grupos': (i, j), 'longitud': vmax - vmin,
            'normal_a': grupos[i]['normal'], 'normal_b': grupos[j]['normal'],
        })
    if len(sin_emparejar) == 2:
        i, j = sin_emparejar
        ca, cb = _centro_grupo(faces_planas, grupos[i]), _centro_grupo(faces_planas, grupos[j])
        direccion = _normalizar(_resta(cb, ca))
        vmin, vmax = _extension_en_direccion(vertices, direccion)
        candidatos.append({
            'grupos': (i, j), 'longitud': vmax - vmin,
            'normal_a': grupos[i]['normal'], 'normal_b': grupos[j]['normal'],
        })

    elegido = max(candidatos, key=lambda c: c['longitud'])
    i_ext, j_ext = elegido['grupos']

    ca, cb = _centro_grupo(faces_planas, grupos[i_ext]), _centro_grupo(faces_planas, grupos[j_ext])
    eje = _normalizar(_resta(cb, ca))

    def angulo_corte(normal_cara):
        d = max(-1.0, min(1.0, abs(_dot(normal_cara, eje))))
        return math.degrees(math.acos(d))

    otros_pares = [p for p in pares if p != elegido['grupos']]
    otras_sueltas = sin_emparejar if (i_ext, j_ext) in pares else []

    seccion = []
    for gi, gj in otros_pares:
        vmin, vmax = _extension_en_direccion(vertices, grupos[gi]['normal'])
        seccion.append((gi, vmax - vmin))
    seccion_regular = len(otras_sueltas) == 0
    if not seccion_regular:
        i, j = otras_sueltas
        ca2, cb2 = _centro_grupo(faces_planas, grupos[i]), _centro_grupo(faces_planas, grupos[j])
        seccion.append((i, math.sqrt(sum((a - b) ** 2 for a, b in zip(ca2.toTuple(), cb2.toTuple())))))

    eje_min, eje_max = _extension_en_direccion(vertices, eje)

    return {
        'longitud': elegido['longitud'],
        'angulo_corte_a': angulo_corte(elegido['normal_a']),
        'angulo_corte_b': angulo_corte(elegido['normal_b']),
        'seccion': sorted(s for _, s in seccion),
        'seccion_regular': seccion_regular,
        'grupos_extremo': (i_ext, j_ext),
        'eje': eje,
        'eje_min': eje_min,
        'vertices': vertices,
    }, None


# ── Detección de taladros ────────────────────────────────────────────────────

def _propiedades_cilindro(face):
    adaptor = BRepAdaptor_Surface(face.wrapped, True)
    cyl = adaptor.Cylinder()
    ax = cyl.Axis()
    loc = ax.Location()
    dirv = ax.Direction()
    return {
        'radio': cyl.Radius(),
        'origen': Vector(loc.X(), loc.Y(), loc.Z()),
        'direccion': _normalizar(Vector(dirv.X(), dirv.Y(), dirv.Z())),
    }


def _mismo_taladro(a, b):
    if abs(a['radio'] - b['radio']) > 0.05:
        return False
    if abs(_dot(a['direccion'], b['direccion'])) < 0.999:
        return False
    delta = _resta(b['origen'], a['origen'])
    largo_delta = math.sqrt(delta.x ** 2 + delta.y ** 2 + delta.z ** 2)
    if largo_delta < 1e-6:
        return True
    proy = abs(_dot(delta, a['direccion'])) / largo_delta
    return proy > 0.999


def _detectar_taladros_crudo(solid):
    caras_cilindro = [f for f in solid.Faces() if f.geomType() == 'CYLINDER']
    props = [_propiedades_cilindro(f) for f in caras_cilindro]
    grupos = []
    for p in props:
        grupo = next((g for g in grupos if _mismo_taladro(g['props'][0], p)), None)
        if grupo:
            grupo['props'].append(p)
        else:
            grupos.append({'props': [p]})
    return [{'origen': g['props'][0]['origen'], 'direccion': g['props'][0]['direccion'], 'diametro': g['props'][0]['radio'] * 2} for g in grupos]


def _asignar_taladros(taladros_crudos, grupos, envolvente):
    """Asigna cada taladro a la cara del envolvente cuya normal coincide con
    su eje (cualquiera de las 6 — 4 laterales o las 2 de extremo, un taladro
    axial en el testero de la barra es un caso real, no una excepción) y
    calcula su posición local 2D sobre esa cara. Taladros que no se puedan
    asignar con confianza se devuelven con cara=None (se anotan aparte, no se
    descartan)."""
    eje = envolvente['eje']
    eje_min = envolvente['eje_min']
    i_ext, j_ext = envolvente['grupos_extremo']
    vertices = envolvente['vertices']
    # Una dirección "lateral" cualquiera (perpendicular al eje largo) para
    # poder construir una base local también sobre las caras de extremo,
    # donde normal_cara ≈ eje y no sirve como referencia de "ancho".
    lado_normal = next((g['normal'] for k, g in enumerate(grupos) if k not in (i_ext, j_ext)), None)

    resultado = []
    for t in taladros_crudos:
        mejor, mejor_score = None, -1.0
        for k, g in enumerate(grupos):
            score = abs(_dot(t['direccion'], g['normal']))
            if score > mejor_score:
                mejor_score, mejor = score, k

        if mejor is None or mejor_score < UMBRAL_EJE_TALADRO:
            resultado.append({**t, 'cara': None, 'x': None, 'y': None})
            continue

        normal_cara = grupos[mejor]['normal']
        es_extremo = mejor in (i_ext, j_ext)
        if es_extremo:
            # Cara de extremo: el "ancho" de la base local sale de una
            # dirección lateral cualquiera, no del eje (normal_cara ≈ eje).
            base1 = lado_normal if lado_normal is not None else eje
        else:
            base1 = eje
        cruzado = _normalizar(_cruz(normal_cara, base1))
        base1_min, _ = _extension_en_direccion(vertices, base1)
        cruzado_min, _ = _extension_en_direccion(vertices, cruzado)

        x_local = _dot(t['origen'], base1) - base1_min
        y_local = _dot(t['origen'], cruzado) - cruzado_min

        if es_extremo:
            nombre_cara = 'extremo_a' if mejor == i_ext else 'extremo_b'
        else:
            nombre_cara = f'lateral_{mejor}'

        resultado.append({
            'cara': nombre_cara, 'es_extremo': es_extremo,
            'x': round(x_local, 2), 'y': round(y_local, 2),
            'diametro': round(t['diametro'], 2),
        })
    return resultado


# ── Punto de entrada ──────────────────────────────────────────────────────────

def analizar_solido(solid):
    faces_planas = [f for f in solid.Faces() if _es_plana(f)]
    if len(faces_planas) < 4:
        return {'ok': False, 'motivo': f'{len(faces_planas)} caras planas (insuficiente para una caja)'}

    grupos = _clusterizar_caras_planas(faces_planas)
    if len(grupos) > 6:
        return {'ok': False, 'motivo': f'{len(grupos)} direcciones de normal distintas (sección real, no soportada)'}

    envolvente, motivo = _analizar_envolvente(solid, faces_planas, grupos)
    if envolvente is None:
        return {'ok': False, 'motivo': motivo}

    taladros_crudos = _detectar_taladros_crudo(solid)
    taladros = _asignar_taladros(taladros_crudos, grupos, envolvente)

    return {
        'ok': True,
        'longitud': envolvente['longitud'],
        'angulo_corte_a': envolvente['angulo_corte_a'],
        'angulo_corte_b': envolvente['angulo_corte_b'],
        'seccion': envolvente['seccion'],
        'seccion_regular': envolvente['seccion_regular'],
        'taladros': taladros,
    }
