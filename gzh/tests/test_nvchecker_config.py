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


from gzh.nvchecker_config import sort_overlay_toml


def test_sort_overlay_toml_alphabetical_with_comments():
    src = ('[__config__]\nnewver = "n.json"\n\n'
           '# b comment\n["cat/b"]\nsource = "pypi"\n\n'
           '# a comment\n["cat/a"]\nsource = "github"\n')
    out = sort_overlay_toml(src)
    assert out.index('["cat/a"]') < out.index('["cat/b"]')
    # a's comment travels with a block (after header, before/at a)
    a_idx = out.index('["cat/a"]')
    assert "# a comment" in out[:a_idx]
    # b's comment travels with b block (after a)
    assert "# b comment" in out[out.index('["cat/a"]'):out.index('["cat/b"]') + 10] or \
           "# b comment" in out[:a_idx]
    assert out.startswith("[__config__]")


def test_sort_overlay_toml_preserves_commented_table():
    src = ('[__config__]\nnewver = "n"\n\n'
           '# disabled\n#["cat/old"]\n\n'
           '["cat/active"]\nsource = "github"\n')
    out = sort_overlay_toml(src)
    assert '#["cat/old"]' in out
    assert '["cat/active"]' in out


def test_set_entry_sorts_after_add(tmp_path):
    t = tmp_path / "overlay.toml"
    t.write_text('[__config__]\nnewver = "n"\n\n["cat/z"]\nsource = "x"\n', encoding="utf-8")
    set_entry(t, "cat/a", {"source": "y"})
    content = t.read_text()
    assert content.index('["cat/a"]') < content.index('["cat/z"]')


def test_sort_overlay_toml_normalizes_blank_lines():
    # source has: multiple blanks before cat/b, zero blank between b and a
    src = ('[__config__]\nnewver = "n"\n\n\n\n'
           '["cat/b"]\nsource = "pypi"\n'
           '["cat/a"]\nsource = "github"\n')
    out = sort_overlay_toml(src)
    lines = out.split("\n")
    # no consecutive blank lines
    for i in range(len(lines) - 1):
        assert not (lines[i] == "" and lines[i + 1] == ""), f"consecutive blanks at {i}"
    # exactly one blank before each block
    a_idx = lines.index('["cat/a"]')
    b_idx = lines.index('["cat/b"]')
    assert lines[a_idx - 1] == ""
    assert lines[b_idx - 1] == ""


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
