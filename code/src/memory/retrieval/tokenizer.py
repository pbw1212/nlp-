from __future__ import annotations

import unicodedata
from typing import Iterable, Tuple


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def _cjk_tokens(sequence: str) -> Iterable[str]:
    yield from sequence
    yield from (sequence[index : index + 2] for index in range(len(sequence) - 1))


def tokenize(text: str) -> Tuple[str, ...]:
    """NFKC + casefold；Latin/number words 与 CJK unigram+bigram。"""

    if not isinstance(text, str):
        raise TypeError("tokenizer input 必须是字符串")
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    word_buffer: list[str] = []
    cjk_buffer: list[str] = []

    def flush_word() -> None:
        if word_buffer:
            tokens.append("".join(word_buffer))
            word_buffer.clear()

    def flush_cjk() -> None:
        if cjk_buffer:
            tokens.extend(_cjk_tokens("".join(cjk_buffer)))
            cjk_buffer.clear()

    for character in normalized:
        if _is_cjk(character):
            flush_word()
            cjk_buffer.append(character)
        elif character.isalnum():
            flush_cjk()
            word_buffer.append(character)
        else:
            flush_word()
            flush_cjk()
    flush_word()
    flush_cjk()
    return tuple(tokens)


def unique_terms(tokens: Iterable[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(tokens))
