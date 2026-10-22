from crewguard.textutil import chunk_message


def test_short_text_is_one_piece():
    assert chunk_message("hello\nworld") == ["hello\nworld"]


def test_splits_over_the_limit_on_line_boundaries_losslessly():
    text = "\n".join(f"line number {i}" for i in range(500))  # well over 2000 chars
    parts = chunk_message(text)
    assert len(parts) > 1
    assert all(len(p) <= 2000 for p in parts)
    assert "".join(parts) == text


def test_hard_splits_a_single_over_long_line():
    parts = chunk_message("x" * 5000)
    assert all(len(p) <= 2000 for p in parts)
    assert "".join(parts) == "x" * 5000
