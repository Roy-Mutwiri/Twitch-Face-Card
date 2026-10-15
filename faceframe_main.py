import multiprocessing
import sys

from faceframe.ui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
