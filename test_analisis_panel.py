"""Tests de regresión geométrica de analisis_panel.py con STEP sintéticos.

Mismo patrón que test_analisis.py: cada caso construye con cadquery una pieza
de GEOMETRÍA CONOCIDA (ground truth), la exporta a STEP en un temporal, la
reimporta (mismo camino que en producción) y comprueba que
analizar_solido_panel() devuelve exactamente lo esperado. Ejecutar con el
venv local:

    .venv-step/bin/python services/step-to-dxf/test_analisis_panel.py
"""
import sys
import tempfile
import os

import cadquery as cq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analisis_panel import analizar_solido_panel  # noqa: E402

FALLOS = []


def check(nombre, cond, detalle=''):
    if cond:
        print(f'  ✓ {nombre}')
    else:
        print(f'  ✗ {nombre} — {detalle}')
        FALLOS.append(f'{nombre}: {detalle}')


def analizar(workplane_o_solid):
    """Exporta a STEP y reimporta — mismo pipeline que producción."""
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
        path = tmp.name
    try:
        cq.exporters.export(workplane_o_solid, path, exportType='STEP')
        result = cq.importers.importStep(path)
        solids = result.solids().vals()
        assert len(solids) == 1, f'esperaba 1 sólido, hay {len(solids)}'
        return analizar_solido_panel(solids[0])
    finally:
        os.unlink(path)


def aprox(a, b, tol=0.15):
    return abs(a - b) <= tol


# ── Caso 1: panel simple sin taladros ────────────────────────────────────────
def caso_panel_simple():
    print('Caso 1: panel simple sin taladros (600×400×18)')
    panel = cq.Workplane('XY').box(600, 400, 18, centered=(False, False, False))
    r = analizar(panel)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('largo 600', aprox(r['largo'], 600), f"{r['largo']}")
    check('ancho 400', aprox(r['ancho'], 400), f"{r['ancho']}")
    check('grosor 18', aprox(r['grosor'], 18, 0.3), f"{r['grosor']}")
    check('sin taladros', len(r['taladros']) == 0, f"{r['taladros']}")
    check('sin advertencias', len(r['advertencias']) == 0, f"{r['advertencias']}")


# ── Caso: inglete a 45° — sierra vs. forma fresable ─────────────────────────
def caso_inglete_grosor():
    print('Caso: inglete 45° — se distingue el de sierra del fresable')
    from analisis_panel import _advertencias_panel
    # Los dos casos llegan con el MISMO ángulo: lo único que los separa es
    # dónde está inclinado el corte. Antes salían con el mismo aviso y no
    # había forma de saber si al .bpp le faltaba trabajo o estaba bien vacío.
    sierra = _advertencias_panel(592, 60, 15, 45.0, 45.0, 'grosor', 'grosor')
    check('el inglete a través del grosor manda a la sierra',
          any('sierra' in a for a in sierra), f'{sierra}')
    check('y no se le llama "borde no rectangular"',
          not any('Borde no rectangular' in a for a in sierra), f'{sierra}')

    planta = _advertencias_panel(592, 60, 15, 45.0, 45.0, 'planta', 'planta')
    check('una forma en PLANTA sigue avisando de que hay que revisarla',
          any('Borde no rectangular' in a for a in planta), f'{planta}')
    check('y esa no dice sierra', not any('sierra' in a for a in planta), f'{planta}')

    recto = _advertencias_panel(592, 60, 15, 0.0, 0.0, 'recto', 'recto')
    check('un corte recto no avisa de nada', recto == [], f'{recto}')

    # Las 5 piezas que salían vacías Y MUDAS del trabajo del 06-08: sección no
    # constante, sin contorno y sin un solo mecanizado. El silencio se lee en
    # el taller como "no lleva nada", que es justo lo que no se sabe.
    muda = _advertencias_panel(100, 70, 19, 0.0, 0.0, 'recto', 'recto',
                               seccion_regular=False, sin_mecanizados=True)
    check('una pieza vacía con sección irregular YA NO sale muda',
          any('COMPRUÉBALA' in a for a in muda), f'{muda}')
    conmec = _advertencias_panel(100, 70, 19, 0.0, 0.0, 'recto', 'recto',
                                 seccion_regular=False, sin_mecanizados=False)
    check('si sí trae mecanizados, no molesta con ese aviso',
          not any('COMPRUÉBALA' in a for a in conmec), f'{conmec}')


# ── Caso 2: dos cazoletas de bisagra Ø35 prof. 13 ────────────────────────────
def caso_bisagras():
    print('Caso 2: panel con 2 cazoletas de bisagra Ø35 prof. 13')
    panel = (
        cq.Workplane('XY').box(600, 400, 18, centered=(False, False, False))
        .faces('>Z').workplane(origin=(0, 0, 0))
        .pushPoints([(37, 100), (37, 300)]).hole(35, depth=13)
    )
    r = analizar(panel)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    tal = sorted(r['taladros'], key=lambda t: t['y'])
    check('2 taladros Ø35', len(tal) == 2 and all(t['diametro'] == 35 for t in tal), f'{tal}')
    if len(tal) == 2:
        check('ninguno pasante', all(not t['pasante'] for t in tal), f'{tal}')
        check('profundidad ≈ 13 ambos', all(aprox(t['profundidad'], 13, 0.5) for t in tal),
              f"{[t['profundidad'] for t in tal]}")
        check('x=37 ambos', all(aprox(t['x'], 37) for t in tal), f"{[t['x'] for t in tal]}")
        check('y = 100 y 300', aprox(tal[0]['y'], 100) and aprox(tal[1]['y'], 300),
              f"{tal[0]['y']}, {tal[1]['y']}")
        check('cara frontal o trasera', all(t['cara'] in ('frontal', 'trasera') for t in tal), f'{tal}')


