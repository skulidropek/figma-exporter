from figma_exporter.cli import extract_file_key


def test_extract_file_key_from_design_url() -> None:
    assert (
        extract_file_key("https://www.figma.com/design/abc123/File-Name?node-id=1-2")
        == "abc123"
    )


def test_extract_file_key_rejects_non_figma_url() -> None:
    assert extract_file_key("https://example.com/not-a-file") == ""
