"""63-bit snowflake-style IDs: time-sortable, collision-resistant.

Layout (64-bit integer, sign bit always 0):
  43 bits — milliseconds since Unix epoch (fits ~278 years from 1970)
  20 bits — random counter or monotonic sequence number
   1 bit  — sign (always 0)

Time bits mean IDs sort roughly chronologically. Random bits prevent
collisions across concurrent writers.  ``snowflake_sequence(n)`` uses
incrementing counter bits instead of random, guaranteeing monotonic
IDs within a single millisecond.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterator

_TIME_BITS = 43
_RAND_BITS = 20
_SEQ_MASK = (1 << _RAND_BITS) - 1
_MAX_TIME_MS = (1 << _TIME_BITS) - 1


def snowflake_id() -> int:
    """Generate a 63-bit positive snowflake ID."""
    ms = int(time.time() * 1000)
    if ms > _MAX_TIME_MS:
        ms = ms & _MAX_TIME_MS
    rand = random.getrandbits(_RAND_BITS)
    return (ms << _RAND_BITS) | rand


def snowflake_sequence(n: int) -> Iterator[int]:
    """Generate *n* monotonic snowflake IDs sharing a single ms timestamp.

    Uses an incrementing counter for the low 20 bits instead of random
    values, guaranteeing IDs are strictly increasing and collision-free
    within the batch.  If *n* exceeds the 20-bit counter space (~1M),
    subsequent IDs spill into a new ms bucket.
    """
    if n < 1:
        return
    ms = int(time.time() * 1000)
    generated = 0
    while generated < n:
        remaining = n - generated
        batch = min(remaining, _SEQ_MASK + 1)
        for seq in range(batch):
            yield (ms << _RAND_BITS) | seq
        generated += batch
        ms += 1
