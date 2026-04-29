"""Probe SCIA.OpenAPI Environment + Project for attach-to-existing methods."""
import sys, winreg
from pathlib import Path
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\SCIA\ESA\24.0\Admin\Dir") as key:
    sen = Path(winreg.QueryValueEx(key, "SYSTEM")[0])
oad = sen / "OpenAPI_dll"
sys.path += [str(sen), str(oad)]
import clr, System
for dll in ["UnitsNet.dll","AutoMapper.dll","AutoMapper.Extensions.EnumMapping.dll",
            "ModelExchanger.AnalysisDataModel.dll","ModelExchanger.AnalysisDataModel.Implementation.dll",
            "ModelExchanger.AnalysisDataModel.Contracts.dll",
            "ModelExchanger.AnalysisDataModel.Integration.Bootstrapper.dll",
            "ModelExchanger.Shared.Implementation.dll","ModelExchanger.Shared.Models.dll",
            "CSInfrastructure.dll","FluentValidation.dll","Newtonsoft.Json.dll",
            "SimpleInjector.dll","Infrastructure.Geometry.dll","MathNet.Spatial.dll","MathNet.Numerics.dll",
            "SciaTools.Kernel.Common.Contracts.dll","SciaTools.Kernel.Common.Implementation.dll",
            "SciaTools.Kernel.Common.Models.dll","SciaTools.Kernel.ModelExchangerExtension.Contracts.dll",
            "SciaTools.Kernel.ModelExchangerExtension.Implementation.dll",
            "SciaTools.Kernel.ModelExchangerExtension.Models.dll","SCIA.OpenAPI.dll"]:
    for f in (sen, oad):
        p = f / dll
        if p.is_file():
            clr.AddReference(str(p)); break

from SCIA.OpenAPI import Environment as SciaEnvironment
import System.Reflection as R

env_type = clr.GetClrType(SciaEnvironment)
print("=== SciaEnvironment methods ===")
flags = R.BindingFlags.Public | R.BindingFlags.Instance | R.BindingFlags.Static | R.BindingFlags.DeclaredOnly
for m in env_type.GetMethods(flags):
    params = ", ".join(f"{p.ParameterType.Name} {p.Name}" for p in m.GetParameters())
    print(f"  [{'static' if m.IsStatic else 'instance'}] {m.ReturnType.Name} {m.Name}({params})")

print()
print("=== GuiMode enum values ===")
gmt = clr.GetClrType(SciaEnvironment.GuiMode)
for name in System.Enum.GetNames(gmt):
    print(f"  {name}")

# Also check EsaProject methods (the thing OpenProject returns)
print()
print("=== Looking for EsaProject type ===")
from SCIA.OpenAPI import EsaProject
proj_type = clr.GetClrType(EsaProject)
print(f"Found: {proj_type.FullName}")
print()
print("=== EsaProject methods ===")
for m in proj_type.GetMethods(flags):
    params = ", ".join(f"{p.ParameterType.Name} {p.Name}" for p in m.GetParameters())
    print(f"  {m.ReturnType.Name} {m.Name}({params})")
