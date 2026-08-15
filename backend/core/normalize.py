"""Deterministic cleansing and normalisation.

These rules are pure functions with no LLM involvement, so they are fast,
free, and auditable. The pipeline applies them *after* generation so that
model output is forced into house style rather than trusted to follow it.
"""

from __future__ import annotations

import re
from fractions import Fraction

from backend.core.schema import PLACEHOLDERS

# --- Placeholder handling ---------------------------------------------------


def is_placeholder(value: str | None) -> bool:
    """True when a value carries no information (`-- Unbranded --`, `-`, ...)."""
    if value is None:
        return True
    return str(value).strip().casefold() in PLACEHOLDERS


def clean(value: str | None) -> str:
    """Collapse whitespace and drop placeholder values to an empty string."""
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    return "" if is_placeholder(text) else text


# --- Trademark symbols ------------------------------------------------------
# Brand names must match the approved list exactly, symbols included, so the
# only repair applied is for genuine UTF-8-read-as-cp1252 double encoding
# (`Â®`, `â„¢`). Note that a bare `«` is NOT treated as a broken `®`: the
# supplied data is clean UTF-8, and mapping it would corrupt real guillemets.
MOJIBAKE = {
    "\u00c2\u00ae": "\u00ae",   # Â® -> ®
    "\u00c2\u2122": "\u2122",   # Â™ -> ™
    # `™` is UTF-8 E2 84 A2. Read as cp1252 that is `â„¢`, where byte 0x84 maps
    # to U+201E, the double low quotation mark - not to the control character
    # U+0084, which is what a codepoint-for-byte reading would suggest and which
    # therefore never matched any real input.
    "\u00e2\u201e\u00a2": "\u2122",  # â„¢ -> ™
    "\u00e2\u0084\u00a2": "\u2122",  # the control-character variant, for safety
    "\u00e2\u201a\u00ac": "\u20ac",  # â‚¬ -> €
}


def repair_symbols(value: str | None) -> str:
    """Repair double-encoded trademark symbols; otherwise pass text through."""
    text = value or ""
    for bad, good in MOJIBAKE.items():
        text = text.replace(bad, good)
    return text


def strip_symbols(value: str | None) -> str:
    """Remove ® / ™ / © — used for Mobile Desc, which carries no symbols."""
    return re.sub(r"[\u00ae\u2122\u00a9]", "", value or "").strip()


# --- Decimal to fraction ----------------------------------------------------
# Manufacturers publish decimals; trade buyers search fractions.
# The reference table covers 1/64 (0.015625) through 63/64 (0.984375).
_DENOMINATOR = 64
_TOLERANCE = 1e-4


