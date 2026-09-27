"""
A word-level before/after diff for the review UI. Nothing here touches a database or
the network.

Safety: the texts being compared come from web pages and from a model, so every
fragment is HTML-escaped BEFORE it is wrapped in <ins> / <del>. The returned Markup
contains only those two tags and escaped text, and is safe to render with |safe.
"""
import unicodedata
from difflib import SequenceMatcher

from markupsafe import Markup, escape


def _kind(ch: str) -> str:
    """space | word | punct. A word is a run of letters, digits, combining marks and
    the zero-width joiners used inside Indic words: the usual regular-expression
    word-character class does not match combining marks, so a Devanagari word would be
    shredded (see
    gap_analysis._tokenize). Each punctuation mark is its own token, so adding words
    after 'college.' shows only the added words, not 'college.' deleted and re-added."""
    if ch.isspace():
        return "space"
    if ch.isalnum() or unicodedata.category(ch)[0] == "M" or ch in ("‌", "‍"):
        return "word"
    return "punct"


def _tokens(text: str | None) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    kind = None
    for ch in text or "":
        k = _kind(ch)
        if cur and k == kind and k != "punct":
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
            cur, kind = [ch], k
    if cur:
        out.append("".join(cur))
    return out


def word_diff(before: str | None, after: str | None) -> list[tuple[str, str]]:
    """[(op, text)] with op in 'equal' | 'delete' | 'insert', comparing whole words
    (whitespace and punctuation are kept, so the result re-assembles both texts exactly)."""
    a, b = _tokens(before), _tokens(after)
    out: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append(("equal", "".join(a[i1:i2])))
        else:
            if i2 > i1:
                out.append(("delete", "".join(a[i1:i2])))
            if j2 > j1:
                out.append(("insert", "".join(b[j1:j2])))
    return out


def diff_html(before: str | None, after: str | None) -> Markup:
    parts = []
    for op, text in word_diff(before, after):
        safe = escape(text)
        if op == "delete":
            parts.append(Markup('<del style="background:#fee2e2;color:#991b1b;text-decoration:line-through;">') + safe + Markup("</del>"))
        elif op == "insert":
            parts.append(Markup('<ins style="background:#dcfce7;color:#166534;text-decoration:none;">') + safe + Markup("</ins>"))
        else:
            parts.append(safe)
    return Markup("").join(parts)
