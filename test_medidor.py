"""Tests del medidor de DXF. Se ejecutan con el venv local:

    .venv-step/bin/python services/step-to-dxf/test_medidor.py
"""
import base64
import io
import math
import os
import sys
import tempfile

import ezdxf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from medidor_dxf import (  # noqa: E402
    caja_minima, envolvente_convexa, punto_dentro, area_con_signo,
    reconstruir_bucles, medir_dxf, detectar_nombre, extraer_segmentos,
    detectar_sector_circular, _segmentos_de_arco,
)

FALLOS = []


def check(nombre, cond, detalle=''):
    if cond:
        print(f'  ✓ {nombre}')
    else:
        print(f'  ✗ {nombre} — {detalle}')
        FALLOS.append(nombre)


def girar(puntos, grados, cx=0.0, cy=0.0):
    a = math.radians(grados)
    return [((x - cx) * math.cos(a) - (y - cy) * math.sin(a) + cx,
             (x - cx) * math.sin(a) + (y - cy) * math.cos(a) + cy) for x, y in puntos]


def caso_calibre_rotatorio():
    """La medida NO puede depender de cómo esté girada la pieza."""
    print('CASO: calibre rotatorio')
    base = [(0, 0), (300, 0), (300, 150), (0, 150)]
    for grados in (0, 17, 37, 45, 90, 123, 179):
        L, A, _ = caja_minima(girar(base, grados))
        check(f'[calibre] 300x150 girado {grados}° mide 300x150',
              abs(L - 300) < 0.01 and abs(A - 150) < 0.01, f'{L:.2f}x{A:.2f}')
    # La caja RECTA de la misma pieza girada sí cambia: es justo lo que se evita.
    g = girar(base, 37)
    xs = [p[0] for p in g]
    check('[calibre] la caja recta girada SÍ mide de más (por eso hace falta)',
          (max(xs) - min(xs)) > 320, f'{max(xs)-min(xs):.1f}')


def caso_envolvente_y_dentro():
    print('CASO: envolvente convexa y punto dentro')
    cuadrado = [(0, 0), (10, 0), (10, 10), (0, 10)]
    conRuido = cuadrado + [(5, 5), (3, 4)]      # puntos interiores
    env = envolvente_convexa(conRuido)
    check('[envolvente] ignora los puntos de dentro', len(env) == 4, str(env))
    check('[dentro] un punto interior está dentro', punto_dentro((5, 5), cuadrado))
    check('[dentro] un punto exterior está fuera', not punto_dentro((15, 5), cuadrado))
    check('[área] el cuadrado de 10 mide 100', abs(abs(area_con_signo(cuadrado)) - 100) < 1e-6)


def caso_reconstruccion():
    print('CASO: reconstrucción de bucles')
    # Cuadrado dado como 4 segmentos SUELTOS y desordenados, con un hueco de
    # 0,05 mm en una esquina — como sale de un CAD real.
    segs = [((10, 0), (10, 10)), ((0, 10), (0, 0)),
            ((0, 0), (10, 0)), ((10, 10), (0.05, 10))]
    bucles = reconstruir_bucles(segs)
    check('[unir] cierra el cuadrado pese al hueco de 0,05', len(bucles) == 1, f'{len(bucles)} bucles')
    # Con segmentos que no cierran, no se inventa un bucle.
    check('[unir] no cierra lo que no cierra',
          reconstruir_bucles([((0, 0), (10, 0)), ((20, 0), (30, 0))]) == [])


def caso_dxf_completo():
    print('CASO: DXF completo con pieza y agujero')
    doc = ezdxf.new()
    msp = doc.modelspace()
    # Pieza de 400x200 girada 30°, con un agujero circular dentro.
    pieza = girar([(0, 0), (400, 0), (400, 200), (0, 200)], 30)
    msp.add_lwpolyline(pieza, close=True)
    centro = girar([(200, 100)], 30)[0]
    msp.add_circle(centro, 25)
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'p.dxf')
        doc.saveas(ruta)
        r = medir_dxf(ruta)
    piezas = r['piezas']
    check('[dxf] encuentra 1 pieza (el agujero no cuenta)', len(piezas) == 1, str(len(piezas)))
    if piezas:
        p = piezas[0]
        check('[dxf] mide 400x200 pese al giro de 30°',
              abs(p['largo'] - 400) < 0.5 and abs(p['ancho'] - 200) < 0.5,
              f"{p['largo']}x{p['ancho']}")
        check('[dxf] detecta el agujero de dentro', p['agujeros'] == 1, str(p['agujeros']))
        check('[dxf] dice a cuántos grados está girada',
              abs(p['angulo'] - 30) < 0.5 or abs(p['angulo'] - 120) < 0.5, str(p['angulo']))


