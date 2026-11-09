"""Soil layer tables and their conversion to a line stiffness along the pile."""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

KH = "kh_kN_m3"
MENARD = "E_menard_MPa"
K_LINE = "k_line_kN_m2"
UNITS = (KH, MENARD, K_LINE)
VARIANTS = ("laag", "gemiddeld", "hoog")

_R0 = 0.3  # m, Ménard reference radius
_MAX_DIAMETER_M = 5.0  # above this it was almost certainly given in mm


class LayerError(ValueError):
    """The table cannot be used. Holds every problem found, not just the first."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class SoilLayer:
    top_nap: float
    bottom_nap: float
    value: float
    unit: str
    description: str
    alpha: float | None = None  # Ménard rheological factor; overrides the table-wide one


def menard_kh(diameter_mm: float, e_kn_m2: float, alpha: float, groepsfactor: float) -> float:
    """Horizontal subgrade reaction kh [kN/m3] after Ménard; E in kN/m2, diameter in mm."""
    radius = (diameter_mm / 1000.0) * 0.5
    if radius >= _R0:
        kh = ((1 / (3 * e_kn_m2)) * (1.3 * _R0 * (2.65 * (radius / _R0)) ** alpha + alpha * radius)) ** -1
    else:
        kh = (2 * radius / e_kn_m2 * ((4 * 2.65**alpha + 3 * alpha) / 18.0)) ** -1
    return kh * groepsfactor


def line_stiffness(
    layer: SoilLayer,
    diameter_m: float | None = None,
    alpha: float | None = None,
    groepsfactor: float | None = None,
) -> float:
    """Line stiffness k_line [kN/m2], i.e. kN/m per metre of pile."""
    if layer.unit == K_LINE:
        return layer.value
    if diameter_m is None:
        raise ValueError(f"{layer.unit} needs the pile diameter")
    if not 0 < diameter_m <= _MAX_DIAMETER_M:
        raise ValueError(f"a pile diameter of {diameter_m} is not plausible; give it in metres")
    if layer.unit == KH:
        return layer.value * diameter_m
    factor = layer.alpha if layer.alpha is not None else alpha
    if factor is None or groepsfactor is None:
        raise ValueError(f"{MENARD} needs alpha and groepsfactor")
    if not 0 < factor <= 1 or groepsfactor <= 0:
        raise ValueError("alpha must be above 0 and at most 1, and groepsfactor above 0")
    return menard_kh(diameter_m * 1000.0, layer.value * 1000.0, factor, groepsfactor) * diameter_m


def _number(raw: object) -> float:
    return float(raw.replace(",", ".").strip()) if isinstance(raw, str) else float(raw)


def parse_layers(rows: list[dict], variant: str | None = None) -> list[SoilLayer]:
    """Turn table rows into validated layers, top down. Raises LayerError with all problems."""
    problems: list[str] = []
    layers: list[SoilLayer] = []
    variant_columns = [f"value_{v}" for v in VARIANTS]
    has_variants = any(column in row for row in rows for column in variant_columns)

    if variant is not None and variant not in VARIANTS:
        problems.append(f"Unknown variant {variant!r}; use one of {', '.join(VARIANTS)}.")
    elif has_variants and variant is None:
        problems.append(f"The table has variant columns; choose one of {', '.join(VARIANTS)}.")
    elif variant is not None and not has_variants:
        problems.append(f"Variant {variant!r} was asked for, but the table has no value_{variant} column.")
    if not rows:
        problems.append("The layer table is empty.")
    if problems:
        raise LayerError(problems)

    column = f"value_{variant}" if variant else "value"
    for number, row in enumerate(rows, start=1):
        try:
            layer = SoilLayer(
                top_nap=_number(row["top"]),
                bottom_nap=_number(row["bottom"]),
                value=_number(row[column]),
                unit=str(row["unit"]).strip(),
                description=str(row.get("description", "") or "").strip(),
                alpha=_number(row["alpha"]) if row.get("alpha") not in (None, "") else None,
            )
        except KeyError as exc:
            problems.append(f"Row {number}: column {exc.args[0]!r} is missing.")
            continue
        except (TypeError, ValueError):
            problems.append(f"Row {number}: a number could not be read: {row}.")
            continue
        if not all(math.isfinite(v) for v in (layer.top_nap, layer.bottom_nap, layer.value)):
            problems.append(f"Row {number}: levels and value must be real numbers.")
            continue
        if layer.unit not in UNITS:
            problems.append(f"Row {number}: unknown unit {layer.unit!r}; use one of {', '.join(UNITS)}.")
        if layer.value <= 0:
            problems.append(f"Row {number}: the value must be positive.")
        if layer.top_nap <= layer.bottom_nap:
            problems.append(f"Row {number}: top NAP {layer.top_nap} is not above bottom NAP {layer.bottom_nap}.")
        layers.append(layer)

    for above, below in zip(layers, layers[1:]):
        if below.top_nap > above.bottom_nap:
            problems.append(
                f"Layers overlap or are out of order: {above.top_nap}..{above.bottom_nap} then "
                f"{below.top_nap}..{below.bottom_nap}. List them top down without overlap."
            )
    if problems:
        raise LayerError(problems)
    return layers


def read_layer_rows(path: Path) -> list[dict]:
    """Read table rows from a .json or .csv file (comma- or semicolon-separated)."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(rows, list):
            raise LayerError([f"{path.name}: expected a list of layers."])
        return rows
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:  # Excel's default "CSV" on a Dutch Windows
        text = raw.decode("cp1252")
    first_line = text.splitlines()[0] if text else ""
    delimiter = ";" if ";" in first_line else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    return [{(k or "").strip().lower(): v for k, v in row.items()} for row in reader]
