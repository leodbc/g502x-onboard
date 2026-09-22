# SPDX-License-Identifier: GPL-3.0-only
# Derived from lexr1/omm.py:
# https://github.com/lexr1/omm.py
# Upstream commit: 8e6c3f00a20acbde1d3001244c9ca24db613804f
# See NOTICE.md and docs/PROVENANCE.md.

# Inherited from the recorded omm.py lineage.
from enum import IntEnum

            
class Feature(IntEnum):
    root = 0
    device_name = 0x0005
    switch_host = 0x1814
    host_info = 0x1815
    hires_wheel = 0x2121
    adjustable_dpi = 0x2201
    pointer_speed = 0x2205
    report_rate = 0x8060
    extended_report_rate = 0x8061    
    color_led_control = 0x8070
    onboard_profile = 0x8100
