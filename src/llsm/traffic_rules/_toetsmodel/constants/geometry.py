"""
Geometry and lane constants for SCIA integration.

These constants define lane dimensions, bridge geometry thresholds,
and spatial relationships used in load positioning calculations.
"""

# Lane dimensions
DEFAULT_LANE_WIDTH = 3.0  # Standard lane width in meters
MIN_BRIDGE_WIDTH_SINGLE_LANE = 5.4  # Minimum width for single lane configuration (m)
MAX_BRIDGE_WIDTH_TWO_LANES = 6.0  # Maximum width for two-lane configuration (m)

# Tandem system wheel offsets and spacing
TANDEM_WHEEL_SPACING_LONGITUDINAL = 1.2  # Distance between wheels in longitudinal direction (meters)
TANDEM_WHEEL_SPACING_TRANSVERSE = 2.0  # Distance between wheels in transverse direction (meters)

# Bridge geometry thresholds
MINIMUM_BRIDGE_WIDTH_FOR_MULTIPLE_LANES = 9.0  # Minimum bridge width for certain lane configurations (meters)

# Load positioning offsets
TANDEM_START_Y_OFFSET = 1.2  # Y-offset for tandem positioning calculations (meters)
LANE_CENTER_OFFSET_FACTOR = 0.5  # Factor for lane center calculations (0.5 = half lane width, used for lane_width / 2)

# Vehicle positioning
VEHICLE_INSET_FROM_BRIDGE_EDGE = 0.5  # Standard inset distance from bridge edge (meters)

# Vehicle dimensions
TANDEM_VEHICLE_LENGTH = 1.6  # Length of tandem vehicle for sequencing (meters)

# Tandem system spacing and positioning
TANDEM_SPACING_LONGITUDINAL = 1.0  # Default spacing between tandem systems (meters)
TANDEM_START_Y_OFFSET_FACTOR = 0.9  # Factor for tandem start Y calculation (0.9 * thickness)

# Additional geometry constants
LANE_CENTER_OFFSET_FACTOR = 0.5  # Factor for lane center calculations (0.5 = half lane width, used for lane_width / 2)
TANDEM_WHEEL_SIZE = 0.4  # Standard tandem wheel size (meters)

# Support stiffness values
SUPPORT_STIFFNESS_X_DEFAULT = 1e7  # N/m (flexible support X-direction)
SUPPORT_STIFFNESS_Y_DEFAULT = 1e6  # N/m (flexible support Y-direction)

# Plate structure indices
PLATES_PER_SEGMENT = 3  # Number of plates per bridge segment (Z1, Z2, Z3)
SUPPORT_EDGE_START = 4  # Edge index for support at bridge start
SUPPORT_EDGE_END = 2  # Edge index for support at bridge end

# Load dispersion
MINIMUM_LOAD_DISPERSION = 0.5  # Minimum dispersion distance (m)
