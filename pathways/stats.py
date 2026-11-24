"""Statistical helpers used by path and seat analyses.

The module intentionally depends only on the standard library.  The intervals
are small-sample Wilson intervals, which remain well defined for ``n=0`` and
for observed rates of zero or one.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, Tuple


def wilson(k: int, n: int, z: float = 1.959963984540054) -> Tuple[float, float, float]:
    """Return ``(point, low, high)`` for a Wilson binomial interval.

    With no observations the point estimate is reported as ``None``-like
    ``float("nan")`` by callers?  Here we use 0.5 as the neutral centre of the
    Wilson interval but explicitly tell callers that there is no support via
    :func:`support_label`.  The interval is [0, 1].
    """
    if n <= 0:
        return 0.5, 0.0, 1.0
    k = max(0, min(int(k), int(n)))
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    margin = (z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))) / denom
    low = max(0.0, centre - margin)
    high = min(1.0, centre + margin)
    return p, low, high


def bayes_rate(k: int, n: int, prior_k: float = 1.0, prior_n: float = 2.0) -> float:
    """Beta-binomial posterior mean with a weak prior."""
    return (k + prior_k) / (n + prior_n)


def support_label(n_students: int, n_terms: int, min_students: int = 10,
                  min_terms: int = 3) -> str:
    """Classify empirical support.

    ``established`` meets both transparent thresholds; ``limited`` has at least
    five observations or two terms; anything smaller is ``insufficient`` and
    zero evidence is ``no_evidence``.
    """
    if n_students == 0 and n_terms == 0:
        return "no_evidence"
    if n_students >= min_students and n_terms >= min_terms:
        return "established"
    if n_students >= 5 or n_terms >= 2:
        return "limited"
    return "insufficient"


def brier_score(pairs: Iterable[Tuple[float, int]]) -> float | None:
    values = list(pairs)
    if not values:
        return None
    return sum((p - y) ** 2 for p, y in values) / len(values)


def log_loss(pairs: Iterable[Tuple[float, int]], eps: float = 1e-6) -> float | None:
    values = list(pairs)
    if not values:
        return None
    total = 0.0
    for p, y in values:
        p = min(1.0 - eps, max(eps, p))
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(values)


def quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("quantile of empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] * (hi - pos) + sorted_values[hi] * (pos - lo)


def binomial_upper_count(n: int, p_high: float) -> int:
    """Conservative integer count implied by an upper rate bound."""
    if n <= 0:
        return 0
    return min(n, math.ceil(n * max(0.0, min(1.0, p_high)) - 1e-12))
