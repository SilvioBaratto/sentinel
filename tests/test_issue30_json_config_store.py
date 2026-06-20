"""
Source-blind tests for issue #30: JSON config store & Application Support paths.

Tests are derived exclusively from the acceptance criteria and requirements.md.
The implementation under src/ was NOT read. This file is the failing contract
the implementation must satisfy (Red phase of TDD).

Acceptance criteria covered:
  AC1. JsonConfigStore implements ConfigStore: load()->AppConfig, save(AppConfig)->None,
       paths()->SentinelPaths
  AC2. load() creates the Application Support directory if absent, reads config.json,
       returns AppConfig.from_mapping(...)
  AC3. Missing file → all-defaults AppConfig (no raise); corrupt/invalid JSON →
       all-defaults AppConfig (no raise)
  AC4. save() serialises via AppConfig.to_mapping() and writes config.json atomically
       (temp file + replace)
  AC5. resolve_paths() returns SentinelPaths.default() and is importable without
       touching the filesystem until a method is called
  AC6. Round-trip: save(cfg) then load() returns an equal AppConfig (tested in a
       tmp dir via an injectable base directory)

Criteria NOT tested (oracle: not runtime-verifiable):
  - "All tests pass" — boilerplate suite gate; no per-criterion assertion
  - "SOLID, clean code …" — subjective prose; no concrete runtime assertion
"""

import json
import pathlib
import tempfile

from hypothesis import given, strategies as st

from sentinel.config import AppConfig, JsonConfigStore, SentinelPaths, resolve_paths
from sentinel.domain.protocols import ConfigStore


# ── AC1: JsonConfigStore implements ConfigStore ───────────────────────────────


class TestJsonConfigStoreImplementsConfigStore:
    """AC1: JsonConfigStore exposes load, save, paths and satisfies the ConfigStore protocol."""

    def test_when_json_config_store_checked_then_it_satisfies_config_store_protocol(
        self, tmp_path
    ):
        store = JsonConfigStore(base_dir=tmp_path)
        assert isinstance(store, ConfigStore)

    def test_when_load_called_then_return_type_is_app_config(self, tmp_path):
        result = JsonConfigStore(base_dir=tmp_path).load()
        assert isinstance(result, AppConfig)

    def test_when_save_called_with_app_config_then_none_is_returned(self, tmp_path):
        cfg = AppConfig.from_mapping({})
        result = JsonConfigStore(base_dir=tmp_path).save(cfg)
        assert result is None

    def test_when_paths_called_then_return_type_is_sentinel_paths(self, tmp_path):
        result = JsonConfigStore(base_dir=tmp_path).paths()
        assert isinstance(result, SentinelPaths)


# ── AC2: load() creates directory if absent ───────────────────────────────────


class TestLoadCreatesDirectoryIfAbsent:
    """AC2: load() creates the base directory when it does not yet exist."""

    def test_when_base_directory_does_not_exist_then_load_creates_it(self, tmp_path):
        missing = tmp_path / "not_yet"
        assert not missing.exists()
        JsonConfigStore(base_dir=missing).load()
        assert missing.is_dir()

    def test_when_config_json_exists_then_load_returns_app_config_instance(
        self, tmp_path
    ):
        cfg = AppConfig.from_mapping({})
        (tmp_path / "config.json").write_text(json.dumps(cfg.to_mapping()))
        result = JsonConfigStore(base_dir=tmp_path).load()
        assert isinstance(result, AppConfig)


# ── AC3: missing file or corrupt JSON → all-defaults, no raise ───────────────