# ── Caso 3: línea Sistema 32 (Ø5, paso 32mm) ─────────────────────────────────
def caso_sistema32():
    print('Caso 3: panel con línea de taladros Sistema 32 (Ø5, paso 32mm)')
    panel = (
        cq.Workplane('XY').box(600, 400, 18, centered=(False, False, False))
        .faces('>Z').workplane(origin=(0, 0, 0))
        .pushPoints([(37, 64), (37, 96), (37, 128)]).hole(5, depth=12)
    )
    r = analizar(panel)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    tal = sorted(r['taladros'], key=lambda t: t['y'])
    check('3 taladros Ø5', len(tal) == 3 and all(t['diametro'] == 5 for t in tal), f'{tal}')
    if len(tal) == 3:
        ys = [t['y'] for t in tal]
        check('paso de 32mm entre taladros', aprox(ys[1] - ys[0], 32) and aprox(ys[2] - ys[1], 32), f'{ys}')
        check('ninguno pasante', all(not t['pasante'] for t in tal), f'{tal}')
        check('profundidad ≈ 12', all(aprox(t['profundidad'], 12, 0.5) for t in tal),
              f"{[t['profundidad'] for t in tal]}")


# ── Caso 4: taladro de canto (dowel) en el lateral corto ─────────────────────
def caso_taladro_canto():
    print('Caso 4: taladro de canto (dowel Ø8 prof. 21) en el lateral corto')
    base = cq.Workplane('XY').box(600, 400, 18, centered=(False, False, False)).val()
    dowel = cq.Solid.makeCylinder(4, 21, cq.Vector(0, 200, 9), cq.Vector(1, 0, 0))
    panel = base.cut(dowel)
    r = analizar(panel)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    tal = r['taladros']
    check('1 taladro de canto', len(tal) == 1, f'{tal}')
    if len(tal) == 1:
        t = tal[0]
        check('cara lateral (canto corto, renombrada)', t['cara'] in ('lateral_iz', 'lateral_de'), f"{t['cara']}")
        check('diametro 8', t['diametro'] == 8, f"{t['diametro']}")
        check('profundidad ≈ 21', aprox(t['profundidad'], 21, 0.5), f"{t['profundidad']}")
        check('no pasante', not t['pasante'], f'{t}')


