# Register asset:confirm before validating this example.
@on_start()
async def wait_for_confirmation():
    result = await vision.wait_for("asset:confirm", condition="appear", timeout_ms=500)
    if result.status == "FOUND":
        await mouse.click_at(result.center_x, result.center_y)
    else:
        if result.status == "UNKNOWN":
            await macro.stop()
        else:
            await macro.sleep(ms=100)
