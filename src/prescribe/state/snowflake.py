"""63-bit snowflake-style IDs: time-sortable, collision-resistant.

Layout (64-bit integer, sign bit always 0):
  43 bits — milliseconds since Unix epoch (fits ~278 years from 1970)
  20 bits — random counter
   1 bit  — sign (always 0)

Time bits mean IDs sort roughly chronologically. Random bits prevent
collisions across concurrent writers.
"""

import random
import time

_TIME_BITS = 43
_RAND_BITS = 20
_MAX_TIME_MS = (1 << _TIME_BITS) - 1


def snowflake_id() -> int:
    """Generate a 63-bit positive snowflake ID."""
    ms = int(time.time() * 1000)
    if ms > _MAX_TIME_MS:
        ms = ms & _MAX_TIME_MS
    rand = random.getrandbits(_RAND_BITS)
    return (ms << _RAND_BITS) | rand
