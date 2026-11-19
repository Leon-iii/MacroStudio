@workflow("press_with_delay")
async def press_with_delay(delay_ms: int):
    await macro.sleep(ms=delay_ms)
    await keyboard.tap("A", hold_ms=30)


@on_start()
async def initialize():
    await workflow.call("press_with_delay", 10)


@on_hotkey("CTRL+F6", reentrancy="restart", priority=1)
async def hotkey_action():
    await keyboard.tap("B", hold_ms=30)


@on_interval(ms=1000, reentrancy="ignore_if_running")
async def periodic_action():
    await macro.sleep(ms=10)
