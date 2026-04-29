import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from llsm.scia.loader import load as load_scia
load_scia()

import clr, System
import System.Reflection as R
from SCIA.OpenAPI import ApiGuid

t = clr.GetClrType(ApiGuid)
print(f"=== ApiGuid type: {t.FullName} ===")
print("\n--- constructors ---")
for c in t.GetConstructors():
    params = ", ".join(f"{p.ParameterType.Name} {p.Name}" for p in c.GetParameters())
    print(f"  ApiGuid({params})")

print("\n--- public methods ---")
flags = R.BindingFlags.Public | R.BindingFlags.Instance | R.BindingFlags.Static | R.BindingFlags.DeclaredOnly
for m in t.GetMethods(flags):
    params = ", ".join(f"{p.ParameterType.Name} {p.Name}" for p in m.GetParameters())
    static = "static " if m.IsStatic else ""
    print(f"  {static}{m.ReturnType.Name} {m.Name}({params})")

print("\n--- public properties ---")
for p in t.GetProperties(flags):
    print(f"  {p.PropertyType.Name} {p.Name}")

print("\n--- public fields ---")
for f in t.GetFields(flags):
    print(f"  {f.FieldType.Name} {f.Name}")

# Try constructing one
g = System.Guid.NewGuid()
print(f"\nTest Guid: {g}")
try:
    ag = ApiGuid(g)
    print(f"ApiGuid(g): str={ag} repr={ag!r}")
    # Look at instance methods
    print(f"  type: {type(ag).__name__}")
    # Try GetGuid / Guid property if exists
    for attr in dir(ag):
        if not attr.startswith('_'):
            try:
                v = getattr(ag, attr)
                if not callable(v):
                    print(f"  .{attr} = {v}")
            except Exception:
                pass
except Exception as e:
    print(f"ctor err: {e}")
