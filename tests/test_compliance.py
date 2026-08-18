from app.adapter.compliance import check_compliance


def test_check_compliance():
    result = check_compliance("全网最低价，加微信详聊", ["加微信"], ["全网最低"])
    assert result["sensitive_hits"] == ["加微信"]
    assert result["ad_hits"] == ["全网最低"]
    assert check_compliance("正常内容", [], [])["sensitive_hits"] == []
