import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.benchmark import make_program
from macro_studio.ir import validate_program
from macro_studio.output import (
    DESKTOP_CAPABILITIES,
    SENDINPUT_PROFILE,
    BackendCapabilities,
    HidMode,
    OutputKind,
    OutputProfile,
    OutputRoute,
    OutputRouter,
    RunConfig,
    UnsupportedCapability,
)
from macro_studio.runtime import Runtime


def _unexpected_backend():
    pytest.fail("unexpected backend creation")


HID_PROFILE = OutputProfile("test-hid", OutputKind.EXTERNAL_HID, HidMode.STREAMING)
ONBOARD_PROFILE = OutputProfile("test-onboard", OutputKind.EXTERNAL_HID, HidMode.ONBOARD)


@pytest.mark.parametrize("profile", [SENDINPUT_PROFILE, HID_PROFILE, ONBOARD_PROFILE])
def test_dry_run_uses_fake_even_when_physical_profile_is_armed(profile):
    clock = VirtualClock()
    fake = FakeInputBackend(clock)
    forbidden = OutputRoute(DESKTOP_CAPABILITIES, _unexpected_backend)
    router = OutputRouter({
        OutputKind.FAKE: OutputRoute(DESKTOP_CAPABILITIES, lambda: fake),
        OutputKind.SENDINPUT: forbidden,
        OutputKind.EXTERNAL_HID: forbidden,
    })
    program = make_program(100, 1, "A")
    session = router.bind(program, RunConfig(profile, armed=True))
    Runtime(program, clock, session.backend).run(20_000_000, [])

    assert session.kind is OutputKind.FAKE
    assert session.capabilities == DESKTOP_CAPABILITIES
    assert [(event["action"], event["key"]) for event in fake.events] == [
        ("key_down", "scan:1E"),
        ("key_up", "scan:1E"),
    ]


def test_dry_run_with_no_fake_route_cannot_use_sendinput():
    router = OutputRouter({
        OutputKind.SENDINPUT: OutputRoute(DESKTOP_CAPABILITIES, _unexpected_backend),
    })
    with pytest.raises(ValueError, match="unavailable.*fake"):
        router.bind(make_program(100, 1, "A"), RunConfig(SENDINPUT_PROFILE, armed=True))


def test_live_route_requires_arm_and_returns_only_the_selected_backend():
    backend = FakeInputBackend(VirtualClock())
    router = OutputRouter({
        OutputKind.FAKE: OutputRoute(DESKTOP_CAPABILITIES, _unexpected_backend),
        OutputKind.SENDINPUT: OutputRoute(DESKTOP_CAPABILITIES, lambda: backend),
    })
    program = make_program(100, 1, "A")
    with pytest.raises(ValueError, match="ARM"):
        router.bind(program, RunConfig(SENDINPUT_PROFILE, dry_run=False))

    session = router.bind(program, RunConfig(SENDINPUT_PROFILE, dry_run=False, armed=True))
    assert session.kind is OutputKind.SENDINPUT
    assert session.backend is backend


def test_unavailable_hid_does_not_fall_back():
    forbidden = OutputRoute(DESKTOP_CAPABILITIES, _unexpected_backend)
    router = OutputRouter({OutputKind.FAKE: forbidden, OutputKind.SENDINPUT: forbidden})
    with pytest.raises(ValueError, match="unavailable.*external_hid"):
        router.bind(make_program(100, 1, "A"), RunConfig(HID_PROFILE, dry_run=False, armed=True))


@pytest.mark.parametrize("profile", [
    OutputProfile("missing-mode", OutputKind.EXTERNAL_HID),
    ONBOARD_PROFILE,
    OutputProfile("sendinput-with-mode", OutputKind.SENDINPUT, HidMode.STREAMING),
])
def test_live_rejects_missing_or_unsupported_hid_mode(profile):
    forbidden = OutputRoute(DESKTOP_CAPABILITIES, _unexpected_backend)
    router = OutputRouter({OutputKind.EXTERNAL_HID: forbidden, OutputKind.SENDINPUT: forbidden})
    with pytest.raises(ValueError, match="HID mode"):
        router.bind(make_program(100, 1, "A"), RunConfig(profile, dry_run=False, armed=True))


@pytest.mark.parametrize(("caps", "missing"), [
    (BackendCapabilities(mouse_relative=True, mouse_buttons=True), "mouse_absolute"),
    (BackendCapabilities(mouse_absolute=True), "mouse_buttons"),
])
def test_nested_coordinate_click_requires_absolute_movement_and_buttons(caps, missing):
    program = validate_program({
        "ir_version": 1,
        "project_id": "capability-check",
        "workflows": {"click": {"steps": [{
            "op": "repeat", "count": 2, "steps": [{
                "op": "if", "condition": True, "then": [],
                "else": [{
                    "op": "mouse_click_at", "block_id": "coordinate-click",
                    "position": {"x": 20, "y": 30}, "button": "left", "hold_ns": 1_000_000,
                }],
            }],
        }]}},
        "handlers": [{"id": "run", "workflow_id": "click", "event": {"type": "project_start"}}],
    })
    router = OutputRouter({OutputKind.EXTERNAL_HID: OutputRoute(caps, _unexpected_backend)})
    with pytest.raises(UnsupportedCapability) as caught:
        router.bind(program, RunConfig(HID_PROFILE, dry_run=False, armed=True))
    message = str(caught.value)
    assert "workflows.click.steps[0].steps[0].else[0]" in message
    assert "coordinate-click" in message
    assert missing in message