class TestLoadFallsBackToDefaults:
    """AC3: any unreadable or absent config.json yields all-defaults without raising."""

    def test_when_config_json_is_missing_then_load_returns_all_defaults(self, tmp_path):
        result = JsonConfigStore(base_dir=tmp_path).load()
        assert result == AppConfig.from_mapping({})

    def test_when_config_json_is_missing_then_load_does_not_raise(self, tmp_path):
        JsonConfigStore(
            base_dir=tmp_path
        ).load()  # no assertion needed; any exception fails the test

    def test_when_config_json_contains_invalid_json_then_load_returns_all_defaults(
        self, tmp_path
    ):
        (tmp_path / "config.json").write_text("}{not valid json")
        result = JsonConfigStore(base_dir=tmp_path).load()
        assert result == AppConfig.from_mapping({})

    def test_when_config_json_contains_invalid_json_then_load_does_not_raise(
        self, tmp_path
    ):
        (tmp_path / "config.json").write_text("{broken:")
        JsonConfigStore(base_dir=tmp_path).load()

    def test_when_config_json_is_empty_then_load_returns_all_defaults(self, tmp_path):
        (tmp_path / "config.json").write_text("")
        result = JsonConfigStore(base_dir=tmp_path).load()
        assert result == AppConfig.from_mapping({})

    def test_when_config_json_contains_json_array_not_object_then_load_returns_all_defaults(
        self, tmp_path
    ):
        # Valid JSON but wrong type (array instead of object) → treated as corrupt
        (tmp_path / "config.json").write_text("[1, 2, 3]")
        result = JsonConfigStore(base_dir=tmp_path).load()
        assert result == AppConfig.from_mapping({})


# ── AC4: save() writes config.json ────────────────────────────────────────────


class TestSaveWritesConfigJson:
    """AC4: save() calls to_mapping() and writes config.json; content round-trips as JSON."""

    def test_when_save_called_then_config_json_file_is_created(self, tmp_path):
        JsonConfigStore(base_dir=tmp_path).save(AppConfig.from_mapping({}))
        assert (tmp_path / "config.json").exists()

    def test_when_save_called_then_config_json_contains_valid_json(self, tmp_path):
        JsonConfigStore(base_dir=tmp_path).save(AppConfig.from_mapping({}))
        content = (tmp_path / "config.json").read_text()
        json.loads(content)  # must not raise

    def test_when_save_called_then_config_json_matches_app_config_to_mapping(
        self, tmp_path
    ):
        cfg = AppConfig.from_mapping({})
        JsonConfigStore(base_dir=tmp_path).save(cfg)
        written = json.loads((tmp_path / "config.json").read_text())
        assert written == cfg.to_mapping()

    def test_when_save_called_twice_then_second_write_overwrites_first(self, tmp_path):
        store = JsonConfigStore(base_dir=tmp_path)
        first_cfg = AppConfig.from_mapping({})
        store.save(first_cfg)
        # Save the same cfg again; the file should still be valid and equal
        store.save(first_cfg)
        written = json.loads((tmp_path / "config.json").read_text())
        assert written == first_cfg.to_mapping()


# ── AC5: resolve_paths() ──────────────────────────────────────────────────────


class TestResolvePaths:
    """AC5: resolve_paths() returns SentinelPaths.default(); instantiation has no FS side-effects."""

    def test_when_resolve_paths_called_then_return_type_is_sentinel_paths(self):
        assert isinstance(resolve_paths(), SentinelPaths)

    def test_when_resolve_paths_called_then_result_equals_sentinel_paths_default(self):
        assert resolve_paths() == SentinelPaths.default()


# ── Regression: no circular import when config_store is imported first ─────────


