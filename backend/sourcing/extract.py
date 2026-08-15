"""Turning a fetched page into evidence: readable text, spec tables, documents.

Three extractions happen here, in decreasing order of value:

1. **Specification tables.** A manufacturer product page almost always carries
   the exact attribute grid we are trying to fill, as a `<table>`, a `<dl>`, or
   a list of label/value pairs. Parsed into a mapping, these are the highest
   quality evidence available — they are the manufacturer's own words for its
   own attribute names, which is precisely what the delivery format wants.
2. **Readable prose.** Navigation, cookie banners, scripts and footers are
   stripped so the prompt context is specification text rather than site
   furniture.
3. **Document links.** PDFs linked from the page, classified by filename and
   anchor text, so a specification sheet can be fetched and read as well as
   merely named.

`lxml` does the parsing. It is already in the environment as a transitive
dependency, so this adds no install burden.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urldefrag, urljoin

from backend.core.normalize import clean, repair_symbols

logger = logging.getLogger(__name__)

# Elements that never contain product data.
_DROP_TAGS = (
    "script", "style", "noscript", "nav", "footer", "header", "form",
    "svg", "iframe", "template", "aside", "button",
)

# Anchor text or filename fragments that identify a document type. Order
# matters: `installation` is checked before the looser `manual`.
_DOCUMENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("specification", "specification"),
    ("spec-sheet", "specification"),
    ("spec sheet", "specification"),
    ("specsheet", "specification"),
    ("submittal", "specification"),
    ("technical-data", "specification"),
    ("tech-data", "specification"),
    ("datasheet", "specification"),
    ("data-sheet", "specification"),
    ("cut-sheet", "specification"),
    ("installation", "manual"),
    ("install", "manual"),
    ("owner", "manual"),
    ("user-guide", "manual"),
    ("use-and-care", "manual"),
    ("instruction", "manual"),
    ("manual", "manual"),
    ("dimension", "specification"),
    ("energy-guide", "specification"),
    ("energyguide", "specification"),
)

# Table rows whose label is site furniture rather than a product attribute.
_JUNK_LABELS = frozenset({
    "", "share", "compare", "quantity", "qty", "price", "add to cart", "sku",
    "reviews", "rating", "availability", "in stock", "shipping", "warranty?",
    "email", "print", "save", "wishlist", "colour", "select",
})

_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n\s*\n\s*\n+")
_LABELISH = re.compile(r"^[A-Za-z][A-Za-z0-9 /()°%.,'\"+&-]{1,48}$")


def _text_of(node) -> str:
    return clean(repair_symbols(_WS.sub(" ", node.text_content() or "")))


def _looks_like_label(text: str) -> bool:
    if not text or text.casefold().strip(": ") in _JUNK_LABELS:
        return False
    return bool(_LABELISH.match(text.strip().rstrip(":")))


def classify_document(url: str, anchor: str = "") -> str:
    """Guess a document kind from its filename and link text."""
    blob = f"{anchor} {url}".casefold()
    for needle, kind in _DOCUMENT_PATTERNS:
        if needle in blob:
            return kind
    return "other"


# Paths that are on the manufacturer's own site but are not about one product.
# A press release naming a part number is a real mention and a bad source: it
# also names six other models, so any attribute read from it may belong to a
# different tool.
_LOW_VALUE_PATHS: tuple[str, ...] = (
    "/news", "press-release", "/blog", "/article", "/media", "/events",
    "/careers", "/about", "/legal", "/privacy", "/terms", "/rebate",
    "/promotions", "/catalog", "/search", "/sitemap", "/dealer", "/where-to-buy",
)


def classify_page(url: str, title: str = "") -> str:
    """Rough page kind, used to rank evidence and pick the `MFR URL`."""
    blob = f"{url} {title}".casefold()
    if any(k in blob for k in _LOW_VALUE_PATHS):
        return "editorial"
    if any(k in blob for k in ("/support", "product-support", "owner-center", "/service")):
        return "support-page"
    if any(k in blob for k in ("spec", "technical", "datasheet")):
        return "specification"
    return "product-page"


_LD_BLOCK = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I
)


def _walk_ld(node, out: list[dict]) -> None:
    """Collect every dict in a JSON-LD tree, following `@graph` and lists."""
    if isinstance(node, dict):
        out.append(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                _walk_ld(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_ld(item, out)


def json_ld(html: str) -> tuple[dict[str, str], dict[str, str]]:
    """Read structured product data out of JSON-LD blocks.

    Modern manufacturer sites are JavaScript applications: the visible
    specification grid is hydrated client-side and simply is not in the HTML we
    receive. What *is* in the HTML is the schema.org block the same sites publish
    for search engines, and for a `Product` that block frequently carries
    `additionalProperty` — a list of name/value pairs, which is exactly the shape
    the delivery format's attribute triplets need.

    Returns `(fields, pairs)`: identity fields such as name, brand, mpn and
    description, and the attribute pairs.
    """
    if not html or "ld+json" not in html.lower():
        return {}, {}

    import json

    nodes: list[dict] = []
    for block in _LD_BLOCK.findall(html):
        try:
            _walk_ld(json.loads(block.strip()), nodes)
        except Exception:  # noqa: BLE001 - a broken block is not fatal
            continue

    fields: dict[str, str] = {}
    pairs: dict[str, str] = {}

    def as_text(value) -> str:
        if isinstance(value, str):
            return clean(repair_symbols(_WS.sub(" ", value)))
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict):
            return as_text(value.get("name") or value.get("value") or "")
        if isinstance(value, list) and value:
            return as_text(value[0])
        return ""

    for node in nodes:
        types = node.get("@type")
        types = [types] if isinstance(types, str) else (types or [])
        if not any(str(t).casefold() == "product" for t in types):
            continue

        for key, target in (
            ("name", "name"),
            ("description", "description"),
            ("mpn", "mpn"),
            ("sku", "sku"),
            ("gtin13", "gtin"),
            ("gtin12", "upc"),
            ("gtin14", "gtin"),
            ("gtin8", "gtin"),
            ("gtin", "gtin"),
            ("brand", "brand"),
            ("manufacturer", "manufacturer"),
            ("color", "color"),
            ("material", "material"),
            ("countryOfOrigin", "country"),
        ):
            value = as_text(node.get(key))
            if not value or target in fields:
                continue
            # Barcode fields must pass their own check digit: a schema.org
            # block is machine-written but not machine-checked.
            if target in ("gtin", "upc") and not valid_gtin(value):
                continue
            fields[target] = value

        for prop in node.get("additionalProperty") or []:
            if not isinstance(prop, dict):
                continue
            label = clean(as_text(prop.get("name") or prop.get("propertyID")))
            value = as_text(prop.get("value") or prop.get("valueReference"))
            if _looks_like_label(label) and value and len(value) <= 220:
                pairs.setdefault(label.rstrip(":"), value)

    return fields, pairs


def parse_html(html: str, base_url: str) -> tuple[str, str, dict[str, str], list[tuple[str, str]]]:
    """Return `(title, readable_text, spec_table, document_links)`.

    `document_links` is a list of `(absolute_url, anchor_text)` for PDFs only.
    Every failure path returns empties rather than raising: a page we cannot
    parse is simply not evidence.
    """
    if not html or not html.strip():
        return "", "", {}, []
    try:
        import lxml.html
    except ImportError:  # pragma: no cover - lxml is present in the venv
        logger.debug("lxml unavailable; cannot parse HTML")
        return "", "", {}, []

    try:
        tree = lxml.html.fromstring(html)
    except Exception as exc:  # noqa: BLE001 - malformed markup is common
        logger.debug("html parse failed for %s: %s", base_url, exc)
        return "", "", {}, []

    title = ""
    found = tree.findtext(".//title")
    if found:
        title = clean(repair_symbols(_WS.sub(" ", found)))[:200]

    documents = _document_links(tree, base_url)
    # Tables are read before the tree is stripped, because some sites wrap
    # specification blocks in elements that also match the drop list.
    tables = _spec_pairs(tree)

    # Structured data wins where both exist: a schema.org pair is the
    # manufacturer's own machine-readable claim, not a scraped table cell.
    ld_fields, ld_pairs = json_ld(html)
    for label, value in ld_pairs.items():
        tables[label] = value
    for key in ("material", "color", "country"):
        if ld_fields.get(key):
            tables.setdefault(key.title(), ld_fields[key])
    # Validated barcodes travel as spec pairs so the research stage can write
    # them into the delivery columns with their citation.
    if ld_fields.get("upc"):
        tables.setdefault("UPC", ld_fields["upc"])
    if ld_fields.get("gtin"):
        tables.setdefault("GTIN", ld_fields["gtin"])
    if ld_fields.get("description"):
        # Appended so the JSON-LD description is searchable for grounding even
        # when the rendered page body is a near-empty application shell.
        tables.setdefault("_ld_description", ld_fields["description"])

    for tag in _DROP_TAGS:
        for node in tree.iter(tag):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

    text = _BLANKS.sub("\n\n", clean(repair_symbols(tree.text_content() or "")))
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)

    # Spec pages often print the barcode as a labelled line rather than in
    # structured data: `UPC: 012345678901`. A labelled, check-digit-valid code
    # is as trustworthy as a schema.org field.
    for label, code in _labelled_barcodes(text):
        tables.setdefault(label, code)

    return title, text, tables, documents


_BARCODE_LABEL = re.compile(
    r"\b(UPC|GTIN|EAN)\b[\s:#]*([0-9][0-9 -]{10,17}[0-9])", re.I
)


def _labelled_barcodes(text: str) -> list[tuple[str, str]]:
    """`UPC: 012345678901` style pairs, check-digit validated."""
    out: list[tuple[str, str]] = []
    for match in _BARCODE_LABEL.finditer(text or ""):
        label = match.group(1).upper()
        code = re.sub(r"\D", "", match.group(2))
        if not valid_gtin(code):
            continue
        target = "UPC" if (label == "UPC" and len(code) == 12) else "GTIN"
        if label == "EAN" and len(code) == 13:
            target = "GTIN"
        out.append((target, code))
    return out


def _document_links(tree, base_url: str) -> list[tuple[str, str]]:
    """Every PDF linked from the page, de-duplicated, with its anchor text."""
    out: dict[str, str] = {}
    for anchor in tree.iter("a"):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urldefrag(urljoin(base_url, href))[0]
        if not absolute.lower().split("?")[0].endswith(".pdf"):
            continue
        label = _text_of(anchor)[:120] or (anchor.get("title") or "").strip()[:120]
        out.setdefault(absolute, label)
    return list(out.items())


def _spec_pairs(tree) -> dict[str, str]:
    """Label/value pairs from tables, definition lists and label/value divs."""
    pairs: dict[str, str] = {}

    def offer(label: str, value: str) -> None:
        label, value = clean(label).rstrip(":"), clean(value)
        if not _looks_like_label(label) or not value or len(value) > 220:
            return
        if label.casefold() == value.casefold():
            return
        pairs.setdefault(label, repair_symbols(value))

    for table in tree.iter("table"):
        for row in table.iter("tr"):
            cells = [c for c in row.iter("th", "td")]
            if len(cells) == 2:
                offer(_text_of(cells[0]), _text_of(cells[1]))

    for definition in tree.iter("dl"):
        terms = [_text_of(t) for t in definition.iter("dt")]
        values = [_text_of(d) for d in definition.iter("dd")]
        for label, value in zip(terms, values):
            offer(label, value)

    # Sites that render specs as sibling divs/spans with label-ish class names.
    for node in tree.iter("div", "li", "tr", "p"):
        classes = (node.get("class") or "").casefold()
        if "spec" not in classes and "attribute" not in classes and "feature" not in classes:
            continue
        children = [c for c in node.iterchildren() if isinstance(c.tag, str)]
        if len(children) == 2:
            offer(_text_of(children[0]), _text_of(children[1]))

    return pairs


def mentions(text: str, needle: str) -> bool:
    """Case- and punctuation-insensitive containment, for MPN confirmation."""
    if not needle:
        return False
    strip = re.compile(r"[^a-z0-9]+")
    folded_needle = strip.sub("", needle.casefold())
    if len(folded_needle) < 3:
        return False
    return folded_needle in strip.sub("", (text or "").casefold())


# --- GTIN / UPC identifiers -------------------------------------------------

_DIGIT_RUN = re.compile(r"\b\d{12,14}\b")


def valid_gtin(code: str) -> bool:
    """Check-digit validation for GTIN-8/12/13/14 (UPC-A and EAN included).

    A barcode that fails its own check digit is a misread or a coincidence of
    digits, never an identifier, so nothing invalid ever reaches the output.
    """
    digits = re.sub(r"\D", "", code or "")
    if len(digits) not in (8, 12, 13, 14):
        return False
    body, check = digits[:-1], int(digits[-1])
    # Weights alternate 3,1,3,... starting from the digit nearest the check.
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def find_gtins(text: str, limit: int = 5) -> list[str]:
    """Digit runs of barcode length that pass check-digit validation.

    Only 12-14 digit runs are considered: 8-digit runs are far too often part
    numbers or dates, while UPC-A is 12 digits, EAN-13 is 13 and GTIN-14 is 14.
    """
    seen: dict[str, None] = {}
    for match in _DIGIT_RUN.finditer(text or ""):
        code = match.group(0)
        if valid_gtin(code):
            seen.setdefault(code, None)
        if len(seen) >= limit:
            break
    return list(seen)
