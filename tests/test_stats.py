from prism import stats


def test_visibility_by_day():
    runs = [
        {"day": "2026-07-01", "mentioned": True},
        {"day": "2026-07-01", "mentioned": False},
        {"day": "2026-07-02", "mentioned": True},
    ]
    series = stats.visibility_by_day(runs, "Nike")
    assert series == [
        {"day": "2026-07-01", "pct": 50.0},
        {"day": "2026-07-02", "pct": 100.0},
    ]


def test_overall_visibility_zero_runs():
    assert stats.overall_visibility(0, 0) == 0.0
    assert stats.overall_visibility(10, 7) == 70.0


def test_share_of_voice_sums_to_100():
    table = stats.share_of_voice({"Nike": 50, "Adidas": 30, "Asics": 20})
    assert table[0]["brand"] == "Nike"
    assert table[0]["rank"] == 1
    assert round(sum(r["share"] for r in table)) == 100


def test_stability_identical_days_is_100():
    daily = [
        {"day": "2026-07-01", "domain": "a.com", "count": 3},
        {"day": "2026-07-01", "domain": "b.com", "count": 1},
        {"day": "2026-07-02", "domain": "a.com", "count": 6},
        {"day": "2026-07-02", "domain": "b.com", "count": 2},
    ]
    assert stats.stability_score(daily) == 100


def test_stability_full_churn_is_zero():
    daily = [
        {"day": "2026-07-01", "domain": "a.com", "count": 1},
        {"day": "2026-07-02", "domain": "b.com", "count": 1},
    ]
    assert stats.stability_score(daily) == 0


def test_stability_needs_two_days():
    assert stats.stability_score([{"day": "2026-07-01", "domain": "a.com", "count": 1}]) is None


def test_opportunity_score():
    # fully visible -> no opportunity
    assert stats.prompt_opportunity_score(100, 10, 10) == 0.0
    # invisible + competitors always present -> max opportunity
    assert stats.prompt_opportunity_score(0, 10, 10) == 100.0
    # invisible + competitors present half the time
    assert stats.prompt_opportunity_score(0, 5, 10) == 50.0