def caso_robusto():
    print('CASO: no se tumba con basura')
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_text('esto es un texto, no geometría')
    msp.add_point((5, 5))
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'v.dxf')
        doc.saveas(ruta)
        r = medir_dxf(ruta)
    check('[robusto] un DXF sin piezas devuelve aviso, no excepción',
          r['piezas'] == [] and len(r['avisos']) > 0, str(r))
    r2 = medir_dxf('/no/existe/de/verdad.dxf')
    check('[robusto] un fichero que no existe devuelve aviso', r2['piezas'] == [] and r2['avisos'])


def caso_deteccion_nombre():
    """Qué pieza es cada una: el texto que cae dentro (o pegado a) su contorno."""
    print('CASO: detección del nombre de la pieza por texto')
    cuadrado = [(0, 0), (10, 0), (10, 10), (0, 10)]

    check('[nombre] texto dentro del contorno se detecta',
          detectar_nombre(cuadrado, [((5, 5), 'P01')]) == 'P01')
    check('[nombre] texto pegado pero fuera del contorno se detecta',
          detectar_nombre(cuadrado, [((15, 5), 'P02')]) == 'P02')
    check('[nombre] texto lejos del contorno no se detecta',
          detectar_nombre(cuadrado, [((1000, 1000), 'LEJOS')]) is None)
    check('[nombre] sin ningún texto no se detecta',
          detectar_nombre(cuadrado, []) is None)
    check('[nombre] con varios textos elige el más cercano',
          detectar_nombre(cuadrado, [((1000, 1000), 'LEJOS'), ((5, 5), 'CERCA')]) == 'CERCA')


def caso_dxf_con_nombres_y_cotas():
    """Extremo a extremo: medir_dxf pone nombre a la pieza con texto y genera
    un DXF acotado con cotas nativas y etiqueta en la que no tenía texto."""
    print('CASO: DXF completo con nombres de pieza y DXF acotado')
    doc = ezdxf.new()
    msp = doc.modelspace()
    # Pieza 1: 100x50 con su nombre escrito dentro.
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True)
    msp.add_text('P01', height=5).set_placement((50, 25))
    # Pieza 2: 100x50 girada 20°, sin ningún texto cerca.
    pieza2 = girar([(0, 300), (100, 300), (100, 350), (0, 350)], 20, cx=0, cy=325)
    msp.add_lwpolyline(pieza2, close=True)

    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'p.dxf')
        doc.saveas(ruta)
        r = medir_dxf(ruta)

    piezas = r['piezas']
    check('[dxf] detecta las 2 piezas', len(piezas) == 2, str(len(piezas)))
    check('[dxf] la pieza con texto dentro lleva su nombre',
          any(p['nombre'] == 'P01' for p in piezas), str(piezas))
    check('[dxf] la pieza sin texto cerca queda sin nombre',
          any(p['nombre'] is None for p in piezas), str(piezas))

    b64 = r.get('dxf_acotado_base64')
    check('[acotado] la respuesta trae un DXF acotado', bool(b64))
    if b64:
        doc2 = ezdxf.read(io.StringIO(base64.b64decode(b64).decode('utf-8')))
        msp2 = doc2.modelspace()
        dims = list(msp2.query('DIMENSION'))
        check('[acotado] hay 2 cotas por pieza (largo + ancho) × 2 piezas',
              len(dims) == 4, str(len(dims)))
        medidas = sorted(round(d.get_measurement(), 1) for d in dims)
        check('[acotado] las cotas miden 50 y 100, como las piezas',
              medidas == [50.0, 50.0, 100.0, 100.0], str(medidas))

        # El número de la cota tiene que verse en CUALQUIER visor: muchos
        # lectores de DXF de taller (CNC, nesting) dibujan LINE/ARC/INSERT
        # pero no entienden MTEXT, así que el texto tiene que ser TEXT plano.
        for d in dims:
            bloque = doc2.blocks.get(d.dxf.geometry)
            tipos_texto = [e.dxftype() for e in bloque if e.dxftype() in ('TEXT', 'MTEXT')]
            check(f'[acotado] la cota {d.dxf.geometry} usa TEXT, no MTEXT',
                  tipos_texto == ['TEXT'], str(tipos_texto))

        textos = [t.plain_text() for t in msp2.query('TEXT')]
        check('[acotado] la pieza sin nombre lleva una etiqueta "Pieza N" en el plano',
              any('Pieza' in t for t in textos), str(textos))


