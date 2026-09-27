# Indicator instances and editable EMA periods

Status: accepted for implementation from the requested EMA configurability gap.
Scope: run configuration and registry boundary; the installation demo remains
non-research-grade until a real configuration/run UI is available.

## Context and alternatives

`ComponentSelection.component_id` currently serves as both implementation
identity and the unique selection key. That forces `ema_fast` and `ema_trend`
to have separate registry entries even though they run identical EMA code.
The approved MVP scope requires schema-driven editable EMA and ATR periods,
with immutable, hashed resolved configuration for each run.

1. Register every alias as a component type. This is a small patch but leaves
   the coupling in place and makes aliases proliferate in the registry.
2. Replace the component schema with a new definition/instance hierarchy.
   This is clean but changes existing canonical payloads and hash fixtures.
3. Add an optional `instance_id` to `ComponentSelection`, retaining
   `component_id`/`component_version` as type identity. This is additive and
   preserves existing single-instance payloads. **Chosen.**

## Contract

- An omitted `instance_id` means the legacy effective instance ID is
  `component_id`. Explicit instance IDs are unique within a detection config.
- Definitions are registered once by `(component_id, component_version)`;
  parameter defaults and bounds are applied independently to every instance.
- The built-in EMA v1 definition declares an integer `period`, default 45,
  minimum 1. `trend_ema` and `fast_ema` may use periods 45 and 9 under the same
  `ema` v1 definition. Neither period is encoded in the component type ID.
- Canonical serialization sorts components by effective instance ID and emits
  `instance_id` only when explicit. A changed instance ID or period changes the
  detection hash; omitted legacy IDs retain their prior canonical bytes.
- `EmaState` selects an EMA instance by effective ID. The declared warm-up is
  `5 * period`; a run's preflight will use the maximum of all enabled
  requirements. Detectors will refer to a stable instance ID, not a hardcoded
  period or private EMA calculation.
- A UI-facing definition schema exposes the type, version, field metadata,
  default and bounds. The server resolves/validates any submitted value; a
  browser must not invent unregistered calculation types or silently change an
  active run. Saving a changed named preset creates a new revision.

## Trade-offs and verification

An optional field temporarily permits legacy and explicit instance forms.
Validation must reject an explicit ID that collides with another effective ID,
including a legacy selection. Integration must distinguish a component's type
from its instance ID. Tests will cover two EMA instances using one registry
definition, default/override resolution, order-independent hashes, collision
rejection, legacy golden hashes, snapshot reload, and independent EMA output.
The actual detector runtime and full preset editor are separate later work;
this change prepares their authoritative configuration contract.
