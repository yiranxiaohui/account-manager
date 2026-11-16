"""项目从 claude-register 改名为 account-manager 后的兼容行为。"""
from server import oauth_import
from server.deps import LEGACY_DB_FILENAME, DB_FILENAME, resolve_db_path


def test_new_db_filename_used_by_default(tmp_path):
    assert resolve_db_path(tmp_path) == tmp_path / DB_FILENAME
    assert DB_FILENAME == "account-manager.db"


def test_legacy_db_kept_when_only_legacy_exists(tmp_path):
    legacy = tmp_path / LEGACY_DB_FILENAME
    legacy.write_bytes(b"")
    assert resolve_db_path(tmp_path) == legacy


def test_new_db_preferred_when_both_exist(tmp_path):
    (tmp_path / LEGACY_DB_FILENAME).write_bytes(b"")
    (tmp_path / DB_FILENAME).write_bytes(b"")
    assert resolve_db_path(tmp_path) == tmp_path / DB_FILENAME


def test_batch_content_types_accept_legacy():
    assert oauth_import.BATCH_CONTENT_TYPE == "application/vnd.account-manager.oauth-files+json"
    assert "application/vnd.claude-register.oauth-files+json" in oauth_import.BATCH_CONTENT_TYPES
