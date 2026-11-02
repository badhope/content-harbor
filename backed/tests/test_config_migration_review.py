from service.publishing import db


def test_explicit_config_migrates_once_without_overwriting(tmp_path, monkeypatch):
    legacy = tmp_path / 'legacy.json'
    target = tmp_path / 'data' / 'config.json'
    legacy.write_text('{"marker": "old"}', encoding='utf-8')
    monkeypatch.setattr(db, 'legacy_config_file', lambda: legacy)
    monkeypatch.setenv('HARBOUR_CONFIG_FILE', str(target))
    assert db.config_file() == target
    assert target.read_text(encoding='utf-8') == '{"marker": "old"}'
    legacy.write_text('{"marker": "changed"}', encoding='utf-8')
    assert db.config_file() == target
    assert target.read_text(encoding='utf-8') == '{"marker": "old"}'