# ── Caso 5 (límite conocido): pletina fina de aluminio — NO se detecta ──────
# Probado y descartado un aviso por ancho/largo tras calibrar con 7 STEP
# reales (services/step-to-dxf, jul 2026): paneles de madera legítimos y
# estrechos daban ratios ancho/largo tan bajos como 0.033 (ver
# caso_panel_estrecho_real_sin_avisos) — geométricamente INDISTINGUIBLES de
# una pletina de aluminio con la misma proporción. Cualquier umbral sin falsos
# positivos sobre esos paneles reales tampoco cazaría esta pletina. Limitación
# aceptada (no se inventa un aviso poco fiable), no un descuido.
def caso_pletina_aluminio_no_se_detecta():
    print('Caso 5 (límite conocido): pletina fina de aluminio (2000×40×4) — NO se detecta')
    pletina = cq.Workplane('XY').box(2000, 40, 4, centered=(False, False, False))
    r = analizar(pletina)
    check('ok (no bloquea piezas límite)', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('sin advertencias (limitación conocida: indistinguible de un panel de madera estrecho real)',
          r['advertencias'] == [], f"{r['advertencias']}")


# ── Casos 9-15: discriminador panel-vs-barra endurecido con STEP reales ─────
# Calibrado contra 7 STEP reales de muebles de madera del taller (jul 2026,
# ~1200 sólidos entre todos) — no solo sintéticos. Ver el hallazgo completo
# en analisis_panel.py junto a UMBRAL_PANEL_GROSOR_ANCHO.

def caso_casi_cuadrado_sin_avisos():
    print('Caso 9: panel casi cuadrado (400×380×18) — sin advertencias')
    r = analizar(cq.Workplane('XY').box(400, 380, 18, centered=(False, False, False)))
    check('ok', r['ok'], r.get('motivo', ''))
    if r['ok']:
        check('sin advertencias', r['advertencias'] == [], f"{r['advertencias']}")


def caso_encimera_gruesa_sin_avisos():
    print('Caso 10: encimera gruesa (2400×600×40) — sin advertencias')
    r = analizar(cq.Workplane('XY').box(2400, 600, 40, centered=(False, False, False)))
    check('ok', r['ok'], r.get('motivo', ''))
    if r['ok']:
        check('sin advertencias (40mm de grosor es normal en una encimera de 600mm)',
              r['advertencias'] == [], f"{r['advertencias']}")


def caso_liston_estrecho_sin_avisos():
    print('Caso 11: listón/tapajuntas estrecho (150×100×18) — sin advertencias')
    # Antes de subir UMBRAL_PANEL_GROSOR_ANCHO de 0.15 a 0.3, este caso real
    # (relleno/tapajuntas de cocina, muy habitual) daba un falso aviso.
    r = analizar(cq.Workplane('XY').box(150, 100, 18, centered=(False, False, False)))
    check('ok', r['ok'], r.get('motivo', ''))
    if r['ok']:
        check('sin advertencias (100mm de ancho es estrecho pero normal en tablero)',
              r['advertencias'] == [], f"{r['advertencias']}")


def caso_perfil_cuadrado_avisa():
    print('Caso 12 (negativo): perfil cuadrado de sección maciza (200×40×40) — sí avisa')
    # Sección cuadrada (grosor/ancho=1.0) — el aviso de grosor lo detecta.
    r = analizar(cq.Workplane('XY').box(200, 40, 40, centered=(False, False, False)))
    check('ok', r['ok'], r.get('motivo', ''))
    if r['ok']:
        check('advierte por grosor/ancho (sección maciza, no de tablero)',
              any('Grosor' in a for a in r['advertencias']), f"{r['advertencias']}")


def caso_panel_pequeño_cuadrado_sin_avisos():
    print('Caso 13: panel pequeño cuadrado (120×120×18) — sin advertencias')
    r = analizar(cq.Workplane('XY').box(120, 120, 18, centered=(False, False, False)))
    check('ok', r['ok'], r.get('motivo', ''))
    if r['ok']:
        check('sin advertencias (recorte pequeño pero de proporción normal)',
              r['advertencias'] == [], f"{r['advertencias']}")


def caso_liston_real_avisa():
    print('Caso 14 (dato real): listón/travesaño de madera (1940×40×30, taller jul 2026) — sí avisa')
    # Dimensión real observada en services/step-to-dxf/2876_6327.stp y
    # 2876_6329.stp (5 piezas idénticas cada uno) — grosor/ancho=0.75.
    r = analizar(cq.Workplane('XY').box(1940, 40, 30, centered=(False, False, False)))
    check('ok', r['ok'], r.get('motivo', ''))
    if r['ok']:
        check('advierte (sección casi cuadrada, típica de travesaño/pata, no de tablero)',
              any('Grosor' in a for a in r['advertencias']), f"{r['advertencias']}")


def caso_panel_estrecho_real_sin_avisos():
    print('Caso 15 (dato real): panel estrecho (3050×100×19, taller jul 2026) — sin advertencias')
    # Dimensión real observada en services/step-to-dxf/2876+0998.step (5
    # piezas idénticas) — ancho/largo=0.033 (bajísimo) pero grosor/ancho=0.19:
    # es un panel de tablero legítimo y estrecho, no una barra. Confirma por
    # qué se descartó el aviso por ancho/largo (ver UMBRAL_PANEL_GROSOR_ANCHO).
    r = analizar(cq.Workplane('XY').box(3050, 100, 19, centered=(False, False, False)))
    check('ok', r['ok'], r.get('motivo', ''))
    if r['ok']:
        check('sin advertencias (panel de tablero estrecho real, no una barra)',
              r['advertencias'] == [], f"{r['advertencias']}")


# ── Caso 6: cajeado cerrado (bolsillo rectangular) ───────────────────────────
def caso_cajeado_cerrado():
    print('Caso 6: cajeado cerrado (rebaje rectangular 100×80×5) — detectado, no como taladro')
    panel = (
        cq.Workplane('XY').box(600, 400, 18, centered=(False, False, False))
        .faces('>Z').workplane(origin=(0, 0, 0))
        .center(300, 200).rect(100, 80).cutBlind(-5)
    )
    r = analizar(panel)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('dimensiones del panel no afectadas por el cajeado',
          aprox(r['largo'], 600) and aprox(r['ancho'], 400) and aprox(r['grosor'], 18, 0.3),
          f"{r['largo']}, {r['ancho']}, {r['grosor']}")
    check('el cajeado NO se cuenta como taladro (contorno no circular)', len(r['taladros']) == 0, f"{r['taladros']}")
    check('exactamente 1 cajeado detectado (las 4 paredes laterales no se duplican)',
          len(r['cajeados']) == 1, f"{r['cajeados']}")
    if len(r['cajeados']) == 1:
        c = r['cajeados'][0]
        check('forma cajeado (100×80, ninguna dimensión < 15mm)', c['forma'] == 'cajeado', f'{c}')
        check('cara frontal', c['cara'] == 'frontal', f'{c}')
        check('posición esquina inferior-izq. x=250 y=160 (centro 300,200 menos mitades 50,40)',
              aprox(c['x'], 250) and aprox(c['y'], 160), f'{c}')
        check('dimensiones 100×80', aprox(c['largo'], 100) and aprox(c['ancho'], 80), f'{c}')
        check('profundidad ≈ 5', aprox(c['profundidad'], 5, 0.3), f'{c}')


# ── Caso 7: ranura estrecha cerrada → clasificada como 'ranura', no 'cajeado' ─
def caso_ranura_estrecha():
    print('Caso 7: ranura estrecha cerrada (200×8×5) clasificada como ranura, no cajeado')
    panel = (
        cq.Workplane('XY').box(600, 400, 18, centered=(False, False, False))
        .faces('>Z').workplane(origin=(0, 0, 0))
        .center(300, 200).rect(200, 8).cutBlind(-5)
    )
    r = analizar(panel)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('1 mecanizado detectado', len(r['cajeados']) == 1, f"{r['cajeados']}")
    if r['cajeados']:
        c = r['cajeados'][0]
        check("forma 'ranura' (dimensión menor 8mm < 15mm)", c['forma'] == 'ranura', f'{c}')
        check('dimensiones 200×8', aprox(c['largo'], 200) and aprox(c['ancho'], 8), f'{c}')


# ── Caso 8 (límite conocido): ranura ABIERTA a un canto — no se detecta ─────
# Su fondo toca directamente la cara exterior del canto (no hay pared de por
# medio en ese lado) — el mismo criterio que descarta paredes de bolsillo
# también descarta esto. Es un fallo SEGURO (se omite, no se inventa un dato
# erróneo que pueda acabar en un corte real) — ver docstring del módulo.
def caso_ranura_abierta_a_canto():
    print('Caso 8 (límite conocido): ranura abierta a un canto — NO se detecta (fallo seguro)')
    base = cq.Workplane('XY').box(600, 400, 18, centered=(False, False, False)).val()
    rebaje = cq.Solid.makeBox(150, 100, 5, cq.Vector(0, 150, 13))
    panel = base.cut(rebaje)
    r = analizar(panel)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('dimensiones del panel no afectadas', aprox(r['largo'], 600) and aprox(r['ancho'], 400),
          f"{r['largo']}, {r['ancho']}")
    check('la ranura abierta a canto NO se detecta (limitación conocida y documentada, no dato falso)',
          len(r['cajeados']) == 0, f"{r['cajeados']}")


# ── Caso 16 (dato real): trapecio — ahora sale con contorno REAL ─────────────
# Confirmado en un STEP real del taller (2876_6423): 3 de 13 piezas válidas de
# un mismo montaje traían un canto en ángulo (3.6°-11.1°). Desde la ruta de
# "panel con forma", estas piezas ya no se aproximan como rectángulo: el DXF
# lleva su contorno real y largo/ancho son la envolvente (avisada).
def caso_borde_angulado_avisa():
    print('Caso 16 (dato real): trapecio (recto + 45°) — contorno real, no rectángulo aproximado')
    panel = (
        cq.Workplane('XY')
        .polyline([(0, 0), (340, 0), (400, 60), (0, 60)]).close()
        .extrude(30)
    )
    r = analizar(panel)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('largo 400 (envolvente)', aprox(r['largo'], 400, 0.5), f"{r['largo']}")
    check('ancho 60, grosor 30', aprox(r['ancho'], 60) and aprox(r['grosor'], 30), f"{r['ancho']}, {r['grosor']}")
    check('contorno real de 4 puntos (trapecio, no rectángulo)',
          len(r.get('contorno', [])) == 4, f"{r.get('contorno')}")
    check('avisa de contorno no rectangular',
          any('NO rectangular' in a for a in r['advertencias']), f"{r['advertencias']}")


# ── Casos 17-19: paneles con forma (contorno real) ──────────────────────────

def caso_pentagono():
    print('Caso 17: pentágono (esquina cortada) — contorno real de 5 puntos + taladro bien situado')
    base = cq.Workplane('XY').box(600, 400, 18, centered=(False, False, False))
    corte = cq.Workplane('XY').polyline([(500, 400), (600, 300), (600, 400)]).close().extrude(18).val()
    pent = base.val().cut(corte)
    con_taladro = (cq.Workplane('XY').newObject([pent])
                   .faces('>Z').workplane(origin=(0, 0, 0)).pushPoints([(100, 100)]).hole(8, depth=10))
    r = analizar(con_taladro)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('envolvente 600×400×18 (no la diagonal)',
          aprox(r['largo'], 600) and aprox(r['ancho'], 400) and aprox(r['grosor'], 18, 0.3),
          f"{r['largo']}, {r['ancho']}, {r['grosor']}")
    contorno = r.get('contorno', [])
    check('contorno de exactamente 5 puntos (líneas exactas por vértice)', len(contorno) == 5, f'{contorno}')
    if len(contorno) == 5:
        # El vértice (500,400) y el (600,300) del corte deben estar en el contorno.
        tiene = lambda px, py: any(aprox(x, px) and aprox(y, py) for x, y in contorno)
        check('vértices del corte presentes (500,400) y (600,300)', tiene(500, 400) and tiene(600, 300), f'{contorno}')
    tal = r['taladros']
    check('1 taladro frontal en x=100 y=100 (mismo marco que el contorno)',
          len(tal) == 1 and tal[0]['cara'] in ('frontal', 'trasera')
          and aprox(tal[0]['x'], 100) and aprox(tal[0]['y'], 100), f'{tal}')


def caso_esquina_redondeada():
    print('Caso 18: esquina redondeada (fillet r50) — contorno curvo discretizado, envolvente correcta')
    panel = (cq.Workplane('XY').box(600, 400, 18, centered=(False, False, False))
             .edges('|Z and >X and >Y').fillet(50))
    r = analizar(panel)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    # Antes de la ruta de forma, la envolvente daba 616×422 (eje girado) — MAL.
    check('envolvente exacta 600×400×18', aprox(r['largo'], 600) and aprox(r['ancho'], 400)
          and aprox(r['grosor'], 18, 0.3), f"{r['largo']}, {r['ancho']}, {r['grosor']}")
    contorno = r.get('contorno', [])
    check('contorno con el arco discretizado (más de 8 puntos)', len(contorno) > 8, f'{len(contorno)} puntos')


def caso_rectangulo_sin_contorno():
    print('Caso 19: panel rectangular normal — NO lleva contorno (sigue la ruta de caja)')
    panel = (cq.Workplane('XY').box(600, 400, 18, centered=(False, False, False))
             .faces('>Z').workplane(origin=(0, 0, 0)).center(300, 200).rect(100, 80).cutBlind(-5))
    r = analizar(panel)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('sin campo contorno (ruta rectangular intacta)', 'contorno' not in r, f"{list(r.keys())}")
    check('el cajeado se sigue detectando por la ruta rectangular', len(r['cajeados']) == 1, f"{r['cajeados']}")


# ── Caso 17: el plano debe VERSE ────────────────────────────────────────────
# Todo el mecanizado (canales y fresados de la cara oculta) va en capas
# DISCONTINUAS. Con la escala de línea por defecto los trazos miden 1,3 mm y
# en un panel de 2450 mm el CAD no los pinta: el plano parecía salir sin
# mecanizar aunque las entidades estuvieran ahí.
def caso_escala_de_linea():
    print('Caso 17: la línea discontinua se ve a la escala de la pieza')
    import io as _io, re as _re
    from dxf_panel import generar_dxf_panel
    panel = (
        cq.Workplane('XY').box(2450, 1160, 30, centered=(False, False, False))
        .faces('>Z').workplane(origin=(0, 0, 0)).center(1200, 580).rect(200, 100).cutBlind(-8)
    )
    r = analizar(panel)
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    doc = generar_dxf_panel('prueba', r)
    buf = _io.StringIO(); doc.write(buf)
    m = _re.search(r'\$LTSCALE\s*\n\s*40\s*\n\s*([\d.]+)', buf.getvalue())
    check('el DXF fija $LTSCALE', m is not None, 'no aparece en la cabecera')
    if m:
        trazo = 1.27 * float(m.group(1))
        check(f'trazo visible en un panel de 2450mm ({trazo:.0f}mm, no 1.3mm)',
              trazo > 10, f'{trazo:.1f} mm')


# ── Caso 18: la canal de canto no se apoya sobre el contorno ────────────────
# Una canal de canto se dibujaba como una rayita a un offset decorativo del
# borde, así que en el CAD parecía ir "encima del canto" y no se podía medir.
# Es material que el disco se lleva ENTRANDO en el panel: en planta tiene que
# arrancar exactamente en el borde y meterse su profundidad real.
def caso_canal_entra_desde_el_borde():
    print('Caso 18: la canal de canto entra desde el borde, no se apoya en él')
    from dxf_panel import generar_dxf_panel
    L, A, P = 800.0, 400.0, 30.0
    # `puntos` = traza real del fondo en planta, tal como la da el análisis.
    r = {
        'ok': True, 'largo': L, 'ancho': A, 'grosor': P, 'taladros': [],
        'cajeados': [
            {'cara': 'inferior',   'x': 0.0, 'y': 10.0, 'largo': L, 'ancho': 10.0,
             'profundidad': 16.5, 'forma': 'ranura', 'en_canto': True,
             'puntos': [(0.0, 16.5), (L, 16.5)], 'normal': [0.0, -1.0]},
            {'cara': 'lateral_de', 'x': 0.0, 'y': 10.0, 'largo': A, 'ancho': 10.0,
             'profundidad': 16.5, 'forma': 'ranura', 'en_canto': True,
             'puntos': [(L - 16.5, 0.0), (L - 16.5, A)], 'normal': [1.0, 0.0]},
        ],
    }
    bandas = [[(round(x, 2), round(y, 2)) for x, y, *_ in e.get_points()]
              for e in generar_dxf_panel('prueba', r).modelspace().query('LWPOLYLINE[layer=="CANALES"]')]
    check('se dibuja una banda por canal', len(bandas) == 2, f'{len(bandas)}')
    if len(bandas) != 2:
        return
    inf, der = bandas
    # Abiertas: cerrarlas pondría un trazo discontinuo sobre la línea de corte.
    check('las bandas quedan abiertas hacia el canto',
          all(not e.closed for e in
              generar_dxf_panel('prueba', r).modelspace().query('LWPOLYLINE[layer=="CANALES"]')))
    # El fondo de la canal inferior va a 16.5 del borde y=0, no encima de él.
    check('el fondo de la canal inferior está a 16.5 del borde',
          all(abs(y - 16.5) < 0.01 for _, y in inf), f'{inf}')
    check('no se sale del panel', max(y for _, y in inf) < A, f'{inf}')
    # Y la lateral derecha, a 16.5 del borde x=L.
    check(f'el fondo de la lateral_de está a 16.5 del borde x={L:.0f}',
          all(abs(x - (L - 16.5)) < 0.01 for x, _ in der), f'{der}')
    # Conectores hasta la línea de corte por los dos extremos de cada canal.
    lineas = list(generar_dxf_panel('prueba', r).modelspace().query('LINE[layer=="CANALES"]'))
    check('cada canal se remata contra el corte por sus dos extremos',
          len(lineas) == 4, f'{len(lineas)} conectores')


# ── Caso 19: canal en un panel NO rectangular ───────────────────────────────
# El fallo que hacía que casi todas las piezas reales salieran sin mecanizar:
# los cantos se buscaban POR NOMBRE (superior/inferior/lateral_*), y un panel
# con un borde en ángulo no tiene esos cantos, así que se saltaba entero. En el
# montaje 2876_6426 eran 12 de 16 piezas.
def caso_canal_en_panel_con_forma():
    print('Caso 19: la canal se detecta también en un panel no rectangular')
    L, A, P = 800.0, 400.0, 30.0
    # Panel con una esquina cortada en diagonal + canal perimetral de 16.5x10.
    base = (cq.Workplane('XY')
            .polyline([(0, 0), (L, 0), (L, A - 150), (L - 200, A), (0, A)]).close()
            .extrude(P))
    canal = (cq.Workplane('XY').workplane(offset=10.0)
             .rect(L * 2, A * 2, centered=True).extrude(10.0)
             .cut(cq.Workplane('XY').workplane(offset=10.0)
                  .polyline([(16.5, 16.5), (L - 16.5, 16.5), (L - 16.5, A - 150),
                             (L - 200, A - 16.5), (16.5, A - 16.5)]).close().extrude(10.0)))
    r = analizar(base.cut(canal))
    check('ok', r['ok'], r.get('motivo', ''))
    if not r['ok']:
        return
    check('el panel sale con contorno propio', bool(r.get('contorno')), 'sin contorno')
    canales = [c for c in r.get('cajeados', []) if c.get('en_canto') and c['forma'] == 'ranura']
    check('detecta canal en el panel con forma', len(canales) >= 3, f'{len(canales)} canales')
    if not canales:
        return
    # Los cuatro cantos rectos van a 16.5 exactos. El de la diagonal NO: el
    # polígono interior de esta pieza de prueba se construye desplazando los
    # vértices, que en un borde oblicuo no da un paralelo — ahí la canal mide
    # menos de verdad, y el análisis debe decir lo que hay, no 16.5.
    rectas = [c for c in canales if not c['oblicuo']]
    check('los cantos rectos dan la profundidad exacta (16.5)',
          len(rectas) >= 4 and all(abs(c['profundidad'] - 16.5) < 0.1 for c in rectas),
          str([c['profundidad'] for c in rectas]))
    check('ninguna profundidad es mayor que el retranqueo pedido',
          all(0 < c['profundidad'] <= 16.6 for c in canales),
          str([c['profundidad'] for c in canales]))
    check('y con traza en planta para poder dibujarla',
          all(len(c.get('puntos', [])) >= 2 for c in canales))
    # Ninguna canal puede quedarse en 'frontal': el lado TS lo tomaría por un
    # fresado de CARA y lo metería en el .bpp — estropearía la pieza.
    check('ninguna canal se etiqueta como cara ancha',
          all(c['cara'] not in ('frontal', 'trasera') for c in canales),
          str([c['cara'] for c in canales]))



# ── Caso 20: el plano de panel no lleva texto ni cotas ──────────────────────
# El taller no necesita explicaciones en el DXF, solo la geometría de corte.
def caso_plano_de_panel_sin_texto():
    print('Caso 20: el plano de panel (dxf_panel.py) no lleva texto ni cotas')
    from dxf_panel import generar_dxf_panel
    r = {
        'ok': True, 'largo': 800.0, 'ancho': 400.0, 'grosor': 19.0,
        'taladros': [
            {'cara': 'frontal', 'x': 100.0, 'y': 100.0, 'diametro': 35.0, 'profundidad': 13.0, 'pasante': False},
        ],
        'cajeados': [
            {'cara': 'frontal', 'x': 50.0, 'y': 50.0, 'largo': 120.0, 'ancho': 60.0,
             'profundidad': 12.0, 'forma': 'cajeado', 'en_canto': False},
        ],
        'advertencias': [],
    }
    doc = generar_dxf_panel('prueba', r)
    msp = doc.modelspace()
    check('no genera ninguna entidad TEXT', len(list(msp.query('TEXT'))) == 0)
    check('no genera ninguna entidad MTEXT', len(list(msp.query('MTEXT'))) == 0)
    check('no genera ninguna cota (DIMENSION)', len(list(msp.query('DIMENSION'))) == 0)
    check('sigue dibujando el taladro como círculo', len(list(msp.query('CIRCLE'))) == 1)
    check('sigue dibujando el cajeado como rectángulo (LWPOLYLINE)',
          len(list(msp.query('LWPOLYLINE[layer=="FRESADO"]'))) == 1)


# ── Galce de canto abierto a una cara ancha (pieza real 1232.5×900×26.6) ────
# El galce deja huella en el wire exterior de la cara ancha a la que abre. Si
# esa cara es justo la que decide forma-vs-rectangular, el panel se trataba
# como "con forma", el contorno seguía el escalón del galce y el galce
# desaparecía del análisis (0 cajeados), del DXF y del BPP. Una forma real
# atraviesa el grosor y marca las DOS caras anchas; el galce, solo una.
def _panel_con_galce(abierto_a_z_alta):
    L, A, G = 1232.5, 900.0, 26.6
    panel = cq.Workplane('XY').box(L, A, G, centered=(False, False, False))
    z0 = (G - 18.5) if abierto_a_z_alta else -0.5
    galce = (cq.Workplane('XY')
             .transformed(offset=(300, -0.5, z0))
             .box(633, 16.5 + 0.5, 18.5 + 0.5, centered=(False, False, False)))
    return panel.cut(galce)


def caso_galce_abierto_a_cada_cara():
    print('Caso: galce de canto 633×16.5×18.5 — se detecta abra a la cara que abra')
    for lado, alta in (('cara z alta', True), ('cara z baja', False)):
        r = analizar(_panel_con_galce(alta))
        check(f'[{lado}] ok', r['ok'], r.get('motivo', ''))
        if not r['ok']:
            continue
        # El panel es rectangular: el galce NO debe convertirse en "forma".
        check(f'[{lado}] sin contorno de forma', r.get('contorno') is None,
              f"contorno de {len(r.get('contorno') or [])} puntos")
        check(f'[{lado}] sin advertencias', len(r['advertencias']) == 0, f"{r['advertencias']}")
        galces = [c for c in r['cajeados'] if c.get('en_canto') and c['cara'] == 'inferior']
        check(f'[{lado}] galce detectado en el canto inferior', len(galces) == 1, f"{r['cajeados']}")
        if galces:
            g = galces[0]
            # La traza recta proyecta un único segmento (abierta): el largo es
            # el real, no la mitad como cuando la traza va y vuelve.
            check(f'[{lado}] largo 633', aprox(g['largo'], 633, 1.0), f"{g['largo']}")
            check(f'[{lado}] ancho 18.5', aprox(g['ancho'], 18.5, 0.6), f"{g['ancho']}")
            check(f'[{lado}] profundidad 16.5', aprox(g['profundidad'], 16.5, 0.6), f"{g['profundidad']}")
            check(f'[{lado}] abierto a una cara ancha', g['abierto_a'] in ('frontal', 'trasera'),
                  f"{g['abierto_a']}")


def caso_hueco_pasante_interior():
    """Panel con un hueco PASANTE rectangular en medio (paso de cables).

    Antes no aparecía en NINGUNA salida: un hueco pasante no tiene cara-fondo,
    así que el detector de cajeados no lo veía, y los wires interiores de la
    cara no se leían nunca. La pieza salía a máquina sin el hueco.
    """
    print('CASO: hueco pasante interior')
    panel = (cq.Workplane('XY').box(600, 400, 16)
             .faces('>Z').workplane().rect(120, 80).cutThruAll())
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'hueco.step')
        cq.exporters.export(panel, ruta)
        solido = cq.importers.importStep(ruta).solids().val()
    r = analizar_solido_panel(solido)
    check('[hueco] el panel se analiza', r['ok'], str(r.get('motivo')))
    huecos = r.get('huecos') or []
    check('[hueco] detecta 1 hueco interior', len(huecos) == 1, f'huecos={len(huecos)}')
    if huecos:
        xs = [p[0] for p in huecos[0]]
        ys = [p[1] for p in huecos[0]]
        largo_h = round(max(xs) - min(xs))
        ancho_h = round(max(ys) - min(ys))
        check('[hueco] mide 120x80', (largo_h, ancho_h) == (120, 80), f'{largo_h}x{ancho_h}')

    liso = cq.Workplane('XY').box(600, 400, 16)
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'liso.step')
        cq.exporters.export(liso, ruta)
        solido2 = cq.importers.importStep(ruta).solids().val()
    r2 = analizar_solido_panel(solido2)
    check('[hueco] un panel liso no inventa huecos', len(r2.get('huecos') or []) == 0,
          str(r2.get('huecos')))


