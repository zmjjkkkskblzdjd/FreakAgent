from __future__ import annotations

from enum import Enum


class PermissionLevel(Enum):
    NONE = 0          
    READ_ONLY = 1     
    WRITE = 2         
    IRREVERSIBLE = 3  
