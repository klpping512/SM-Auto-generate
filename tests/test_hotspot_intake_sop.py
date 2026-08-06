"""批 6 #26：hotspot_intake_sop 保留活函数 retrieve_service_evidence 的回归保护。

模型 RAG 下载决策链（select_for_hook_ingestion）已随批 6 删除；证据检索函数
仍被 run_dual_library_preview / audit_existing_dual_preview 使用，必须有测试护住。
"""


def _corpus():
    return [
        {
            "id": "brand:1",
            "kind": "brand_evidence",
            "category": "已确认品牌证据",
            "title": "Buffalo 仓库包裹检查与装卸作业",
            "text": "Buffalo 有已确认的仓库、包裹检查和装卸日常作业能力。",
            "category_priority": 3,
        },
        {
            "id": "kb:1",
            "kind": "knowledge_base",
            "category": "公司介绍",
            "title": "Buffalo 跨境物流公司简介",
            "text": "Buffalo 提供南非跨境清关与最后一公里配送服务。",
            "category_priority": 2,
        },
    ]


def test_retrieve_service_evidence_returns_cited_excerpts():
    import hotspot_intake_sop

    result = hotspot_intake_sop.retrieve_service_evidence(
        {"title": "Warehouse team inspects parcels before loading"},
        _corpus(),
    )

    assert 0 < len(result) <= hotspot_intake_sop.MAX_EVIDENCE_PER_CANDIDATE
    for item in result:
        assert set(item) == {"id", "kind", "category", "title", "excerpt", "retrieval_score"}
        assert item["id"].startswith(("brand:", "kb:"))
        assert item["excerpt"]
    # 词法重叠最高的品牌证据应排首位（仓库/包裹/检查均命中）
    assert result[0]["id"] == "brand:1"


def test_retrieve_service_evidence_empty_corpus_returns_empty():
    import hotspot_intake_sop

    assert hotspot_intake_sop.retrieve_service_evidence({"title": "任意热点"}, []) == []
