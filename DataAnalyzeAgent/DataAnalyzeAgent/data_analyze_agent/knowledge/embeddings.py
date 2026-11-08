from __future__ import annotations

import sys
from pathlib import Path

_ALLFUSION_ROOT = str(Path(__file__).resolve().parents[4])
if _ALLFUSION_ROOT not in sys.path:
    sys.path.insert(0, _ALLFUSION_ROOT)

from kb_service.embeddings import (  # noqa: E402,F401
    EMBED_BATCH_SIZE,
    EMBED_DIM,
    EMBED_MODEL_TAG,
    get_embedding,
    get_embedding_model,
    get_embeddings_batch,
    resolve_model_path,
)
