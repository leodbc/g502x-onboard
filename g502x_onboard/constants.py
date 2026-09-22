from __future__ import annotations

SECTOR_SIZE = 255
SECTOR_COUNT = 16
CRC_SIZE = 2
PAGE_DATA_SIZE = SECTOR_SIZE - CRC_SIZE

SAFE_PROFILE = 1
PROGRAMMABLE_PROFILES = (2, 3, 4, 5)
RECOVERY_SECTORS = (1, 6, 7)
DIRECTORY_SECTOR = 0
# Firmware/G HUB may address post-profile sectors 6..15 as Macro Format 1 pages.
# Our compiler deliberately allocates only 8..15 so sectors 6/7 remain protected.
ADDRESSABLE_MACRO_SECTORS = tuple(range(6, 16))
GLOBAL_MACRO_SECTORS = tuple(range(8, 16))
PROGRAMMABLE_SECTORS = (0, 2, 3, 4, 5, *GLOBAL_MACRO_SECTORS)

GLOBAL_RAW_CAPACITY = len(GLOBAL_MACRO_SECTORS) * SECTOR_SIZE
GLOBAL_PAYLOAD_CAPACITY = len(GLOBAL_MACRO_SECTORS) * PAGE_DATA_SIZE

PROFILE_DIRECTORY_ENABLE_OFFSETS = {
    1: 0x02,
    2: 0x06,
    3: 0x0A,
    4: 0x0E,
    5: 0x12,
}

PROFILE3_TARGET_ORDER = (
    "G1",
    "G2",
    "G3",
    "G4",
    "G6",
    "G5",
    "TILT_LEFT",
    "TILT_RIGHT",
    "G9",
    "G8",
    "G7",
    "WHEEL_DOWN",
    "WHEEL_UP",
)

PROFILE3_NORMAL_OFFSETS = {
    target: 0x20 + index * 4
    for index, target in enumerate(PROFILE3_TARGET_ORDER)
}
PROFILE3_GSHIFT_OFFSETS = {
    target: 0x60 + index * 4
    for index, target in enumerate(PROFILE3_TARGET_ORDER)
}

PROFILE_BINDING_NOOP = bytes.fromhex("90 00 00 00")
PROFILE_BINDING_GSHIFT = bytes.fromhex("90 0B 00 00")

REPORT_RATE_CODES = {
    1000: 0x01,
    500: 0x02,
    250: 0x04,
    125: 0x08,
}
REPORT_RATE_BY_CODE = {value: key for key, value in REPORT_RATE_CODES.items()}

OP_WAIT_FOR_RELEASE = 0x01
OP_REPEAT_WHILE_PRESSED = 0x02
OP_ROLLER = 0x20
OP_ACPAN = 0x21
OP_DELAY = 0x40
OP_KEY_DOWN = 0x43
OP_KEY_UP = 0x44
OP_CONSUMER_DOWN = 0x45
OP_CONSUMER_UP = 0x46
OP_JUMP = 0x60
OP_XY = 0x61
OP_END = 0xFF

KEYS = {
    **{chr(ord("A") + i): 0x04 + i for i in range(26)},
    "1": 0x1E,
    "2": 0x1F,
    "3": 0x20,
    "4": 0x21,
    "5": 0x22,
    "6": 0x23,
    "7": 0x24,
    "8": 0x25,
    "9": 0x26,
    "0": 0x27,
    "ENTER": 0x28,
    "ESC": 0x29,
    "BACKSPACE": 0x2A,
    "TAB": 0x2B,
    "SPACE": 0x2C,
    "F1": 0x3A,
    "F2": 0x3B,
    "F3": 0x3C,
    "F4": 0x3D,
    "F5": 0x3E,
    "F6": 0x3F,
    "F7": 0x40,
    "F8": 0x41,
    "F9": 0x42,
    "F10": 0x43,
    "F11": 0x44,
    "F12": 0x45,
    "RIGHT": 0x4F,
    "LEFT": 0x50,
    "DOWN": 0x51,
    "UP": 0x52,
}
KEY_NAMES_BY_USAGE = {value: key for key, value in KEYS.items()}

MODS = {
    "CTRL": (0x01, 0xE0),
    "SHIFT": (0x02, 0xE1),
    "ALT": (0x04, 0xE2),
    "WIN": (0x08, 0xE3),
}

DIRECT_MOUSE_MASKS = {
    "left": 0x0001,
    "right": 0x0002,
    "middle": 0x0004,
    "back": 0x0008,
    "forward": 0x0010,
}

