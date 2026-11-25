"""Try 17: why did the beam grids of try 16 give no results, and does SCIA use a material's own stiffness?

Variants, each pushed into a copy of the v24 start model, saved and calculated in one batch run:
  grid_pinned   2 separate 6 m beams, pinned + roller Z (as try 16: no rows at all)
  grid_fixed    the same with a fixed support at one end
  beam_default  the 2 x 12 m test beam, material as LLSM makes it today (E = 0, no density)
  beam_e33      the same with E = 33 GPa, G = E/2.4, nu = 0.2, density 2500 kg/m3 set on the ADM material
Hand values (beam, 9 kN/m on both spans, 1000 x 600, E = 33 GPa, I = 0.018 m4): |My| at B 162 kNm;
max deflection in a span qL^4/(185 EI) = 1.70 mm; deflection at 0.4 L = 0.00540 qL^4/EI = 1.70 mm;
rotation at A = qL^3/(48 EI) = 0.545 mrad. Self weight 1.0 x 0.6 x 25 kN/m3 = 15 kN/m: |My| at B = 270 kNm.
"""
import sys
from pathlib import Path

from common import launch, log, run, t
from try16_start_model import START, W, batch, push_into, save

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from llsm.scia.xmlio import parse_xmlio  # noqa: E402

ONLY = sys.argv[1:]


PROPS = {  # E (MPa), nu, density (kg/m3)
    "Concrete": (33000.0, 0.2, 2500.0),
    "Steel": (210000.0, 0.3, 7850.0),
}


def set_props(ctx, name: str, kind: str, thermal: bool) -> None:
    import UnitsNet as un
    U = un.Units
    e, nu, rho = PROPS[kind]
    m = ctx.builder.get(name)
    m.EModulus = un.Pressure(e, U.PressureUnit.Megapascal)
    m.GModulus = un.Pressure(e / (2 * (1 + nu)), U.PressureUnit.Megapascal)
    m.PoissonCoefficient = nu
    m.UnitMass = un.Density(rho, U.DensityUnit.KilogramPerCubicMeter)
    if thermal:
        m.ThermalExpansion = un.CoefficientOfThermalExpansion(1.2e-5, U.CoefficientOfThermalExpansionUnit.InverseKelvin)
    log("material", name, kind, "E", m.EModulus, "G", m.GModulus, "nu", m.PoissonCoefficient, "rho", m.UnitMass, "alpha", m.ThermalExpansion)


def beam(ctx, material_e: bool, quality: str | None = None, kind: str = "Concrete", thermal: bool = True) -> list[str]:
    t.new_model(ctx, name="beam")
    t.add_material(ctx, "CB_C30/37", kind=kind)
    if material_e:
        set_props(ctx, "CB_C30/37", kind, thermal)
    if quality:
        ctx.builder.get("CB_C30/37").Quality = quality
        log("material quality", ctx.builder.get("CB_C30/37").Quality)
    t.add_rectangular_section(ctx, "CB_R", material="CB_C30/37", width_mm=1000, height_mm=600)
    for n, x in (("CB_A", 0), ("CB_B", 12000), ("CB_C", 24000)):
        t.add_node(ctx, n, x, 0, 0)
    t.add_beam(ctx, "CB_V1", "CB_A", "CB_B", "CB_R")
    t.add_beam(ctx, "CB_V2", "CB_B", "CB_C", "CB_R")
    t.add_pinned_support(ctx, "CB_SA", "CB_A")
    t.add_roller_support(ctx, "CB_SB", "CB_B", "Z")
    t.add_roller_support(ctx, "CB_SC", "CB_C", "Z")
    t.add_load_group(ctx, "CB LGP", kind="Permanent")
    t.add_load_group(ctx, "CB LGV", kind="Variable")
    t.add_load_case(ctx, "CB EG", group="CB LGP", kind="SelfWeight")
    t.add_load_case(ctx, "CB Q", group="CB LGV")
    t.add_line_load(ctx, "CB_q1", beam="CB_V1", load_case="CB Q", magnitude_kn_per_m=-9)
    t.add_line_load(ctx, "CB_q2", beam="CB_V2", load_case="CB Q", magnitude_kn_per_m=-9)
    return ["CB EG", "CB Q"]


def grid(ctx, fixed: bool) -> list[str]:
    t.new_model(ctx, name="grid")
    t.add_material(ctx, "G_C30/37", kind="Concrete")
    t.add_rectangular_section(ctx, "G_R", material="G_C30/37", width_mm=300, height_mm=500)
    t.add_load_group(ctx, "G LG", kind="Variable")
    t.add_load_case(ctx, "G Q", group="G LG")
    for i in range(2):
        t.add_node(ctx, f"G_{i}a", 0, i * 2000, 0)
        t.add_node(ctx, f"G_{i}b", 6000, i * 2000, 0)
        t.add_beam(ctx, f"G_B{i}", f"G_{i}a", f"G_{i}b", "G_R")
        (t.add_fixed_support if fixed else t.add_pinned_support)(ctx, f"G_S{i}a", f"G_{i}a")
        t.add_roller_support(ctx, f"G_S{i}b", f"G_{i}b", "Z")
        t.add_line_load(ctx, f"G_q{i}", beam=f"G_B{i}", load_case="G Q", magnitude_kn_per_m=-10)
    return ["G Q"]


def rows(xml: Path, cases: list[str]) -> dict:
    out = {}
    for tb in parse_xmlio(xml):
        if tb.selection in cases:
            for r in tb.rows:
                d = dict(zip(tb.columns, r))
                key = f"{tb.selection} | {tb.name[:6]} | {d['Naam']} @ {round(float(d['dx']), 2)}"
                if tb.name.startswith("Interne"):
                    out[key] = {"Vz_kN": round(float(d["V_z"]) / 1e3, 2), "My_kNm": round(float(d["M_y"]) / 1e3, 2)}
                else:
                    out[key] = {"uz_mm": round(float(d["u_z"]) * 1e3, 3), "fy_mrad": round(float(d["f_y"]) * 1e3, 3)}
    return out


def body(ctx, result):
    variants = {"grid_pinned": lambda: grid(ctx, False), "grid_fixed": lambda: grid(ctx, True),
                "beam_default": lambda: beam(ctx, False), "beam_e33": lambda: beam(ctx, True),
                "beam_steel": lambda: beam(ctx, False, None, "Steel"),
                "beam_e33_nothermal": lambda: beam(ctx, True, None, "Concrete", False),
                "beam_steel_props": lambda: beam(ctx, True, None, "Steel", True),
                "beam_quality": lambda: beam(ctx, False, "C30/37"), "beam_quality_s235": lambda: beam(ctx, False, "C45/55")}
    if ONLY:
        variants = {k: v for k, v in variants.items() if k in ONLY}
    launch(ctx)
    sess = ctx.session
    for label, build in variants.items():
        cases = build()
        ctx.session = sess
        p = push_into(ctx, START, f"{label}.esa")
        target = W / f"t17-{label}.esa"
        save(sess, target)
        b = batch(target, cases, f"t17-{label}")
        r = rows(Path(b["xml"]), cases) if b.get("xml") else {}
        result[label] = {"push_errors": len(p["errors"]), "batch": b["message"], "s": b["s"], "rows": r}
        log(label, b["message"], b["s"], "s, push errors", len(p["errors"]))
        for k, v in r.items():
            log("    ", k, v)


if __name__ == "__main__":
    run("try17", body)
