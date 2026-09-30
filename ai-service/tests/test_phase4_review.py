from scripts.phase4_validation import citation_contains_expected_reference, distribution


def test_fixture_citation_relevance_catches_extra_adjacent_chunk():
    expected = "within 30 minutes of discovery"
    supporting = "A suspected incident must be reported within 30 minutes of discovery."
    adjacent = "Production access is reviewed every 90 days."
    assert citation_contains_expected_reference(expected, supporting)
    assert not citation_contains_expected_reference(expected, adjacent)
    assert not citation_contains_expected_reference(None, supporting)


def test_latency_distribution_uses_recorded_samples():
    assert distribution([10, 20, 30, 40])["median"] == 25
    assert distribution([10, 20, 30, 40])["p95"] == 38.5
