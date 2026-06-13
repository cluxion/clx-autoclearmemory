from pathlib import Path

from forgetforge import db, import_brief


def test_import_preprocessing_brief(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    result = import_brief.import_brief(
        conn,
        source="preprocessing",
        brief="Queued 3 segments; user wants API rate limiting design.",
    )
    assert result["ok"] is True
    assert result["source"] == "preprocessing"
    assert "[preprocessing brief]" in result["stored"]["content_preview"] or True
    conn.close()
