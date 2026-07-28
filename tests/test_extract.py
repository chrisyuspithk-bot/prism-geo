from prism.extract import categorize, find_citations, find_mentions, normalize_domain


def test_find_mentions_ranks_by_first_occurrence():
    text = "Adidas and Nike are top brands. Nike has the best lineup; Asics trails."
    mentions = find_mentions(text)
    names = [m["brand"] for m in mentions]
    assert names[0] == "Adidas"  # appears first in text
    assert names[1] == "Nike"
    assert mentions[1]["position"] == 2
    assert mentions[1]["count"] == 2
    assert "Asics" in names


def test_find_mentions_word_boundary():
    # "On" must not match inside "recommendation"; "NB" alias maps to New Balance
    text = "My recommendation: NB 1080s are great for motion control."
    mentions = find_mentions(text)
    brands = {m["brand"] for m in mentions}
    assert "New Balance" in brands
    assert "On" not in brands


def test_find_mentions_alias_case_insensitive():
    mentions = find_mentions("HOKA one one makes soft shoes.")
    assert any(m["brand"] == "Hoka" for m in mentions)


def test_find_citations_dedupes_and_categorizes():
    text = ("See https://www.runnersworld.com/best-running-shoes and "
            "https://www.reddit.com/r/running/comments/x plus "
            "https://runnersworld.com/best-running-shoes again.")
    cites = find_citations(text, brand_domains={"nike.com"})
    urls = [c["url"] for c in cites]
    assert len(urls) == len(set(urls)) == 2
    by_domain = {c["domain"]: c for c in cites}
    assert by_domain["runnersworld.com"]["category"] == "editorial"
    assert by_domain["reddit.com"]["category"] == "social"


def test_categorize_brand_domain_wins():
    assert categorize("www.nike.com", {"nike.com"}) == "brand"
    assert categorize("shop.nike.com", {"nike.com"}) == "brand"
    assert categorize("nike.com") == "other"  # no brand_domains -> not brand


def test_normalize_domain():
    assert normalize_domain("https://www.Example.com/path?q=1") == "example.com"
