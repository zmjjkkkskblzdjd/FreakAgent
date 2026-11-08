__version__ = "1.2.0"

import sys as _sys
from pathlib import Path as _Path

_ALLFUSION_ROOT = str(_Path(__file__).resolve().parents[1])
if _ALLFUSION_ROOT not in _sys.path:
    _sys.path.insert(0, _ALLFUSION_ROOT)