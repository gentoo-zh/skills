from pathlib import Path

import pytest

from gzh.needed_deps import check, main, rdepend_cps


def _install(vdb: Path, cpv: str, *, provides: str = "", needed: list[str] = (),
             rdepend: str = "", own: str = "") -> None:
    cat, pf = cpv.split("/", 1)
    d = vdb / cat / pf
    d.mkdir(parents=True)
    if provides:
        (d / "PROVIDES").write_text(f"x86_64: {provides}\n")
    if needed:
        (d / "NEEDED.ELF.2").write_text(
            f"X86_64;/usr/bin/{pf};{own};;{','.join(needed)};;\n")
    (d / "RDEPEND").write_text(rdepend + "\n")


@pytest.fixture
def vdb(tmp_path):
    root = tmp_path / "vdb"
    _install(root, "sys-libs/glibc-2.43", provides="libc.so.6 libm.so.6")
    _install(root, "dev-libs/glib-2.88.2", provides="libglib-2.0.so.0")
    _install(root, "gui-libs/gtk-4.22.5", provides="libgtk-4.so.1",
             rdepend="dev-libs/glib:2 x11-libs/pango")
    _install(root, "x11-libs/pango-1.56.4", provides="libpango-1.0.so.0",
             rdepend="dev-libs/glib:2")
    _install(root, "sys-libs/libseccomp-2.6.0", provides="libseccomp.so.2")
    _install(root, "sys-apps/file-5.46", rdepend="sys-libs/libseccomp")
    _install(root, "app-admin/eselect-1.4.27", rdepend="sys-apps/file")
    _install(root, "media-video/mpv-0.41.0", provides="libmpv.so.2",
             rdepend="app-admin/eselect")
    return root


def test_rdepend_cps_ignores_use_conditionals_and_operators():
    cps = rdepend_cps('>=gui-libs/gtk-4.22:4[X?] || ( a/b c/d ) foo? ( e/f )')
    assert cps == {"gui-libs/gtk", "a/b", "c/d", "e/f"}


def test_direct_provider_passes_and_libc_is_implicit(vdb):
    _install(vdb, "app-misc/demo-1.0",
             needed=["libgtk-4.so.1", "libc.so.6"], rdepend=">=gui-libs/gtk-4.22")
    report = check("app-misc/demo", vdb=vdb)
    assert report["ok"] and report["findings"] == []


def test_one_hop_through_a_direct_dependency_is_transitive(vdb):
    _install(vdb, "app-misc/demo-1.0",
             needed=["libgtk-4.so.1", "libglib-2.0.so.0"], rdepend="gui-libs/gtk")
    report = check("app-misc/demo", vdb=vdb)
    [found] = report["findings"]
    assert found["state"] == "transitive"
    assert found["via"] == ["gui-libs/gtk", "dev-libs/glib"]
    assert report["ok"]


def test_a_long_chain_counts_as_missing(vdb):
    _install(vdb, "app-misc/demo-1.0",
             needed=["libmpv.so.2", "libseccomp.so.2"], rdepend="media-video/mpv")
    report = check("app-misc/demo", vdb=vdb)
    [found] = report["findings"]
    assert found["state"] == "missing" and found["provider"] == "sys-libs/libseccomp"
    assert found["via"][0] == "media-video/mpv" and len(found["via"]) > 2
    assert not report["ok"]


def test_a_soname_nobody_provides_is_unresolved(vdb):
    _install(vdb, "app-misc/demo-1.0", needed=["libnowhere.so.9"], rdepend="")
    report = check("app-misc/demo", vdb=vdb)
    assert report["findings"] == [
        {"soname": "libnowhere.so.9", "provider": None, "state": "unresolved"}]
    assert not report["ok"]


def test_the_ebuild_rdepend_overrides_the_vdb_copy(vdb, tmp_path):
    _install(vdb, "app-misc/demo-1.0", needed=["libseccomp.so.2"],
             rdepend="sys-libs/libseccomp")
    ebuild = tmp_path / "demo-1.0.ebuild"
    ebuild.write_text('RDEPEND="\n\tgui-libs/gtk\n"\nDEPEND="${RDEPEND}"\n')
    assert main(["app-misc/demo", "--ebuild", str(ebuild), "--vdb", str(vdb)]) == 1
    ebuild.write_text('RDEPEND="${DEPEND}"\nDEPEND="sys-libs/libseccomp"\n')
    assert main(["app-misc/demo", "--ebuild", str(ebuild), "--vdb", str(vdb)]) == 0


def test_own_sonames_are_not_needs(vdb):
    _install(vdb, "app-misc/demo-1.0", needed=["libdemo.so.1"], own="libdemo.so.1")
    assert check("app-misc/demo", vdb=vdb)["needed"] == []