def decimal_to_fraction(value: float | str) -> str:
    """Convert `0.5` to `1/2` and `50.25` to `50-1/4`.

    Trade fractions are halves through sixty-fourths — the denominator is always
    a power of two, which is what the reference conversion table encodes. A bare
    `limit_denominator(64)` does not respect that: it turns `1.68` into
    `1-17/25`, a fraction no catalogue would print. So the value is snapped to
    the nearest sixty-fourth and only accepted if that snap is exact; otherwise
    the decimal is left alone, which is what the delivery format does with
    measurements that are genuinely decimal.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    sign = "-" if number < 0 else ""
    number = abs(number)
    whole = int(number)
    remainder = number - whole

    if remainder < _TOLERANCE:
        return f"{sign}{whole}"

    sixty_fourths = round(remainder * _DENOMINATOR)
    if sixty_fourths == 0 or sixty_fourths >= _DENOMINATOR:
        return f"{sign}{number:g}"
    if abs(sixty_fourths / _DENOMINATOR - remainder) > _TOLERANCE:
        return f"{sign}{number:g}"

    # Reducing a /64 fraction can only ever yield a power-of-two denominator.
    frac = Fraction(sixty_fourths, _DENOMINATOR)
    if whole == 0:
        return f"{sign}{frac.numerator}/{frac.denominator}"
    return f"{sign}{whole}-{frac.numerator}/{frac.denominator}"


_DECIMAL_RE = re.compile(r"(?<![\w/.-])(\d+\.\d+)(?![\w/])")

# House style writes mixed numbers hyphenated: `42-3/4`, never `42 3/4`.
_MIXED_FRACTION_RE = re.compile(r"\b(\d+)\s+(\d+/\d+)\b")


def hyphenate_mixed_fractions(text: str) -> str:
    """Rewrite `42 3/4` as `42-3/4`."""
    return _MIXED_FRACTION_RE.sub(r"\1-\2", text or "")


def fractions_in_text(text: str) -> str:
    """Rewrite standalone decimals as fractions and hyphenate mixed numbers."""
    converted = _DECIMAL_RE.sub(lambda m: decimal_to_fraction(m.group(1)), text or "")
    return hyphenate_mixed_fractions(converted)


# Manufacturers publish country of origin as an ISO alpha-2 code about as often
# as a name. The delivery format writes the name, so codes are expanded. Only
# unambiguous two-letter codes are mapped; anything else passes through.
_COUNTRY_NAMES: dict[str, str] = {
    "cn": "China", "us": "United States", "usa": "United States",
    "mx": "Mexico", "vn": "Vietnam", "tw": "Taiwan", "th": "Thailand",
    "in": "India", "id": "Indonesia", "my": "Malaysia", "ph": "Philippines",
    "kr": "South Korea", "jp": "Japan", "de": "Germany", "it": "Italy",
    "fr": "France", "gb": "United Kingdom", "uk": "United Kingdom",
    "ca": "Canada", "br": "Brazil", "pl": "Poland", "cz": "Czech Republic",
    "tr": "Turkey", "es": "Spain", "at": "Austria", "ch": "Switzerland",
    "se": "Sweden", "fi": "Finland", "nl": "Netherlands", "hu": "Hungary",
    "ro": "Romania", "sk": "Slovakia", "pt": "Portugal", "il": "Israel",
}


def country_name(value: str) -> str:
    """Expand an ISO alpha-2 country code to its name; pass anything else through.

    `CN` becomes `China`. A value that is already a name is returned unchanged, so
    this is safe to apply to whatever a source happened to publish. Note that `IN`
    is mapped to India rather than treated as the inch abbreviation: this is only
    ever called on a country-of-origin value.
    """
    text = clean(value)
    if not text:
        return ""
    return _COUNTRY_NAMES.get(text.casefold().strip(". "), text)


def snap_inches(value: float | str, denominator: int = 16) -> str:
    """Render a decimal inch as the nearest trade fraction, e.g. `5.6` -> `5-5/8`.

    `decimal_to_fraction` refuses a decimal that is not an exact sixty-fourth,
    and for general text that is the right call: inventing precision is worse
    than leaving a decimal alone.

    A dimension read off a manufacturer's specification is the one case where the
    refusal produces a worse answer. Those figures are unit-conversion artefacts
    — `5.6 in` is 142 mm rounded — and the house style requires fractions, so
    emitting the decimal both breaks the rule and misrepresents the precision the
    manufacturer actually claimed. Catalogues quote inches to the sixteenth, so
    that is what this snaps to.

    Used only where the unit is known to be inches. The rounding is at most 1/32
    of an inch, and the value as published stays in the record's provenance.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    exact = decimal_to_fraction(number)
    if "." not in exact:
        return exact  # already an exact trade fraction

    sign = "-" if number < 0 else ""
    number = abs(number)
    whole = int(number)
    sixteenths = round((number - whole) * denominator)
    if sixteenths >= denominator:  # rounded up into the next whole inch
        return f"{sign}{whole + 1}"
    if sixteenths == 0:
        return f"{sign}{whole}"

    frac = Fraction(sixteenths, denominator)
    if whole == 0:
        return f"{sign}{frac.numerator}/{frac.denominator}"
    return f"{sign}{whole}-{frac.numerator}/{frac.denominator}"


# `5.6 in`, `8.5in`, `3.25"` - a decimal that is explicitly an inch measurement.
_DECIMAL_INCH_RE = re.compile(r"(?<![\w/.-])(\d+\.\d+)\s*(in\b|inch\b|inches\b|\")")


def snap_decimal_inches(text: str) -> str:
    """Rewrite any decimal inch measurement in `text` as a trade fraction."""
    return _DECIMAL_INCH_RE.sub(
        lambda m: f"{snap_inches(m.group(1))} in", text or ""
    )


