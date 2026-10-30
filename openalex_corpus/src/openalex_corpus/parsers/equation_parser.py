"""Literal linear-math transliteration; no formula reconstruction or inference."""

from __future__ import annotations

import re

# Only observed Unicode glyphs are replaced. A word such as "nu" is never
# interpreted as a Greek letter, and stacked/subscript/superscript forms are
# deliberately outside this mapping.
GREEK_GLYPHS = {
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "ε": r"\varepsilon",
    "ζ": r"\zeta",
    "η": r"\eta",
    "θ": r"\theta",
    "ι": r"\iota",
    "κ": r"\kappa",
    "λ": r"\lambda",
    "μ": r"\mu",
    "ν": r"\nu",
    "ξ": r"\xi",
    "π": r"\pi",
    "ρ": r"\rho",
    "σ": r"\sigma",
    "τ": r"\tau",
    "υ": r"\upsilon",
    "φ": r"\varphi",
    "χ": r"\chi",
    "ψ": r"\psi",
    "ω": r"\omega",
    "Γ": r"\Gamma",
    "Δ": r"\Delta",
    "Θ": r"\Theta",
    "Λ": r"\Lambda",
    "Ξ": r"\Xi",
    "Π": r"\Pi",
    "Σ": r"\Sigma",
    "Υ": r"\Upsilon",
    "Φ": r"\Phi",
    "Ψ": r"\Psi",
    "Ω": r"\Omega",
}
LINEAR_SYMBOLS = {
    "−": "-",
    "×": r"\times",
    "·": r"\cdot",
    "≤": r"\le",
    "≥": r"\ge",
    "≠": r"\ne",
    "≈": r"\approx",
}


def literal_latex_candidate(text: str) -> dict | None:
    """Return a marked lexical candidate only for a narrow, linear expression.

    Refuse multiline text, words, braces/TeX syntax, exponents, scripts,
    fractions, integrals and matrices. Even accepted input stays unverified;
    geometry may encode structure that a text layer alone cannot establish.
    """
    if "\n" in text or len(text) > 160 or not any(c in text for c in "=<>≤≥≠≈"):
        return None
    mapping = {**GREEK_GLYPHS, **LINEAR_SYMBOLS}
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 +-*/=<>()[].,:\t") | set(
        mapping
    )
    if any(c not in allowed for c in text) or re.search(r"[A-Za-z]{2,}", text):
        return None
    replacements = []
    translated = []
    for char in text:
        if char in mapping:
            target = mapping[char]
            translated.append(target + ("{}" if target.startswith("\\") else ""))
            replacements.append({"source_glyph": char, "latex_token": target})
        else:
            translated.append(char)
    return {
        "latex_candidate": "".join(translated),
        "latex_candidate_method": "literal_linear_glyph_map_v1",
        "latex_candidate_status": "unverified",
        "latex_candidate_mapping": replacements,
        "latex_candidate_source_text": text,
    }
