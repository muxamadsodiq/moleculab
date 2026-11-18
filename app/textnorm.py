import re
import unicodedata

_WS = re.compile(r"\s+", re.UNICODE)


def normalize(text: str) -> str:
    """Normalise Unicode and collapse all whitespace (incl. thin/non-breaking spaces)."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text or "")).strip()


def excerpt_in_source(excerpt: str, *sources: str) -> bool:
    """True when the excerpt occurs verbatim in any source, ignoring only whitespace differences."""
    needle = normalize(excerpt)
    return bool(needle) and any(needle in normalize(s) for s in sources if s)


_APOSTROPHES = str.maketrans({"\u2019": "'", "\u02bb": "'", "\u02bc": "'", "\u2018": "'", "`": "'", "\u00b4": "'"})


def fold(text: str) -> str:
    """Case- and apostrophe-insensitive key for multilingual name matching (Latin, Cyrillic, Uzbek o'/g')."""
    return normalize(text).translate(_APOSTROPHES).casefold()
