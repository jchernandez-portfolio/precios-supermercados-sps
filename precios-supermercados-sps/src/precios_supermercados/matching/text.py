"""Similitud de cadenas en Python puro (sin dependencias pesadas).

Incluye Jaro-Winkler, token-set ratio, coincidencia "suave" de tokens (para
abreviaturas de retail como ``Acondi``/``acondicionador``) y un vectorizador
TF-IDF de n-gramas de caracteres con vectores dispersos normalizados.
"""
from __future__ import annotations

import math
from collections import Counter
from difflib import SequenceMatcher
from typing import Iterable, Mapping, Sequence


def jaro(left: str, right: str) -> float:
    if left == right:
        return 1.0 if left else 0.0
    len_left, len_right = len(left), len(right)
    if not len_left or not len_right:
        return 0.0
    window = max(len_left, len_right) // 2 - 1
    window = max(window, 0)
    left_flags = [False] * len_left
    right_flags = [False] * len_right
    matches = 0
    for index, character in enumerate(left):
        start = max(0, index - window)
        end = min(index + window + 1, len_right)
        for other in range(start, end):
            if not right_flags[other] and right[other] == character:
                left_flags[index] = right_flags[other] = True
                matches += 1
                break
    if not matches:
        return 0.0
    transpositions = 0
    pointer = 0
    for index in range(len_left):
        if left_flags[index]:
            while not right_flags[pointer]:
                pointer += 1
            if left[index] != right[pointer]:
                transpositions += 1
            pointer += 1
    transpositions //= 2
    return (
        matches / len_left + matches / len_right + (matches - transpositions) / matches
    ) / 3.0


def jaro_winkler(left: str, right: str, *, prefix_scale: float = 0.1) -> float:
    similarity = jaro(left, right)
    prefix = 0
    for a, b in zip(left[:4], right[:4]):
        if a != b:
            break
        prefix += 1
    return similarity + prefix * prefix_scale * (1.0 - similarity)


def token_set_ratio(left: Sequence[str], right: Sequence[str]) -> float:
    """Variante del token-set ratio (estilo fuzzywuzzy) en escala 0..1."""

    left_set, right_set = set(left), set(right)
    if not left_set or not right_set:
        return 0.0
    common = " ".join(sorted(left_set & right_set))
    left_rest = " ".join(sorted(left_set - right_set))
    right_rest = " ".join(sorted(right_set - left_set))
    combined_left = (common + " " + left_rest).strip()
    combined_right = (common + " " + right_rest).strip()

    def ratio(a: str, b: str) -> float:
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    candidates = [ratio(combined_left, combined_right)]
    if common:
        candidates.append(ratio(common, combined_left))
        candidates.append(ratio(common, combined_right))
    return max(candidates)


def tokens_match(left: str, right: str) -> bool:
    """Igualdad tolerante: plural simple, prefijo de abreviatura o JW alto."""

    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 4 and longer.startswith(shorter):
        return True
    if len(shorter) >= 5 and jaro_winkler(left, right) >= 0.93:
        return True
    return False


def soft_token_overlap(left: Sequence[str], right: Sequence[str]) -> float:
    """Dice suave entre conjuntos de tokens (cada token empareja una vez)."""

    left_tokens = list(dict.fromkeys(left))
    right_tokens = list(dict.fromkeys(right))
    if not left_tokens or not right_tokens:
        return 0.0
    used: set[int] = set()
    matched = 0
    for token in left_tokens:
        for index, other in enumerate(right_tokens):
            if index in used:
                continue
            if tokens_match(token, other):
                used.add(index)
                matched += 1
                break
    return 2.0 * matched / (len(left_tokens) + len(right_tokens))


def char_ngrams(text: str, sizes: Iterable[int] = (3, 4)) -> Counter[str]:
    """N-gramas de caracteres por token con bordes marcados."""

    grams: Counter[str] = Counter()
    for token in text.split():
        padded = f"#{token}#"
        for size in sizes:
            if len(padded) < size:
                continue
            for start in range(len(padded) - size + 1):
                grams[padded[start : start + size]] += 1
    return grams


SparseVector = Mapping[str, float]


class TfidfModel:
    """TF-IDF sublineal sobre n-gramas de caracteres; vectores L2-normalizados."""

    def __init__(self, documents: Iterable[str], *, sizes: Sequence[int] = (3, 4)) -> None:
        self.sizes = tuple(sizes)
        document_frequency: Counter[str] = Counter()
        count = 0
        for document in documents:
            count += 1
            document_frequency.update(set(char_ngrams(document, self.sizes)))
        self.document_count = count
        self.idf = {
            gram: math.log((1 + count) / (1 + frequency)) + 1.0
            for gram, frequency in document_frequency.items()
        }
        self._default_idf = math.log(1 + count) + 1.0

    def vector(self, text: str) -> dict[str, float]:
        grams = char_ngrams(text, self.sizes)
        weights = {
            gram: (1.0 + math.log(frequency)) * self.idf.get(gram, self._default_idf)
            for gram, frequency in grams.items()
        }
        norm = math.sqrt(sum(value * value for value in weights.values()))
        if norm == 0:
            return {}
        return {gram: value / norm for gram, value in weights.items()}


def cosine(left: SparseVector, right: SparseVector) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())