def caso_cara_buena_siempre_la_mecanizada():
    """La cara de referencia es SIEMPRE la que lleva el mecanizado.

    Antes el frente se elegía por un criterio de signo geométrico: la misma
    pieza, según cómo viniera girada en el STEP, salía unas veces con los
    taladros en 'frontal' y otras en 'trasera'. El operario no sabía por qué
    lado ponerla en la máquina.
    """
    print('CASO: cara buena = la mecanizada')
    # Cazoletas de bisagra en UNA sola cara, y la pieza girada 180° para que
    # esa cara caiga del lado "malo" del criterio geométrico anterior.
    def con_cazoletas(girado):
        w = cq.Workplane('XY').box(600, 400, 16)
        w = (w.faces('<Z').workplane().pushPoints([(-200, 0), (200, 0)])
              .hole(35, 13))
        if girado:
            w = w.rotate((0, 0, 0), (0, 1, 0), 180)
        return w

    resultados = []
    for girado in (False, True):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, f'caz{int(girado)}.step')
            cq.exporters.export(con_cazoletas(girado), ruta)
            solido = cq.importers.importStep(ruta).solids().val()
        r = analizar_solido_panel(solido)
        check(f'[cara buena] analiza (girado={girado})', r['ok'], str(r.get('motivo')))
        caras = {t.get('cara') for t in r.get('taladros') or [] if t.get('cara')}
        resultados.append(caras)

    # En los dos casos el mecanizado tiene que quedar en la MISMA cara.
    check('[cara buena] la pieza girada da la misma cara que sin girar',
          resultados[0] == resultados[1], f'{resultados[0]} vs {resultados[1]}')
    check('[cara buena] y esa cara es la frontal (la de referencia)',
          resultados[0] == {'frontal'}, str(resultados[0]))


