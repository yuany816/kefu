from app.services.chunking import TextChunker


def test_chunker_preserves_sections_and_limits_size() -> None:
    text = """# 退货政策
商品签收后7天内可以申请退货。
商品需保持完好，不影响二次销售。

# 物流政策
通常在48小时内发货。
"""
    chunks = TextChunker(target_chars=100, overlap_chars=10).split(text)

    assert chunks
    assert chunks[0].section_title == "退货政策"
    assert any(chunk.section_title == "物流政策" for chunk in chunks)
    assert all(chunk.token_count > 0 for chunk in chunks)


def test_chunker_rejects_invalid_configuration() -> None:
    try:
        TextChunker(target_chars=100, overlap_chars=100)
    except ValueError as exc:
        assert "overlap_chars" in str(exc)
    else:
        raise AssertionError("expected ValueError")

