"""Behavior checks for the separated workspace and stable entry point."""
from qagent.app import RuntimeSettings, create_store
from qagent.core.storage import RunBusy
import pytest


def test_default_state_and_explicit_override_keep_inputs_outputs_separate(tmp_path):
    settings = RuntimeSettings(state_dir=tmp_path / "data/state",
                               result_dir=tmp_path / "result",
                               input_archive_dir=tmp_path / "config/history")
    store = create_store(None, settings)
    assert store.root == tmp_path / "data/state"
    assert store.result_root == tmp_path / "result"
    assert store.config_root == tmp_path / "config/history"
    assert not (tmp_path / "runs").exists()
    with store.run_lock("experiment"):
        assert list(store.lock_root.glob("*.lock"))
        assert not list(store.root.glob("*.lock"))
        with pytest.raises(RunBusy):
            with create_store(None, settings).run_lock("experiment"):
                pytest.fail("second executor acquired an active lock")
    override = create_store(tmp_path / "custom-state", settings)
    assert override.root == tmp_path / "custom-state"
    assert override.result_root == store.result_root


def test_saved_journal_is_readable_after_recreating_store(tmp_path):
    settings = RuntimeSettings(state_dir=tmp_path / "data/state",
                               result_dir=tmp_path / "result",
                               input_archive_dir=tmp_path / "config/history")
    first = create_store(None, settings)
    with first.connect() as db:
        db.execute("CREATE TABLE migration_probe (value TEXT)")
        db.execute("INSERT INTO migration_probe VALUES ('preserved')")
    with create_store(None, settings).connect() as db:
        assert db.execute("SELECT value FROM migration_probe").fetchone()[0] == "preserved"
