# Register desktop-actions.json in the Python menu before validation.
@on_start()
async def confirm_dialog():
    await actions.run("desktop.pause")
    await actions.run("desktop.confirm")
