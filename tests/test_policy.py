"""The sourcing rule is a compliance requirement, so it gets assertions.

The brief states product data must come from the manufacturer's own site or
documentation and explicitly excludes marketplaces and distributors. A
regression here is not a quality dip, it is a rule violation, which is why these
tests assert the negative cases as firmly as the positive ones.
"""

from __future__ import annotations

import pytest

from backend.sourcing.policy import (
    allowed,
    domains_for,
    is_blocked,
    official_domain,
    registrable,
)


class TestRegistrable:
    @pytest.mark.parametrize(
        "host,expected",
        [
            ("www.frigidaire.com", "frigidaire.com"),
            ("frigidaire.com", "frigidaire.com"),
            ("www.shop.frigidaire.com", "frigidaire.com"),
            ("assets.dewalt.com", "dewalt.com"),
            ("lithonia.acuitybrands.com", "acuitybrands.com"),
            ("example.co.uk", "example.co.uk"),
            ("shop.example.co.uk", "example.co.uk"),
            ("WWW.DEWALT.COM", "dewalt.com"),
        ],
    )
    def test_reduces_to_registrable_pair(self, host, expected):
        assert registrable(host) == expected


class TestBlocked:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.homedepot.com/p/whatever",
            "https://www.amazon.com/dp/B000",
            "https://www.grainger.com/product/123",
            "https://www.zoro.com/x",
            "https://www.manualslib.com/manual/1/x.html",
            "https://www.ajmadison.com/x",
            "https://www.ferguson.com/product/x",
            "https://en.wikipedia.org/wiki/Dishwasher",
            "https://www.youtube.com/watch",
        ],
    )
    def test_marketplaces_and_distributors_are_blocked(self, url):
        assert is_blocked(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.frigidaire.com/en/p/x",
            "https://www.dewalt.com/product/x",
            "https://assets.dewalt.com/a/b.pdf",
        ],
    )
    def test_manufacturer_sites_are_not_blocked(self, url):
        assert is_blocked(url) is False

    def test_subdomain_of_a_blocked_domain_is_still_blocked(self):
        assert is_blocked("https://smartsearch.homedepot.com/x") is True

    def test_garbage_is_blocked(self):
        assert is_blocked("not a url") is True
        assert is_blocked("") is True


class TestOfficialDomain:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("FRIGIDAIRE(R)", "frigidaire.com"),
            ("frigidaire", "frigidaire.com"),
            ("Whirlpool", "whirlpool.com"),
            ("DEWALT", "dewalt.com"),
            ("Milwaukee Tool", "milwaukeetool.com"),
            ("Leviton Manufacturing", "leviton.com"),
        ],
    )
    def test_resolves_known_brands(self, name, expected):
        assert official_domain(name) == expected

    def test_falls_back_to_a_leading_token(self):
        # Supplier strings carry noise: `Satco Prod Inc` must still find Satco.
        assert official_domain("Satco Prod Inc (5573)") == "satco.com"
        assert official_domain("Milwaukee Accessory (4031)") == "milwaukeetool.com"

    def test_unknown_brand_returns_empty(self):
        assert official_domain("Totally Made Up Brand Ltd") == ""
        assert official_domain("") == ""


class TestAllowed:
    PERMITTED = {"frigidaire.com"}

    def test_permitted_domain_passes(self):
        assert allowed("https://www.frigidaire.com/en/p/x", self.PERMITTED) is True

    def test_fails_closed_for_an_unrelated_domain(self):
        # The decisive property: a domain we have no reason to trust is refused
        # even though it is on nobody's block list.
        assert allowed("https://random-spec-site.example/x", self.PERMITTED) is False

    def test_blocked_domain_never_passes_even_if_permitted(self):
        assert allowed("https://www.homedepot.com/x", {"homedepot.com"}) is False

    def test_non_http_schemes_are_refused(self):
        for url in ("ftp://frigidaire.com/x", "file:///etc/passwd", "javascript:alert(1)"):
            assert allowed(url, self.PERMITTED) is False

    def test_first_party_cdn_subdomain_is_allowed(self):
        assert allowed("https://assets.dewalt.com/a/b.pdf", {"dewalt.com"}) is True

    def test_empty_permission_set_allows_nothing(self):
        assert allowed("https://www.frigidaire.com/x", set()) is False


class TestDomainsFor:
    def test_learned_domain_and_seed_are_combined(self):
        found = domains_for("FRIGIDAIRE(R)", "Rheem Manufacturing", "https://www.frigidaire.com/en/p/x")
        assert "frigidaire.com" in found
        assert "rheem.com" in found

    def test_a_distributor_learned_by_mistake_is_dropped(self):
        # If the labelled data ever carried a distributor URL, it must not become
        # a permitted source.
        found = domains_for("Some Brand", "", "https://www.grainger.com/x")
        assert "grainger.com" not in found

    def test_unknown_brand_yields_nothing(self):
        assert domains_for("Totally Made Up Brand", "", "") == set()
