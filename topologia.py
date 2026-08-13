"""Acceso rápido a la topología (caras, aristas, vértices) de un sólido.

POR QUÉ EXISTE ESTE MÓDULO
--------------------------
cadquery ofrece `forma.Vertices()`, `forma.Edges()` y `forma.Faces()`, que es
lo natural. Por dentro cada una hace dos cosas:

    mapa = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(forma.wrapped, TIPO, mapa)     # rápido, en C++
    return [Envoltorio(x) for x in mapa]              # ← recorre el mapa

Ese recorrido `for x in mapa` usa el protocolo de iteración de Python sobre un
objeto C++ de OCP, y en la versión que usa el servicio es PATOLÓGICO. Medido
sobre una pieza real de 2876_6425 (45 caras, 258 aristas):

    e.Vertices() de cadquery ............. 639,5 ms   (2350 µs por arista)
    el mismo mapa, indexado con FindKey ..   0,4 ms   (    2 µs por arista)
                                            ─────────────────────────────
                                            1556 veces más lento

Y no es un caso raro: el análisis de un mueble entero llama a `Vertices()`
12.627 veces. En el perfil de 2876_6425 (136 piezas), `Vertices()` se comía
30,9 s y `Edges()` otros 6,7 s de los 46,5 s totales — el 81 % del tiempo se
iba en recorrer mapas, no en analizar nada.

Eso importa de verdad porque el servicio corre en el plan gratuito de Render,
que va unas 20 veces más lento que un portátil (medido con /salud?banco=1:
260 ms allí frente a 13,7 ms aquí en la misma cuenta). 46 s de trabajo se
convierten en varios minutos, y la conversión se pasaba del tiempo de espera
del navegador: el usuario veía "el servicio no respondió a tiempo".

QUÉ HACE
--------
Exactamente lo mismo que cadquery — el MISMO mapa, con el mismo criterio de
duplicados y el mismo orden — pero leyéndolo con `FindKey(i)`, que se queda
en C++. No cambia ningún resultado; solo el tiempo.

Las funciones devuelven objetos de cadquery (Face, Edge) para que el código
que las usa siga siendo el de siempre.
"""
from __future__ import annotations

from OCP.BRep import BRep_Tool
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_IndexedMapOfShape

from cadquery.occ_impl.shapes import Edge, Face, Vertex


def _mapa(forma, tipo):
    """Mapa indexado de subformas de `tipo`, como lista de TopoDS_Shape.

    `MapShapes_s` ya quita duplicados por identidad topológica (una arista
    compartida por dos caras aparece una sola vez), que es justo el criterio
    de cadquery. La diferencia está en el bucle: `FindKey(i)` es una llamada
    directa a C++, mientras que iterar el mapa a la manera de Python recorre
    el objeto entero en cada paso.
    """
    wrapped = getattr(forma, 'wrapped', forma)
    mapa = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(wrapped, tipo, mapa)
    return [mapa.FindKey(i) for i in range(1, mapa.Extent() + 1)]


def _como_edge(s):
    """TopoDS_Shape → TopoDS_Edge. El nombre del método estático cambia entre
    versiones de OCP (`Edge_s` en unas, `Edge` en otras), así que se prueban
    las dos en vez de atarse a una."""
    conversor = getattr(TopoDS, 'Edge_s', None) or TopoDS.Edge
    return conversor(s)


def caras(forma) -> list[Face]:
    """Caras de la forma. Equivalente a `forma.Faces()`."""
    return [Face(s) for s in _mapa(forma, TopAbs_ShapeEnum.TopAbs_FACE)]


def aristas(forma) -> list[Edge]:
    """Aristas de la forma. Equivalente a `forma.Edges()`.

    Se saltan las aristas DEGENERADAS igual que cadquery: son artefactos del
    modelo (por ejemplo el vértice-polo de una esfera, que topológicamente es
    una arista de longitud cero) y no representan ningún borde real.
    """
    salida = []
    for s in _mapa(forma, TopAbs_ShapeEnum.TopAbs_EDGE):
        try:
            if BRep_Tool.Degenerated_s(_como_edge(s)):
                continue
        except Exception:
            pass
        salida.append(Edge(s))
    return salida


def vertices(forma) -> list[Vertex]:
    """Vértices de la forma. Equivalente a `forma.Vertices()`."""
    return [Vertex(s) for s in _mapa(forma, TopAbs_ShapeEnum.TopAbs_VERTEX)]


def puntos(forma) -> list[tuple[float, float, float]]:
    """Vértices ya como coordenadas (x, y, z).

    Es lo que quiere casi todo el código: nadie usa el objeto Vertex, solo su
    posición. Saltarse el envoltorio ahorra construir miles de objetos Python
    que se tiran acto seguido.
    """
    salida = []
    for s in _mapa(forma, TopAbs_ShapeEnum.TopAbs_VERTEX):
        try:
            p = BRep_Tool.Pnt_s(TopoDS.Vertex_s(s) if hasattr(TopoDS, 'Vertex_s') else TopoDS.Vertex(s))
        except Exception:
            continue
        salida.append((p.X(), p.Y(), p.Z()))
    return salida


def extremos(arista) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """Los dos extremos de una arista, o None si no tiene dos.

    Devuelve None para una arista CERRADA (una circunferencia completa), que
    topológicamente tiene un único vértice: no define un segmento. El código
    que la llama depende de esa distinción para no inventarse segmentos de
    longitud cero al comparar aristas compartidas.
    """
    pts = puntos(arista)
    if len(pts) < 2:
        return None
    return pts[0], pts[-1]
