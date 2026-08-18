"""Who we are allowed to read from.

The content guidelines are unambiguous: product data must come from the
manufacturer's own site or its own documentation. Marketplaces and distributor
sites are explicitly excluded. That rule is the whole reason this module exists
as a separate, testable unit rather than an `if` buried in the fetcher — a
sourcing violation is a compliance failure, not a quality issue, so it is
enforced in one place and asserted in the test suite.

Three lists do the work, plus a fourth for the fallback:

* `BLOCKED_DOMAINS` — marketplaces, distributors, manual aggregators and
  document-scraping sites. Never read, never cited, even if a search engine
  ranks them first.
* `OFFICIAL_DOMAINS` — brand or manufacturer name to its own web property.
  This stands in for `UniCat_Manufacturer_and_Brand_List.xlsx`, which is not in
  the working set. Domains learned from the labelled rows take precedence over
  this seed list; the seed only extends coverage to brands whose training rows
  had no `MFR URL`.
* `DOCUMENT_HOSTS` — content-delivery hosts that are still first-party, such as
  the CDN a manufacturer serves its own PDFs from.
* `REPUTABLE_THIRD_PARTY_DOMAINS` — standards bodies, certification
  organisations, government databases and the GS1 registry. Read only when the
  manufacturer publishes nothing about a part, never e-commerce, and always
  cited as third-party with a lower confidence ceiling.

`allowed()` fails closed. A domain that is neither the resolved manufacturer
domain nor a known official domain is refused, so an unexpected search result
cannot leak into the output.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Marketplaces, distributors, resellers and document aggregators.
BLOCKED_DOMAINS: frozenset[str] = frozenset({
    # marketplaces
    "amazon.com", "amazon.ca", "ebay.com", "walmart.com", "target.com",
    "wayfair.com", "alibaba.com", "aliexpress.com", "etsy.com", "costco.com",
    "samsclub.com", "bestbuy.com", "sears.com", "newegg.com", "overstock.com",
    "temu.com", "wish.com", "houzz.com",
    # big-box and building retail
    "homedepot.com", "lowes.com", "menards.com", "acehardware.com",
    "truevalue.com", "doitbest.com", "build.com", "ferguson.com",
    "buildwithbmc.com", "84lumber.com", "sherwin-williams.com",
    # industrial distributors
    "grainger.com", "fastenal.com", "mscdirect.com", "zoro.com",
    "globalindustrial.com", "motionindustries.com", "wesco.com",
    "graybar.com", "supplyhouse.com", "pexuniverse.com", "plumbersstock.com",
    "webstaurantstore.com", "northerntool.com", "toolup.com", "acmetools.com",
    "ohiopowertool.com", "cpotools.com", "toolnut.com", "rexelusa.com",
    "platt.com", "borderstates.com", "1000bulbs.com", "bulbs.com",
    "lightingsupply.com", "electricalwholesaler.com", "ajmadison.com",
    "applianceconnection.com", "abt.com", "us-appliance.com",
    # manual / document aggregators and scrapers
    "manualslib.com", "manualsonline.com", "manualzz.com", "manua.ls",
    "scribd.com", "yumpu.com", "pdfcoffee.com", "studylib.net",
    "docslib.org", "vdocuments.net", "partselect.com", "repairclinic.com",
    "encompass.parts", "searspartsdirect.com", "ereplacementparts.com",
    # social, forums, aggregators
    "facebook.com", "instagram.com", "pinterest.com", "twitter.com", "x.com",
    "reddit.com", "youtube.com", "tiktok.com", "linkedin.com",
    "wikipedia.org", "quora.com", "alibaba.com",
})

# The retail half of `BLOCKED_DOMAINS`, named separately so the third-party
# fallback can refuse "anyone who sells the product" on principle, not just
# anyone on the block list. Marketplaces, big-box retail and industrial
# distributors all live here; manual aggregators and social hosts are blocked
# for other reasons but are not shops.
ECOMMERCE_DOMAINS: frozenset[str] = frozenset({
    # marketplaces
    "amazon.com", "amazon.ca", "ebay.com", "walmart.com", "target.com",
    "wayfair.com", "alibaba.com", "aliexpress.com", "etsy.com", "costco.com",
    "samsclub.com", "bestbuy.com", "sears.com", "newegg.com", "overstock.com",
    "temu.com", "wish.com", "houzz.com",
    # big-box and building retail
    "homedepot.com", "lowes.com", "menards.com", "acehardware.com",
    "truevalue.com", "doitbest.com", "build.com", "ferguson.com",
    "buildwithbmc.com", "84lumber.com", "sherwin-williams.com",
    # industrial distributors
    "grainger.com", "fastenal.com", "mscdirect.com", "zoro.com",
    "globalindustrial.com", "motionindustries.com", "wesco.com",
    "graybar.com", "supplyhouse.com", "pexuniverse.com", "plumbersstock.com",
    "webstaurantstore.com", "northerntool.com", "toolup.com", "acmetools.com",
    "ohiopowertool.com", "cpotools.com", "toolnut.com", "rexelusa.com",
    "platt.com", "borderstates.com", "1000bulbs.com", "bulbs.com",
    "lightingsupply.com", "electricalwholesaler.com", "ajmadison.com",
    "applianceconnection.com", "abt.com", "us-appliance.com",
})

# Brand or manufacturer name -> its own web property. Keys are matched loosely
# (see `_key`), so "FRIGIDAIRE(R)" and "frigidaire" collide onto one entry.
OFFICIAL_DOMAINS: dict[str, str] = {
    # appliances
    "frigidaire": "frigidaire.com",
    "electrolux": "electrolux.com",
    "whirlpool": "whirlpool.com",
    "maytag": "maytag.com",
    "ge appliances": "geappliances.com",
    "ge": "geappliances.com",
    "cafe": "cafeappliances.com",
    "profile": "geappliances.com",
    "speed queen": "speedqueen.com",
    "alliance laundry systems": "alliancelaundry.com",
    "beko": "bekous.com",
    "xo appliance": "xoappliance.com",
    "rheem manufacturing": "rheem.com",
    "rheem": "rheem.com",
    # building products
    "trex": "trex.com",
    "trex company": "trex.com",
    "dsi westbury": "westburyrailing.com",
    "westbury": "westburyrailing.com",
    "digger specialties": "diggerspecialties.com",
    "rdi": "rdirail.com",
    "aj manufacturing": "ajmfg.com",
    "a j manufacturing": "ajmfg.com",
    "united window door": "unitedwindow.com",
    "united window and door": "unitedwindow.com",
    "velux": "velux.com",
    "velux america": "veluxusa.com",
    "hager": "hagerco.com",
    "hager companies": "hagerco.com",
    "huber": "huberwood.com",
    "huber engineered woods": "huberwood.com",
    "zip system": "huberwood.com",
    # electrical
    "southwire": "southwire.com",
    "woods": "southwire.com",
    "satco": "satco.com",
    "nuvo": "satco.com",
    "nuvo lighting": "satco.com",
    "kichler": "kichler.com",
    "kichler lighting": "kichler.com",
    "cooper lighting": "cooperlighting.com",
    "halo": "cooperlighting.com",
    "lithonia": "lithonia.com",
    "lithonia lighting": "lithonia.com",
    "acuity brands": "acuitybrands.com",
    "philips": "lighting.philips.com",
    "signify": "signify.com",
    "leviton": "leviton.com",
    "leviton manufacturing": "leviton.com",
    "schumacher": "schumacherelectric.com",
    "schumacher electric": "schumacherelectric.com",
    "police security": "policesecurityflashlights.com",
    # tools
    "dewalt": "dewalt.com",
    "black decker": "blackanddecker.com",
    "stanley black decker": "stanleyblackanddecker.com",
    "milwaukee": "milwaukeetool.com",
    "milwaukee tool": "milwaukeetool.com",
    "makita": "makitatools.com",
    "makita usa": "makitatools.com",
    "senco": "senco.com",
    "mirka": "mirka.com",
    "malco": "malcotools.com",
    "malco products": "malcotools.com",
    "wera": "wera.de",
    "wera tools": "wera.de",
    "diablo": "diablotools.com",
    "freud": "freudtools.com",
    "cmt": "cmtorangetools.com",
    "cmt usa": "cmtorangetools.com",
    "oliver machinery": "olivermachinery.net",
    "jet": "jettools.com",
    "jpw industries": "jpwindustries.com",
    "edge eyewear": "edgeeyewear.com",
    "wolf peak": "edgeeyewear.com",
    "us tape": "ustape.com",
    "u s tape": "ustape.com",
    "keson": "keson.com",
}

# First-party content hosts: a manufacturer's own CDN or document store.
# Reputable third-party sources, consulted only as a fallback.
#
# The sourcing rule still stands: product data comes from the manufacturer's
# own site first. But when the manufacturer publishes nothing about a part —
# no known domain, or no page that names the part number — the row used to
# degrade to zero evidence. These hosts are the exception we are willing to
# read in that situation: standards bodies, certification organisations,
# government and regulatory databases, and the GS1 barcode registry. They are
# authoritative, they are not selling the product, and they are not
# marketplaces, distributors or resellers — e-commerce hosts are refused here
# even if someone adds them to this list by mistake (see `is_ecommerce`).
#
# Every document read from one of these hosts is flagged `third_party` end to
# end, cited with its tier, and scored below first-party evidence, so the
# fallback can supplement a record but never outrank the manufacturer's own
# word.
REPUTABLE_THIRD_PARTY_DOMAINS: frozenset[str] = frozenset({
    # standards bodies
    "ansi.org", "nema.org", "iso.org", "iec.ch", "astm.org", "ieee.org",
    "ul.com", "nfpa.org", "ashrae.org",
    # certification and testing organisations
    "csagroup.org", "intertek.com", "etl.intertek.com", "tuv.com",
    "sgs.com", "nsf.org",
    # government / regulatory databases
    "energy.gov", "energystar.gov", "epa.gov", "nist.gov", "data.gov",
    "gsa.gov", "osha.gov", "cpsc.gov", "fda.gov", "usda.gov",
    "ec.europa.eu", "europa.eu",
    # barcode / product registry
    "gs1.org", "gepir.gs1.org",
})

# Content-delivery hosts that still serve first-party documents, such as the
# CDN a manufacturer serves its own PDFs from.
DOCUMENT_HOSTS: frozenset[str] = frozenset({
    "s7d2.scene7.com", "scene7.com", "cloudfront.net", "azureedge.net",
    "akamaized.net", "widen.net", "cdn.shopify.com", "documentlibrary.com",
})

_NON_WORD = re.compile(r"[^a-z0-9]+")


def _key(name: str) -> str:
    """Loose lookup key: `Speed Queen(R)` and `speed-queen` both give `speed queen`."""
    return _NON_WORD.sub(" ", (name or "").casefold()).strip()


def registrable(host: str) -> str:
    """Strip `www.` and any leading subdomain down to the registrable pair.

    `www.shop.frigidaire.com` -> `frigidaire.com`. Two-label public suffixes
    such as `co.uk` are handled so `example.co.uk` survives intact.
    """
    host = (host or "").casefold().strip().removeprefix("www.")
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if parts[-2] in {"co", "com", "org", "net", "gov", "ac"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").casefold()
    except ValueError:
        return ""


def is_blocked(url: str) -> bool:
    """True for any marketplace, distributor or aggregator."""
    host = host_of(url)
    if not host:
        return True
    return registrable(host) in BLOCKED_DOMAINS


def is_ecommerce(url: str) -> bool:
    """True for any host that sells the product.

    Marketplaces, big-box retail and industrial distributors (see
    `ECOMMERCE_DOMAINS`). Used as a belt-and-braces check on the third-party
    allowlist, so a retail domain can never sneak into the fallback even if it
    is added to `REPUTABLE_THIRD_PARTY_DOMAINS` by mistake.
    """
    host = host_of(url)
    if not host:
        return True
    return registrable(host) in ECOMMERCE_DOMAINS


def is_reputable_third_party(url: str) -> bool:
    """True only for a host on the reputable third-party allowlist.

    Fails closed like everything else in this module: an unknown domain is not
    reputable, a blocked domain is never reputable, and an e-commerce domain is
    never reputable no matter what the allowlist says.
    """
    if not url.lower().startswith(("http://", "https://")):
        return False
    if is_blocked(url) or is_ecommerce(url):
        return False
    host = host_of(url)
    if not host:
        return False
    domain = registrable(host)
    return domain in REPUTABLE_THIRD_PARTY_DOMAINS or host in REPUTABLE_THIRD_PARTY_DOMAINS


def official_domain(*names: str) -> str:
    """The known official domain for any of these brand / manufacturer names."""
    for name in names:
        key = _key(name)
        if not key:
            continue
        if key in OFFICIAL_DOMAINS:
            return OFFICIAL_DOMAINS[key]
        # `Milwaukee Accessory` and `Satco Prod Inc` carry a real brand in the
        # first token or two; try progressively shorter prefixes.
        tokens = key.split()
        for size in range(min(3, len(tokens)), 0, -1):
            prefix = " ".join(tokens[:size])
            if prefix in OFFICIAL_DOMAINS:
                return OFFICIAL_DOMAINS[prefix]
    return ""


def allowed(url: str, permitted_domains: set[str]) -> bool:
    """Fail-closed check: only first-party hosts for this product pass.

    `permitted_domains` is the set of registrable domains resolved for this
    record — the domain learned from the labelled rows plus the seeded official
    domain. A first-party document CDN is also allowed, because a specification
    sheet served from the manufacturer's own CDN is still the manufacturer's
    document.
    """
    if not url.lower().startswith(("http://", "https://")):
        return False
    if is_blocked(url):
        return False
    host = host_of(url)
    if not host:
        return False
    domain = registrable(host)
    if domain in {registrable(d) for d in permitted_domains if d}:
        return True
    return domain in DOCUMENT_HOSTS or host in DOCUMENT_HOSTS


def domains_for(brand: str, manufacturer: str, learned: str = "") -> set[str]:
    """Every domain we are willing to read for this record, best first."""
    out: list[str] = []
    if learned:
        host = host_of(learned) or learned
        if host and not is_blocked(f"https://{registrable(host)}"):
            out.append(registrable(host))
    for name in (brand, manufacturer):
        seeded = official_domain(name)
        if seeded:
            out.append(registrable(seeded))
    return {d for d in dict.fromkeys(out) if d}
