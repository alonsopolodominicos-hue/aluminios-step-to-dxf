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
from cadquery import Vector

from analisis import (
    _es_plana,
    _clusterizar_caras_planas,
    _analizar_envolvente,
    _caras_cilindricas,
    _agrupar_cilindros,
    _fusionar_avellanados,
    _asignar_taladros,
    _dot,
    _resta,
    _norma,
    BARRIDO_MIN_TALADRO,
)

RENOMBRE_CARA = {'extremo_a': 'lateral_iz', 'extremo_b': 'lateral_de'}

# Solo se busca en las caras anchas — nunca en los cantos cortos
# (lateral_iz/lateral_de) — para reducir el riesgo de confundir la pared de un
# bolsillo cortado desde un canto con un cajeado real; los cajeados/ranuras de
# canto (mucho más raros en tablero) quedan fuera de esta primera versión.
GRUPOS_CAJEADO = ('frontal', 'trasera', 'superior', 'inferior')
PROFUNDIDAD_MIN_CAJEADO = 0.5   # mm — por debajo es ruido numérico, no un cajeado real
AREA_MIN_CAJEADO = 20.0         # mm² — descarta fragmentos/virutas de geometría
TOL_ARISTA_COMPARTIDA = 0.1     # mm — distancia entre extremos para considerar la misma arista
UMBRAL_RANURA_ANCHO = 15.0      # mm — dimensión menor por debajo de esto se reporta como 'ranura'


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


def _detectar_cajeados(faces_planas, grupos, env):
    """Bolsillos/ranuras rectangulares (no cilíndricos) en las caras anchas.
    Devuelve una lista de dicts {cara, forma, x, y, largo, ancho, profundidad}
    — x,y = esquina inferior-izquierda del rectángulo en el marco local de la
    cara (misma convención que los taladros)."""
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
        nombre = env['nombres'].get(k)
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
            eje_dir = env['Z'] if nombre in ('superior', 'inferior') else env['Y']
            eje_min_dir = env['z_min'] if nombre in ('superior', 'inferior') else env['y_min']
            xs = [_dot(v, env['eje']) - env['eje_min'] for v in vs]
            ys = [_dot(v, eje_dir) - eje_min_dir for v in vs]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            largo, ancho = x1 - x0, y1 - y0
            if largo <= 0 or ancho <= 0:
                continue
            forma = 'ranura' if min(largo, ancho) < UMBRAL_RANURA_ANCHO else 'cajeado'
            resultado.append({
                'cara': nombre, 'forma': forma,
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


def analizar_solido_panel(solid):
    """Analiza un sólido como panel de tablero. Devuelve:
      {'ok': True, 'largo', 'ancho', 'grosor', 'seccion_regular',
       'taladros', 'taladros_descartados', 'advertencias'}
    o {'ok': False, 'motivo'} si el sólido no es una caja reconocible.
    """
    faces_planas = [f for f in solid.Faces() if _es_plana(f)]
    if len(faces_planas) < 4:
        return {'ok': False, 'motivo': f'{len(faces_planas)} caras planas (insuficiente para un panel)'}

    grupos = _clusterizar_caras_planas(faces_planas)
    if len(grupos) > 6:
        return {'ok': False, 'motivo': f'{len(grupos)} direcciones de normal distintas (forma no soportada)'}

    env, motivo = _analizar_envolvente(solid, faces_planas, grupos)
    if env is None:
        return {'ok': False, 'motivo': motivo}

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

    cajeados = _detectar_cajeados(faces_planas, grupos, env)

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
