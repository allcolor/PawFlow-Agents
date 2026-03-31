"""Unicode sanitization — strips invisible/malicious characters from text.

Applies NFKC normalization and removes zero-width chars, directional marks,
format characters, private-use characters, and unassigned codepoints.
Iterates until convergence (max 10 rounds) since normalization can reveal
new problematic characters.
"""

import unicodedata

# Zero-width characters
_ZERO_WIDTH = frozenset([
    '\u200b',  # zero-width space
    '\u200c',  # zero-width non-joiner
    '\u200d',  # zero-width joiner
    '\ufeff',  # byte order mark / zero-width no-break space
    '\u00ad',  # soft hyphen
])

# Directional override / embedding marks
_DIRECTIONAL = frozenset([
    '\u200e',  # left-to-right mark
    '\u200f',  # right-to-left mark
    '\u202a',  # left-to-right embedding
    '\u202b',  # right-to-left embedding
    '\u202c',  # pop directional formatting
    '\u202d',  # left-to-right override
    '\u202e',  # right-to-left override
    '\u2066',  # left-to-right isolate
    '\u2067',  # right-to-left isolate
    '\u2068',  # first strong isolate
    '\u2069',  # pop directional isolate
])

# Combined set for fast lookup
_STRIP_CHARS = _ZERO_WIDTH | _DIRECTIONAL

# Common whitespace characters to keep (even though they may be category Cf or Cc)
_KEEP_WHITESPACE = frozenset(['\t', '\n', '\r', ' '])

_MAX_ITERATIONS = 10


def sanitize_unicode(text: str) -> str:
    """Sanitize unicode text by normalizing and stripping invisible characters.

    - NFKC normalization (collapses compatibility decompositions)
    - Strips zero-width characters
    - Strips directional marks / overrides
    - Strips format characters (category Cf) except common whitespace
    - Strips private-use characters (category Co)
    - Strips unassigned characters (category Cn)
    - Iterates until stable (max 10 rounds)

    Returns cleaned text.
    """
    if not text:
        return text

    for _ in range(_MAX_ITERATIONS):
        # NFKC normalization
        normalized = unicodedata.normalize('NFKC', text)

        # Strip problematic characters
        cleaned = []
        for ch in normalized:
            # Fast path: common ASCII
            if ch < '\x80':
                cleaned.append(ch)
                continue

            # Explicit strip set
            if ch in _STRIP_CHARS:
                continue

            # Keep common whitespace
            if ch in _KEEP_WHITESPACE:
                cleaned.append(ch)
                continue

            # Category-based filtering
            cat = unicodedata.category(ch)
            if cat == 'Cf':
                # Format character (not in keep set) — strip
                continue
            if cat == 'Co':
                # Private use — strip
                continue
            if cat == 'Cn':
                # Unassigned — strip
                continue

            cleaned.append(ch)

        result = ''.join(cleaned)

        # Converged?
        if result == text:
            return result
        text = result

    return text
