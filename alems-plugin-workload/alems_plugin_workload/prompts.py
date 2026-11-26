"""
Seeded synthetic prompts, sized in tokens of the templated chat input.

Text is built from a fixed built in word list, so prompts carry no dataset
licence and anyone can regenerate them from (seed, round, index). Each prompt
starts with a unique marker so requests do not share a prefix beyond what the
server's chat template adds; reuse is measured from reported cached tokens.
"""
import random
from typing import Callable, Optional, Tuple

# Plain, common words; content is irrelevant to the measurement, only length is.
WORDS = (
    "river", "stone", "light", "garden", "window", "paper", "music", "winter",
    "summer", "market", "bridge", "forest", "ocean", "letter", "number", "silver",
    "orange", "valley", "engine", "planet", "signal", "harbor", "candle", "ladder",
    "mirror", "pocket", "rocket", "saddle", "tunnel", "violet", "wander", "yellow",
    "anchor", "basket", "castle", "doctor", "falcon", "ginger", "helmet", "island",
    "jacket", "kitten", "lemon", "meadow", "needle", "pepper", "quiet", "rabbit",
    "season", "timber", "useful", "velvet", "walnut", "copper", "desert", "empire",
    "fabric", "gentle", "hollow", "insect", "jungle", "kernel", "lizard", "magnet",
    "nectar", "oyster", "pillow", "quartz", "ribbon", "sunset", "tomato", "united",
    "vessel", "wizard", "zipper", "almond", "branch", "cotton", "dragon", "echo",
    "feather", "glacier", "horizon", "iron", "journey", "kettle", "lantern", "maple",
    "north", "orbit", "puzzle", "rain", "shadow", "thunder", "upper", "voice",
    "wheat", "young", "bright", "calm", "deep", "early", "fresh", "grand",
)
INSTRUCTION = "Read these words and keep writing in the same style:"


def make_prompt(seed, rnd, idx, n_words):
    # type: (int, int, int, int) -> str
    """
    Deterministic prompt for (seed, round, index) with n_words filler words.
    A string seed is hashed with SHA-512 by random.Random, so the text is
    identical across processes and Python versions.
    """
    r = random.Random("alems-workload/%s/%s/%s" % (seed, rnd, idx))
    tag = "%016x" % r.getrandbits(64)  # unique marker at the very start of the user text
    words = " ".join(r.choice(WORDS) for _ in range(max(1, n_words)))
    return "[%s] %s %s" % (tag, INSTRUCTION, words)


def within(actual, target, pct):
    # type: (Optional[float], float, float) -> Optional[int]
    """1 if actual is within pct percent of target, 0 if not, None when unknown."""
    if actual is None:
        return None
    return int(abs(actual - target) <= target * pct / 100.0)


def size_prompt(build, count_fn, target, tol_pct, max_iter):
    # type: (Callable[[int], str], Callable[[str], Optional[int]], int, float, int) -> Tuple[str, Optional[int], int]
    """
    Find a word count whose templated token count is within tol_pct of target.

    Proportional correction: words scale by target / count each round.
    Returns (text, token count or None when uncountable, iterations used);
    the caller decides whether the final count is acceptable.
    """
    n = max(1, int(target * 0.7))  # about 1.3 tokens per word for this word list
    text, count = build(n), None
    for it in range(1, max_iter + 1):
        text = build(n)
        count = count_fn(text)
        if count is None or within(count, target, tol_pct):
            return text, count, it
        nxt = max(1, int(round(n * target / float(count))))
        n = nxt if nxt != n else n + (1 if count < target else -1)
    return text, count, max_iter