# --- Unit of measure --------------------------------------------------------
# Canonical forms observed in the delivery data. Keys are lowercase variants.
UOM_CANONICAL: dict[str, str] = {
    "inch": "in", "inches": "in", "in.": "in", '"': "in", "”": "in", "in": "in",
    "foot": "ft", "feet": "ft", "ft.": "ft", "'": "ft", "ft": "ft",
    "millimeter": "mm", "millimetre": "mm", "mm.": "mm", "mm": "mm",
    "centimeter": "cm", "centimetre": "cm", "cm": "cm",
    "meter": "m", "metre": "m", "m": "m",
    "pound": "lb", "pounds": "lb", "lbs": "lb", "lbs.": "lb", "lb.": "lb", "lb": "lb",
    "ounce": "oz", "ounces": "oz", "oz.": "oz", "oz": "oz",
    "kilogram": "kg", "kilograms": "kg", "kgs": "kg", "kg": "kg",
    "gram": "g", "grams": "g", "g": "g",
    "volt": "V", "volts": "V", "v": "V", "vac": "V AC", "vdc": "V DC",
    "amp": "A", "amps": "A", "ampere": "A", "amperes": "A", "a": "A",
    "watt": "W", "watts": "W", "w": "W",
    "hertz": "Hz", "hz": "Hz",
    "decibel": "dB", "db": "dB", "dba": "dBA",
    "gallon": "gal", "gallons": "gal", "gal.": "gal", "gal": "gal",
    "liter": "L", "litre": "L", "liters": "L", "l": "L",
    "psi": "psi", "degree": "deg", "degrees": "deg",
    "rpm": "RPM", "kw-hr": "kW-hr", "kwh": "kW-hr",
    "hour": "hr", "hours": "hr", "hrs": "hr", "hr": "hr",
    "lumen": "lm", "lumens": "lm", "lm": "lm",
    "kelvin": "K", "k": "K",
    "cubic feet": "cu ft", "cu.ft.": "cu ft", "cfm": "CFM",
    "square feet": "sq ft", "sq.ft.": "sq ft",
    "piece": "pc", "pieces": "pc", "pcs": "pc", "pc": "pc",
    "pack": "PK", "pk": "PK", "each": "EA", "ea": "EA",
    "gauge": "ga", "ga.": "ga", "ga": "ga", "awg": "AWG",
    "newton meter": "N-m", "nm": "N-m", "in-lb": "in-lb", "ft-lb": "ft-lb",
    "mil": "mil", "gpm": "GPM", "btu": "BTU", "ton": "ton",
    "micron": "micron", "ohm": "ohm", "mah": "mAh", "ah": "Ah",
    "minute": "min", "minutes": "min", "min": "min",
    "second": "sec", "seconds": "sec", "sec": "sec",
}


def canonical_uom(value: str | None) -> str:
    """Map a messy unit string onto its single approved abbreviation."""
    text = clean(value)
    if not text:
        return ""
    return UOM_CANONICAL.get(text.casefold(), text)


# `24in` -> `24 in`: always keep one space between number and unit.
_NUM_UNIT_RE = re.compile(
    r"(?<=\d)\s*(in|ft|mm|cm|lb|oz|kg|gal|psi|V|A|W|Hz|dBA|dB|RPM|AWG|ga|hr|lm|K)\b",
    re.IGNORECASE,
)

_UNIT_CASING = {
    "in": "in", "ft": "ft", "mm": "mm", "cm": "cm", "lb": "lb", "oz": "oz",
    "kg": "kg", "gal": "gal", "psi": "psi", "v": "V", "a": "A", "w": "W",
    "hz": "Hz", "dba": "dBA", "db": "dB", "rpm": "RPM", "awg": "AWG",
    "ga": "ga", "hr": "hr", "lm": "lm", "k": "K",
}


def space_units(text: str) -> str:
    """Insert the required space in `24in` and normalise unit casing."""
    def _fix(match: re.Match[str]) -> str:
        unit = match.group(1)
        return " " + _UNIT_CASING.get(unit.casefold(), unit)

    return _NUM_UNIT_RE.sub(_fix, text or "")


def normalize_measure_text(text: str) -> str:
    """Full measurement clean-up: inch marks, fractions, unit spacing."""
    result = repair_symbols(text or "")
    result = re.sub(r'(\d)\s*"', r"\1 in", result)
    result = re.sub(r"(\d)\s*'", r"\1 ft", result)
    result = fractions_in_text(result)
    result = space_units(result)
    # Anything still decimal *and* explicitly in inches is a manufacturer figure
    # that has to become a fraction to satisfy the house style.
    result = snap_decimal_inches(result)
    return re.sub(r"\s+", " ", result).strip()


