"""The XML update that adds load groups, load cases and point forces to a saved model.

Written by :func:`llsm.scia.esa_xml.run_update` with ``def_file=LOADS_DEF_FILE``. What the
numbers mean, and that the XML takes N and m, was measured on the plate fixture:
docs/verify/phase3-moving-load.md. Objects are keyed by name: a name that already exists
in the model is overwritten (S7), so callers must check for collisions first.
"""
from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import quoteattr

from .esa_xml import LOADS_DEF_FILE, RESULT_CLASSES_DEF_FILE

RELATION = {"standard": 0, "exclusive": 1, "together": 2}
VARIABLE = 1  # load group "Load" and load case "Action type": 0 permanent, 1 variable
STATIC = 0  # load case "Load type" of a variable case: SCIA's first option (static; not checked in
# the GUI). For a permanent case 0 is self weight (paaltje.xml's "BG1 Eigen gewicht"): that is
# why this module only writes variable cases.

_BEAM = ("{ECB5D684-7357-11D4-9F6C-00104BC3B443}", "EP_DSG_Elements.EP_Beam.1")
_GROUPS = ("{F9D4AA72-49D5-11D4-A3CF-000000000000}", "DataSetScia.EP_LoadGroup.1", "196E8F76-7A40-441C-81DB-6476DAC8BCFF")
_CASES = ("{0908D21F-481F-11D4-AB84-00C06C452330}", "DataSetScia.EP_LoadCase.1", "CA2AF6CC-B980-4C45-AFE3-85EC125810E8")
_FREE = ("{E03984FC-B420-4C03-8D2F-72EA2FAB147D}", "DataAddLoad.EP_PointForceFree.1", "9C68624A-8E5C-47BD-A150-2AEC783D8A93")
_ON_BEAM = ("{BC16B3C2-F464-11D4-94D3-000000000000}", "DataAddLoad.EP_PointForceLine.1", "D7DC5706-2FCC-434E-9E37-8C0D0156F034")
_COMBINATIONS = ("{C0FBF7E1-4A71-11D4-AB86-00C06C452330}", "DataSetSciaTom.EP_LoadCombi.1", "9F5CF12B-1690-400E-8030-62B13CE4E579")
_RESULT_CLASSES = ("{8E5A5442-7217-460C-853D-9D35EC18FAB9}", "DataSetSciaTom.EP_SelectedLC.1", "84DFD127-4444-4378-A414-814FB757AE04")
LINEAR_SLS = 3  # combination type "Lineair - BGT": the cases added up with their coefficients, nothing else
# Ids for new combinations and result classes. A result class written with the id of an existing
# one was stored as an extra class (report spike, second run), so new objects get ids far from
# SCIA's own; objects that already exist are referred to by name with an id not in the document,
# which SCIA resolves by name (phase0/moving_load/reactions.py: the combination of three existing
# cases gave the 900 kN of their loads).
_FIRST_NEW_ID = 9001
_UNLISTED_ID = 8001


def newton(kilonewton: float) -> float:
    """kN to N. The one place LLSM converts a force for SCIA's XML."""
    return kilonewton * 1000.0


def metre(millimetre: float) -> float:
    """mm to m. The one place LLSM converts a length for SCIA's XML."""
    return millimetre / 1000.0


@dataclass(frozen=True)
class LoadCaseRow:
    name: str
    description: str = ""


@dataclass(frozen=True)
class PointForceRow:
    """A vertical point force, already in SI: N, and m or a relative position.

    Free when ``member_name`` is None (global x, y, z); otherwise on that beam at the
    relative position ``position`` from its start.
    """

    name: str
    case: str
    value_n: float
    x: float | None = None
    y: float | None = None
    z: float = 0.0
    member_name: str | None = None
    member_id: int | None = None
    position: float | None = None


def _table(spec: tuple[str, str, str], name: str, headers: list[str], objects: list[str]) -> str:
    head = "\n".join(f'            <h{i} t="{text}"/>' for i, text in enumerate(headers))
    body = "\n".join(objects)
    return f"""   <container id="{spec[0]}" t="{spec[1]}">
      <table id="{spec[2]}" t="{spec[1]}" name="{name}">
         <h>
{head}
         </h>
{body}
      </table>
   </container>"""


def _object(object_id: int, name: str, cells: list[str]) -> str:
    inner = "\n".join(f"           {cell}" for cell in cells)
    return f"""         <obj id="{object_id}" nm={quoteattr(name)}>
{inner}
         </obj>"""


def number(value: float) -> str:
    """Plain decimal notation, never exponent: 1e-16 from path arithmetic is written as 0.

    Nine decimals: 1e-6 mm for a coordinate in m, 1e-9 N for a force.
    """
    text = f"{float(value):.9f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _value(index: int, value: object) -> str:
    return f"<p{index} v={quoteattr(number(value) if isinstance(value, float) else str(value))}/>"


def _ref(index: int, object_id: int, name: str) -> str:
    return f"<p{index} i=\"{object_id}\" n={quoteattr(name)}/>"


