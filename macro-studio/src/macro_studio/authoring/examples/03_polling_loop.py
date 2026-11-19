@on_start()
async def poll_with_delay():
    remaining = 3
    while remaining > 0:
        await keyboard.tap("A", hold_ms=10)
        await macro.sleep(ms=100)
        remaining -= 1
