from pathlib import Path

from gzh.nvchecker_config import get_entry, set_entry


def test_get_entry(tmp_path):
    t = tmp_path / "overlay.toml"
    t.write_text('["dev-python/foo"]\nsource = "github"\ngithub = "x/foo"\n')
    assert get_entry(t, "dev-python/foo") == {"source": "github", "github": "x/foo"}
    assert get_entry(t, "dev-python/missing") is None


def test_set_entry_roundtrip(tmp_path):
    t = tmp_path / "overlay.toml"
    t.write_text('[__config__]\nnewver = "n.json"\n')
    set_entry(t, "dev-python/bar", {"source": "pypi", "pypi": "bar"})
    assert get_entry(t, "dev-python/bar") == {"source": "pypi", "pypi": "bar"}
    # config section preserved
    import tomllib
    assert tomllib.loads(t.read_text())["__config__"]["newver"] == "n.json"
