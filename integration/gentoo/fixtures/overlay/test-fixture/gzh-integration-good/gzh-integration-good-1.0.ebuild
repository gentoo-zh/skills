EAPI=8

DESCRIPTION="Known-good gzh integration fixture"
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
	sh "${FILESDIR}/gzh-integration-good" --self-test || die
}

src_install() {
	newbin "${FILESDIR}/gzh-integration-good" gzh-integration-good
}
