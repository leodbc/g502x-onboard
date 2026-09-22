# Configuration

G502 X Onboard configuration files are JSON with `"format": 1`.

Only Profiles 2-5 are programmable by the compiler. Profile 1 is reserved as the
SAFE/recovery profile and is never synthesized from a config file.

## Minimal structure

```json
{
  "format": 1,
  "profiles": {
    "2": {
      "settings": {
        "name": "WORK",
        "polling_rate_hz": 1000,
        "dpi": [800, 1600, 3200],
        "default_dpi": 1600,
        "shift_dpi": 800
      },
      "buttons": {
        "G4": "copy"
      }
    }
  }
}
```

Profile 2 is required so omitted programmable state is never ambiguous.
Profiles 3-5 are optional.

## Profile settings

Supported settings:

- `name` — profile name, up to 24 UTF-16 code units;
- `polling_rate_hz` — `1000`, `500`, `250`, or `125`;
- `dpi` — 1-5 DPI values;
- `default_dpi` — must match an effective DPI slot;
- `shift_dpi` — must match an effective DPI slot.

The public config uses DPI values rather than slot indexes. The compiler resolves
those values to the Profile Format 3 indexes written to the mouse.

## Buttons and G-Shift

`buttons` describes the normal layer. `g_shift_button` selects the physical
G-Shift activator and `g_shift` describes the shifted layer.

Supported targets are G1-G9, TILT_LEFT, TILT_RIGHT, WHEEL_UP and WHEEL_DOWN.
The wheel-event slots have narrower hardware-validated semantics; invalid
combinations fail configuration validation.

## Actions

Bindings can be aliases such as:

- `copy`
- `paste`
- `select_all`
- `switch_window`
- `task_manager`
- `volume_up`
- `volume_down`

Structured actions include:

- `hotkey`
- `tap`
- `text`
- `delay`
- `consumer`
- `mouse` for validated direct mouse commands
- `wheel`
- `horizontal_wheel`
- `wait_for_release`
- `repeat_while_pressed`
- `sequence`

Host-semantic actions such as launching applications, arbitrary Unicode/emoji,
OBS/Discord/Overwolf integrations are not represented as autonomous onboard
primitives.

## Workflow

Validate and inspect a plan without writing:

```bash
python g502x.py plan my-config.json
python g502x.py capacity my-config.json
```

Apply only after setup and review:

```bash
python g502x.py apply my-config.json
python g502x.py validate
python g502x.py status
```

The bundled JSON Schema is available for editor/tooling assistance:

```bash
python g502x.py schema
python g502x.py schema --path
```

Runtime validation remains authoritative for semantic constraints that JSON
Schema does not fully express, including known aliases, DPI membership,
G-Shift relationships and hardware-validated wheel/action combinations.

See [../examples/basic.json](../examples/basic.json) for a complete example.
