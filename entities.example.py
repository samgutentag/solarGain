"""Sensor map template for the solar-gain study.

Copy this file to entities.py and edit it for your house:

    cp entities.example.py entities.py

entities.py is gitignored (like .env) because it describes your home. This
example is a working generic layout; `./run.sh --mock` builds a full demo
dashboard from it without touching a real Home Assistant.

How resolution works: `match` is the friendly name looked up against
/api/states — exact match first, then unique substring. Keys are stable series
ids used across the pipeline; the keys 'outdoor' and 'attic' are REQUIRED
(every model fits against them). Add or remove rooms freely.

`group` controls how a series is treated:
  driver     — outdoor and attic, the heat sources every model fits against
  room       — living spaces (an optional 'ac' key links a room to its AC unit;
               two rooms may share one unit, e.g. an open floor plan)
  reference  — spaces outside the attic envelope, kept as a control group
"""

TEMPERATURE_SENSORS = {
    # drivers (required)
    'outdoor': {'match': 'Outdoor Temperature', 'label': 'Outdoor', 'group': 'driver'},
    'attic': {'match': 'Attic Temperature', 'label': 'Attic', 'group': 'driver'},
    # conditioned rooms
    'bedroom': {'match': 'Bedroom Temperature', 'label': 'Bedroom', 'group': 'room', 'ac': 'bedroom_ac'},
    'living_room': {'match': 'Living Room Temperature', 'label': 'Living Room', 'group': 'room', 'ac': 'living_room_ac'},
    'kitchen': {'match': 'Kitchen Temperature', 'label': 'Kitchen', 'group': 'room', 'ac': 'living_room_ac'},
    # unconditioned rooms in the same attic envelope make great test
    # instruments for the insulation model — keep at least one if you can
    'bathroom': {'match': 'Bathroom Temperature', 'label': 'Bathroom', 'group': 'room'},
    # reference zone outside the attic envelope (delete if you have none)
    'garage': {'match': 'Garage Temperature', 'label': 'Garage', 'group': 'reference'},
}

SOLAR_SENSORS = {
    # any entity reporting current PV production in kW (Enphase Envoy, SolarEdge, ...)
    'solar_power': {'match': 'Current Power Production', 'label': 'Solar production', 'unit': 'kW'},
}

WIND_SENSORS = {
    # optional — the wind card stays empty without it
    'wind_speed': {'match': 'Wind Speed', 'label': 'Wind speed', 'unit': 'mph', 'group': 'wind'},
}

CLIMATE_ENTITIES = {
    # climate-domain AC units (runtime from state history; no power data)
    'living_room_ac': {'match': 'Living Room AC', 'label': 'Living Room AC'},
}

# AC units on energy-monitoring smart plugs (Emporia, Kasa, ...). Point each at
# the plug's cumulative DAILY energy sensor (the "Energy Today" kind that climbs
# all day and resets at midnight) by exact entity id — power is derived from its
# slope. threshold_w marks "compressor running": above standby, below compressor.
POWER_AC_SENSORS = {
    'bedroom_ac': {'entity_id': 'sensor.bedroom_ac_energy_today', 'label': 'Bedroom AC', 'threshold_w': 150},
}
