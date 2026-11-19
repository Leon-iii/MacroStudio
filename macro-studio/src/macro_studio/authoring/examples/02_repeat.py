@on_hotkey("CTRL+F6")
async def repeat_key():
    await keyboard.repeat("A", frequency_hz=100, hold_ms=2, count=10)