def caso_lwpolyline_con_bulge():
    """Una LWPOLYLINE con bulge tiene un lado CURVO (así lo codifica DXF: no
    hay entidad ARC aparte, el propio vértice lleva el bulge). Ignorarlo
    convierte una pieza en forma de cuña en un triángulo de lados rectos con
    la medida completamente inventada — bug real visto en un DXF de taller
    (piezas "candilejas" en abanico con un borde curvo)."""
    print('CASO: LWPOLYLINE con bulge (lado curvo)')
    doc = ezdxf.new()
    msp = doc.modelspace()
    # Triángulo con un lado recto (0,0)-(100,0), uno curvo (100,0)-(50,80)
    # con bulge, y uno recto de vuelta a (0,0).
    msp.add_lwpolyline([(0, 0, 0.0), (100, 0, 0.35), (50, 80, 0.0)], format='xyb', close=True)

    segmentos = extraer_segmentos(msp)
    check('[bulge] el lado curvo se trocea en varios segmentos, no uno recto',
          len(segmentos) > 5, f'{len(segmentos)} segmentos')

    bucles = reconstruir_bucles(segmentos)
    check('[bulge] el contorno reconstruido tiene muchos vértices (sigue la curva)',
          len(bucles) == 1 and len(bucles[0]) > 8, str([len(b) for b in bucles]))

    if bucles:
        # El punto medio del lado recto (100,0)-(50,80) por cuerda estaría en
        # (75,40); con el bulge hacia afuera, el contorno real debe llegar
        # más lejos de esa cuerda que una simple línea recta.
        contorno = bucles[0]
        cuerda_media = (75.0, 40.0)
        dist_max = max(math.dist(p, cuerda_media) for p in contorno)
        check('[bulge] el arco se aleja de la cuerda recta (no es un triángulo de lados rectos)',
              dist_max > 5, f'{dist_max:.2f}')


def caso_deteccion_sector_circular():
    """Una pieza en abanico (cuña/sector circular) no la describe bien
    "largo x ancho de la caja mínima": ningún lado de esa caja coincide con
    un lado real de la pieza. Hay que detectarla para cotarla distinto
    (radio + ángulo, como se acotaría a mano)."""
    print('CASO: detección de piezas en forma de sector circular (cuña)')
    centro = (100.0, 200.0)
    radio = 500.0
    angulo_a, angulo_b = 10.0, 55.0
    arco = _segmentos_de_arco(centro[0], centro[1], radio, angulo_a, angulo_b)
    contorno = [centro] + arco  # centro → arco[0] (recto) → … → arco[-1] (recto de vuelta)

    resultado = detectar_sector_circular(contorno)
    check('[sector] detecta la cuña', resultado is not None)
    if resultado:
        c, r, _a1, _a2, barrido = resultado
        check('[sector] centro correcto', math.dist(c, centro) < 0.5, str(c))
        check('[sector] radio correcto', abs(r - radio) < 1, str(r))
        check('[sector] barrido correcto (45°)', abs(barrido - 45) < 0.5, str(barrido))

    rectangulo = [(0, 0), (100, 0), (100, 50), (0, 50)]
    check('[sector] un rectángulo no se confunde con una cuña',
          detectar_sector_circular(rectangulo) is None)

    radios_distintos = [(0, 0), (300, 0)] + _segmentos_de_arco(0, 0, 500, 90, 100)
    check('[sector] dos "radios" de longitud muy distinta no cuentan como cuña limpia',
          detectar_sector_circular(radios_distintos) is None)


