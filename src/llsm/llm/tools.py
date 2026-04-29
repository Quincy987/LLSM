"""JSON Schemas for LLSM tools — fed to Anthropic's tool-use API.

Each entry defines the tool the LLM can call, in Anthropic's format:
    {"name": ..., "description": ..., "input_schema": {...}}

Names MUST match function names in llsm.scia.tools.
"""
from __future__ import annotations

TOOL_DEFS = [
    {
        "name": "new_model",
        "description": "Start a fresh in-memory ADM model. Call this FIRST before adding anything.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable model name."},
            },
            "required": [],
        },
    },
    {
        "name": "add_material",
        "description": "Add a structural material (Steel / Concrete / Timber / Aluminium). Use well-known grade names like S235, S355, C25/30.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["Steel", "Concrete", "Timber", "Aluminium"]},
            },
            "required": ["name"],
        },
    },
    {
        "name": "add_rectangular_section",
        "description": "Add a rectangular cross-section parameterised by width x height (in mm). Use for simple beams/columns when real IPE/HEA profiles are not needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "material": {"type": "string", "description": "Name of previously-added material."},
                "width_mm": {"type": "number"},
                "height_mm": {"type": "number"},
            },
            "required": ["name", "material", "width_mm", "height_mm"],
        },
    },
    {
        "name": "add_node",
        "description": "Add a structural point (node) at coordinates in mm. Global axes: X horizontal, Y horizontal-perpendicular, Z vertical (up positive).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "x_mm": {"type": "number"},
                "y_mm": {"type": "number"},
                "z_mm": {"type": "number"},
            },
            "required": ["name", "x_mm", "y_mm", "z_mm"],
        },
    },
    {
        "name": "add_beam",
        "description": "Add a straight 1D beam member between two existing nodes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "node_a": {"type": "string"},
                "node_b": {"type": "string"},
                "cross_section": {"type": "string"},
            },
            "required": ["name", "node_a", "node_b", "cross_section"],
        },
    },
    {
        "name": "add_fixed_support",
        "description": "Add a fully-restrained (fixed) 3D point support on a node. All 6 DOFs rigid.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "node": {"type": "string"},
            },
            "required": ["name", "node"],
        },
    },
    {
        "name": "add_pinned_support",
        "description": "Add a pinned point support — translations restrained, rotations free.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "node": {"type": "string"},
            },
            "required": ["name", "node"],
        },
    },
    {
        "name": "add_roller_support",
        "description": "Add a roller support — rigid only in one direction (X/Y/Z), free elsewhere.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "node": {"type": "string"},
                "direction": {"type": "string", "enum": ["X", "Y", "Z"]},
            },
            "required": ["name", "node"],
        },
    },
    {
        "name": "add_load_group",
        "description": "Add a load group. Required before creating any load cases.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["Permanent", "Variable", "Accidental"]},
            },
            "required": ["name"],
        },
    },
    {
        "name": "add_load_case",
        "description": "Add a load case inside a load group. Use kind=SelfWeight for auto-self-weight, Standard for user loads.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "group": {"type": "string"},
                "kind": {"type": "string", "enum": ["Standard", "SelfWeight"]},
            },
            "required": ["name", "group"],
        },
    },
    {
        "name": "add_line_load",
        "description": "Add a uniform line load on an existing beam, in kN/m. Negative direction=Z = gravity-style downward.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "beam": {"type": "string"},
                "load_case": {"type": "string"},
                "magnitude_kn_per_m": {"type": "number"},
                "direction": {"type": "string", "enum": ["X", "Y", "Z"]},
            },
            "required": ["name", "beam", "load_case", "magnitude_kn_per_m"],
        },
    },
    {
        "name": "model_summary",
        "description": "Return a summary of the current in-memory ADM model (item count + list of named objects). Use to verify state before pushing.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "launch_scia",
        "description": "Launch the SCIA Engineer application. Runs minimized by default (visible=false). Call this once before push_model.",
        "input_schema": {
            "type": "object",
            "properties": {
                "visible": {"type": "boolean", "description": "If true, SCIA window is shown normally instead of minimized."},
            },
            "required": [],
        },
    },
    {
        "name": "push_model",
        "description": "Open an empty SCIA project and push the current in-memory ADM model into it. Call AFTER all add_* tools.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "run_analysis",
        "description": "Generate the FE mesh and run linear analysis. Returns whether the Results API is ready.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "close_scia",
        "description": "Close SCIA and clean up. Call at the end of the workflow.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_DEFS]


# Phase 2 result tools
TOOL_DEFS.extend([
    {
        "name": "get_beam_inner_forces",
        "description": "Read beam internal forces (N, Vy, Vz, Mx, My, Mz per section) for a given load case. Forces in N, moments in N*m. Call AFTER run_analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "beam": {"type": "string"},
                "load_case": {"type": "string"},
            },
            "required": ["beam", "load_case"],
        },
    },
    {
        "name": "get_beam_deflection",
        "description": "Read beam deformation (Ux, Uy, Uz, Fix, Fiy, Fiz per section) for a given load case. Translations in metres (multiply by 1000 for mm). Call AFTER run_analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "beam": {"type": "string"},
                "load_case": {"type": "string"},
            },
            "required": ["beam", "load_case"],
        },
    },
])
TOOL_NAMES = [t["name"] for t in TOOL_DEFS]
