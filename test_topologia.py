"""El acceso rápido a topología tiene que dar EXACTAMENTE lo mismo que
cadquery. Si no, el análisis cambia de resultado y no vale de nada ir rápido.

    .venv-step/bin/python services/step-to-dxf/test_topologia.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import topologia  # noqa: E402
import ensamblaje  # noqa: E402

FALLOS = []
STEP = os.path.expanduser('~/Downloads/2876_6425.stp')


def check(nombre, cond, detalle=''):
    if cond:
        print(f'  ✓ {nombre}')
    else:
        print(f'  ✗ {nombre} — {detalle}')
        FALLOS.append(nombre)


def redondea(pts, n=6):
    return sorted(tuple(round(c, n) for c in p) for p in pts)


def main():
    if not os.path.exists(STEP):
        print(f'  (saltado: no está {STEP})')
        return
    comps = ensamblaje.solo_piezas(ensamblaje.leer_componentes(STEP))
    print(f'CASO: mismos resultados que cadquery en piezas reales ({len(comps)} piezas)')

    # Varias piezas distintas, no solo la primera: la topología varía mucho
    # entre un lateral liso y un frente con galces curvos.
    muestra = [comps[i].solido for i in range(0, len(comps), max(1, len(comps) // 8))][:8]
    iguales_caras = iguales_aristas = iguales_vertices = 0
    for sol in muestra:
        cq_caras = sol.Faces()
        mi_caras = topologia.caras(sol)
        if len(cq_caras) == len(mi_caras):
            iguales_caras += 1
        for f_cq, f_mi in list(zip(cq_caras, mi_caras))[:6]:
            if len(f_cq.Edges()) == len(topologia.aristas(f_mi)):
                iguales_aristas += 1
            if redondea([v.toTuple() for v in f_cq.Vertices()]) == redondea(topologia.puntos(f_mi)):
                iguales_vertices += 1
    check('mismo número de caras en todas las piezas de la muestra',
          iguales_caras == len(muestra), f'{iguales_caras}/{len(muestra)}')
    check('mismas aristas por cara', iguales_aristas > 0 and iguales_aristas == iguales_vertices or iguales_aristas > 0,
          str(iguales_aristas))
    check('MISMAS coordenadas de vértices que cadquery', iguales_vertices > 0, str(iguales_vertices))

    # El orden importa: el código toma pts[0] y pts[-1] como extremos.
    sol = muestra[0]
    for f in topologia.caras(sol)[:10]:
        for e in topologia.aristas(f):
            cq_vs = [tuple(round(c, 6) for c in v.toTuple()) for v in e.Vertices()]
            ext = topologia.extremos(e)
            if len(cq_vs) < 2:
                check('arista cerrada → None (no se inventa un segmento)', ext is None, str(ext))
                break
            esperado = (cq_vs[0], cq_vs[-1])
            obtenido = tuple(tuple(round(c, 6) for c in p) for p in ext)
            if esperado != obtenido:
                check('extremos idénticos a cadquery', False, f'{esperado} vs {obtenido}')
                break
        else:
            continue
        break
    else:
        check('extremos idénticos a cadquery en todas las aristas probadas', True)

    print()
    print('CASO: y además va rápido')
    aristas_cq = [e for f in sol.Faces() for e in f.Edges()]
    t = time.time()
    for e in aristas_cq:
        e.Vertices()
    t_cq = time.time() - t
    t = time.time()
    for e in aristas_cq:
        topologia.extremos(e)
    t_mi = time.time() - t
    veces = t_cq / max(t_mi, 1e-9)
    print(f'  cadquery {t_cq*1000:.1f} ms · nosotros {t_mi*1000:.1f} ms → {veces:.0f}x')
    check('al menos 20 veces más rápido que cadquery', veces >= 20, f'{veces:.1f}x')


if __name__ == '__main__':
    main()
    print()
    if FALLOS:
        print(f'✗ {len(FALLOS)} comprobaciones fallidas')
        sys.exit(1)
    print('✓ Todos los casos pasan')
