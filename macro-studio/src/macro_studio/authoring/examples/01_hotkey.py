@on_hotkey("CTRL+F6")
async def tap_key():
    await keyboard.tap("A", hold_ms=30)