def caso_cajeado_en_L_no_se_aplana():
    """Un bolsillo en L sale con su forma real, no como un rectángulo.

    Antes solo se guardaba la caja envolvente (x/y/largo/ancho), así que un
    bolsillo en L, redondeado o con arcos se fresaba entero: se comía material
    que tenía que quedarse.
    """
    print('CASO: cajeado en L con su contorno real')
    # Bolsillo en L de 10 mm de profundidad en la cara de arriba.
    panel = cq.Workplane('XY').box(600, 400, 18)
    panel = (panel.faces('>Z').workplane()
             .moveTo(-100, -50).lineTo(100, -50).lineTo(100, 0)
             .lineTo(0, 0).lineTo(0, 50).lineTo(-100, 50).close()
             .cutBlind(-10))
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'ele.step')
        cq.exporters.export(panel, ruta)
        solido = cq.importers.importStep(ruta).solids().val()
    r = analizar_solido_panel(solido)
    check('[cajeado L] analiza', r['ok'], str(r.get('motivo')))
    cajeados = [c for c in r.get('cajeados') or [] if not c.get('en_canto')]
    check('[cajeado L] detecta el bolsillo', len(cajeados) >= 1, f'{len(cajeados)}')
    if cajeados:
        c = cajeados[0]
        pts = c.get('puntos')
        check('[cajeado L] lleva el contorno real (6 vértices, no un rectángulo)',
              bool(pts) and len(pts) >= 5, f'puntos={len(pts) if pts else 0}')


