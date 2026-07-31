import hotspot_topic_packages


def test_cluster_signals_into_one_event_and_score_logistics_relevance():
    signals = [
        {
            "source_type": "news",
            "source_name": "SABC",
            "title": "E-hailing drivers begin national shutdown",
            "summary": "Johannesburg drivers protest",
            "source_url": "https://a.example/1",
            "published_at": "2026-07-24T08:00:00+00:00",
        },
        {
            "source_type": "news",
            "source_name": "News24",
            "title": "Uber drivers protest in Johannesburg",
            "summary": "Major disruption expected",
            "source_url": "https://b.example/2",
            "published_at": "2026-07-24T08:20:00+00:00",
        },
    ]

    packages = hotspot_topic_packages.cluster_signals(signals)

    package = packages[0]
    assert len(package["signals"]) == 2
    assert package["event_type"] == "strike"
    assert package["logistics_relevance"] >= 70
    assert package["heat_score"] > 0
    assert set(package["breakdown"]) == {
        "search_growth", "local_coverage", "cross_platform", "video_growth",
        "freshness", "logistics_relevance", "media_richness",
    }
