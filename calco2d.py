"""Calco 2D de emergencia: la silueta de la pieza, sin reconocer nada.

El analizador inteligente (analisis_panel.py) mide la pieza y reconoce sus
taladros, cajeados y canales. Cuando la geometría es rara, se planta y la
pieza acaba en omitidas.txt: el taller se queda SIN PLANO, que es el peor
resultado posible — peor que un plano imperfecto.

Este módulo es la red: proyecta las aristas del sólido sobre su plano y
devuelve el contorno tal cual, como hace cualquier exportador comercial. No
sabe qué es un taladro ni una ranura, y no genera operaciones de máquina;
solo garantiza que SIEMPRE haya un dibujo a escala 1:1 del que partir.

Se usa de dos formas:
  · A propósito, cuando el operario solo quiere el calco (forzar_2d).
  · Como salvavidas automático, cuando el análisis inteligente falla.
"""
from __future__ import annotations

import math

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

from cadquery.occ_impl.shapes import Shape

# Paso de discretización de las curvas al pasarlas a polilínea (mm). El mismo
# criterio que el resto del servicio: fino para que un arco no se note
# poligonal en la máquina, sin llenar el DXF de puntos.
PASO_CURVA = 0.2
# Tramos mínimos de una curva, aunque sea corta.
TRAMOS_MIN = 8


def _direccion_de_vista(solido):
    """Por dónde se mira la pieza para calcar: perpendicular a su cara ancha.

    Se toma la normal de la cara PLANA de mayor área, que en un panel es
    siempre una de las dos caras grandes. Si no hay ninguna cara plana (una
    pieza enteramente curva), se cae a la dimensión más pequeña de la caja
    envolvente, que es el grosor.
    """
    mejor = None
    for f in solido.Faces():
        ad = BRepAdaptor_Surface(f.wrapped)
        if ad.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
            continue
        area = f.Area()
        if mejor is None or area > mejor[0]:
            n = ad.Plane().Axis().Direction()
            mejor = (area, (n.X(), n.Y(), n.Z()))
    if mejor is not None:
        return mejor[1]

    bb = solido.BoundingBox()
    dims = [(bb.xlen, (1.0, 0.0, 0.0)), (bb.ylen, (0.0, 1.0, 0.0)), (bb.zlen, (0.0, 0.0, 1.0))]
    dims.sort(key=lambda d: d[0])
    return dims[0][1]


def _discretizar(edge) -> list[tuple[float, float]]:
    """Arista → lista de puntos 2D (ya proyectados, así que la z se ignora).

    Se usa BRepAdaptor_Curve y no BRep_Tool.Curve_s porque el adaptador vale
    para CUALQUIER tipo de arista (recta, arco, spline, curva sobre
    superficie) con la misma llamada, y porque la firma de Curve_s cambia
    entre versiones de OCP — con la de aquí devuelve solo la curva y el
    desempaquetado reventaba en silencio, dejando el calco vacío.
    """
    try:
        ad = BRepAdaptor_Curve(edge.wrapped)
        u0, u1 = ad.FirstParameter(), ad.LastParameter()
    except Exception:
        return []
    if not (u1 > u0):
        return []
    try:
        p0, p1 = ad.Value(u0), ad.Value(u1)
        largo = math.dist((p0.X(), p0.Y()), (p1.X(), p1.Y()))
    except Exception:
        largo = 0.0
    # Una recta no necesita puntos intermedios; una curva, uno cada PASO_CURVA.
    from OCP.GeomAbs import GeomAbs_CurveType
    es_recta = False
    try:
        es_recta = ad.GetType() == GeomAbs_CurveType.GeomAbs_Line
    except Exception:
        pass
    tramos = 1 if es_recta else max(TRAMOS_MIN, int(largo / PASO_CURVA) + 1)
    puntos = []
    for i in range(tramos + 1):
        u = u0 + (u1 - u0) * (i / tramos)
        try:
            p = ad.Value(u)
        except Exception:
            continue
        puntos.append((p.X(), p.Y()))
    return puntos


def calcar_solido(solido, incluir_ocultas: bool = False):
    """Calco 2D del sólido. Devuelve el mismo dict que analizar_solido_panel,
    con 'calco': True y sin mecanizados, o {'ok': False, 'motivo'}.

    `polilineas` lleva los trazos ya en coordenadas locales de la pieza (su
    esquina inferior izquierda en 0,0), listos para dibujar 1:1.
    """
    try:
        dx, dy, dz = _direccion_de_vista(solido)
        origen = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(dx, dy, dz))
        proyector = HLRAlgo_Projector(origen)

        algo = HLRBRep_Algo()
        algo.Add(solido.wrapped)
        algo.Projector(proyector)
        algo.Update()
        algo.Hide()

        aristas = HLRBRep_HLRToShape(algo)
        formas = [aristas.VCompound(), aristas.OutLineVCompound()]
        if incluir_ocultas:
            formas += [aristas.HCompound(), aristas.OutLineHCompound()]

        polilineas: list[list[tuple[float, float]]] = []
        for forma in formas:
            # El HLR devuelve una forma NULA (no None) cuando ese grupo de
            # aristas está vacío — p.ej. una pieza sin siluetas curvas. Hay
            # que comprobarlo antes de convertirla, o revienta con
            # "Null TopoDS_Shape" y se pierde el calco entero.
            if forma is None or forma.IsNull():
                continue
            for e in Shape.cast(forma).Edges():
                pts = _discretizar(e)
                if len(pts) >= 2:
                    polilineas.append(pts)
    except Exception as e:
        return {'ok': False, 'motivo': f'no se pudo calcar la pieza: {type(e).__name__}: {e}'}

    if not polilineas:
        return {'ok': False, 'motivo': 'el calco 2D no ha encontrado ninguna arista'}

    xs = [p[0] for pl in polilineas for p in pl]
    ys = [p[1] for pl in polilineas for p in pl]
    x0, y0 = min(xs), min(ys)
    largo = round(max(xs) - x0, 2)
    ancho = round(max(ys) - y0, 2)
    # A la esquina, como el resto de piezas: el DXF va 1:1 en el origen.
    polilineas = [[(round(x - x0, 2), round(y - y0, 2)) for x, y in pl] for pl in polilineas]

    bb = solido.BoundingBox()
    grosor = round(min(bb.xlen, bb.ylen, bb.zlen), 2)

    return {
        'ok': True,
        'calco': True,
        'largo': largo,
        'ancho': ancho,
        'grosor': grosor,
        'seccion_regular': False,
        'polilineas': polilineas,
        'taladros': [],
        'taladros_descartados': 0,
        'cajeados': [],
        'huecos': [],
        'angulo_corte_a': 0.0, 'angulo_corte_b': 0.0,
        'plano_corte_a': 'recto', 'plano_corte_b': 'recto',
        'advertencias': [
            'CALCO 2D: solo el dibujo de la pieza. NO lleva mecanizados '
            'reconocidos y su .bpp sale sin operaciones — los taladros, '
            'cajeados y ranuras hay que hacerlos a mano.',
        ],
    }
