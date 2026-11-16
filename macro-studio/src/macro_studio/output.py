from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum

from .backend import InputBackend


class OutputKind(str, Enum):
    FAKE = "fake"
    SENDINPUT = "sendinput"
    EXTERNAL_HID = "external_hid"


class HidMode(str, Enum):
    STREAMING = "streaming"
    ADAPTIVE = "adaptive"
    ONBOARD = "onboard"


@dataclass(frozen=True)
class OutputProfile:
    profile_id: str
    output_kind: OutputKind
    hid_mode: HidMode | None = None


@dataclass(frozen=True)
class RunConfig:
    profile: OutputProfile
    dry_run: bool = True
    armed: bool = False


@dataclass(frozen=True)
class BackendCapabilities:
    keyboard: bool = False
    mouse_buttons: bool = False
    mouse_relative: bool = False
    mouse_absolute: bool = False
    onboard_tap_train: bool = False
    timed_batch: bool = False


@dataclass(frozen=True)
class OutputRoute:
    capabilities: BackendCapabilities
    create_backend: Callable[[], InputBackend]


@dataclass(frozen=True)
class OutputSession:
    kind: OutputKind
    backend: InputBackend
    capabilities: BackendCapabilities


class UnsupportedCapability(ValueError):
    pass


FAKE_PROFILE = OutputProfile("fake", OutputKind.FAKE)
SENDINPUT_PROFILE = OutputProfile("sendinput", OutputKind.SENDINPUT)
DESKTOP_CAPABILITIES = BackendCapabilities(keyboard=True, mouse_buttons=True, mouse_absolute=True)
FAKE_CAPABILITIES = BackendCapabilities(
    keyboard=True, mouse_buttons=True, mouse_relative=True, mouse_absolute=True, timed_batch=True,
)

_STEP_CAPABILITIES = {
    "key_tap": ("keyboard",),
    "tap_sequence": ("keyboard",),
    "mouse_click": ("mouse_buttons",),
    "mouse_move": ("mouse_absolute",),
    "mouse_click_at": ("mouse_absolute", "mouse_buttons"),
    "timed_batch": ("timed_batch",),
}


def _validate_steps(steps: list[dict], path: str, capabilities: BackendCapabilities) -> None:
    for index, step in enumerate(steps):
        location = f"{path}[{index}]"
        for capability in _STEP_CAPABILITIES.get(step["op"], ()):
            if not getattr(capabilities, capability):
                block_id = step.get("block_id")
                source = f"{location} (block {block_id})" if block_id else location
                raise UnsupportedCapability(f"{source}: output does not support {capability}")
        if step["op"] == "timed_batch":
            for edge in step["events"]:
                action = edge["action"]
                capability = ("keyboard" if action.startswith("key") else
                              "mouse_relative" if action == "mouse_move" else "mouse_buttons")
                if not getattr(capabilities, capability):
                    raise UnsupportedCapability(f"{location}: timed batch requires {capability}")
        for branch in ("steps", "then", "else"):
            if branch in step:
                _validate_steps(step[branch], f"{location}.{branch}", capabilities)


class OutputRouter:
    def __init__(self, routes: Mapping[OutputKind, OutputRoute]) -> None:
        self._routes = dict(routes)

    def bind(self, program: dict, config: RunConfig) -> OutputSession:
        if config.dry_run:
            kind = OutputKind.FAKE
        else:
            kind = OutputKind(config.profile.output_kind)
            mode = config.profile.hid_mode
            if kind is OutputKind.EXTERNAL_HID:
                if mode is None:
                    raise ValueError("external_hid output requires a HID mode")
                if mode not in (HidMode.STREAMING, HidMode.ADAPTIVE, HidMode.ONBOARD):
                    raise ValueError(f"unsupported HID mode: {mode}")
            elif mode is not None:
                raise ValueError("HID mode requires external_hid output")
            if kind is not OutputKind.FAKE and config.armed is not True:
                raise ValueError(f"{kind.value} output requires explicit ARM")

        route = self._routes.get(kind)
        if route is None:
            raise ValueError(f"output route unavailable: {kind.value}")
        if (kind is OutputKind.EXTERNAL_HID and config.profile.hid_mode == HidMode.ONBOARD
                and not route.capabilities.onboard_tap_train):
            raise ValueError("unsupported HID mode: onboard requires TAP_TRAIN capability")
        for name, workflow in program["workflows"].items():
            _validate_steps(workflow["steps"], f"workflows.{name}.steps", route.capabilities)
        return OutputSession(kind, route.create_backend(), route.capabilities)
