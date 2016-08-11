import os
import sys


if __name__ == '__main__':
    try:
        from matrix_gitter.main import main
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from matrix_gitter.main import main
    main()
