"""Probe the 'Structure' type returned by EsaProject.get_Model().

We need to know, for open_project: after OpenProject + RefreshModel_FromSCIAEngineer,
is there a way to read the ADM model contents back (so the builder can wrap it)?
Reflection only — no SCIA launch.
"""
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

from SCIA.OpenAPI import EsaProject
import System.Reflection as R

proj_type = clr.GetClrType(EsaProject)
model_prop = proj_type.GetMethod("get_Model")
struct_type = model_prop.ReturnType
print(f"=== proj.Model type: {struct_type.FullName} ===")
print(f"    assembly: {struct_type.Assembly.GetName().Name}")
print()

flags = R.BindingFlags.Public | R.BindingFlags.Instance | R.BindingFlags.Static
print("=== Structure (proj.Model) methods ===")
for m in sorted(struct_type.GetMethods(flags), key=lambda x: x.Name):
    if m.Name.startswith(("get_Type","ToString","Equals","GetHashCode","GetType")):
        continue
    params = ", ".join(f"{p.ParameterType.Name} {p.Name}" for p in m.GetParameters())
    print(f"  {m.ReturnType.Name} {m.Name}({params})")

print()
print("=== Structure properties ===")
for p in struct_type.GetProperties(flags):
    print(f"  {p.PropertyType.Name} {p.Name}")
