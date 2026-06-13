from forgetforge import rust_bridge


def test_higher_recall_increases_retention():
    low = rust_bridge.compute_retention(
        days_since_recall=10.0,
        retrieval_count=0.0,
        importance=0.5,
        frequency=0.2,
    )
    high = rust_bridge.compute_retention(
        days_since_recall=10.0,
        retrieval_count=5.0,
        importance=0.5,
        frequency=0.2,
    )
    assert high["retention"] > low["retention"]


def test_hot_tier_recent_recall():
    decision = rust_bridge.decide_tier(
        days_since_recall=3.0,
        retrieval_count=2.0,
        importance=0.5,
        frequency=0.2,
    )
    assert decision["tier"] == "hot"


def test_cold_tier_low_retention():
    decision = rust_bridge.decide_tier(
        days_since_recall=200.0,
        retrieval_count=0.0,
        importance=0.1,
        frequency=0.0,
    )
    assert decision["tier"] == "cold"
