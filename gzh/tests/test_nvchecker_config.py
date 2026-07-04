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


def test_set_entry_preserves_comments(tmp_path):
    t = tmp_path / "overlay.toml"
    t.write_text(
        '# top comment\n[__config__]\nnewver = "n.json"\n'
        '# pkg comment\n["cat/foo"]\nsource = "github"\n', encoding="utf-8")
    set_entry(t, "cat/bar", {"source": "pypi", "pypi": "bar"})
    content = t.read_text()
    assert "# top comment" in content
    assert "# pkg comment" in content
    assert get_entry(t, "cat/bar") == {"source": "pypi", "pypi": "bar"}


from click.testing import CliRunner

from gzh.cli import cli


def test_nvchecker_config_get_via_cli(tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    t = tmp_path / ".github" / "workflows" / "overlay.toml"
    t.parent.mkdir(parents=True)
    t.write_text('["cat/foo"]\nsource = "github"\ngithub = "o/foo"\n')
    monkeypatch.setattr("gzh.cli.find_overlay_root", lambda: tmp_path)
    result = CliRunner().invoke(cli_mod.cli, ["nvchecker-config", "get", "cat/foo"])
    assert result.exit_code == 0
    assert "github" in result.output


def test_nvchecker_config_set_via_cli(tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    t = tmp_path / ".github" / "workflows" / "overlay.toml"
    t.parent.mkdir(parents=True)
    t.write_text('[__config__]\nnewver = "n.json"\n')
    monkeypatch.setattr("gzh.cli.find_overlay_root", lambda: tmp_path)
    result = CliRunner().invoke(cli_mod.cli,
                                ["nvchecker-config", "set", "cat/bar",
                                 "--json", '{"source":"pypi","pypi":"bar"}'])
    assert result.exit_code == 0
    assert get_entry(t, "cat/bar") == {"source": "pypi", "pypi": "bar"}
