# devmanual — 权威参考索引

所有 ebuild 写法以 Gentoo 官方 devmanual 为唯一标准。skill 引用本文件，不在 skill 文档里抄规范。

- 根: <https://devmanual.gentoo.org/ebuild-writing/index.html>
- file-format: <https://devmanual.gentoo.org/ebuild-writing/file-format/index.html>
- EAPI: <https://devmanual.gentoo.org/ebuild-writing/eapi/index.html>
- variables: <https://devmanual.gentoo.org/ebuild-writing/variables/index.html>
- functions (phase 顺序 `pkg_pretend→pkg_setup→src_unpack→src_prepare→src_configure→src_compile→src_test→src_install→pkg_preinst→pkg_postinst`, `default_*` 约定): <https://devmanual.gentoo.org/ebuild-writing/functions/index.html>
- use-conditional-code: <https://devmanual.gentoo.org/ebuild-writing/use-conditional-code/index.html>
- using-eclasses: <https://devmanual.gentoo.org/ebuild-writing/using-eclasses/index.html>
- error-handling: <https://devmanual.gentoo.org/ebuild-writing/error-handling/index.html>
- common-mistakes: <https://devmanual.gentoo.org/ebuild-writing/common-mistakes/index.html>
- misc-files (metadata.xml / patches / files): <https://devmanual.gentoo.org/ebuild-writing/misc-files/index.html>

gentoo-zh 附加铁律：KEYWORDS 仅 `~arch`，无 stable。
