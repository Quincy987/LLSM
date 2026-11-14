"""
Tandem system sequencer for generating load positions.

This module provides utilities for calculating tandem system positions along bridge decks.
"""

from llsm.traffic_rules._toetsmodel.constants.geometry import (
    TANDEM_SPACING_LONGITUDINAL,
    TANDEM_START_Y_OFFSET_FACTOR,
    TANDEM_WHEEL_SPACING_LONGITUDINAL,
    TANDEM_WHEEL_SPACING_TRANSVERSE,
)

# Standard tandem wheel offsets from bottom left corner
TANDEM_WHEEL_OFFSETS = [
    (0, 0),
    (TANDEM_WHEEL_SPACING_LONGITUDINAL, 0),
    (0, TANDEM_WHEEL_SPACING_TRANSVERSE),
    (TANDEM_WHEEL_SPACING_LONGITUDINAL, TANDEM_WHEEL_SPACING_TRANSVERSE),
]


def calculate_start_of_lanes(thickness_bridgedeck: float) -> float:
    """
    Calculate the distance from the edge of the bridge deck, from where the tandem systems start.
    Assuming a spread under 45 degrees, the distance is equal to 0.9 times the thickness of the bridge deck.

    Args:
        thickness_bridgedeck (float): The thickness of the bridge deck in meters.

    Returns:
        distance(float): The distance in meters from the edge of the bridge deck to the start of the tandem systems.

    """
    return TANDEM_START_Y_OFFSET_FACTOR * thickness_bridgedeck


def tandem_system_sequencer(  # noqa: C901
    length_bridgedeck: float,
    thickness_bridgedeck: float,
    length_vehicle: float = 0.0,
    spacing: float = TANDEM_SPACING_LONGITUDINAL,
    support_x_coords: list[float] | None = None,
) -> list[float]:
    """
    Calculate the longitudinal x-positions of tandem systems along the bridge deck.

    The tandem systems are positioned starting from an offset based on the bridge deck thickness
    (accounting for load spread), and distributed at intervals along the deck length.
    For improved accuracy near supports (where shear forces are critical), the function uses
    fine spacing (0.5 * spacing) for 3 positions on each side of every support, while middle
    positions use the standard spacing. The function ensures that mid-span positions for each
    span are always included (critical for maximum bending moment analysis).

    The starting offset is calculated as TANDEM_START_Y_OFFSET_FACTOR * thickness_bridgedeck,
    then adjusted by the vehicle length.

    Args:
        length_bridgedeck (float): The total length of the bridge deck in meters.
        thickness_bridgedeck (float): The thickness of the bridge deck in meters, used to calculate
            the starting offset based on load spread assumption (45-degree angle).
        length_vehicle (float): The length of the vehicle in meters. Defaults to 0.0.
        spacing (float): The longitudinal spacing between consecutive tandem systems in meters.
            Defaults to TANDEM_SPACING_LONGITUDINAL (1.0 meters).
        support_x_coords (list[float] | None): X-coordinates of all support locations from bridge geometry.
            If None, defaults to supports at start (X=0) and end (X=length_bridgedeck) only.
            If provided, fine spacing is applied around all supports including intermediate ones.

    Returns:
        list[float]: A sorted list of unique x-positions (in meters) where tandem systems should be
            placed along the bridge deck, with fine spacing near supports and mid-span positions.

    """
    start_of_lanes = calculate_start_of_lanes(thickness_bridgedeck) - length_vehicle
    end_span_position = length_bridgedeck - start_of_lanes - length_vehicle
    fine_spacing = 0.5 * spacing  # Fine spacing for near-support zones

    # Use support coordinates from bridge geometry, or default to start/end
    support_locations = [0.0, length_bridgedeck] if support_x_coords is None or len(support_x_coords) == 0 else sorted(set(support_x_coords))

    # Define fine spacing zones around each support (3 positions on each side)
    # Zone extends from support - 2×fine_spacing to support + 2×fine_spacing
    # This gives: [support-2×fine, support-fine, support, support+fine, support+2×fine]
    fine_zones = []
    for support_x in support_locations:
        zone_start = support_x - 2.0 * fine_spacing
        zone_end = support_x + 2.0 * fine_spacing
        fine_zones.append((zone_start, zone_end))

    def is_in_fine_zone(x: float) -> bool:
        """Check if position x is within any fine spacing zone."""
        return any(zone_start - 1e-6 <= x <= zone_end + 1e-6 for zone_start, zone_end in fine_zones)

    def will_enter_fine_zone(current_x: float, next_spacing: float) -> bool:
        """Check if taking a coarse step would overshoot into or past a fine zone."""
        next_x = current_x + next_spacing
        # Check if we'd cross into a fine zone with this step
        for zone_start, zone_end in fine_zones:
            # Check if we'd cross the zone start boundary
            if current_x < zone_start - 1e-6 and next_x >= zone_start - 1e-6:
                return True
            # Also check if we're approaching the zone and next step would overshoot end_span_position
            if zone_start <= end_span_position <= zone_end and current_x < zone_start - 1e-6 and next_x > end_span_position:
                # The end_span_position is in this fine zone and we'd skip past positions that should be in fine spacing
                return True
        return False

    tandem_systems = []
    pos = start_of_lanes

    # Generate positions from start to end
    while pos <= end_span_position + 1e-6:
        tandem_systems.append(round(pos, 6))

        # Determine spacing for next step
        in_zone = is_in_fine_zone(pos)
        will_enter = will_enter_fine_zone(pos, spacing)

        if in_zone:
            # Currently in fine zone, use fine spacing
            next_spacing = fine_spacing
        elif will_enter:
            # Would overshoot into fine zone with coarse spacing, switch to fine
            next_spacing = fine_spacing
        else:
            # Safe to use coarse spacing
            next_spacing = spacing

        pos += next_spacing

    # Ensure end_span_position is included
    if not any(abs(p - end_span_position) < 1e-6 for p in tandem_systems):
        tandem_systems.append(round(end_span_position, 6))

    # Ensure mid-span position for each span is included
    for i in range(len(support_locations) - 1):
        span_start = support_locations[i]
        span_end = support_locations[i + 1]
        span_mid = (span_start + span_end) / 2

        # Check if mid-span is already included (within tolerance)
        if not any(abs(p - span_mid) < 1e-6 for p in tandem_systems):
            tandem_systems.append(round(span_mid, 6))

    return sorted(set(tandem_systems))
