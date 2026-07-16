"""Análisis geométrico de sólidos STEP: extrae longitud, ángulos de corte y
sección de piezas de perfil (cajas de 6 caras planas, posiblemente con
extremos a inglete). Ver /Users/alonsopolo/.claude/plans/snuggly-kindling-peach.md
para el razonamiento completo — validado contra archivos STEP reales del
cliente (perfiles de aluminio de la carpeta "dxf y bpp/step" en Drive).

Sólidos que no son una caja de 6 caras planas (herraje: tornillos, bisagras,
piezas con geometría curva) se reportan como no soportados, no se fuerzan.
"""
import math

from cadquery import Vector

UMBRAL_PARALELO = -0.99  # dot de normales opuestas; ~8° de tolerancia


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


def _resta(a, b):
    return Vector(a.x - b.x, a.y - b.y, a.z - b.z)


def _emparejar_caras_paralelas(faces):
    """Empareja caras por normales opuestas (dot < UMBRAL_PARALELO), aceptando
    emparejamientos parciales: las 4 caras "laterales" de un prisma siempre
    forman 2 pares paralelos entre sí, pero las 2 caras "de extremo" pueden
    NO ser paralelas entre sí si la pieza lleva un corte recto en un lado y
    a inglete en el otro (o ingletes distintos en cada lado) — caso real
    observado en los archivos del cliente, no una excepción rara.

    Devuelve (pares, sin_emparejar)."""
    n = len(faces)
    normales = [_normal_unitaria(f) for f in faces]
    candidatos = []
    for i in range(n):
        for j in range(i + 1, n):
            d = _dot(normales[i], normales[j])
            if d < UMBRAL_PARALELO:
                candidatos.append((d, i, j))
    candidatos.sort(key=lambda t: t[0])  # más antiparalelo primero

    usadas = set()
    pares = []
    for d, i, j in candidatos:
        if i in usadas or j in usadas:
            continue
        pares.append((i, j))
        usadas.add(i)
        usadas.add(j)

    sin_emparejar = [i for i in range(n) if i not in usadas]
    return pares, sin_emparejar


def _separacion_entre_planos(face_a, face_b):
    normal = _normal_unitaria(face_a)
    vec = _resta(face_b.Center(), face_a.Center())
    return abs(_dot(vec, normal))


def analizar_solido(solid):
    """Devuelve un dict con 'ok': True y las medidas, o 'ok': False y 'motivo'."""
    faces = solid.Faces()
    if len(faces) != 6:
        return {'ok': False, 'motivo': f'{len(faces)} caras (se esperaban 6)'}
    if not all(_es_plana(f) for f in faces):
        return {'ok': False, 'motivo': 'alguna cara no es plana'}

    pares, sin_emparejar = _emparejar_caras_paralelas(faces)
    if len(pares) < 2:
        return {'ok': False, 'motivo': f'solo {len(pares)} pares paralelos encontrados (geometría no soportada)'}

    # El eje largo NO es siempre el par emparejado con más separación: en
    # piezas con sección trapezoidal (no rectangular), el par que sobra sin
    # emparejar puede ser un lateral biselado, no los extremos reales. Señal
    # robusta: probar TODOS los candidatos (cada par emparejado, y también el
    # par sobrante si existe) y quedarse con el de mayor separación — la
    # longitud de una pieza siempre es mayor que su sección transversal.
    candidatos = []
    for p in pares:
        candidatos.append({'idx': p, 'longitud': _separacion_entre_planos(faces[p[0]], faces[p[1]]), 'de_par': True})
    if len(sin_emparejar) == 2:
        i, j = sin_emparejar
        vec = _resta(faces[j].Center(), faces[i].Center())
        dist = math.sqrt(vec.x ** 2 + vec.y ** 2 + vec.z ** 2)
        candidatos.append({'idx': (i, j), 'longitud': dist, 'de_par': False})

    elegido = max(candidatos, key=lambda c: c['longitud'])
    i_ext, j_ext = elegido['idx']
    face_a, face_b = faces[i_ext], faces[j_ext]
    normal_a = _normal_unitaria(face_a)
    normal_b = _normal_unitaria(face_b)

    eje = _resta(face_b.Center(), face_a.Center())
    largo_eje = math.sqrt(eje.x ** 2 + eje.y ** 2 + eje.z ** 2)
    eje_unitario = Vector(eje.x / largo_eje, eje.y / largo_eje, eje.z / largo_eje)

    def angulo_corte(normal_cara):
        # Ángulo entre la normal de la cara de extremo y el eje largo.
        # 0° = corte recto/perpendicular; 45° = inglete típico.
        d = _dot(normal_cara, eje_unitario)
        d = max(-1.0, min(1.0, abs(d)))
        return math.degrees(math.acos(d))

    otros_pares = [p for p in pares if p != elegido['idx']]
    otras_sueltas = sin_emparejar if elegido['de_par'] else []

    seccion = [_separacion_entre_planos(faces[p[0]], faces[p[1]]) for p in otros_pares]
    seccion_regular = len(otras_sueltas) == 0
    if not seccion_regular:
        # Sección no rectangular (p.ej. trapezoidal): las 2 caras sueltas no
        # son paralelas, no hay separación limpia — se aproxima con la
        # distancia centro a centro y se marca como aproximada.
        i, j = otras_sueltas
        vec = _resta(faces[j].Center(), faces[i].Center())
        seccion.append(math.sqrt(vec.x ** 2 + vec.y ** 2 + vec.z ** 2))

    return {
        'ok': True,
        'longitud': largo_eje,
        'angulo_corte_a': angulo_corte(normal_a),
        'angulo_corte_b': angulo_corte(normal_b),
        'seccion': sorted(seccion),
        'seccion_regular': seccion_regular,
    }
