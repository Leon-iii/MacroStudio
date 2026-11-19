# Register asset:confirm before validating this example.
@on_start()
async def find_confirmation():
    result = await vision.find("asset:confirm", timeout_ms=1000)
    if result.status == "FOUND":
        await mouse.click_at(result.center_x, result.center_y)