# --- Casing -----------------------------------------------------------------
# Words that stay lowercase inside a title, and tokens that stay fully upper.
_LOWER_WORDS = frozenset(
    {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or",
     "the", "to", "with", "per", "vs"}
)
_FORCE_UPPER = frozenset(
    {"led", "gfci", "afci", "ul", "csa", "nsf", "ansi", "asme", "asse", "nema",
     "pvc", "cpvc", "abs", "sst", "ss", "mnpt", "fnpt", "npt", "usb", "ac",
     "dc", "hd", "lcd", "oem", "rohs", "epa", "cee", "awg", "btu", "cfm",
     "gpm", "psi", "rpm", "id", "od", "mdf", "osb", "pex", "hvac"}
)


def title_case(text: str) -> str:
    """Title Case with trade-term exceptions, e.g. `LED`, `GFCI`, `with`."""
    source = clean(text)
    if not source:
        return ""

    words = source.split(" ")
    out: list[str] = []
    for index, word in enumerate(words):
        if not word:
            continue
        bare = word.strip("(),.:;")
        folded = bare.casefold()

        if folded in _FORCE_UPPER:
            out.append(word.replace(bare, bare.upper()))
        elif re.search(r"\d", word):
            out.append(word)  # leave measurements and part numbers alone
        elif folded in _LOWER_WORDS and index != 0:
            out.append(word.lower())
        elif bare.isupper() and len(bare) > 1:
            out.append(word)  # already an intentional acronym
        else:
            out.append(word[:1].upper() + word[1:].lower() if bare else word)
    return " ".join(out)


# --- Length enforcement -----------------------------------------------------


def truncate_clean(text: str, limit: int) -> str:
    """Trim to `limit` characters without splitting a word or leaving punctuation."""
    source = clean(text)
    if len(source) <= limit:
        return source
    cut = source[:limit]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:-|/")


# Abbreviations for squeezing an invoice line into 40 characters.
INVOICE_ABBREV: dict[str, str] = {
    "STAINLESS STEEL": "SST", "STAINLESS": "SST", "RECEPTACLE": "RCPT",
    "INDUSTRIAL": "INDL", "SQUARE": "SQ", "COVER": "COVER", "SWITCH": "SW",
    "TOGGLE": "TGL", "DUPLEX": "DX", "STEEL": "STL", "GALVANIZED": "GALV",
    "ALUMINUM": "ALUM", "DISHWASHER": "DISHWASHER", "REFRIGERATOR": "REFRIG",
    "ELECTRIC": "ELEC", "ELECTRICAL": "ELEC", "MOUNTING": "MTG",
    "ASSEMBLY": "ASSY", "PACKAGE": "PKG", "CARTRIDGE": "CTG",
    "COUPLING": "CPLG", "FITTING": "FTG", "CONNECTOR": "CONN",
    "ADAPTER": "ADPT", "BRACKET": "BRKT", "WITH": "W/",
    "WITHOUT": "W/O", "AND": "&", "NUMBER": "NO", "MAXIMUM": "MAX",
    "MINIMUM": "MIN", "DIAMETER": "DIA", "LENGTH": "LG", "WIDTH": "W",
    "HEIGHT": "HT", "THICKNESS": "THK", "CORDLESS": "CRDLS",
    "BATTERY": "BATT", "CHARGER": "CHGR", "FLUORESCENT": "FLUOR",
    "INCANDESCENT": "INCAND", "HALOGEN": "HAL", "RECESSED": "RECSD",
    "BRASS": "BRS", "PLASTIC": "PLAS", "BLACK": "BK", "WHITE": "WH",
}


def abbreviate_for_invoice(text: str, limit: int = 40) -> str:
    """Uppercase and abbreviate until the string fits the invoice limit."""
    result = strip_symbols(clean(text)).upper()
    result = re.sub(r"\s+", " ", result)
    if len(result) <= limit:
        return result

    for full, short in sorted(INVOICE_ABBREV.items(), key=lambda kv: -len(kv[0])):
        if len(result) <= limit:
            break
        result = re.sub(rf"\b{re.escape(full)}\b", short, result)

    result = re.sub(r"\s+", " ", result).strip()
    return truncate_clean(result, limit)
