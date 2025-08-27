def func(seed: int, rounds: int = 50_000) -> int:
    """
    Deterministically mixes a 64-bit integer using only simple operations,
    repeated many times. Returns a 64-bit result.

    Params:
      seed   : any Python int (will be reduced mod 2^64)
      rounds : how many mixing rounds (>= 1; more rounds = harder to predict)
    """
    MASK = (1 << 64) - 1
    x = seed & MASK

    for i in range(rounds):
        # simple, fast integer ops only:
        x = (x + 0x9E3779B97F4A7C15) & MASK       # add (golden ratio step)
        x ^= (x >> 30)                             # xor-shift
        x = (x * 0xBF58476D1CE4E5B9) & MASK       # multiply
        x ^= (x >> 27)
        x = (x * 0x94D049BB133111EB) & MASK
        x ^= (x >> 31)

        # tiny rotate using only shifts/or (still just bit ops)
        k = 13 + (i & 31)                          # rotate 13..44
        x = ((x << k) | (x >> (64 - k))) & MASK

        # mix in loop index via a simple linear congruential step
        i_mix = (i * 6364136223846793005 + 1442695040888963407) & MASK
        x ^= i_mix

    return x

if __name__ == "__main__":
    print(func(1234567890))