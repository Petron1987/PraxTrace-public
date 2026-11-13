"""Small auditable synonym expansion, with explicit scope filters preserved."""
import json
from pathlib import Path
import re
import unicodedata


def norm(text):
    return unicodedata.normalize("NFKC", str(text)).casefold()


def contains(text, phrase):
    phrase = norm(phrase)
    left = r"(?<![a-z0-9_])" if phrase[0].isascii() and phrase[0].isalnum() else ""
    right = r"(?![a-z0-9_])" if phrase[-1].isascii() and phrase[-1].isalnum() else ""
    return re.search(left + re.escape(phrase) + right, norm(text)) is not None


def groups():
    return json.loads(Path(__file__).parent.joinpath("search-aliases.json").read_text(encoding="utf-8"))["groups"]


def equivalents(term):
    return next((g for g in groups() if norm(term) in [norm(x) for x in g]), [term])


def clauses(query):
    remaining = norm(query)
    selected = []
    # Longest phrases first so repayment link is not split into unrelated fragments.
    for group in sorted(groups(), key=lambda g: max(map(len, g)), reverse=True):
        hits = [x for x in sorted(group, key=len, reverse=True) if contains(remaining, x)]
        if not hits:
            continue
        selected.append(group)
        for hit in hits:
            left = r"(?<![a-z0-9_])" if hit[0].isascii() else ""
            right = r"(?![a-z0-9_])" if hit[-1].isascii() else ""
            remaining = re.sub(left + re.escape(norm(hit)) + right, " ", remaining)
    for phrase in ("\u4ee5\u524d\u5206\u6790\u8fc7", "\u6709\u6ca1\u6709\u5206\u6790\u8fc7", "\u6709\u6ca1\u6709", "\u5e2e\u6211\u67e5\u4e0b", "\u5e2e\u6211\u627e", "\u8bf7\u67e5\u627e", "\u4e4b\u524d", "\u76f8\u5173\u62a5\u544a", "\u5206\u6790\u62a5\u544a", "\u62a5\u544a", "\u5417", "\u7684", "\u8bf7"):
        remaining = remaining.replace(phrase, " ")
    selected += [[x] for x in re.findall(r"[a-z0-9_.+-]+|[\u4e00-\u9fff]+", remaining) if len(x) > 1]
    return selected


def match(query, text):
    parts = clauses(query)
    matched = [g[0] for g in parts if any(contains(text, x) for x in g)]
    return len(matched) == len(parts), matched
