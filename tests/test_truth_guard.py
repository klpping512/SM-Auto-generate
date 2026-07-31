import truth_guard


def test_current_event_requires_sentence_level_evidence():
    title = "德班港最新动态"
    body = "Transnet 今日宣布德班港部分作业延误。请及时核对船期。"
    result = truth_guard.evaluate(title, body, [])
    assert result["status"] == "needs_evidence"
    assert result["uncovered"]

    evidence = [{
        "claim": "德班港最新动态",
        "url": "https://example.org/port-update",
        "source_title": "Port operational update",
        "publisher": "Transnet",
        "excerpt": "Operations at one terminal are delayed.",
    }, {
        "claim": "Transnet 今日宣布德班港部分作业延误",
        "url": "https://example.org/port-update",
        "source_title": "Port operational update",
        "publisher": "Transnet",
        "excerpt": "Operations at one terminal are delayed.",
    }]
    assert truth_guard.evaluate(title, body, evidence)["status"] == "verified"


def test_bad_or_unmapped_evidence_does_not_unlock_publish():
    result = truth_guard.evaluate("南非最新政策", "南非政府今日发布新政策。", [{
        "claim": "另一条不存在的事实", "url": "javascript:alert(1)",
        "source_title": "", "publisher": "", "excerpt": "",
    }])
    assert result["status"] == "needs_evidence"
    assert result["invalid_evidence"] == [0]


def test_non_factual_advice_does_not_require_citation():
    result = truth_guard.evaluate("清关资料准备指南", "请提前核对发票、装箱单与收件人信息。", [])
    assert result["status"] == "not_required"
