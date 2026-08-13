"""El STL del conjunto tiene que salir CORRECTO y sin comerse la memoria.

    .venv-step/bin/python services/step-to-dxf/test_stl_conjunto.py
"""
import os
import struct
import sys
import resource
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cadquery as cq  # noqa: E402
from stl_conjunto import escribir_stl_conjunto  # noqa: E402

FALLOS = []
STEP = os.path.expanduser('~/Downloads/2876_6425.stp')


def check(nombre, cond, detalle=''):
    if cond:
        print(f'  ✓ {nombre}')
    else:
        print(f'  ✗ {nombre} — {detalle}')
        FALLOS.append(nombre)


def rss():
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / (1024 * 1024) if v > 10 ** 7 else v / 1024


def leer_stl(ruta):
    """Devuelve (nº triángulos declarado, lista de vértices)."""
    with open(ruta, 'rb') as f:
        f.read(80)
        n = int.from_bytes(f.read(4), 'little')
        vs = []
        for _ in range(n):
            d = struct.unpack('<12fH', f.read(50))
            vs += [d[3:6], d[6:9], d[9:12]]
    return n, vs


def caso_cubo():
    print('CASO: un cubo conocido')
    cubo = cq.Workplane().box(100, 50, 20).val()
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'c.stl')
        n = escribir_stl_conjunto([cubo], ruta)
        declarado, vs = leer_stl(ruta)
        tam = os.path.getsize(ruta)
    check('escribe triángulos', n > 0, str(n))
    check('la cabecera declara los mismos que hay', declarado == n, f'{declarado} vs {n}')
    check('el tamaño cuadra con el formato binario (84 + 50·n)', tam == 84 + 50 * n, str(tam))
    check('12 triángulos para una caja (2 por cara)', n == 12, str(n))
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]; zs = [v[2] for v in vs]
    check('mide 100 x 50 x 20 de verdad',
          abs((max(xs)-min(xs)) - 100) < 0.2 and abs((max(ys)-min(ys)) - 50) < 0.2
          and abs((max(zs)-min(zs)) - 20) < 0.2,
          f'{max(xs)-min(xs):.1f} x {max(ys)-min(ys):.1f} x {max(zs)-min(zs):.1f}')


def caso_normales():
    print('CASO: normales bien puestas (si no, el visor lo pinta negro)')
    cubo = cq.Workplane().box(10, 10, 10).val()
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'c.stl')
        escribir_stl_conjunto([cubo], ruta)
        with open(ruta, 'rb') as f:
            f.read(84)
            normales = []
            for _ in range(12):
                dd = struct.unpack('<12fH', f.read(50))
                normales.append(dd[0:3])
    largos = [(n[0]**2 + n[1]**2 + n[2]**2) ** 0.5 for n in normales]
    check('todas las normales son unitarias', all(abs(l - 1) < 1e-4 for l in largos),
          f'min {min(largos):.3f} max {max(largos):.3f}')
    # Un cubo tiene 6 direcciones distintas, dos triángulos cada una.
    distintas = {tuple(round(c) for c in n) for n in normales}
    check('apuntan a las 6 caras del cubo', len(distintas) == 6, str(sorted(distintas)))


def caso_mueble_real():
    if not os.path.exists(STEP):
        print(f'CASO: mueble real — saltado (no está {STEP})')
        return
    print('CASO: la cocina entera sin reventar la memoria')
    resultado = cq.importers.importStep(STEP)
    solidos = resultado.solids().vals()
    antes = rss()
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, 'conjunto.stl')
        n = escribir_stl_conjunto(solidos, ruta)
        pico = rss() - antes
        declarado, vs = leer_stl(ruta)
        mb = os.path.getsize(ruta) / 1e6
    print(f'  {len(solidos)} sólidos · {n} triángulos · {mb:.1f} MB · pico +{pico:.0f} MB')
    check('tesela la cocina entera', n > 1000, str(n))
    check('la cabecera cuadra', declarado == n)
    # El motivo de existir de este módulo: la llamada de cadquery pedía +293 MB
    # y el contenedor de Render solo tiene 512.
    check('el pico se queda MUY por debajo de los +293 MB de antes', pico < 60, f'+{pico:.0f} MB')
    # Y que salga el mueble entero, no una pieza suelta.
    bb = resultado.val().BoundingBox()
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    check('abarca todo el conjunto, no una parte',
          abs((max(xs) - min(xs)) - (bb.xmax - bb.xmin)) < 5
          and abs((max(ys) - min(ys)) - (bb.ymax - bb.ymin)) < 5,
          f'STL {max(xs)-min(xs):.0f}x{max(ys)-min(ys):.0f} vs STEP {bb.xmax-bb.xmin:.0f}x{bb.ymax-bb.ymin:.0f}')


if __name__ == '__main__':
    for c in (caso_cubo, caso_normales, caso_mueble_real):
        c()
        print()
    if FALLOS:
        print(f'✗ {len(FALLOS)} comprobaciones fallidas')
        sys.exit(1)
    print('✓ Todos los casos pasan')
