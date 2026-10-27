import asyncio
import json
import stat

import pytest
from club_ops.bootstrap import BootstrapConfig, bootstrap
from club_ops.database_transfer import export_snapshot, model_tables, read_snapshot


async def test_postgres_export_is_complete_private_and_does_not_overwrite(database_app, tmp_path):
    app = database_app
    if app.state.database.vendor != "postgresql":
        pytest.skip("Исходный экспорт проверяется на PostgreSQL")
    async with app.state.database.connection() as connection:
        await bootstrap(
            connection,
            BootstrapConfig(
                "production", False, "office@example.test", "synthetic-office-password", "https://club.example.test"
            ),
        )
    path = tmp_path / "transfer.json"
    url = app.state.settings.secret("CHECKOUT_DATABASE_URL")
    fingerprints = await asyncio.to_thread(export_snapshot, path, url)
    before = path.read_bytes()
    snapshot = json.loads(before)
    assert set(snapshot["tables"]) == set(model_tables())
    assert len(fingerprints) == 43
    assert fingerprints["directus_users"]["rows"] == 1
    assert snapshot["tables"]["directus_users"][0]["password"].startswith("bcrypt_sha256$")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        await asyncio.to_thread(export_snapshot, path, url)
    assert path.read_bytes() == before


def test_snapshot_rejects_public_files_and_symlinks(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text("{}")
    path.chmod(0o644)
    with pytest.raises(ValueError):
        read_snapshot(path)
    path.chmod(0o600)
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(OSError):
        read_snapshot(alias)
    assert read_snapshot(path) == {}
