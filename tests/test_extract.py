"""Extraction: specification pairs, JSON-LD, document links, page kinds.

The JSON-LD cases matter most. Manufacturer sites are JavaScript applications
whose visible specification grid is hydrated client-side and simply absent from
the HTML we receive; the schema.org block they publish for search engines is
where the attribute pairs actually live. That was measured, not assumed, and
these tests pin the behaviour.
"""

from __future__ import annotations

from backend.sourcing.extract import (
    classify_document,
    classify_page,
    json_ld,
    mentions,
    parse_html,
)


class TestSpecTables:
    def test_two_cell_table_rows_become_pairs(self):
        html = """
        <html><body><table>
          <tr><th>Voltage Rating</th><td>120 V</td></tr>
          <tr><th>Sound Level</th><td>47 dBA</td></tr>
          <tr><td>Colspan row</td><td>keep</td><td>drop</td></tr>
        </table></body></html>
        """
        _, _, tables, _ = parse_html(html, "https://www.example.com/p")
        assert tables["Voltage Rating"] == "120 V"
        assert tables["Sound Level"] == "47 dBA"

    def test_definition_lists_become_pairs(self):
        html = "<dl><dt>Material</dt><dd>Stainless Steel</dd><dt>Mounting</dt><dd>Leg</dd></dl>"
        _, _, tables, _ = parse_html(html, "https://www.example.com/p")
        assert tables["Material"] == "Stainless Steel"
        assert tables["Mounting"] == "Leg"

    def test_site_furniture_is_rejected(self):
        html = """<table>
          <tr><th>Price</th><td>$499</td></tr>
          <tr><th>Quantity</th><td>1</td></tr>
          <tr><th>Sound Level</th><td>47 dBA</td></tr>
        </table>"""
        _, _, tables, _ = parse_html(html, "https://www.example.com/p")
        assert "Price" not in tables
        assert "Quantity" not in tables
        assert "Sound Level" in tables

    def test_label_equal_to_value_is_rejected(self):
        html = "<table><tr><th>Brand</th><td>Brand</td></tr></table>"
        _, _, tables, _ = parse_html(html, "https://www.example.com/p")
        assert tables == {}


class TestJsonLd:
    PAGE = """
    <html><head><script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[
      {"@type":"WebPage","name":"ignore me"},
      {"@type":"Product","name":"20V MAX Hammer Drill","mpn":"DCD1007B",
       "brand":{"@type":"Brand","name":"DEWALT"},
       "description":"A cordless hammer drill.",
       "countryOfOrigin":"China",
       "additionalProperty":[
         {"@type":"PropertyValue","name":"Chuck Size","value":"1/2 in"},
         {"@type":"PropertyValue","name":"Motor Type","value":"Brushless"},
         {"@type":"PropertyValue","name":"Price","value":"199"}
       ]}
    ]}
    </script></head><body>shell</body></html>
    """

    def test_reads_product_fields_from_a_graph(self):
        fields, pairs = json_ld(self.PAGE)
        assert fields["mpn"] == "DCD1007B"
        assert fields["brand"] == "DEWALT"
        assert fields["country"] == "China"
        assert pairs["Chuck Size"] == "1/2 in"
        assert pairs["Motor Type"] == "Brushless"

    def test_junk_labels_are_still_filtered(self):
        _, pairs = json_ld(self.PAGE)
        assert "Price" not in pairs

    def test_pairs_reach_the_parse_result(self):
        # The end-to-end path the fetcher relies on: an application shell with no
        # DOM table still yields attributes.
        _, _, tables, _ = parse_html(self.PAGE, "https://www.dewalt.com/p")
        assert tables["Chuck Size"] == "1/2 in"
        assert tables["Country"] == "China"

    def test_broken_block_does_not_raise(self):
        assert json_ld('<script type="application/ld+json">{not json</script>') == ({}, {})

    def test_absent_block_is_cheap_and_empty(self):
        assert json_ld("<html><body>nothing</body></html>") == ({}, {})


class TestDocumentLinks:
    def test_only_pdfs_are_collected_and_made_absolute(self):
        html = """<body>
          <a href="/docs/spec-sheet.pdf">Specification Sheet</a>
          <a href="https://cdn.example.com/manual.pdf">Owners Manual</a>
          <a href="/about.html">About</a>
          <a href="#top">Top</a>
        </body>"""
        _, _, _, links = parse_html(html, "https://www.example.com/p/x")
        urls = dict(links)
        assert "https://www.example.com/docs/spec-sheet.pdf" in urls
        assert "https://cdn.example.com/manual.pdf" in urls
        assert len(urls) == 2

    def test_duplicate_links_collapse(self):
        html = '<a href="/a.pdf">One</a><a href="/a.pdf">Two</a>'
        _, _, _, links = parse_html(html, "https://www.example.com/")
        assert len(links) == 1