CONSUMER_USAGES = {
    "volume_up": 0x00E9,
    "volume_down": 0x00EA,
}

ALIASES = {
    "copy": {"action": "hotkey", "keys": ["CTRL", "C"]},
    "paste": {"action": "hotkey", "keys": ["CTRL", "V"]},
    "select_all": {"action": "hotkey", "keys": ["CTRL", "A"]},
    "switch_window": {"action": "hotkey", "keys": ["ALT", "TAB"]},
    "task_manager": {"action": "hotkey", "keys": ["CTRL", "SHIFT", "ESC"]},
    "volume_up": {"action": "consumer", "command": "volume_up"},
    "volume_down": {"action": "consumer", "command": "volume_down"},
}

REQUIRES_HOST_ACTIONS = {
    "run",
    "launch",
    "start_application",
    "unicode",
    "unicode_text",
    "emoji",
    "obs",
    "discord",
    "overwolf",
    "app_action",
}

US_CHAR_MAP = {
    "-": (0x00, 0x2D),
    "_": (0x02, 0x2D),
    "=": (0x00, 0x2E),
    "+": (0x02, 0x2E),
    "[": (0x00, 0x2F),
    "{": (0x02, 0x2F),
    "]": (0x00, 0x30),
    "}": (0x02, 0x30),
    "\\": (0x00, 0x31),
    "|": (0x02, 0x31),
    ";": (0x00, 0x33),
    ":": (0x02, 0x33),
    "'": (0x00, 0x34),
    '"': (0x02, 0x34),
    "`": (0x00, 0x35),
    "~": (0x02, 0x35),
    ",": (0x00, 0x36),
    "<": (0x02, 0x36),
    ".": (0x00, 0x37),
    ">": (0x02, 0x37),
    "/": (0x00, 0x38),
    "?": (0x02, 0x38),
    "!": (0x02, 0x1E),
    "@": (0x02, 0x1F),
    "#": (0x02, 0x20),
    "$": (0x02, 0x21),
    "%": (0x02, 0x22),
    "^": (0x02, 0x23),
    "&": (0x02, 0x24),
    "*": (0x02, 0x25),
    "(": (0x02, 0x26),
    ")": (0x02, 0x27),
}

CAPABILITIES = {
    "direct_keyboard": ("HARDWARE_VALIDATED", "autonomous-hid", "Direct Keyboard HID."),
    "direct_mouse": ("HARDWARE_VALIDATED", "autonomous-hid", "left/right/middle/back/forward."),
    "direct_consumer": ("HARDWARE_VALIDATED", "autonomous-hid", "Volume Up/Down."),
    "macro_keyboard": ("HARDWARE_VALIDATED", "onboard-vm", "KEY_DOWN/KEY_UP and HID text."),
    "macro_consumer": ("HARDWARE_VALIDATED", "onboard-vm", "Consumer DOWN/UP."),
    "wheel_hpan": ("HARDWARE_VALIDATED", "onboard-vm", "ROLLER and ACPAN."),
    "wait_repeat": ("HARDWARE_VALIDATED", "onboard-vm", "WAIT_FOR_RELEASE and REPEAT_WHILE_PRESSED."),
    "jump": ("HARDWARE_VALIDATED", "onboard-vm", "5-byte cross-page JUMP."),
    "global_store": (
        "HARDWARE_VALIDATED",
        "profile-format-3",
        "Sectors 8..15, 2040 raw / 2024 VM payload bytes.",
    ),
    "gshift": ("HARDWARE_VALIDATED", "profile-format-3", "Direct HID and Macro VM under G-Shift."),
    "profile_metadata": ("HARDWARE_VALIDATED", "profile-format-3", "name/report-rate/DPI fields."),
    "text_basic": (
        "HARDWARE_VALIDATED_LIMITED",
        "autonomous-hid",
        "Basic keyboard text; US punctuation opt-in and layout-dependent.",
    ),
    "unicode_emoji": ("HOST_REQUIRED", "host-semantic", "No autonomous Unicode primitive found."),
    "start_application": ("HOST_REQUIRED", "host-semantic", "G HUB native launch not autonomous."),
    "app_integrations": ("HOST_REQUIRED", "host-semantic", "OBS/Discord/Overwolf not onboard primitives."),
    "macro_mouse_down_up": ("EXTERNAL_EVIDENCE", "onboard-vm", "0x41/0x42 bitmask encoding is implemented elsewhere; local hardware validation intentionally remains pending."),
    "repeat_until_cancelled": ("DISABLED", "onboard-vm", "0x03 intentionally disabled."),
}
