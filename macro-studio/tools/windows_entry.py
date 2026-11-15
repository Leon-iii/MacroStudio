from multiprocessing import freeze_support
import sys


if __name__ == "__main__":
    freeze_support()
    if sys.argv[1:] == ["--editor-runner"]:
        from macro_studio.editor_runner import main
    else:
        from macro_studio.gui import main

    raise SystemExit(main())