class TestClassification:
    def test_document_kinds(self):
        assert classify_document("/x/spec-sheet.pdf", "") == "specification"
        assert classify_document("/x/submittal.pdf", "") == "specification"
        assert classify_document("/x/NA1234.pdf", "Installation Instructions") == "manual"
        assert classify_document("/x/owners.pdf", "") == "manual"
        assert classify_document("/x/whatever.pdf", "Brochure") == "other"

    def test_editorial_pages_are_identified(self):
        # A press release naming the part number is on the right domain and is
        # still the wrong source: it names several models.
        assert classify_page("https://www.milwaukeetool.com/News/Press-Releases/x") == "editorial"
        assert classify_page("https://www.example.com/blog/best-drills") == "editorial"
        assert classify_page("https://www.example.com/search?q=x") == "editorial"

    def test_product_and_support_pages(self):
        assert classify_page("https://www.dewalt.com/en-us/product/dcd1007b/x") == "product-page"
        assert classify_page("https://www.frigidaire.com/en/p/owner-center/product-support/X") == "support-page"


class TestMentions:
    def test_ignores_case_and_punctuation(self):
        assert mentions("Model PDSH-4816-AF ships today", "PDSH4816AF") is True
        assert mentions("model pdsh4816af", "PDSH4816AF") is True

    def test_rejects_absent_part_number(self):
        assert mentions("A different dishwasher entirely", "PDSH4816AF") is False

    def test_very_short_needles_are_refused(self):
        # A two-character needle appears everywhere and proves nothing.
        assert mentions("anything at all", "A1") is False

    def test_empty_inputs(self):
        assert mentions("", "ABC123") is False
        assert mentions("text", "") is False


class TestGtin:
    def test_valid_upc_and_ean(self):
        from backend.sourcing.extract import valid_gtin

        # Real codes from the labelled dataset.
        assert valid_gtin("045923658457") is True  # UPC-A
        assert valid_gtin("785592526519") is True  # UPC-A
        assert valid_gtin("00045923658457") is True  # same code as GTIN-14

    def test_bad_check_digit_rejected(self):
        from backend.sourcing.extract import valid_gtin

        assert valid_gtin("045923658458") is False
        assert valid_gtin("12345") is False  # wrong length
        assert valid_gtin("") is False

    def test_find_gtins_scans_text(self):
        from backend.sourcing.extract import find_gtins

        text = "UPC: 045923658457, order code 785592526519, part 12345"
        assert find_gtins(text) == ["045923658457", "785592526519"]

    def test_json_ld_gtin_must_validate(self):
        html = """<html><head><script type="application/ld+json">
        {"@type": "Product", "name": "Lamp", "gtin12": "045923658457",
         "gtin13": "0459236584571"}
        </script></head><body></body></html>"""
        fields, _ = json_ld(html)
        assert fields.get("upc") == "045923658457"
        # The gtin13 above fails its check digit, so it must not survive.
        assert "gtin" not in fields or fields["gtin"] != "0459236584571"

    def test_labelled_barcode_in_page_text(self):
        html = "<html><body><p>UPC: 045923658457</p></body></html>"
        _, _, tables, _ = parse_html(html, "https://x.example")
        assert tables.get("UPC") == "045923658457"

    def test_invalid_labelled_barcode_ignored(self):
        html = "<html><body><p>UPC: 045923658458</p></body></html>"
        _, _, tables, _ = parse_html(html, "https://x.example")
        assert "UPC" not in tables


class TestParseRobustness:
    def test_empty_html(self):
        assert parse_html("", "https://x.example") == ("", "", {}, [])

    def test_script_and_nav_are_stripped_from_text(self):
        html = """<html><body>
          <nav>Home Shop Cart</nav>
          <script>var junk = 1;</script>
          <p>Sound Level 47 dBA</p>
          <footer>Copyright</footer>
        </body></html>"""
        _, text, _, _ = parse_html(html, "https://x.example")
        assert "47 dBA" in text
        assert "junk" not in text
        assert "Copyright" not in text
