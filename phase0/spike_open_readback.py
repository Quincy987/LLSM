"""Live spike: can we read an opened .esa model back into an AnalysisModel?

Question driving open_project: after env.OpenProject(existing.esa), is there ANY
way to get the file's contents into an AnalysisModel the builder can read?

Candidate read-back paths tried here:
  A) proj.Model.TrySynchronizeModel(emptyModel) -> enumerate emptyModel
  B) proj.Model.RefreshModel_FromSCIAEngineer() then inspect non-public fields
     of the Structure for an AnalysisModel reference
  C) reflection dump of any AnalysisModel found on the Structure instance

Run:  .venv/Scripts/python.exe phase0/spike_open_readback.py "<path to .esa>"
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llsm.scia import loader
from llsm.scia.session import SciaSession


def _count_by_type(model) -> dict:
    counts: dict[str, int] = {}
    for obj in model:
        try:
            t = type(obj).__name__
        except Exception:
            t = "?"
        counts[t] = counts.get(t, 0) + 1
    return counts


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: spike_open_readback.py <path to .esa>")
        sys.exit(2)
    esa = Path(sys.argv[1])
    if not esa.is_file():
        print(f"no such file: {esa}")
        sys.exit(2)

    loader.load()

    # Build an empty AnalysisModel via the same bootstrapper the builder uses.
    import ModelExchanger.AnalysisDataModel as adm
    boot = adm.Integration.Bootstrapper.AnalysisDataModelBootstrapper()
    scope = boot.CreateThreadedScope()
    empty_model = adm.Models.AnalysisModel()

    with SciaSession(visible=False, kill_stale=True) as sess:
        def _open_and_probe():
            proj = sess.env.OpenProject(str(esa))
            sess.set_project(proj)
            out = {}

            # --- Path A: TrySynchronizeModel into an empty model ---
            try:
                ok = proj.Model.TrySynchronizeModel(empty_model)
                out["A_TrySynchronizeModel_ok"] = bool(ok)
                out["A_counts_after"] = _count_by_type(empty_model)
            except Exception as e:
                out["A_error"] = f"{type(e).__name__}: {e}"

            # --- Path B: RefreshModel_FromSCIAEngineer, then look for internal model ---
            try:
                proj.Model.RefreshModel_FromSCIAEngineer()
                out["B_refresh_ok"] = True
            except Exception as e:
                out["B_error"] = f"{type(e).__name__}: {e}"

            # Re-check empty_model after refresh (in case refresh fills the last synced model)
            try:
                out["B_empty_counts_after_refresh"] = _count_by_type(empty_model)
            except Exception as e:
                out["B_count_error"] = f"{type(e).__name__}: {e}"

            # --- Path C: reflect over Structure instance non-public fields ---
            try:
                import System
                import System.Reflection as R
                st = proj.Model
                t = st.GetType()
                flags = (R.BindingFlags.NonPublic | R.BindingFlags.Public
                         | R.BindingFlags.Instance)
                fields = []
                for f in t.GetFields(flags):
                    fields.append(f"{f.FieldType.Name} {f.Name}")
                out["C_structure_fields"] = fields

                # Are the name->ID dicts populated after refresh? Read their counts + samples.
                for fname in ("NodeNameToID_Dict", "BeamNameToID_Dict", "SlabNameToID_Dict"):
                    try:
                        fi = t.GetField(fname, flags)
                        d = fi.GetValue(st)
                        cnt = d.Count if d is not None else None
                        keys = []
                        if d is not None and cnt:
                            i = 0
                            for k in d.Keys:
                                keys.append(str(k)); i += 1
                                if i >= 5:
                                    break
                        out[f"C_{fname}_count"] = cnt
                        out[f"C_{fname}_sample"] = keys
                    except Exception as e:
                        out[f"C_{fname}_err"] = f"{type(e).__name__}: {e}"
            except Exception as e:
                out["C_error"] = f"{type(e).__name__}: {e}"

            # --- Path D: results API on the opened model (no recalc) ---
            # Many .esa files are saved WITH results. Can we get a RAPI handle
            # straight from the opened file, without meshing/calculating?
            try:
                rapi = proj.Model.InitializeResultsAPI()
                out["D_rapi_initialized_no_calc"] = rapi is not None
            except Exception as e:
                out["D_error"] = f"{type(e).__name__}: {e}"

            return out

        result = sess.submit(_open_and_probe, timeout_s=300)

    print("\n=== SPIKE RESULT ===")
    for k, v in result.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
