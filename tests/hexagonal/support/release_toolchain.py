"""The interpreter of a fake release toolchain: the current xorchestrate run as another release.

``fixtures/fake_uv.py`` (with ``FAKE_UV_REAL=release``) writes toolchain
interpreters that run this file as
``python release_toolchain.py RELEASE DATA_VERSION -P -m xorchestrate ARGS...``.
It sets ``__version__`` to ``RELEASE`` and, for a data version above 1, makes
the storage adapter read that version with a migration step that marks every
``work`` record ``{"fixture_migrated": DATA_VERSION}``.
``$FAKE_TOOLCHAIN_MIGRATION=fail`` makes the step fail. Both are patched before
the storage adapter, which binds them at import, is imported.
"""
import os
import sys


def run(release, data_version, arguments):
    import xorchestrate

    xorchestrate.__version__ = release.removeprefix("v")
    if data_version > 1:
        from xorchestrate.adapters import storage_migrations

        def mark(transaction):
            if os.environ.get("FAKE_TOOLCHAIN_MIGRATION") == "fail":
                raise RuntimeError("simulated migration failure")
            records = transaction.list("work")
            for record in records:
                transaction.put("work", record.key, {**record.data, "fixture_migrated": data_version},
                                expected_version=record.version)
            return {"work": len(records)}

        storage_migrations.DATA_VERSION = data_version
        storage_migrations.RELEASE = release
        storage_migrations.REGISTRY = storage_migrations.MigrationRegistry(
            data_version, {source: mark for source in range(1, data_version)})
    if arguments[:3] != ["-P", "-m", "xorchestrate"]:
        sys.stderr.write(f"unexpected toolchain arguments: {arguments}\n")
        return 2
    sys.argv = ["xorchestrate", *arguments[3:]]
    from xorchestrate.cli import main

    return main()


if __name__ == "__main__":
    # Run as a script: drop this directory from the path so nothing here shadows a package.
    sys.path.pop(0)
    sys.exit(run(sys.argv[1], int(sys.argv[2]), sys.argv[3:]))
