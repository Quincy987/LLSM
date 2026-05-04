"""DLL discovery and load for SCIA Engineer OpenAPI.

Reads the SCIA install path from the Windows registry, resolves all required
.NET assemblies, and loads them via pythonnet. Must be called exactly once,
before any SCIA imports.
"""
from __future__ import annotations
import sys
import winreg
from pathlib import Path
from typing import NamedTuple

DEFAULT_SCIA_VERSION = "24.0"

_DLLS = [
    "UnitsNet.dll", "System.ValueTuple.dll",
    "ModelExchanger.Excel.Conversion.Contracts.dll",
    "ModelExchanger.Excel.Conversion.Implementation.dll",
    "ModelExchanger.Excel.Integration.Bootstrapper.dll",
    "AutoMapper.dll", "AutoMapper.Extensions.EnumMapping.dll",
    "ModelExchanger.AnalysisDataModel.dll",
    "ModelExchanger.AnalysisDataModel.Implementation.dll",
    "ModelExchanger.AnalysisDataModel.Contracts.dll",
    "ModelExchanger.AnalysisDataModel.Integration.Bootstrapper.dll",
    "ModelExchanger.Shared.Implementation.dll",
    "ModelExchanger.Shared.Models.dll",
    "CSInfrastructure.dll", "FluentValidation.dll", "Newtonsoft.Json.dll",
    "SimpleInjector.dll", "Infrastructure.Geometry.dll",
    "MathNet.Spatial.dll", "MathNet.Numerics.dll",
    "SciaTools.Kernel.Common.Contracts.dll",
    "SciaTools.Kernel.Common.Implementation.dll",
    "SciaTools.Kernel.Common.Models.dll",
    "SciaTools.Kernel.ModelExchangerExtension.Contracts.dll",
    "SciaTools.Kernel.ModelExchangerExtension.Implementation.dll",
    "SciaTools.Kernel.ModelExchangerExtension.Models.dll",
    "SCIA.OpenAPI.dll",
]


class SciaPaths(NamedTuple):
    install_folder: Path
    temp_folder: Path
    openapi_folder: Path


_loaded = False
_paths: SciaPaths | None = None


def discover(version: str = DEFAULT_SCIA_VERSION) -> SciaPaths:
    """Read install + temp paths from SCIA's registry key."""
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        rf"Software\SCIA\ESA\{version}\Admin\Dir",
    ) as key:
        install = Path(winreg.QueryValueEx(key, "SYSTEM")[0])
        temp = Path(winreg.QueryValueEx(key, "Temp")[0])
    return SciaPaths(install, temp, install / "OpenAPI_dll")


def load(version: str = DEFAULT_SCIA_VERSION) -> SciaPaths:
    """Load SCIA OpenAPI DLLs via pythonnet. Idempotent — safe to call again."""
    global _loaded, _paths
    if _loaded:
        return _paths  # type: ignore
    paths = discover(version)
    sys.path.extend([str(paths.install_folder), str(paths.openapi_folder)])
    import clr  # noqa: F401  imported for side-effect
    for name in _DLLS:
        for folder in (paths.install_folder, paths.openapi_folder):
            p = folder / name
            if p.is_file():
                clr.AddReference(str(p))
                break
    _loaded = True
    _paths = paths
    return paths


def paths() -> SciaPaths:
    if not _loaded:
        raise RuntimeError("scia.loader.load() must be called first")
    return _paths  # type: ignore
