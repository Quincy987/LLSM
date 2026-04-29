"""
Phase 0 smoke test: verify pythonnet can load SCIA v24 OpenAPI DLLs
and create an in-memory ADM model WITHOUT launching SCIA or touching the license.

This is a pure assembly-load + object-construction test. It does NOT:
  - launch SCIA Engineer
  - consume a floating license
  - write anything to disk

If this passes, the Python-> .NET bridge works and we can proceed
to the live test (which will launch SCIA).
"""
import sys
import winreg
from pathlib import Path

SCIA_VERSION = "24.0"

print(f"[1/5] Reading registry for SCIA {SCIA_VERSION}...")
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\SCIA\ESA\{SCIA_VERSION}\Admin\Dir") as key:
    sen_installation_folder = Path(winreg.QueryValueEx(key, "SYSTEM")[0])
    sen_temp_folder = Path(winreg.QueryValueEx(key, "Temp")[0])
sen_openapi_installation_folder = sen_installation_folder / "OpenAPI_dll"
print(f"    install folder: {sen_installation_folder}")
print(f"    openapi folder: {sen_openapi_installation_folder}")
print(f"    temp folder:    {sen_temp_folder}")
assert sen_openapi_installation_folder.exists(), "OpenAPI_dll folder missing!"

print(f"[2/5] Adding folders to sys.path...")
sys.path.append(str(sen_installation_folder))
sys.path.append(str(sen_openapi_installation_folder))

print(f"[3/5] Loading CLR and adding DLL references...")
import clr
import System

admrefs = [
    "UnitsNet.dll",
    "System.ValueTuple.dll",
    "ModelExchanger.AnalysisDataModel.dll",
    "ModelExchanger.AnalysisDataModel.Implementation.dll",
    "ModelExchanger.AnalysisDataModel.Contracts.dll",
    "ModelExchanger.AnalysisDataModel.Integration.Bootstrapper.dll",
    "CSInfrastructure.dll",
    "FluentValidation.dll",
    "Newtonsoft.Json.dll",
    "SimpleInjector.dll",
    "Infrastructure.Geometry.dll",
    "MathNet.Spatial.dll",
    "MathNet.Numerics.dll",
    "AutoMapper.dll",
    "AutoMapper.Extensions.EnumMapping.dll",
    "ModelExchanger.Shared.Implementation.dll",
    "ModelExchanger.Shared.Models.dll",
]
for dll_name in admrefs:
    for folder in (sen_installation_folder, sen_openapi_installation_folder):
        dll_path = folder / dll_name
        if dll_path.is_file():
            clr.AddReference(str(dll_path))
            print(f"    + {dll_name}")
            break
    else:
        print(f"    ! NOT FOUND: {dll_name}")

print(f"[4/5] Importing ADM namespace...")
import ModelExchanger.AnalysisDataModel as adm
import UnitsNet
print(f"    ADM module loaded: {adm}")

print(f"[5/5] Creating in-memory ADM model (no SCIA launch)...")
adm_model = adm.Models.AnalysisModel()
adm_bootstrapper = adm.Integration.Bootstrapper.AnalysisDataModelBootstrapper()
adm_scope = adm_bootstrapper.CreateThreadedScope()
adm_service = adm_scope.GetService[adm.Contracts.IAnalysisModelService]()
print(f"    model created: {adm_model}")
print(f"    service ready: {adm_service}")

model_info = adm.ModelInformation(System.Guid.NewGuid(), "SmokeTest")
model_info.NationalCode = adm.Enums.NationalCode.EC_Standard_EN
model_info.SourceApplication = "LLSM Phase 0 smoke test"
adm_service.AddItemToModel(adm_model, model_info)
print(f"    model info added: {model_info.Name}")

print()
print("SUCCESS: DLLs load, ADM namespace works, in-memory model creation OK.")
print("Next step (interactive, launches SCIA): run the Jupyter notebook.")

adm_scope.Dispose()
