import multiprocessing
import sys

if __name__ == "__main__":
    # Required for frozen builds (PyInstaller): the render pipeline spawns
    # worker processes, which on Windows/onefile re-exec this entry point.
    multiprocessing.freeze_support()

    if len(sys.argv) > 1:
        from rhr2mp4.cli import main

        sys.exit(main())
    else:
        from rhr2mp4.gui.app import main

        main()
