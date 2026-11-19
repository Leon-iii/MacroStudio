@on_start()
async def move_pointer():
    await mouse.move_to(320, 240, profile_id="builtin.smooth", duration_ms=300)
    await mouse.click(button="left", hold_ms=30)
