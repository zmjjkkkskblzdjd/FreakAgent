import sys
from pathlib import Path

_DA_ROOT = Path(__file__).resolve().parents[1]      
_ALLFUSION = _DA_ROOT.parent.parent                 
for _p in (str(_DA_ROOT), str(_ALLFUSION)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