def caso_taladro_de_canto_en_panel_con_forma():
    """Un taladro en el canto de un panel con FORMA no desaparece.

    Antes solo se exploraban las dos caras anchas: el taladro se quedaba sin
    cara ('cara': None, sin x/y) y no salía ni en el DXF ni en el BPP ni en
    pantalla. Ahora se le asigna un canto y su posición en el plano de la
    pieza, para poder marcarlo (se hace a mano, no entra al BPP).
    """
    print('CASO: taladro de canto en panel con forma')
    # Panel pentagonal (contorno NO rectangular) con un taladro horizontal
    # entrando por el canto.
    panel = (cq.Workplane('XY')
             .polyline([(0, 0), (600, 0), (600, 300), (300, 400), (0, 300)]).close()
             .extrude(18))
    panel = (panel.faces('<Y').workplane(centerOption='CenterOfBoundBox')
             .center(0, 0).hole(8, 30))
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'canto.step')
        cq.exporters.export(panel, ruta)
        solido = cq.importers.importStep(ruta).solids().val()
    r = analizar_solido_panel(solido)
    check('[canto forma] analiza', r['ok'], str(r.get('motivo')))
    tal = r.get('taladros') or []
    check('[canto forma] detecta el taladro', len(tal) >= 1, f'{len(tal)}')
    situados = [t for t in tal if t.get('x') is not None]
    check('[canto forma] queda situado (antes se perdía sin x/y)',
          len(situados) >= 1, str(tal))
    if situados:
        check('[canto forma] se le asigna un canto, no una cara ancha',
              str(situados[0].get('cara') or '').startswith('canto_'),
              str(situados[0].get('cara')))


