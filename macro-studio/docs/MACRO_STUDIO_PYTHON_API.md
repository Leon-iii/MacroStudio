# Macro Studio Python API v1.4

This reference is generated from the compiler Registry.

Each action call uses `await`. Functions use `async def` and a supported decorator.

| API | Parameters | Result |
|---|---|---|
| `on_start` | reentrancy (String) = 'ignore_if_running', priority (Integer) = 0 | Declaration |
| `on_hotkey` | key (String), reentrancy (String) = 'ignore_if_running', priority (Integer) = 0 | Declaration |
| `on_interval` | ms (Integer), reentrancy (String) = 'ignore_if_running', priority (Integer) = 0 | Declaration |
| `workflow` | name (String) | Declaration |
| `macro.sleep` | ms (Integer) | None |
| `macro.stop` |  | None |
| `keyboard.tap` | key (String), hold_ms (Integer) = 30 | None |
| `keyboard.repeat` | key (String), frequency_hz (Integer), hold_ms (Integer), count (Integer) | None |
| `mouse.click` | button (String) = 'left', hold_ms (Integer) = 50 | None |
| `mouse.click_at` | x (Integer), y (Integer), button (String) = 'left' | None |
| `mouse.move_to` | x (Integer), y (Integer), profile_id (String) = 'inherit', duration_ms (Integer) optional | None |
| `actions.run` | action_id (String) | None |
| `vision.find` | template_id (String), timeout_ms (Integer) = 1000, profile_id (String) = 'inherit', enter_threshold (Number) optional, exit_threshold (Number) optional, max_age_ms (Integer) optional, roi_left (Integer) optional, roi_top (Integer) optional, roi_width (Integer) optional, roi_height (Integer) optional | RecognitionResult |
| `vision.wait_for` | template_id (String), condition (String), timeout_ms (Integer) = 5000, profile_id (String) = 'inherit', enter_threshold (Number) optional, exit_threshold (Number) optional, max_age_ms (Integer) optional, roi_left (Integer) optional, roi_top (Integer) optional, roi_width (Integer) optional, roi_height (Integer) optional | RecognitionResult |
| `workflow.call` | name (String) | None |

`workflow.call` accepts the named workflow's typed arguments after its name.
`vision.find` and `vision.wait_for` results must be assigned to a local name.
Recognition fields are `x`, `y`, `score`, `status`, `error_code`, `pixel_count`, `fraction`, `center_x`, `center_y`.
Use `FOUND`, `NOT_FOUND` and `UNKNOWN` status values as strings.
`mouse.move_to` uses a physical absolute coordinate and the selected motion profile.
Its omitted duration comes from the profile. A key tap accepts one logical key.
Keyboard modifiers in event hotkeys use names such as `CTRL+F6`.
`actions.run` is available after a version 1 Application Pack is registered.
Its `action_id` is a registered string literal. The action expands to existing cancellable IR steps.
`keyboard.down/up`, `spatial.observe` and `navigation.move_to` are unavailable in this release.
An exported project's `api-manifest.json` contains the exact parameter bounds and available services.
