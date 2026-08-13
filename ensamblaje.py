"""Lectura del STEP como ENSAMBLAJE (no como montón de sólidos).

Un STEP de autokitchen/Solid Edge trae mucho más que geometría: cada cuerpo
lleva su NOMBRE de producto y el ensamblaje dice cuántas veces se coloca cada
uno. Hasta ahora se leía con `solids()`, que tira todo eso: los cuerpos salían
numerados por su orden (pieza_001, pieza_002…), las repeticiones se
convertían en piezas distintas y las HERRAMIENTAS de mecanizado acababan
tratadas como piezas.

Medido en 2876_7616 (el techo de 25 piezas):
  - 62 sólidos sueltos → 46 "piezas", de las que solo 26 eran distintas.
  - El ensamblaje real: 25 piezas P01..P25 (con P01 repetida 11 veces),
    13 cuerpos de otros materiales (PMMA/FLEX) y 2 herramientas
    ("TOOL - … - Ranura tablero recto/curvo") colocadas en varios sitios.

Los cuerpos "TOOL - …" NO son mecanizados: son geometría de construcción del
CAD (hasta 13,6 m de largo) y hay que EXCLUIRLOS. Las piezas del STEP ya
vienen recortadas — restarles esos cuerpos las destruye (comprobado: P01
pasaba de 203 a 123 mm de ancho).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFDoc import XCAFDoc_DocumentTool

from cadquery.occ_impl.shapes import Shape

# Un cuerpo cuyo nombre empieza por TOOL no es una pieza: es la herramienta con
# la que se mecaniza (una ranura, un galce). Convención de autokitchen.
PREFIJO_HERRAMIENTA = 'TOOL'
# Nombres que NO identifican una pieza: los pone el traductor de STEP, no el
# diseñador. Un STEP exportado por cadquery/OCC los lleva en todos los
# cuerpos, y tomarlos por nombres de pieza haría que todas se llamaran igual.
GENERICOS = re.compile(
    r'^(open[ _]cascade|step[ _]translator|product|shape|solid|compound|part|body)',
    re.IGNORECASE)


@dataclass
class Componente:
    """Un cuerpo colocado en el ensamblaje, con su nombre y su sitio."""
    nombre: str
    solido: object                 # cadquery Shape ya colocado en el mundo
    es_herramienta: bool = False
    material: str | None = None    # 'PMMA', 'FLEX'… si el nombre lo dice
    herramientas_aplicadas: list[str] = field(default_factory=list)

    @property
    def nombre_util(self) -> bool:
        """¿El nombre identifica de verdad a esta pieza?"""
        n = self.nombre.strip()
        return bool(n) and not GENERICOS.match(n)

    @property
    def nombre_limpio(self) -> str:
        """Nombre apto para fichero: sin el prefijo del proyecto ni sufijos de
        instancia ('2876_7616P01' → 'P01'; '2876_7616 - PMMA_3' → 'PMMA_3')."""
        n = self.nombre.strip()
        m = re.search(r'(P\d{2,3})$', n)
        if m:
            return m.group(1)
        if ' - ' in n:
            return n.split(' - ')[-1].strip()
        return n


def _material_de(nombre: str) -> str | None:
    for clave in ('PMMA', 'FLEX', 'DM', 'MDF'):
        if re.search(rf'(?<![A-Za-z0-9]){clave}(?![A-Za-z])', nombre, re.IGNORECASE):
            return clave.upper()
    return None


def leer_componentes(ruta: str) -> list[Componente]:
    """Todos los cuerpos del STEP con su nombre y su posición en el mundo.

    Devuelve [] si el STEP no trae estructura de ensamblaje — el llamador
    debe caer entonces al camino de siempre (`solids()`), que sigue siendo
    válido para los STEP de una sola pieza.
    """
    doc = TDocStd_Document(TCollection_ExtendedString('step'))
    lector = STEPCAFControl_Reader()
    lector.SetNameMode(True)
    if lector.ReadFile(ruta) != IFSelect_ReturnStatus.IFSelect_RetDone:
        return []
    if not lector.Transfer(doc):
        return []

    herramienta_formas = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    raices = TDF_LabelSequence()
    herramienta_formas.GetFreeShapes(raices)
    if raices.Length() == 0:
        return []

    componentes: list[Componente] = []

    def nombre_de(label: TDF_Label) -> str:
        attr = TDataStd_Name()
        if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
            return str(attr.Get().ToExtString())
        return ''

    def recorrer(label: TDF_Label, loc_padre: TopLoc_Location, nombre_padre: str):
        loc = herramienta_formas.GetLocation_s(label)
        loc_total = loc_padre.Multiplied(loc) if not loc.IsIdentity() else loc_padre

        destino = label
        if herramienta_formas.IsReference_s(label):
            ref = TDF_Label()
            herramienta_formas.GetReferredShape_s(label, ref)
            destino = ref

        nombre = nombre_de(destino) or nombre_de(label) or nombre_padre

        if herramienta_formas.IsAssembly_s(destino):
            hijos = TDF_LabelSequence()
            herramienta_formas.GetComponents_s(destino, hijos, False)
            for i in range(1, hijos.Length() + 1):
                recorrer(hijos.Value(i), loc_total, nombre)
            return

        forma = herramienta_formas.GetShape_s(destino)
        if forma.IsNull():
            return
        colocada = forma.Moved(loc_total) if not loc_total.IsIdentity() else forma
        for solido in Shape.cast(colocada).Solids():
            componentes.append(Componente(
                nombre=nombre,
                solido=solido,
                es_herramienta=nombre.upper().startswith(PREFIJO_HERRAMIENTA),
                material=_material_de(nombre),
            ))

    for i in range(1, raices.Length() + 1):
        recorrer(raices.Value(i), TopLoc_Location(), '')

    return componentes


def _se_cruzan(a, b) -> bool:
    """Filtro barato por caja envolvente antes de una resta booleana (cara).

    Defensivo: en un STEP real hay cuerpos degenerados cuya caja envolvente
    OCCT declara vacía ("Bnd_Box is void") y consultarla revienta. Ante la
    duda se devuelve False: como mucho se deja de aplicar una herramienta, que
    es mucho mejor que tumbar la conversión entera.
    """
    try:
        ba, bb = a.BoundingBox(), b.BoundingBox()
    except Exception:
        return False
    return not (ba.xmin > bb.xmax or ba.xmax < bb.xmin
                or ba.ymin > bb.ymax or ba.ymax < bb.ymin
                or ba.zmin > bb.zmax or ba.zmax < bb.zmin)


def tiene_nombres_utiles(componentes: list[Componente]) -> bool:
    """¿Este STEP viene con nombres de pieza de verdad?

    Se exige más de un nombre distinto: si todos los cuerpos se llaman igual
    (típico del traductor de STEP), los nombres no distinguen nada y hay que
    seguir numerando por orden como siempre.
    """
    utiles = {c.nombre.strip() for c in componentes if c.nombre_util}
    return len(utiles) > 1


def solo_piezas(componentes: list[Componente]) -> list[Componente]:
    """Quita del ensamblaje los cuerpos que NO son piezas a cortar.

    Los cuerpos "TOOL - …" NO son fresas ni mecanizados: son la geometría de
    construcción con la que se recortó el conjunto en el CAD (en 2876_7616
    miden hasta 13,6 m y una sola se solapa con el 39 % del volumen de una
    pieza). Se probó a RESTARLAS de las piezas para hacer aparecer las ranuras
    y el resultado fue destruirlas: P01 pasaba de 203 a 123 mm de ancho. Las
    piezas del STEP YA vienen recortadas; estos cuerpos son el residuo del
    proceso de modelado y lo único correcto es dejarlos fuera.
    """
    return [c for c in componentes if not c.es_herramienta]


def agrupar_iguales(piezas: list[Componente]) -> list[tuple[Componente, int]]:
    """Agrupa por NOMBRE de producto y devuelve (pieza, unidades).

    El ensamblaje coloca la misma pieza varias veces (P01 va 11 veces en el
    techo 2876_7616). El profesional saca un fichero por pieza distinta con su
    cantidad; antes se sacaban 11 DXF idénticos.
    """
    porNombre: dict[str, list[Componente]] = {}
    for p in piezas:
        porNombre.setdefault(p.nombre, []).append(p)
    return [(v[0], len(v)) for v in porNombre.values()]
