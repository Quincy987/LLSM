"""XML snippets for the moving-load spike: a plate deck fixture and load tables.

Values are SI, as SCIA's XML wants them (N, m). Everything is keyed by name; the
``i`` attributes are ids inside the same XML document.
"""
from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import quoteattr

NODE = ("{39A7F468-A0D4-4DFF-8E5C-5843E1807D13}", "EP_DSG_Elements.EP_StructNode.1")
BEAM = ("{ECB5D684-7357-11D4-9F6C-00104BC3B443}", "EP_DSG_Elements.EP_Beam.1")


def project(def_uri: str, *containers: str) -> str:
    body = "\n".join(containers)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<project xmlns="http://www.scia.cz">
   <def uri={quoteattr(def_uri)}/>
{body}
</project>
"""


def container(cid: str, progid: str, table_id: str, name: str, headers: list[str], objects: list[str]) -> str:
    head = "\n".join(f'            <h{i} t="{h}"/>' for i, h in enumerate(headers))
    objs = "\n".join(objects)
    return f"""   <container id="{cid}" t="{progid}">
      <table id="{table_id}" t="{progid}" name="{name}">
         <h>
{head}
         </h>
{objs}
      </table>
   </container>"""


def obj(oid: int, name: str, cells: dict[int, str]) -> str:
    inner = "\n".join(f"           {cell(i, v)}" for i, v in sorted(cells.items()))
    return f"""         <obj id="{oid}" nm={quoteattr(name)}>
{inner}
         </obj>"""


def cell(index: int, value) -> str:
    if isinstance(value, Ref):
        return f'<p{index} i="{value.oid}" n={quoteattr(value.name)}/>'
    if isinstance(value, Raw):
        return value.text.replace("<pX", f"<p{index}").replace("</pX>", f"</p{index}>")
    return f"<p{index} v={quoteattr(str(value))}/>"


@dataclass(frozen=True)
class Ref:
    oid: int
    name: str


@dataclass(frozen=True)
class Raw:
    text: str  # use <pX ...> ... </pX>; the index is filled in


def member_ref(kind: tuple[str, str], oid: int, name: str) -> Raw:
    return Raw(f"""<pX t="">
             <h>
               <h0 t="Member Type"/>
               <h1 t="Member Type Name"/>
               <h2 t="Member Id"/>
               <h3 t="Member Name"/>
             </h>
             <row id="0">
               <p0 v="{kind[0]}"/>
               <p1 v="{kind[1]}"/>
               <p2 v="{oid}"/>
               <p3 v={quoteattr(name)}/>
             </row>
           </pX>""")


# --- model tables -----------------------------------------------------------------------

def nodes(points: dict[str, tuple[float, float, float]]) -> str:
    objs = [obj(i + 1, n, {0: n, 1: x, 2: y, 3: z}) for i, (n, (x, y, z)) in enumerate(points.items())]
    return container("{39A7F468-A0D4-4DFF-8E5C-5843E1807D13}", "EP_DSG_Elements.EP_StructNode.1",
                     "B1A479D4-C299-4862-8A70-3BA1B0C684B0", "Node", ["Name", "Coord X", "Coord Y", "Coord Z"], objs)


def circle_section(name: str, material: str, diameter: float) -> str:
    params = Raw(f"""<pX t="">
                <h>
                  <h0 t="Name"/>
                  <h1 t="Material"/>
                  <h4 t="Length"/>
                </h>
                <row id="0">
                  <p0 v="Material"/>
                  <p1 i="0" n={quoteattr(material)}/>
                </row>
                <row id="1">
                  <p0 v="D"/>
                  <p4 v="{diameter}"/>
                </row>
              </pX>""")
    o = obj(1, name, {0: name, 1: "EP_CssLib.EP_ProfLib_Geometric.1", 2: 3, 3: params})
    return f"""   <container id="{{2127A9B3-36BD-11D4-B337-00104BC3B531}}" t="CrossSection.EP_CrossSection.1">
      <table id="A142046F-5D16-47A2-8EA8-CE2456522F63" t="CrossSection.EP_CssGeometry.1" name="Cross-sections">
         <h>
            <h0 t="Name"/>
            <h1 t="Catalog ID"/>
            <h2 t="Catalog item"/>
            <h3 t="Parameters"/>
         </h>
{o}
      </table>
   </container>"""


def beams(items: list[tuple[str, Ref, Ref, Ref]]) -> str:
    objs = [obj(i + 1, n, {0: n, 2: a, 3: b, 4: css}) for i, (n, a, b, css) in enumerate(items)]
    return container("{ECB5D684-7357-11D4-9F6C-00104BC3B443}", "EP_DSG_Elements.EP_Beam.1",
                     "C0A38299-A246-4843-AECC-EBEE7CEFD582", "Member 1D",
                     ["Name", "Layer", "Beg. node", "End node", "Cross-section"], objs)


def plate(name: str, material: str, thickness: float, corners: list[Ref]) -> str:
    rows = "\n".join(
        f'               <row id="{i}"><p0 v="1"/><p1 i="{c.oid}" n={quoteattr(c.name)}/><p2 v="0"/></row>'
        for i, c in enumerate(corners)
    )
    geometry = Raw(f"""<pX t="">
             <h>
               <h0 t="Closed curve"/>
               <h1 t="Node"/>
               <h2 t="Edge"/>
             </h>
{rows}
           </pX>""")
    o = obj(1, name, {0: name, 2: Ref(0, material), 3: 0, 4: thickness, 5: geometry, 9: 0})
    return container("{8708ED31-8E66-11D4-AD94-F6F5DE2BE344}", "EP_DSG_Elements.EP_Plane.1",
                     "C92EDB4A-35EE-43AB-A36B-6D136E7259A2", "Member 2D",
                     ["Name", "Layer", "Material", "Type", "Thickness", "Table of geometry",
                      "Internal nodes", "Swap orientation", "LCS angle", "FEM model"], [o])


def fixed_supports(items: list[tuple[str, int, str]]) -> str:
    objs = [
        obj(i + 1, n, {0: n, 1: member_ref(NODE, nid, node), 2: 0, 3: 0, 4: 1, 6: 1, 8: 1, 10: 1, 12: 1, 14: 1, 17: 0})
        for i, (n, nid, node) in enumerate(items)
    ]
    headers = ["Name", "Reference Table", "Constraint", "Type", "X", "Stiffness X", "Y", "Stiffness Y", "Z",
               "Stiffness Z", "Rx", "Stiffness Rx", "Ry", "Stiffness Ry", "Rz", "Stiffness Rz", "Default size", "System"]
    return container("{1CBCA4DE-355B-40F7-A91D-8EFD26A6404D}", "DataAddSupport.EP_PointSupportPoint.1",
                     "C32168B2-FDF9-4E8E-8AE7-DE29D9FEC4E4", "Supports in node", headers, objs)


def mesh(size: float) -> str:
    return container("{31450A87-BE7E-4EA0-BFD2-4544A3E7BA53}", "EP_Model.10.00.EP_MeshSetup.1",
                     "9C5E8821-5679-4E23-B7CF-D83CE4FB11BC", "Mesh setup",
                     ["Division for 2D-1D upgrade", "Average size of 2d element/curved element",
                      "Average size of cables, tendons, elements on subsoil, nonlinear soil spring"],
                     [obj(1, "", {0: 50, 1: size, 2: 1.0})])


# --- load tables --------------------------------------------------------------------------

def load_groups(items: list[tuple[str, int, int]]) -> str:
    """(name, relation, load)."""
    objs = [obj(i + 1, n, {0: n, 1: rel, 2: load}) for i, (n, rel, load) in enumerate(items)]
    return container("{F9D4AA72-49D5-11D4-A3CF-000000000000}", "DataSetScia.EP_LoadGroup.1",
                     "196E8F76-7A40-441C-81DB-6476DAC8BCFF", "Load groups", ["Name", "Relation", "Load"], objs)


def load_cases(items: list[tuple[str, int, str, Ref, int]]) -> str:
    """(name, action type, description, group, load type)."""
    objs = [obj(i + 1, n, {0: n, 1: act, 2: desc, 3: grp, 4: lt}) for i, (n, act, desc, grp, lt) in enumerate(items)]
    return container("{0908D21F-481F-11D4-AB84-00C06C452330}", "DataSetScia.EP_LoadCase.1",
                     "CA2AF6CC-B980-4C45-AFE3-85EC125810E8", "Load cases",
                     ["Name", "Action type", "Description", "Load group", "Load type"], objs)


def free_point_forces(items: list[tuple[str, Ref, float, float, float, float]], first_id: int = 1) -> str:
    """(name, case, F [N], x, y, z [m]); direction Z, validity All, auto select, GCS."""
    objs = [
        obj(first_id + i, n, {0: n, 1: lc, 2: 2, 3: 0, 4: 0, 5: 0, 6: f, 7: x, 8: y, 9: z, 10: 0})
        for i, (n, lc, f, x, y, z) in enumerate(items)
    ]
    return container("{E03984FC-B420-4C03-8D2F-72EA2FAB147D}", "DataAddLoad.EP_PointForceFree.1",
                     "9C68624A-8E5C-47BD-A150-2AEC783D8A93", "Free point loads",
                     ["Name", "Load case", "Direction", "Type", "Validity", "Select", "Value - F",
                      "Coord X", "Coord Y", "Coord Z", "System"], objs)


def beam_point_forces(items: list[tuple[str, Ref, int, str, float, float]]) -> str:
    """(name, case, member id, member name, F [N], relative x); direction Z, GCS, from start."""
    objs = [
        obj(i + 1, n, {0: n, 1: lc, 2: member_ref(BEAM, mid, mname), 3: 2, 4: 0, 5: f, 6: 0, 7: 1, 8: x, 9: 0})
        for i, (n, lc, mid, mname, f, x) in enumerate(items)
    ]
    return container("{BC16B3C2-F464-11D4-94D3-000000000000}", "DataAddLoad.EP_PointForceLine.1",
                     "D7DC5706-2FCC-434E-9E37-8C0D0156F034", "Point loads on beam",
                     ["Name", "Load case", "Reference Table", "Direction", "Type", "Value - F", "System",
                      "Coord. definition", "Position x", "Origin"], objs)


def combinations(items: list[tuple[str, int, list[tuple[Ref, float]]]]) -> str:
    objs = []
    for i, (n, kind, cases) in enumerate(items):
        rows = "\n".join(
            f'               <row id="{j}"><p0 v={quoteattr(lc.name)}/><p1 v="{c}"/><p2 i="{lc.oid}" n={quoteattr(lc.name)}/></row>'
            for j, (lc, c) in enumerate(cases)
        )
        table = Raw(f"""<pX t="">
             <h>
               <h0 t="Name"/>
               <h1 t="Coeff."/>
               <h2 t="Load case ID"/>
             </h>
{rows}
           </pX>""")
        objs.append(obj(i + 1, n, {0: n, 1: kind, 2: table}))
    return container("{C0FBF7E1-4A71-11D4-AB86-00C06C452330}", "DataSetSciaTom.EP_LoadCombi.1",
                     "9F5CF12B-1690-400E-8030-62B13CE4E579", "Combinations", ["Name", "Type", "Load cases"], objs)