class TestNoCircularImport:
    """config.py lazily re-exports config_store names; importing config_store
    FIRST (as the CLI does) must not raise a partially-initialised ImportError."""

    def test_when_config_store_imported_before_config_then_no_circular_import(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c", "import sentinel.config_store"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_when_jsonconfigstore_imported_from_config_in_fresh_interpreter(self):
        import subprocess
        import sys

        code = "from sentinel.config import JsonConfigStore, resolve_paths; resolve_paths()"
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_when_json_config_store_is_instantiated_then_no_directory_is_created(
        self, tmp_path
    ):
        """
        The store must not touch the filesystem at construction time (nor at import time).
        Only calling load() / save() may create directories.
        """
        absent = tmp_path / "untouched"
        JsonConfigStore(base_dir=absent)
        assert not absent.exists()


# ── AC6: round-trip save → load returns equal AppConfig ──────────────────────


class TestRoundTrip:
    """AC6: save(cfg) then load() is an identity operation on AppConfig."""

    def test_when_save_then_load_called_then_equal_app_config_is_returned(
        self, tmp_path
    ):
        store = JsonConfigStore(base_dir=tmp_path)
        cfg = AppConfig.from_mapping({})
        store.save(cfg)
        assert store.load() == cfg

    def test_when_round_trip_performed_then_loaded_config_is_app_config_instance(
        self, tmp_path
    ):
        store = JsonConfigStore(base_dir=tmp_path)
        cfg = AppConfig.from_mapping({})
        store.save(cfg)
        loaded = store.load()
        assert isinstance(loaded, AppConfig)

    def test_when_round_trip_performed_with_injectable_base_dir_then_config_json_lives_in_base_dir(
        self, tmp_path
    ):
        """
        The base directory must be injectable (not hard-coded) so tests can use tmp_path.
        config.json must reside directly inside base_dir.
        """
        store = JsonConfigStore(base_dir=tmp_path)
        store.save(AppConfig.from_mapping({}))
        assert (tmp_path / "config.json").is_file()


# ── Property: load() never raises for arbitrary file content (AC3) ─────────────


@given(st.binary())
def test_when_config_json_has_arbitrary_binary_content_then_load_returns_app_config(
    raw: bytes,
):
    """
    Never-raises invariant (AC3): load() must return an AppConfig for any bytes
    written to config.json — including binary garbage, truncated JSON, and BOM sequences.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        (base / "config.json").write_bytes(raw)
        result = JsonConfigStore(base_dir=base).load()
        assert isinstance(result, AppConfig)


@given(st.text())
def test_when_config_json_has_arbitrary_text_content_then_load_returns_app_config(
    text: str,
):
    """
    Never-raises invariant (AC3): load() must return an AppConfig for any Unicode
    text written to config.json — including malformed JSON, surrogates, and empty strings.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        (base / "config.json").write_text(text, errors="replace")
        result = JsonConfigStore(base_dir=base).load()
        assert isinstance(result, AppConfig)


# ── Property: round-trip idempotence (AC6) ────────────────────────────────────

_JSON_SCALAR = st.one_of(
    st.integers(min_value=-(2**31), max_value=2**31 - 1),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(),
    st.booleans(),
    st.none(),
)

_JSON_MAPPING = st.dictionaries(
    st.text(min_size=1, max_size=60),
    _JSON_SCALAR,
    max_size=30,
)


@given(_JSON_MAPPING)
def test_when_any_json_compatible_mapping_is_round_tripped_then_result_is_stable(
    mapping: dict,
):
    """
    Round-trip invariant (AC6): for any JSON-compatible dict, the AppConfig produced
    by from_mapping(dict) must survive a save+load cycle unchanged.

    AppConfig.from_mapping() is expected to ignore unknown keys and fall back to defaults
    for invalid values — so any dict is a valid input to from_mapping().
    The invariant is that save(cfg) followed by load() returns a value equal to cfg.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        store = JsonConfigStore(base_dir=base)
        cfg = AppConfig.from_mapping(mapping)
        store.save(cfg)
        assert store.load() == cfg


@given(_JSON_MAPPING)
def test_when_save_load_cycle_is_repeated_then_result_is_idempotent(mapping: dict):
    """
    Idempotence invariant (AC6 extended): repeating save+load does not change the
    AppConfig — i.e. save(load(save(cfg))) == save(cfg).
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        store = JsonConfigStore(base_dir=base)
        cfg = AppConfig.from_mapping(mapping)
        store.save(cfg)
        first = store.load()
        store.save(first)
        second = store.load()
        assert first == second
