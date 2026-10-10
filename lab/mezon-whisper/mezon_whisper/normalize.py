"""Text normalization for WER/CER (plan 7.1). Apply to reference and hypothesis alike.

Covers: Unicode NFC, lowercase, tone-mark placement (old style -> new style),
punctuation removal, whitespace. Number normalization (plan 7.1 step 3) is not
implemented yet.
"""

import re
import unicodedata

# Old-style tone placement puts the mark on the first vowel of an open oa/oe/uy
# rhyme ("hòa", "khỏe", "thủy"); the guideline (plan 6) uses the new style
# ("hoà", "khoẻ", "thuỷ"). With a final consonant both styles agree ("hoàn").
_OLD_TO_NEW = {}
for _plain, _toned in (("oa", ("òa", "óa", "ỏa", "õa", "ọa")),
                       ("oe", ("òe", "óe", "ỏe", "õe", "ọe")),
                       ("uy", ("ùy", "úy", "ủy", "ũy", "ụy"))):
    for _old, _second in zip(_toned, {"oa": "àáảãạ", "oe": "èéẻẽẹ", "uy": "ỳýỷỹỵ"}[_plain]):
        _OLD_TO_NEW[_old] = _plain[0] + _second
_OLD_STYLE = re.compile(r"(" + "|".join(_OLD_TO_NEW) + r")(?!\w)")
_PUNCT = re.compile(r"[^\w\s]|_")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    text = _OLD_STYLE.sub(lambda m: _OLD_TO_NEW[m.group(1)], text)
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def word_error_rate(reference: str, hypothesis: str) -> float:
    """WER of two already-normalized strings. Both empty -> 0, empty reference -> 1."""
    ref, hyp = reference.split(), hypothesis.split()
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, 1):
        current = [i]
        for j, hyp_word in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ref_word != hyp_word)))
        previous = current
    return previous[-1] / len(ref)
