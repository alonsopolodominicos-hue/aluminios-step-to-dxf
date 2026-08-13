"""STL del conjunto entero, escrito pieza a pieza para no quedarse sin memoria.

POR QUÉ NO VALE exporters.export(...)
------------------------------------
El ZIP de la conversión lleva un `conjunto_completo.stl` de referencia: el
mueble entero en 3D, sin separar. Se generaba con una sola llamada de cadquery
sobre el ensamblaje completo, y ahí está el problema: para escribir un fichero
de 1,6 MB, OpenCASCADE teselaba las 136 piezas A LA VEZ y guardaba la malla de
todas ellas colgada de la geometría en memoria.

Medido con 2876_6425 (una cocina real):

    exporters.export del conjunto ....... +293 MB de pico
    igual pero con tolerancia gruesa .... +195 MB
    pieza a pieza, soltando cada malla ... +12 MB   ← esto

El contenedor de Render tiene 512 MB y arranca ya en ~483 MB (cadquery y
OpenCASCADE ocupan eso solo con importarse). Un pico de +293 MB lo mata, Render
lo reinicia, y la conversión en curso muere con él. Desde el navegador se ve
como "el servicio de conversión no respondió a tiempo" — indistinguible de un
cuelgue, y encima ocurre AL FINAL, cuando ya estaba todo el trabajo hecho.

CÓMO
----
Se tesela una pieza, se escriben sus triángulos al fichero, se TIRA su malla
(`BRepTools.Clean_s`) y se pasa a la siguiente. El pico pasa a ser el de la
pieza más grande, no el de todas juntas. El fichero se escribe en binario
directamente a disco, así que tampoco se guarda entero en memoria.
"""
from __future__ import annotations

import struct

from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_ShapeEnum, TopAbs_Orientation
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS

# Desviación máxima entre la malla y la superficie real, en mm. Este STL es
# una VISTA de referencia, no geometría de fabricación (las medidas salen del
# DXF y del .bpp), así que 0,1 mm sobra: a simple vista un panel se ve igual
# de recto que con 0,001 mm, y la malla es muchísimo más ligera.
TOLERANCIA_MM = 0.1
# Ídem para la curvatura, en radianes (~17°).
TOLERANCIA_ANGULAR = 0.3

CABECERA = b'STL del conjunto - Aluminios Carinena'.ljust(80, b'\0')


def _normal(a, b, c):
    """Normal unitaria del triángulo (a, b, c), por producto vectorial.

    Muchos visores se fían del orden de los vértices y no de este vector, pero
    otros lo usan para iluminar; escribir ceros deja el modelo plano y negro
    en esos. Si el triángulo es degenerado (área cero, cosa que pasa en mallas
    de superficies muy curvas) se devuelve un vector nulo, que es lo que marca
    el formato para "no la sé".
    """
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    largo = (nx * nx + ny * ny + nz * nz) ** 0.5
    if largo < 1e-12:
        return 0.0, 0.0, 0.0
    return nx / largo, ny / largo, nz / largo


def _triangulos_de_solido(forma):
    """Tesela un sólido y va soltando sus triángulos ya en coordenadas del
    mundo. Al terminar, quita la malla de la geometría para no acumularla."""
    try:
        BRepMesh_IncrementalMesh(forma, TOLERANCIA_MM, False, TOLERANCIA_ANGULAR, True)
    except Exception:
        return
    try:
        explorador = TopExp_Explorer(forma, TopAbs_ShapeEnum.TopAbs_FACE)
        while explorador.More():
            cara = TopoDS.Face_s(explorador.Current())
            explorador.Next()
            sitio = TopLoc_Location()
            try:
                malla = BRep_Tool.Triangulation_s(cara, sitio)
            except Exception:
                continue
            if malla is None:
                continue
            trsf = sitio.Transformation()
            # Una cara con orientación invertida tiene sus triángulos al revés;
            # hay que darles la vuelta o el sólido se ve "del revés" en el visor.
            invertida = cara.Orientation() == TopAbs_Orientation.TopAbs_REVERSED
            for i in range(1, malla.NbTriangles() + 1):
                t = malla.Triangle(i)
                i1, i2, i3 = t.Value(1), t.Value(2), t.Value(3)
                if invertida:
                    i2, i3 = i3, i2
                try:
                    puntos = [malla.Node(k).Transformed(trsf) for k in (i1, i2, i3)]
                except Exception:
                    continue
                yield [(p.X(), p.Y(), p.Z()) for p in puntos]
    finally:
        # Imprescindible: sin esto la malla se queda pegada al sólido y el
        # ahorro de memoria desaparece.
        try:
            BRepTools.Clean_s(forma)
        except Exception:
            pass


def escribir_stl_conjunto(solidos, ruta: str) -> int:
    """Escribe el STL binario de todos los sólidos en `ruta`.

    Devuelve el número de triángulos escritos (0 si no se pudo teselar nada).
    El recuento va en la cabecera, y como no se sabe hasta el final, se deja
    hueco y se reescribe al cerrar — así no hace falta guardar los triángulos
    en memoria para contarlos.
    """
    total = 0
    with open(ruta, 'wb') as f:
        f.write(CABECERA)
        f.write((0).to_bytes(4, 'little'))   # se corrige al final
        for solido in solidos:
            forma = getattr(solido, 'wrapped', solido)
            for a, b, c in _triangulos_de_solido(forma):
                nx, ny, nz = _normal(a, b, c)
                f.write(struct.pack(
                    '<12fH',
                    nx, ny, nz,
                    a[0], a[1], a[2],
                    b[0], b[1], b[2],
                    c[0], c[1], c[2],
                    0,
                ))
                total += 1
        f.seek(80)
        f.write(total.to_bytes(4, 'little'))
    return total
