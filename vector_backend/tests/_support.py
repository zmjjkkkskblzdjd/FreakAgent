from __future__ import annotations

import hashlib


class Hash512:

    def __call__(self, input):
        out = []
        for doc in input:
            vec = [0.0] * 512
            s = " ".join(doc.split())
            grams = [s[i:i + 2] for i in range(max(len(s) - 1, 1))]
            for g in grams:
                h = int(hashlib.md5(g.encode("utf-8")).hexdigest()[:8], 16)
                vec[h % 512] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out
