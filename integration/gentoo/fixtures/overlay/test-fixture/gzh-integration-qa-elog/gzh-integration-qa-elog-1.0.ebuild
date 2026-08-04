EAPI=8

DESCRIPTION="Known-bad gzh integration fixture"
HOMEPAGE="https://example.invalid/gzh-integration"
SRC_URI=""
S="${WORKDIR}"

LICENSE="CC0-1.0"
SLOT="0"
KEYWORDS="~amd64"

src_unpack() {
	mkdir -p "${S}" || die
}

src_test() {
	sh "${FILESDIR}/gzh-integration-qa-elog" --self-test || die
}

src_install() {
	newbin "${FILESDIR}/gzh-integration-qa-elog" gzh-integration-qa-elog
	eqawarn "intentional gzh integration QA boundary"
}