def _beam_reference(index: int, member_id: int | None, member_name: str) -> str:
    return f"""<p{index} t="">
             <h>
               <h0 t="Member Type"/>
               <h1 t="Member Type Name"/>
               <h2 t="Member Id"/>
               <h3 t="Member Name"/>
             </h>
             <row id="0">
               <p0 v="{_BEAM[0]}"/>
               <p1 v="{_BEAM[1]}"/>
               <p2 v="{member_id or 0}"/>
               <p3 v={quoteattr(member_name)}/>
             </row>
           </p{index}>"""


def build_load_update(
    group: str,
    cases: list[LoadCaseRow],
    forces: list[PointForceRow],
    relation: str = "exclusive",
    def_name: str = LOADS_DEF_FILE.name,
) -> str:
    """One variable load group, its (static, variable) cases and their point forces."""
    if not cases:
        raise ValueError("at least one load case")
    case_id = {c.name: i + 1 for i, c in enumerate(cases)}
    unknown = sorted({f.case for f in forces} - case_id.keys())
    if unknown:
        raise ValueError(f"forces on cases that are not written: {unknown}")
    parts = [
        _table(_GROUPS, "Load groups", ["Name", "Relation", "Load"], [
            _object(1, group, [_value(0, group), _value(1, RELATION[relation]), _value(2, VARIABLE)])
        ]),
        _table(_CASES, "Load cases", ["Name", "Action type", "Description", "Load group", "Load type"], [
            _object(case_id[c.name], c.name, [
                _value(0, c.name), _value(1, VARIABLE), _value(2, c.description), _ref(3, 1, group), _value(4, STATIC),
            ])
            for c in cases
        ]),
    ]
    free = [f for f in forces if f.member_name is None]
    if free:
        parts.append(_table(_FREE, "Free point loads", [
            "Name", "Load case", "Direction", "Type", "Validity", "Select", "Value - F", "Coord X", "Coord Y", "Coord Z", "System",
        ], [
            # Z direction, force, valid on all 2D members, picked automatically, global axes
            _object(i + 1, f.name, [
                _value(0, f.name), _ref(1, case_id[f.case], f.case), _value(2, 2), _value(3, 0), _value(4, 0), _value(5, 0),
                _value(6, float(f.value_n)), _value(7, float(f.x)), _value(8, float(f.y)), _value(9, float(f.z)), _value(10, 0),
            ])
            for i, f in enumerate(free)
        ]))
    on_beam = [f for f in forces if f.member_name is not None]
    if on_beam:
        parts.append(_table(_ON_BEAM, "Point loads on beam", [
            "Name", "Load case", "Reference Table", "Direction", "Type", "Value - F", "System", "Coord. definition", "Position x", "Origin",
        ], [
            # Z direction, force, global axes, relative position from the start
            _object(i + 1, f.name, [
                _value(0, f.name), _ref(1, case_id[f.case], f.case), _beam_reference(2, f.member_id, f.member_name),
                _value(3, 2), _value(4, 0), _value(5, float(f.value_n)), _value(6, 0), _value(7, 1),
                _value(8, float(f.position)), _value(9, 0),
            ])
            for i, f in enumerate(on_beam)
        ]))
    body = "\n".join(parts)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<project xmlns="http://www.scia.cz">
   <def uri={quoteattr(def_name)}/>
{body}
</project>
"""


def build_case_classes_update(cases: list[str], def_name: str = RESULT_CLASSES_DEF_FILE.name) -> str:
    """One linear combination (coefficient 1) and one result class per existing load case.

    Both are named after the case, so a report item set to *all result classes* prints a
    chapter per case. For a copy that is printed and thrown away: it adds objects the engineer
    did not make.
    """
    if not cases:
        raise ValueError("at least one load case")
    if len(set(cases)) != len(cases):
        raise ValueError("load case names must be unique")
    combinations, classes = [], []
    for i, case in enumerate(cases):
        combo_id = _FIRST_NEW_ID + i
        rows = (
            f'               <row id="0">{_value(0, case)}{_value(1, 1.0)}{_ref(2, _UNLISTED_ID + i, case)}</row>'
        )
        combinations.append(_object(combo_id, case, [_value(0, case), _value(1, LINEAR_SLS), f"""<p2 t="">
             <h>
               <h0 t="Name"/>
               <h1 t="Coeff."/>
               <h2 t="Load case ID"/>
             </h>
{rows}
           </p2>"""]))
        classes.append(_object(_FIRST_NEW_ID + i, case, [_value(0, case), f"""<p1 t="">
             <h>
               <h0 t="Load combi ID"/>
               <h1 t="Nonlinear combi ID"/>
               <h2 t="Case_Type"/>
               <h3 t="Case_Model"/>
               <h4 t="Case_tUS"/>
             </h>
             <row id="0">{_ref(0, combo_id, case)}{_value(2, 1001)}{_value(3, 1)}{_value(4, 1)}</row>
           </p1>"""]))
    body = "\n".join([
        _table(_COMBINATIONS, "Combinations", ["Name", "Type", "Load cases"], combinations),
        _table(_RESULT_CLASSES, "Result classes", ["Name", "Case"], classes),
    ])
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<project xmlns="http://www.scia.cz">
   <def uri={quoteattr(def_name)}/>
{body}
</project>
"""
