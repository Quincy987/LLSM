"""Layer tables and the conversion to a line stiffness; expected values worked out by hand."""
import pytest

from llsm.bedding.layers import (
    K_LINE,
    KH,
    MENARD,
    LayerError,
    SoilLayer,
    line_stiffness,
    menard_kh,
    parse_layers,
    read_layer_rows,
)


def row(top, bottom, value=5000, unit=KH, **extra):
    return {"top": top, "bottom": bottom, "value": value, "unit": unit, "description": "klei", **extra}


def test_kh_is_multiplied_by_the_diameter():
    assert line_stiffness(SoilLayer(-2, -8, 5000, KH, ""), diameter_m=0.45) == pytest.approx(2250)


def test_line_stiffness_is_used_as_given():
    assert line_stiffness(SoilLayer(-2, -8, 2250, K_LINE, "")) == 2250


def test_menard_large_pile_branch():
    # R = 0.4 >= R0: 1/kh = (1/(3E)) * (1.3*0.3*(2.65*0.4/0.3)^(1/3) + 0.4/3), E = 10000 kN/m2
    assert menard_kh(800, 10000, 1 / 3, 1.0) == pytest.approx(41246, rel=1e-4)


def test_menard_small_pile_branch():
    # R = 0.2 < R0: 1/kh = (2*0.2/E) * ((4*2.65^(1/3) + 1)/18), E = 10000 kN/m2
    assert menard_kh(400, 10000, 1 / 3, 1.0) == pytest.approx(68858, rel=1e-4)


def test_menard_layer_converts_mpa_and_metres():
    layer = SoilLayer(-2, -8, 10, MENARD, "")
    assert line_stiffness(layer, diameter_m=0.4, alpha=1 / 3, groepsfactor=0.5) == pytest.approx(68858 * 0.5 * 0.4, rel=1e-4)


def test_layer_alpha_overrides_the_table_alpha():
    layer = SoilLayer(-2, -8, 10, MENARD, "", alpha=2 / 3)
    expected = menard_kh(400, 10000, 2 / 3, 1.0) * 0.4
    assert line_stiffness(layer, diameter_m=0.4, alpha=1 / 3, groepsfactor=1.0) == pytest.approx(expected)


@pytest.mark.parametrize("unit,kwargs", [(KH, {}), (MENARD, {"diameter_m": 0.4}), (MENARD, {"diameter_m": 0.4, "alpha": 1 / 3})])
def test_missing_parameters_are_refused(unit, kwargs):
    with pytest.raises(ValueError):
        line_stiffness(SoilLayer(-2, -8, 10, unit, ""), **kwargs)


def test_decimal_comma_and_order():
    layers = parse_layers([row("-2,5", "-8,0", "5000,5"), row(-8, -14)])
    assert (layers[0].top_nap, layers[0].value) == (-2.5, 5000.5)


def test_all_problems_are_reported_together():
    with pytest.raises(LayerError) as caught:
        parse_layers([row(-2, -8, unit="kPa"), row(-8, -6), {"top": -9}])
    text = " ".join(caught.value.problems)
    assert "unknown unit" in text and "not above" in text and "missing" in text


@pytest.mark.parametrize("rows", [[row(-2, -8), row(-6, -10)], [row(-8, -14), row(-2, -8)]])
def test_overlapping_or_unordered_layers_are_refused(rows):
    with pytest.raises(LayerError, match="overlap"):
        parse_layers(rows)


def variant_row(top, bottom):
    return {"top": top, "bottom": bottom, "value_laag": 1, "value_gemiddeld": 2, "value_hoog": 3, "unit": KH}


def test_variant_picks_its_column():
    assert parse_layers([variant_row(-2, -8)], "hoog")[0].value == 3


def test_variant_is_required_when_the_columns_exist():
    with pytest.raises(LayerError, match="variant"):
        parse_layers([variant_row(-2, -8)])


def test_variant_is_refused_without_columns():
    with pytest.raises(LayerError, match="no value_hoog"):
        parse_layers([row(-2, -8)], "hoog")


def test_empty_table_is_refused():
    with pytest.raises(LayerError, match="empty"):
        parse_layers([])


def test_csv_with_semicolons_and_decimal_commas(tmp_path):
    path = tmp_path / "layers.csv"
    path.write_text("top;bottom;value;unit;description\n-2;-8;5000,5;kh_kN_m3;klei\n", encoding="utf-8")
    assert parse_layers(read_layer_rows(path))[0].value == 5000.5


def test_json_table(tmp_path):
    path = tmp_path / "layers.json"
    path.write_text('[{"top": -2, "bottom": -8, "value": 5000, "unit": "kh_kN_m3"}]', encoding="utf-8")
    assert parse_layers(read_layer_rows(path))[0].description == ""