if __name__ == '__main__':
    for caso in (caso_panel_simple, caso_inglete_grosor, caso_bisagras, caso_sistema32, caso_taladro_canto,
                 caso_pletina_aluminio_no_se_detecta, caso_cajeado_cerrado, caso_ranura_estrecha,
                 caso_ranura_abierta_a_canto, caso_casi_cuadrado_sin_avisos,
                 caso_encimera_gruesa_sin_avisos, caso_liston_estrecho_sin_avisos,
                 caso_perfil_cuadrado_avisa, caso_panel_pequeño_cuadrado_sin_avisos,
                 caso_liston_real_avisa, caso_panel_estrecho_real_sin_avisos,
                 caso_borde_angulado_avisa, caso_pentagono, caso_esquina_redondeada,
                 caso_rectangulo_sin_contorno, caso_escala_de_linea,
                 caso_canal_entra_desde_el_borde, caso_canal_en_panel_con_forma,
                 caso_plano_de_panel_sin_texto, caso_galce_abierto_a_cada_cara,
                 caso_hueco_pasante_interior, caso_cara_buena_siempre_la_mecanizada,
                 caso_cajeado_en_L_no_se_aplana, caso_taladro_de_canto_en_panel_con_forma):
        caso()
        print()
    if FALLOS:
        print(f'✗ {len(FALLOS)} comprobaciones fallidas:')
        for f in FALLOS:
            print(f'  - {f}')
        sys.exit(1)
    print('✓ Todos los casos pasan')

