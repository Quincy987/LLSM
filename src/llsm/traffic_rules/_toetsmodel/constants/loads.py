"""
SCIA load specification constants.

These constants define the engineering values used for load calculations
in SCIA Engineer integration, including tandem systems, UDL values,
and alpha factors according to Dutch standards.
"""

# Tandem System (TS) Load Base Value (all lanes start from this base)
TANDEM_LOAD_BASE_VALUE = 100000  # N (100 kN) applied uniformly to all tandem polygons

TANDEM_MAIN_LANE_FACTOR = 3
TANDEM_SECOND_LANE_FACTOR = 2
TANDEM_THIRD_LANE_FACTOR = 1
# Tandem System Contact Dimensions
TANDEM_CONTACT_AREA_SIDE = 0.4  # m (0.4m x 0.4m contact patch)

# UDL (Uniformly Distributed Load) Values
UDL_BASE_VALUE = 2500.0  # N/m² - Base load value for all UDL polygons

# UDL Lane Factors (for load combinations)
UDL_MAIN_LANE_FACTOR = 3.6  # Factor for main notional lane (2500 × 3.6 = 9000)
UDL_OTHER_LANE_FACTOR = 1.0  # Factor for adjacent notional lanes
UDL_REST_AREA_FACTOR = 1.0  # Factor for rest areas

# Crowd load (NEN-EN 1991-2 art. 5.3.2.1 LM4)
CROWD_LOAD_PER_SQM = 5.0  # kN/m²
CROWD_LOAD_PER_SQM_N = CROWD_LOAD_PER_SQM * 1000.0  # Convert to N/m²

# Alpha factors for load calculations
ALPHA_Q_ONDERLIGGEND = 0.8  # Alpha Q factor for underlying road network
ALPHA_Q_MAIN_LANE_ONDERLIGGEND = 1.35  # Alpha Q for main lane, underlying network
ALPHA_Q_OTHER_LANE_ONDERLIGGEND = 1.0  # Alpha Q for other lanes
NOBS_DEFAULT = 20000  # Default number of trucks per year for NEN-EN 1991-2

# Reference period values for load factor calculations
REFERENCE_PERIOD_AFKEUR = 15  # Years for NEN 8700 afkeur design code
REFERENCE_PERIOD_DEFAULT = 30  # Years for other design codes (gebruik, verbouw)

# Signage options (must match app/constants/technical.py SIGNAGE_OPTIONS)
SIGNAGE_WEIGHT_OPTIONS = ["50 ton", "45 ton", "40 ton", "35 ton", "30 ton", "25 ton", "20 ton"]