def caso_dxf_acotado_pieza_en_cuna():
    """Extremo a extremo: una pieza en abanico se acota con radio + ángulo,
    no con las dos cotas de largo/ancho que sí valen para un rectángulo."""
    print('CASO: DXF con una pieza en forma de cuña se acota con radio y ángulo')
    doc = ezdxf.new()
    msp = doc.modelspace()
    centro = (0.0, 0.0)
    radio = 600.0
    angulo_a, angulo_b = 5.0, 50.0  # 45° de barrido
    pa = (radio * math.cos(math.radians(angulo_a)), radio * math.sin(math.radians(angulo_a)))
    pb = (radio * math.cos(math.radians(angulo_b)), radio * math.sin(math.radians(angulo_b)))
    bulge = math.tan(math.radians(angulo_b - angulo_a) / 4)
    msp.add_lwpolyline(
        [(centro[0], centro[1], 0.0), (pa[0], pa[1], bulge), (pb[0], pb[1], 0.0)],
        format='xyb', close=True,
    )
    medio = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
    msp.add_text('T04-01', height=10).set_placement(medio)

    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'cuna.dxf')
        doc.saveas(ruta)
        r = medir_dxf(ruta)

    check('[cuña] detecta 1 pieza', len(r['piezas']) == 1, str(r['piezas']))

    doc2 = ezdxf.read(io.StringIO(base64.b64decode(r['dxf_acotado_base64']).decode('utf-8')))
    dims = list(doc2.modelspace().query('DIMENSION'))
    tipos = sorted(d.dimtype for d in dims)
    check('[cuña] lleva una cota de radio (4) y una de ángulo (5), no largo/ancho',
          tipos == [4, 5], str(tipos))
    if 4 in tipos:
        radio_medido = next(d.get_measurement() for d in dims if d.dimtype == 4)
        check('[cuña] la cota de radio mide lo que mide de verdad',
              abs(radio_medido - radio) < 5, str(radio_medido))
    if 5 in tipos:
        angulo_medido = next(d.get_measurement() for d in dims if d.dimtype == 5)
        check('[cuña] la cota de ángulo mide el barrido real (45°)',
              abs(angulo_medido - 45) < 1, str(angulo_medido))


def caso_cota_a_escala_de_la_pieza():
    """Una pieza de taller real puede medir 130 mm o 3000 mm. Una cota de
    letra fija (3,5 mm) se ve bien en la pequeña y es INVISIBLE en la
    grande — bug real visto con un DXF del taller (piezas T08 de 2-3 m)."""
    print('CASO: la cota se escala con el tamaño de la pieza')
    doc = ezdxf.new()
    msp = doc.modelspace()
    # Pieza grande, tal cual las del taller (T08-11 medía 3000x500).
    msp.add_lwpolyline([(0, 0), (3000, 0), (3000, 500), (0, 500)], close=True)
    # Pieza pequeña, para comprobar que no se dispara al otro extremo.
    msp.add_lwpolyline([(0, 2000), (30, 2000), (30, 2020), (0, 2020)], close=True)

    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'p.dxf')
        doc.saveas(ruta)
        r = medir_dxf(ruta)

    piezas = sorted(r['piezas'], key=lambda p: p['largo'])
    check('[escala] detecta las 2 piezas', len(piezas) == 2, str(len(piezas)))
    grande = next(p for p in piezas if p['largo'] > 1000)
    pequena = next(p for p in piezas if p['largo'] < 100)

    doc2 = ezdxf.read(io.StringIO(base64.b64decode(r['dxf_acotado_base64']).decode('utf-8')))
    msp2 = doc2.modelspace()

    def altura_texto_de(pieza_id):
        # Busca el TEXT dentro de cualquier bloque de cota cuyo valor medido
        # coincida con el largo o el ancho de esa pieza.
        for d in msp2.query('DIMENSION'):
            medida = d.get_measurement()
            if not (abs(medida - pieza_id['largo']) < 1 or abs(medida - pieza_id['ancho']) < 1):
                continue
            bloque = doc2.blocks.get(d.dxf.geometry)
            for e in bloque:
                if e.dxftype() == 'TEXT':
                    return e.dxf.height
        return None

    alto_grande = altura_texto_de(grande)
    alto_pequena = altura_texto_de(pequena)
    check('[escala] la pieza de 3000 mm lleva letra bastante mayor que 3,5 mm (antes era invisible)',
          alto_grande is not None and alto_grande > 10, str(alto_grande))
    check('[escala] la pieza de 30 mm no se dispara a un tamaño absurdo (tope razonable)',
          alto_pequena is not None and alto_pequena < 10, str(alto_pequena))
    check('[escala] la pieza grande lleva letra bastante mayor que la pequeña',
          alto_grande is not None and alto_pequena is not None and alto_grande > alto_pequena * 2,
          f'{alto_grande} vs {alto_pequena}')


if __name__ == '__main__':
    for caso in (caso_calibre_rotatorio, caso_envolvente_y_dentro,
                 caso_reconstruccion, caso_dxf_completo, caso_robusto,
                 caso_deteccion_nombre, caso_dxf_con_nombres_y_cotas,
                 caso_cota_a_escala_de_la_pieza, caso_lwpolyline_con_bulge,
                 caso_deteccion_sector_circular, caso_dxf_acotado_pieza_en_cuna):
        caso()
        print()
    if FALLOS:
        print(f'✗ {len(FALLOS)} comprobaciones fallidas')
        sys.exit(1)
    print('✓ Todos los casos pasan')
