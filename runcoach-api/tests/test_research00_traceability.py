"""F008: research/00 as current rules with stable IDs -- the checkers, the assembler and the review
scope, proven on synthetic text and on one real block per group.

T170 (sprint-007) is the walking skeleton. It writes no research/00 text. Every checker is a pure
function over text, so the four drafting tasks (T171-T174) run it over their ``.txt`` fragments
(``fragment_errors``), the assembly task runs it over all of them together (``assemble_check``), the
cut-over (T175) runs it over the real files, and the critics (T177-T179) take their scope from
``required_review_rows``. The names in the T170 probe's ``need`` list are that interface.

Each checker is proven twice: red on a minimal synthetic violation and green on a minimal valid case,
so a checker that cannot fail is visible here; and green on one hand-written real block per group,
written from its inventory row, so a checker that is too strict for real text is visible here rather
than in four stalled parallel builders (T170 AC4). The real-path cases (``test_real_path_*``) -- the
committed research/00, history and traceability table, and the committed ``OLD_MEANINGS`` -- were
added by the cut-over (T175) in the same commit as the files, so the suite never carried an xfail or
a skip (R11). The drafts under ``specification/research/drafts/`` were deleted by that commit;
``assemble`` and ``fragment_errors`` stay, proven on the synthetic world.

Authority: ``spec/references/F008-rewrite-decisions.md`` (R1-R11) and the inventory
``spec/references/research00-rewrite-inventory.md`` at commit ``4e47d0e``. Neither is read by the
committed tests: CI has no data dir, so every fact taken from them is frozen here as a literal.
"""

import ast
import hashlib
import importlib.util
import json
import re
import subprocess
import textwrap
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUPPORT = Path(__file__).parent / "support" / "research00_old_meanings.py"
_ENDPOINT_SUITE = Path(__file__).with_name("test_hrv_trend_endpoint.py")


def _load_module(name: str, path: Path) -> ModuleType:
    """Import a module from its path: the workspace runs pytest with ``--import-mode=importlib``,
    under which nothing in ``tests/`` is importable by name (as at test_hrv_trend_endpoint.py)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_OM = _load_module("research00_old_meanings", _SUPPORT)
OldMeaning = _OM.OldMeaning
normalize = _OM.normalize


# ---------------------------------------------------------------------------
# Constants, frozen from the inventory and from research/00 at 4e47d0e
# ---------------------------------------------------------------------------

#: The fifteen rule prefixes, in R11's assembly order.
PREFIX_ORDER = (
    "DOC", "PRIN", "ARB", "AUT", "GOAL", "ARCH", "REG", "IND", "COLD", "HRV", "GATE", "FIG", "LT1", "FTO", "DEC",
)

#: The three drafting groups (T172, T173, T174), each owning its prefixes.
GROUPS = {
    "doc-goal": ("DOC", "PRIN", "ARB", "AUT", "GOAL"),
    "arch-dec": ("ARCH", "REG", "IND", "COLD", "GATE", "FIG", "LT1", "FTO", "DEC"),
    "hrv": ("HRV",),
}


def _span(prefix: str, first: int, last: int) -> tuple[str, ...]:
    return tuple(f"{prefix}-{n:02d}" for n in range(first, last + 1))


#: The 149 inventory IDs (inventory Section 1), frozen: ``grep -oE '^\| (<prefixes>)-[0-9]{2,3} \|'``
#: over the inventory yields exactly these, unique. ARCH runs 00-08.
INVENTORY_IDS = frozenset(
    _span("DOC", 1, 15) + _span("PRIN", 1, 16) + _span("ARB", 1, 6) + _span("AUT", 1, 6)
    + _span("GOAL", 1, 6) + _span("ARCH", 0, 8) + _span("REG", 1, 19) + _span("IND", 1, 2)
    + _span("COLD", 1, 7) + _span("HRV", 1, 46) + _span("GATE", 1, 3) + _span("FIG", 1, 5)
    + _span("LT1", 1, 2) + _span("FTO", 1, 6) + _span("DEC", 1, 1)
)

#: The inventory sentence of each of the 149 IDs, frozen as ``_sentence_digest(sentence)``: the sha256
#: of the raw cell with whitespace collapsed and nothing else changed (sprint-007 review iteration 1,
#: S3; iteration 2, S1). The AC9 proxy reads a row's "inventory sentence" cell, so a cell set to ``—``
#: or edited alongside its rule took the row out of the proxy and the suite stayed green. Iteration 1
#: hashed ``normalize(sentence)``, which drops backticks, underscores, quotes and case: a cell whose
#: backticks were removed hashed the same and lost its identifier from the proxy (DOC-13's
#: ``spec_outline.md``). Regenerated 2026-09-26 from the inventory's "Current rule" cell
#: (``inventory_sentences()`` over ``spec/references/research00-rewrite-inventory.md``, the inventory at
#: ``4e47d0e``), not from the table; the committed table's 149 cells matched all 149 digests.
#: Asserted by ``inventory_sentence_errors``.
INVENTORY_SENTENCE_SHA256 = {
    "DOC-01": "db839f374fe76152c770a0fb6787e039aa52fe8251e6af8e7fbf64b49acd44f1",
    "DOC-02": "cf0680478c4892e678cc6e22f6640268ce9e1a247d5387e11920437e57b0f181",
    "DOC-03": "09392b30e7af18907f45b17e3c510683a84a25d4e03dd399f74585ba0a8e3b85",
    "DOC-04": "5300aacf0fc6fd1cebe79d9d5de4d85c241c2b0d3eab4a7ce339f688fae01814",
    "DOC-05": "07768412f26c2fa67a0aa540eb1d05db87ae38b0f4b0e5d9c2885fe113331042",
    "DOC-06": "eb52e3501bae3d81590d863c2a1f0380426f587fca1c23bf31cac5003b33d9bc",
    "DOC-07": "47985bb08254f418c7c8d48f610230319b5566d02bfce990656522bbd127b086",
    "DOC-08": "40ad869b6aacf93fdd653feaa7bd5242ee5f18b2c2e5b230dc4d0ce10bf7a8bb",
    "DOC-09": "b6085b77097c44a8972ee2093f721de645727cde99490780d80cb77b0086a444",
    "DOC-10": "69f29cf25b55260701768a69bd2f7c50831638a25ddc2520d7a5b57ee09e9860",
    "DOC-11": "04bb003ee4b30c1d95d711163b34355424b1c725bf028414668430b7d681aa58",
    "DOC-12": "85f371f6dd4794b20c1bb2feb97a6ce139aa56eb8d043b184c62bba37c0f679b",
    "DOC-13": "e6fb9c944c22978ad738c389e7472608243569f2f0e195efc2e99c2cae7823aa",
    "DOC-14": "ae91e23c9b4af6ff8f9d8ac2dd7084daf5d29072588a4128553d06a0d3c09632",
    "DOC-15": "0be0e79c8015a59a7da97d85fc07b2abeba24c32b49846f59b876c9e6646abe8",
    "PRIN-01": "9c24c99aead50eaae31d04eeefb5b7daf8e35e84dba1e293ea8ea9a9ef82e3bb",
    "PRIN-02": "bc4e27f99a8fb668c7390d8470dafba73591e582ecd6768c853e2d1c378b8226",
    "PRIN-03": "e515ed8fd283a83ddaa256cd050919be77c3f2ef42c3bf5693523e22452ed97c",
    "PRIN-04": "663644c4b4d266e659b7226865743f60b5b3f0d188453ff751c05d2a99e884bd",
    "PRIN-05": "910f76e3cda24719f9d914673af6022062eee5fc083629cc1bb379b0d5da368c",
    "PRIN-06": "51e10f46e03e5ac02e41b692a8d56bf38a53e8f822d6797b27f78bebd7bbf015",
    "PRIN-07": "e2638a9261f8d330acf27fa8807e6d0eb1ffb167c816786415e1e385f023a280",
    "PRIN-08": "86f27db532056caae7bde036c02ae040555c9849258dd89d22842c76d2f2c905",
    "PRIN-09": "36557c69dde9076b516db4ec17c7a1ed08145c5b88434516e30800c9fbe3d015",
    "PRIN-10": "10b0a06f735945cf38a30f4aa25e53515e714e7bc6d2b46ef987fcb08fe81c4f",
    "PRIN-11": "6d57e5f11cd9cb42b72d7bc3d87059a55847b0eb591adea58cd003eeb7bbebf0",
    "PRIN-12": "6cb6ac81454fdf3dee3ef213686169a9f4485768949650f504758ceedd31dcdb",
    "PRIN-13": "d7c14355883fcd77c402013d44b5546ad86e5a4ba7f7644184c55d4a61e24b30",
    "PRIN-14": "8d6698e35470081cb61dbfceebdb932b624e35fc5d0b54592a1f5635766e4d72",
    "PRIN-15": "ac46c591281b1e67fb896caa2e630698bbd77f5b18a3a04156544d14d92f976e",
    "PRIN-16": "141ac72b92bae73b7e92b1558aeac5d6cd931f20d5385e92050e8a0691d40fb3",
    "ARB-01": "cc3f97b168189fa8a68d31a59820effdbf9897c7a6d554e17241ecaec1e6b422",
    "ARB-02": "640b1a4315ff6e0fc3cbaeef87b595fe92ec39a2b02315b225e03cd25a12a834",
    "ARB-03": "20f0b3c35d786766f09b6228efecff2999cfcaabdd972a1834dff69d5046133c",
    "ARB-04": "f889d330da56619b42ee047384ed4dd3c56ed7858159c08d6c543a25a297d0dc",
    "ARB-05": "ee60948c94d77fd56b7a34395d122bbc47903079e34b8278fef63a27fa3a14cb",
    "ARB-06": "cb71b4fab6753083c4a5e5314839e85d3190055d27fd62b89d4d0fce527e3031",
    "AUT-01": "c4e4c0c673c26bd05a10d92b8b504d8d6d3d1e64a9e7633df662d6e7dc3da442",
    "AUT-02": "db401802608a2a1523d1e64e282d10b9dda24830e77dbaf8f4a3543996fc8726",
    "AUT-03": "45f593b40bbd3eb0084c8b6242fbf0aa4a7928c6de7744bd436a21befad7c011",
    "AUT-04": "4140bc60705e2010ea598947d0f1990d4f7bb86a69dd0bf0b7515c1d02830a9a",
    "AUT-05": "e8c9cbdc5f1ced9bed9a2abf3c63736f4b217b28fd068a615fa1952ba02110fa",
    "AUT-06": "20cca433af29b28296dd542add669dc21556479aa6ea08251f1f793d2d6e94db",
    "GOAL-01": "32a82d981e2d96a346a2e832db03ff018c7186ea90269a386f1231d70ac074d3",
    "GOAL-02": "64b6df95acdf71889d48ac7d0f8e16859aeaff92c296cf238f41844427627d03",
    "GOAL-03": "b4ad8a2781c58da69dbab3a4a90992974a273dfcff1f1f696a202cff96dfffab",
    "GOAL-04": "fbfbdd8cde73c6a5d29e7727d75c300100d7496bda9c20455bddfd0d51dd3b5c",
    "GOAL-05": "e88553fb7cca8fcc06b9564ec1404682feeee226bb44efb41cc4ccaea55030e0",
    "GOAL-06": "2b8b3b5512d6f3f4a5ab57bb10616e91958a35d6c237970c1dff1041aeb6d92c",
    "ARCH-00": "e8e67f6e2230f9961eb09ab14b1ae05faeb71e0ef50850234a1e2050fc92135d",
    "ARCH-01": "b8bb673a86b09def5178315e93cbc822c13a600dc98ee2dd3d0c2d8ea5f1db0b",
    "ARCH-02": "04d06b59bb62e8655d98a02f522debe401d084f4539214e2c8763fec81a7cad3",
    "ARCH-03": "485ad4f767e34565bc825434741390c6eb1adfe400cbf0580f11fddfbe5a8016",
    "ARCH-04": "a44d250c76284bea01c491861735d51b7010d0bda82ad32a2aba2a39aa2c8287",
    "ARCH-05": "0eb9375dfd337b0cc4b722881704ceaedf5add9f4655497fed7859afa8b6b151",
    "ARCH-06": "3de99579f98a836e9297fc38bb5a6863bbd3f384ffc823f47f5021a9357d0098",
    "ARCH-07": "a5a07cc2c0af6ba6fb11bcf7d6ce7ab81eafe77d7899f7b37652f7709a428abb",
    "ARCH-08": "2b98a4971bbb5df99c25f6cf4737c989aa472cfc5b027d380bb0c655efa75bdd",
    "REG-01": "ee15dadd5fc10de3556f9394e441f06c4510db32a6c8e298592cbb1c6e89c8de",
    "REG-02": "ee616bc651861c70c444fe6a4c7f1895f172dd64ba6e7333dbef2a6805d7d185",
    "REG-03": "e6cd4bb9de95a6e3b4385e5d06291a8ef173231d4cee52f08d3d4b760355b876",
    "REG-04": "8d240f5f740c9508f0000d5850e6fcb1c67c187b69f8c8198be74ab1a84277e6",
    "REG-05": "82aaa29d1402f52d30ba49e29e79f2ab9df4438f4829aa969c5c72c1cfa121e8",
    "REG-06": "a2360c8cd0f86760f1386569f5aac2e6a84f590de9467a847ce0bf3ae443b933",
    "REG-07": "07205c6f25422cb01e4dca49b9b8625ee6cae26930896959d72b64d2706c1c57",
    "REG-08": "f603aeb013aa8cc87b619df5ba793ac142abb2e7efe88395262505779adf9f1b",
    "REG-09": "4e1e315a715b69f413412504d7dc156d65ddc759f224d9d648e2737f71a4d541",
    "REG-10": "9e957995bd437d615b9d97c51c690533aae0d1fc989e37774603601a8132e0df",
    "REG-11": "82b80437d0970039d535f4658520dd6ddd9759cfc53759c8dd34ecb8f2eb335e",
    "REG-12": "efb64032acf621e22419bb24faf851bd6707fdc8331dda5b032812740320eb97",
    "REG-13": "a0be2143099e0aa9820ad187a86f99e637727c98e215b60050afba438f1987eb",
    "REG-14": "7e31628507076c8335921f5adeeb838d3ec0fa38f5b53164d3f6af2e505e6e12",
    "REG-15": "d3240a8cee0b388f67b8a088774dea26194fda2f090c7d2c256dfb635892078d",
    "REG-16": "117368020848fa6c50e909adb13999a4a020ae8c03d995c0e99532cf25e052fa",
    "REG-17": "cf0a67d461f53a669c7d8bb6f8416d85d529e2888d3df269e657eb4f88d48972",
    "REG-18": "9ce47b2da463a4df402932f2f046fc58725060fea6264505bb5b454c7651a1fa",
    "REG-19": "3de0a38757b798e6ef8f216a61d330f18da9234b8d73755cb4832dcb50aea9b8",
    "IND-01": "9cd5b1ce2b12446877918fcdf8754776ecbb4f3586c686b33f542b0e4dbd6178",
    "IND-02": "4ae2f8dec412184b5e2f5c43a7b4134f1529810a8887bfa36ec3c6c6d9bd25b7",
    "COLD-01": "9effcb79c1f9ca9500da64f2d19cc270bfc8d78f4515edd969411b81954c5108",
    "COLD-02": "8d1f36e9f6cd1a8a0d513856b8ee703b753f6db12d497973f64b85d6c1c430f7",
    "COLD-03": "96c95a803f87b51ed39e539556213932084948cb3619dfdf82f804a65d51e859",
    "COLD-04": "8c60ffc82d32f970e5ff2543c8b088f1c8fb6ab93dc4c20b93c043196b5bad40",
    "COLD-05": "cde718d3f56a78f2cf926997a37a49a30a87ed737003c2ce339ad7dead7ca0dc",
    "COLD-06": "40ce6d9eafe022e83772462ab115ff096b75a01f7dc781c3c035191c2d933a1d",
    "COLD-07": "64789b01c94df39aab91062a100e57cf800d58ac2589383dc33dbe6666f55514",
    "HRV-01": "13a614660b06aaaf4e2dfec7bd8b7170b1b4cb9e1fc534b3c39dcb3f658bdcd6",
    "HRV-02": "528a4ad85b205777f21ccd45b18c833c52af582f72e6e125a4dca2de2085ad64",
    "HRV-03": "15d36d43964e6196cc56af1565cbe7e06721d644f87d43086abf5367b078314e",
    "HRV-04": "b4c4b4ab01f57ca9735b0282deb14b5cb9b142628a0e3211951851fd4a1d538b",
    "HRV-05": "770b84444be6af9e77b9f54e92f4646b949966441a6fd42afa025f9d7a4e8815",
    "HRV-06": "d550490e2ad8492514a098e83566c6b6201eb9ba34659e1819125800a7640a8d",
    "HRV-07": "bbca067bdccdc5b11e6b6eb2fde469aa0d3396e08a4054f28dc02ad5e86be8ed",
    "HRV-08": "5bb27bbd25e09384b5539051d66d520a0836cfd73bfef6c6df59a6e619b7003f",
    "HRV-09": "a34d7bfd9ad4cc8915fabc69e4964627f193e988d977b8f4661c5bd6cb24dc1a",
    "HRV-10": "c631e969ab03a2fa6566d5f4a01f9563a1083e957352f988b0de8653bcd10aed",
    "HRV-11": "7ed69b047f3fdcc56383188f85706222c729a74ee2c8acd81f8fb8a8d2008781",
    "HRV-12": "31c3fc916325910ebd6faf3804e87b87289c4a11d6d5f3ecca417065ce176c86",
    "HRV-13": "9eeaa388dabe82292c7622f96bcc8652b965ae16405fcb4e420a3306bd4910e4",
    "HRV-14": "2f272c5d6deec9ec849fc64dba327eaebcaaa9db493b7dcc5f608b2142088adf",
    "HRV-15": "a5f8bab2b1d026d543860ad611320426ddf2eb58a8c4cf4e18cf77caebae9478",
    "HRV-16": "599b8952be2d24d0a040de0a9680c0f09f65fb2860283af1be4d05ebb9984420",
    "HRV-17": "568765382f56121d14654cdb0522bb97a25f589d32585500bbf8a9be964fe84e",
    "HRV-18": "9164c163de0e688152b5502b3e95696bb0b626f217ca0b975a88628e5657f5ae",
    "HRV-19": "280aa306bc894a1770a52935676daf47d2e65118473425a8a1a3287b4c1701f3",
    "HRV-20": "b6df2edfb657e7604bed5442707cb3009b7857c16dd1d17a5b27f7e36bc56367",
    "HRV-21": "901241c6a2ee4c1a4758eeaec26e9210cd369f88c418c4fef27e43029e7dd2f3",
    "HRV-22": "03a23537a126dc558dc71262618adb8c24f816a67c9c0202f5691ee487ea5dde",
    "HRV-23": "4ebfdbd0d25a287a5995744cd98897b077b834817daba7a1ba09c91ebb2a8597",
    "HRV-24": "7b7f1073e65e7ed91b9867f128a6b1017c21dd725fe230627e73e93310d1a788",
    "HRV-25": "172890d79358a793928f127e35f1237aeda83460ee06d4803813806ccd3709fa",
    "HRV-26": "0e689db70c8c578d731a7bf1e959d2f8b43e17ff3c7cfd64c36ae62ab15ac442",
    "HRV-27": "088b43f2148def35d4b74c14e6c7c132e96f1d5e9994542faa7b1ba3cf941aef",
    "HRV-28": "2690e3e38516e214a12781be5398a171469b169a1e5b3f78cc098cb04d10692e",
    "HRV-29": "7450e8b6ca90d611969a3ba20b8a2c559603d214f6bd33cef80f1a7672d6f06f",
    "HRV-30": "e90ce41db4ca6e77e19075e503ca6ff4925d1ea933e9d9baeb9efa79a99a9e64",
    "HRV-31": "732e7894a8f31efe1cb31f7c928f80ee22428b4fbe3263b5754c5154bfb50cb0",
    "HRV-32": "6396eb3ddd4807e3b5871c58865233e7ce11b02cc068932df9080d311cce76e9",
    "HRV-33": "6f71a595c3c7f345eeefa40cfd0108f7b9c5b0ffefcf1b8b09bb2eaf94777d3f",
    "HRV-34": "0adf15613391bc91a0a6224ab64db34b2977a81abc10a71b59d67b571accd5cd",
    "HRV-35": "550e3512df10d216d0944b3e3d67c7ae9b623db8373abe0eddfcc3b37e3a186f",
    "HRV-36": "83aa4a7625d583a23aaa885ee36283908e3c39d2be196d96ed6facf7534eaef3",
    "HRV-37": "f7cff2c54ef9b8852cfe6f5cbed00533b541ae927a99c0784d6e0ec24f300ebd",
    "HRV-38": "308982854b970a4c225da2ebd54805c715307a15430acf2beb82dc2e32330b1b",
    "HRV-39": "c9dbbd781835c043c70710f3bd8e9ff18e7c1a475026518e795b7be0af2ab0d3",
    "HRV-40": "9f6e3248ce641918edf57ed979318166a91631a1f49330ac3edb28b2a2c3eff0",
    "HRV-41": "a30eb10ac751673b852dde8354db58682bd440646f3615cb6799049befa0605d",
    "HRV-42": "4ea3f04e28efc24858a26f5b5cc96fc3d81fd619d636af0f11a436a32ba42b77",
    "HRV-43": "6c975d1663717bb78d937e8e5e8c60746609c900f82a71e75a0b535941cacd23",
    "HRV-44": "4dab9c88cf6a989b998e91eea1e5f3317f3f695cb007b33f692d6755e72e8b65",
    "HRV-45": "9d2c5dda43b986b78ff735c4f72826e834edc4761cc0fdfedf06aac4a761fa26",
    "HRV-46": "d8b1cb2b89be8ef79160365275787a97331dfe76899340366c083125b209ab98",
    "GATE-01": "45251b819781e90021e8e26906fbdf0ed66fd022aed3c5981c37ba70dd887be7",
    "GATE-02": "f8f51b197a29f6ecb0722af8b7926c3fbcf6ca9af0ade285feec99cbcd42f1c5",
    "GATE-03": "b7acb5f5c82977ca8d2b6186ed9e748b87678a2b3a1416c400bad49e368cb1bf",
    "FIG-01": "524fccb0f809eedf57a44bd0f39256d06664aedb8f1ce879999dadddb9d32b32",
    "FIG-02": "49e2eb0c879cba465b27641c66ed8fa3522ef0ac8b4e777ce2730077339e8eaa",
    "FIG-03": "b749ad48ee2bfdfa75acbf2e0cd4f8d9519bca77cee156263a0aceb8f7339130",
    "FIG-04": "10d5783676a19105b315bd02f746c78b5d13d31b2577e0f0184da24e385d7014",
    "FIG-05": "a1e992520364e283a8044ce6f0ddc56a9134e9535a78338d26efb5c522388937",
    "LT1-01": "0641fd79efe97426c5b87ee87bf16a3d340403a4a52a9b3d5ef096ca538fc746",
    "LT1-02": "691d7b3d4834ee0fc14126c23374fdf02d28b8e55a3541a92f14abcdc9e8b7de",
    "FTO-01": "c0ec8cb8fe64fad8906c8f3ef0254dc5474acf0ec636203d11f4607c4799e971",
    "FTO-02": "fbdce0b431badc9997a8f852cb8ae36793416e44812100e22e3bfc002a4098da",
    "FTO-03": "911fc1dddb18d1fc4a787aaac6eb36b25dd5b4e03c241252a587fb7aa4693282",
    "FTO-04": "43224e6cfe9f5c4c849824ea32168dd6492ecb94d8e2b4401d95740c745c6a97",
    "FTO-05": "a2589d6c6372288df673c352ce654de23c35a0e1c5c41c90c9fee3bc4d74866a",
    "FTO-06": "a15c73237ab44eaecf922b4e035e6dbe3870267edd6b74a45a51525c4f65e818",
    "DEC-01": "cd26e8547d8df067745506d794d93fca22ba6f7837ae4c65ab473baaba5b1f2b",
}

#: The Part and section headings of research/00 at 4e47d0e, in order: every line starting ``#``,
#: minus any carrying an ISO date (none does). A draft may use only these (R11).
HEADINGS = (
    "# Design Decisions & Governing Principles",
    "## Part 1 — Principle hierarchy and tie-breakers",
    "### 1.1 The supreme objective",
    "### 1.2 The arbitration ladder",
    "### 1.3 The meta-rule",
    "### 1.4 Conflict resolution between subjective and objective signals",
    "### 1.5 Raw over derived",
    "### 1.6 Transparency and explainability",
    "### 1.7 Down-regulate freely, up-regulate cautiously",
    "### 1.8 Autonomy posture",
    "### 1.9 System ownership: plan versus goal",
    "## Part 2 — Load-bearing findings",
    "## Part 3 — Decision register",
    "### 3.1 Individualization — the governing rule (resolved)",
    "### 3.2 Cold-start — establishing day-one state (resolved, amends `research/05` §3.2)",
    "### 3.3 Resting-HRV source tiering (resolved, amends the data-quality-gating and HRV-gate register rows)",
    "### 3.4 Aerobic-threshold (LT1) determination — recommended path (open, deferred to a future determinant)",
    "## Part 4 — Design and freedom-to-operate guardrails",
    "## Part 5 — Document map, authority, and decision records",
    "### 5.1 This document's authority",
    "### 5.2 The mechanism research docs (the evidence this document points to)",
    "### 5.3 Decision records (`decisions/`)",
    "### 5.4 Reconciliations and amendments",
)

#: The Glossary's heading. It is not a 4e47d0e heading: ``assemble`` places it directly before the
#: first Part (R11), and ``rule_grammar_errors`` leaves its lines to ``glossary_errors``.
GLOSSARY_HEADING = "## Glossary"

#: F008 AC4: the 33 Glossary terms, by ID, as the committed research/00 names them (sprint-007 review
#: iteration 1, S4). ``glossary_errors`` checks the IDs only, so renaming a term stayed green.
#: Asserted by ``glossary_term_errors``.
GLOSSARY_TERMS = {
    "T-01": "judgeable", "T-02": "selected", "T-03": "established", "T-04": "skipped / struck / stale",
    "T-05": "tier", "T-06": "dataset", "T-07": "band", "T-08": "baseline", "T-09": "baseline window",
    "T-10": "judged week", "T-11": "withhold", "T-12": "silence / coverage gap / hole / days behind",
    "T-13": "sustains", "T-14": "candidate", "T-15": "era boundary", "T-16": "reset", "T-17": "clip",
    "T-18": "reported", "T-19": "strays", "T-20": "return / carrier", "T-21": "fidelity", "T-22": "disagree",
    "T-23": "count", "T-24": "forbidden direction", "T-25": "rate", "T-26": "input tiers",
    "T-27": "rung / loop", "T-28": "single-ecosystem", "T-29": "goal contract",
    "T-30": "safety override / safety pathway", "T-31": "D, R, R+k, k₃", "T-32": "resolved tier / rule 1–4",
    "T-33": "unavailable",
}

#: F008 AC7 C07: T-24 is the forbidden direction, and its definition carries both disjuncts and the
#: judgeability clause, each checked after ``normalize()`` (S4).
FORBIDDEN_DIRECTION_CLAUSES = (
    "asserting `hrv_normal` on evidence the system reports as insufficient",
    "or while any dataset reported in the same response reads below its own HRV SWC band",
    "whether or not that dataset is judgeable",
)

#: Every decision R3 requires to appear in the decision column.
DECISIONS = tuple(f"C{n:02d}" for n in range(1, 34)) + ("C37", "C38")

#: R3: these change no meaning, so they appear only on ``no`` rows.
NO_ONLY = frozenset({"C11", "C16", "C17", "C18", "C19", "C21", "C25", "C29"})

#: The decisions other than a C-number that may authorize a ``yes`` row, each mapped to the inventory
#: IDs it may authorize one on (sprint-007 review iteration 1, M2; iteration 2, M1). A bare set let
#: any row cite ``R13`` and turn ``yes``: DOC-03's "1–4" became "1–3" under R13 and the suite stayed
#: green. Derived 2026-09-26 from the committed table at adcb66d and the rulings, then frozen:
#: - ``HRV-11``: the critique-round call (H-40) rules on HRV-11 alone.
#: - ``T-07``: R9's band renames (H-41), on the four ``yes`` rows citing it: REG-02, REG-16, REG-19,
#:   GATE-03 (REG-19 and GATE-03 also cite C30 and C37).
#: - ``R13``: the review rulings. Only M3 makes a row ``yes`` (the PRIN-12 row, "yes under C33 and
#:   R13"). S9 corrects PRIN-25's counts, whose PRIN-15 row is ``yes`` under C06; S10 and S11 are
#:   ``no``-row restorations (HRV-01, HRV-40).
#: A ``yes`` row authorized by no C-number outside NO_ONLY and by no token whose set holds its ID is
#: a meaning change no decision authorized. Do not widen a set to fit a row.
NON_C_AUTHORITIES = {
    "HRV-11": frozenset({"HRV-11"}),
    "R13": frozenset({"PRIN-12"}),
    "T-07": frozenset({"REG-02", "REG-16", "REG-19", "GATE-03"}),
}

#: R3: the no-only decisions whose old meaning is still stated downstream, so every row citing one
#: names an old-meaning key (F008 AC6; S5).
KEYED_NO_ONLY = ("C19", "C25")

#: Each old-meaning key mapped to the inventory row(s) that may name it (sprint-007 review iteration
#: 3, S1). Checking only that a key's leading decision token is cited let a row borrow any key whose
#: decision it also cites: DOC-03's "1–4" became "1–3" under ``C38 | yes |
#: DOC-09-C38-superseded-text-left-standing`` and the suite stayed green, the third route in one
#: family (iteration 1 M2, iteration 2 M1). Derived 2026-09-26 from the committed table at d8c8275
#: (54 keys, all 54 of ``OLD_MEANINGS``), then frozen. Every key has one owner row except
#: ``C19-hrv-04-reduced-confidence``, which REG-09 and HRV-04 both name.
#: ``test_the_key_owner_map_is_the_committed_tables_key_column`` holds the table to it both ways. Do
#: not widen a set to fit a row.
KEY_OWNERS: dict[str, frozenset[str]] = {
    "AUT-02-C23-override-outside-autonomy": frozenset({"AUT-02"}),
    "AUT-04-C20-two-purposes-only": frozenset({"AUT-04"}),
    "C01-withhold-not-judgeable-only": frozenset({"HRV-31"}),
    "C02-withhold-against-selected": frozenset({"HRV-31"}),
    "C03-return-is-free": frozenset({"HRV-34"}),
    "C04-hole-at-least": frozenset({"HRV-37"}),
    "C05-gate02-worse-rate-reopens": frozenset({"GATE-02"}),
    "C06-gate01-one-exception": frozenset({"GATE-01"}),
    "C06-hrv-25-accepted-cost": frozenset({"HRV-25"}),
    "C08-arch08-silence-tolerated-freely": frozenset({"ARCH-08"}),
    "C08-reg11-readiness-gate-down-weights": frozenset({"REG-11"}),
    "C09-fig05-idea071-sprint": frozenset({"FIG-05"}),
    "C09-residual-carried-to-idea-071": frozenset({"HRV-33"}),
    "C10-lone-candidate-never-struck": frozenset({"HRV-15"}),
    "C10-recency-only-rule-that-acts": frozenset({"HRV-16"}),
    "C12-same-baseline-window": frozenset({"HRV-12"}),
    "C13-era-clip-becomes-hole-clip": frozenset({"HRV-38"}),
    "C14-tier-change-collapses-baseline": frozenset({"HRV-34"}),
    "C15-tier-change-called-re-establishment": frozenset({"HRV-34"}),
    "C16-hrv21-reads-below-that-band": frozenset({"HRV-21"}),
    "C17-hrv24-read-on-last": frozenset({"HRV-24"}),
    "C18-no-tier-from-resolver": frozenset({"HRV-30"}),
    "C19-hrv-03-tag-and-confidence": frozenset({"HRV-03"}),
    "C19-hrv-04-reduced-confidence": frozenset({"HRV-04", "REG-09"}),
    "C21-dec01-bonus-section": frozenset({"DEC-01"}),
    "C24-arch06-ignores-by-default": frozenset({"ARCH-06"}),
    "C26-cold01-hrv-input": frozenset({"COLD-01"}),
    "C27-in-activity-hrv-not-computed-at-all": frozenset({"HRV-05"}),
    "C27-lt1-picked-up-without-amendment": frozenset({"LT1-02"}),
    "C28-lt1-surrogate-refinement": frozenset({"LT1-01"}),
    "C30-ctl-rise-row-deferred": frozenset({"REG-19"}),
    "C31-ind01-remains-tunable": frozenset({"IND-01"}),
    "C32-band-without-floor": frozenset({"HRV-07"}),
    "C33-hrv-17-tolerance-not-published": frozenset({"HRV-17"}),
    "C37-gate03-remeasured-not-cited": frozenset({"GATE-03"}),
    "DOC-06-C31-every-number-tunable": frozenset({"DOC-06"}),
    "DOC-09-C38-superseded-text-left-standing": frozenset({"DOC-09"}),
    "GOAL-02-C22-goal-contract-two-fields": frozenset({"GOAL-02"}),
    "HRV-01-R13-four-tier-hierarchy": frozenset({"HRV-01"}),
    "HRV-11-per-day-collapse-unspecified": frozenset({"HRV-11"}),
    "HRV-40-R13-now-sustaining-tier": frozenset({"HRV-40"}),
    "PRIN-05-C06-conservative-wins-unscoped": frozenset({"PRIN-05"}),
    "PRIN-08-C24-sidecar-ignored-by-default": frozenset({"PRIN-08"}),
    "PRIN-08-C25-rule-file-short-list": frozenset({"PRIN-08"}),
    "PRIN-10-C19-reduced-confidence": frozenset({"PRIN-10"}),
    "PRIN-12-C33-tolerance-not-published": frozenset({"PRIN-12"}),
    "PRIN-12-R13-withheld-response-stays-reproducible": frozenset({"PRIN-12"}),
    "PRIN-14-C07-weak-evidence-only": frozenset({"PRIN-14"}),
    "PRIN-15-C06-accepted-as-priced": frozenset({"PRIN-15"}),
    "PRIN-16-C08-silence-tolerated-freely": frozenset({"PRIN-16"}),
    "T07-acwr-band": frozenset({"REG-02"}),
    "T07-ctl-rise-band": frozenset({"REG-19"}),
    "T07-tolerance-band": frozenset({"GATE-03"}),
    "T07-tsb-target-form-band": frozenset({"REG-16"}),
}

#: The committed traceability table, frozen row by row (sprint-007 review iteration 4, M1 and M2).
#: Five routes in one family (iteration 1 M2, iteration 2 M1, iteration 3 S1, iteration 4 M1 and M2)
#: each edited 00-traceability.md in a way no frozen literal covered: PRIN-10's keyed ``no`` row turned
#: ``yes`` by adding an unrelated authorizing C-number (``C19, C38``), and DOC-03 retired under H-39
#: with its changed text re-homed on an addition row. The table is authored in the same repo as the
#: check, so without a snapshot the check has no independent oracle. Keyed by ``_row_key``: the
#: inventory ID, or ``addition <new ID(s)>`` for an addition row. Each value is ``row_digest(row)``:
#: the seven cells' ``_cell_digest`` (the first 12 hex of ``_sentence_digest``: whitespace collapsed,
#: nothing else changed), in ``TRACE_COLUMNS`` order, joined by ``.``, so a mismatch names its cell.
#: Derived 2026-09-26 from the committed table at 790ea0c: 149 rows, 149 unique keys (the 149
#: inventory IDs), no addition row. Asserted both ways by ``traceability_row_errors``.
#:
#: **Regenerating a digest deliberately.** A legitimate table edit is a reviewed change to this
#: literal, never a widening of a check. Each ``[frozen-table]`` error names the row and the cell and
#: prints the row's new digest, ready to paste here once the edit is reviewed. ``frozen_literals()``
#: prints every digest literal this module freezes, from the committed files:
#: ``uv run --package runcoach-api python runcoach-api/tests/test_research00_traceability.py``.
TRACEABILITY_ROW_SHA256 = {
    "DOC-01": "0db604f4ed7c.db839f374fe7.fd2d36f07f9b.0db604f4ed7c.bda050585a00.9390298f3fb0.bda050585a00",
    "DOC-02": "0768538d8cc1.cf0680478c48.d25cf343afe7.deb5e5239433.bda050585a00.9390298f3fb0.bda050585a00",
    "DOC-03": "bc248cd682e5.09392b30e7af.22b323dbce4a.bc248cd682e5.bda050585a00.9390298f3fb0.bda050585a00",
    "DOC-04": "3e47716b9fd2.5300aacf0fc6.ed7bfeb06544.3e47716b9fd2.bda050585a00.9390298f3fb0.bda050585a00",
    "DOC-05": "d2986df6ef7b.07768412f26c.f3df1b1a6836.d2986df6ef7b.bda050585a00.9390298f3fb0.bda050585a00",
    "DOC-06": "09c0b60f5204.eb52e3501bae.276a439ac097.fb3d99e27995.ba9d0f8031fa.8a798890fe93.c4a72fa5bde9",
    "DOC-07": "6742a589b261.47985bb08254.0fc16dea499d.4d76ce879899.0de00bed0228.9390298f3fb0.bda050585a00",
    "DOC-08": "b7e990e1e770.40ad869b6aac.0fc16dea499d.b7e990e1e770.bda050585a00.9390298f3fb0.bda050585a00",
    "DOC-09": "baf386b45863.b6085b77097c.141502a4d41f.1cc31399cc6b.625808357ae1.8a798890fe93.d17d35ce92b2",
    "DOC-10": "3bd142b2b1e1.69f29cf25b55.bcdadad2fbc7.a47495d71a85.bda050585a00.9390298f3fb0.bda050585a00",
    "DOC-11": "a860874a6373.04bb003ee4b3.b924d37d808d.a860874a6373.bda050585a00.9390298f3fb0.bda050585a00",
    "DOC-12": "45ecfcd46bc1.85f371f6dd47.141502a4d41f.0d543de46eca.bda050585a00.9390298f3fb0.bda050585a00",
    "DOC-13": "467a44b148d4.e6fb9c944c22.141502a4d41f.467a44b148d4.bda050585a00.9390298f3fb0.bda050585a00",
    "DOC-14": "4785a6d27692.ae91e23c9b4a.141502a4d41f.27915a045041.bda050585a00.9390298f3fb0.bda050585a00",
    "DOC-15": "edd9efeaaf28.0be0e79c8015.68e0e541fa4c.edd9efeaaf28.bda050585a00.9390298f3fb0.bda050585a00",
    "PRIN-01": "2880239d9831.9c24c99aead5.e9b8842a6764.2880239d9831.bda050585a00.9390298f3fb0.bda050585a00",
    "PRIN-02": "5a161eec763a.bc4e27f99a8f.e9b8842a6764.5a161eec763a.bda050585a00.9390298f3fb0.bda050585a00",
    "PRIN-03": "b326882eb42c.e515ed8fd283.5cdc045f5ad9.b326882eb42c.bda050585a00.9390298f3fb0.bda050585a00",
    "PRIN-04": "848180c35b6a.663644c4b4d2.3f7b5e6cc46f.848180c35b6a.bda050585a00.9390298f3fb0.bda050585a00",
    "PRIN-05": "c1f7bf41ba01.910f76e3cda2.0f5e0d50cfe3.cdae81e1c37b.e18b75a07d89.8a798890fe93.cd582787b3b2",
    "PRIN-06": "39ba548399e0.51e10f46e03e.0f5e0d50cfe3.eed8c40ff52a.bda050585a00.9390298f3fb0.bda050585a00",
    "PRIN-07": "644752b65331.e2638a9261f8.ae3f7c6f4add.644752b65331.bda050585a00.9390298f3fb0.bda050585a00",
    "PRIN-08": "57c7d3a77f12.86f27db53205.103dc6246b0e.d9af109ec97d.cb031e6f7fc6.8a798890fe93.828906f78405",
    "PRIN-09": "a7ec19cf42b5.36557c69dde9.59464aa69139.a7ec19cf42b5.bda050585a00.9390298f3fb0.bda050585a00",
    "PRIN-10": "9ffe88a4b14d.10b0a06f7359.d663ecf5fa5a.7d7226145cdb.a539dc09c09c.9390298f3fb0.16b7f083a84e",
    "PRIN-11": "94ee3c5975a7.6d57e5f11cd9.8825ccca09f2.253d4600eeb0.bda050585a00.9390298f3fb0.bda050585a00",
    "PRIN-12": "d191ef3229ab.6cb6ac81454f.4fef567a5a58.e579689f5fda.0e01f0971f82.8a798890fe93.5f39f9aa8ebf",
    "PRIN-13": "e47004f3f0c5.d7c14355883f.369c49596864.e47004f3f0c5.bda050585a00.9390298f3fb0.bda050585a00",
    "PRIN-14": "4fc6fb4664dc.8d6698e35470.494da81eb714.4fc6fb4664dc.1751f998fdad.8a798890fe93.ac919b316567",
    "PRIN-15": "d5452db028f5.ac46c591281b.a3e3b9ad5b3b.a6260e38dafd.8d05991678e9.8a798890fe93.ebf2f0bfa089",
    "PRIN-16": "e716bfb58948.141ac72b92ba.0208c460b4e9.e19449ddac9a.acade632f70d.8a798890fe93.d174652dd329",
    "ARB-01": "9ab809362800.cc3f97b16818.78fb27e49156.9ab809362800.bda050585a00.9390298f3fb0.bda050585a00",
    "ARB-02": "be8db9b45932.640b1a4315ff.7d33de084491.71d67f9620cf.e377a9bf7155.9390298f3fb0.bda050585a00",
    "ARB-03": "00f4832e21a5.20f0b3c35d78.006bfc3279ee.00f4832e21a5.bda050585a00.9390298f3fb0.bda050585a00",
    "ARB-04": "f1ea0df1810d.f889d330da56.af4b1bb175b5.f1ea0df1810d.bda050585a00.9390298f3fb0.bda050585a00",
    "ARB-05": "86fb15cf1bb1.ee60948c94d7.dd79e45a6077.86fb15cf1bb1.bda050585a00.9390298f3fb0.bda050585a00",
    "ARB-06": "80dc9b16b0ff.cb71b4fab675.de087c1303c8.80dc9b16b0ff.bda050585a00.9390298f3fb0.bda050585a00",
    "AUT-01": "38e9f8a968e8.c4e4c0c673c2.853237b8c6b0.38e9f8a968e8.bda050585a00.9390298f3fb0.bda050585a00",
    "AUT-02": "d3a0a2760200.db401802608a.bb1728856887.d0548c9f952b.e377a9bf7155.8a798890fe93.fdd31aec7605",
    "AUT-03": "6d5ccf8bc36e.45f593b40bbd.f90d208fa0e0.6d5ccf8bc36e.bda050585a00.9390298f3fb0.bda050585a00",
    "AUT-04": "3b0e88506545.4140bc60705e.853237b8c6b0.3b0e88506545.c9c1dd0d52c9.8a798890fe93.bf6c0488af9b",
    "AUT-05": "df388a05e776.e8c9cbdc5f1c.22b323dbce4a.5c7023df818a.bda050585a00.9390298f3fb0.bda050585a00",
    "AUT-06": "fbea78351434.20cca433af29.853237b8c6b0.fbea78351434.bda050585a00.9390298f3fb0.bda050585a00",
    "GOAL-01": "9989f6257e18.32a82d981e2d.510533124ac0.9989f6257e18.bda050585a00.9390298f3fb0.bda050585a00",
    "GOAL-02": "988c99a19250.64b6df95acdf.510533124ac0.988c99a19250.0ee73df9932d.8a798890fe93.b569d55df7f3",
    "GOAL-03": "0306c222c244.b4ad8a2781c5.510533124ac0.0306c222c244.bda050585a00.9390298f3fb0.bda050585a00",
    "GOAL-04": "ef3ac215f988.fbfbdd8cde73.510533124ac0.ef3ac215f988.bda050585a00.9390298f3fb0.bda050585a00",
    "GOAL-05": "6076c3526989.e88553fb7cca.510533124ac0.6076c3526989.bda050585a00.9390298f3fb0.bda050585a00",
    "GOAL-06": "f0d054ca660e.2b8b3b5512d6.8f7a50b83591.f0d054ca660e.bda050585a00.9390298f3fb0.bda050585a00",
    "ARCH-00": "379875c57851.e8e67f6e2230.5e9b00ff6f83.379875c57851.bda050585a00.9390298f3fb0.bda050585a00",
    "ARCH-01": "3497384177b1.b8bb673a86b0.2d139a584015.3497384177b1.bda050585a00.9390298f3fb0.bda050585a00",
    "ARCH-02": "572ed3eb3f94.04d06b59bb62.30bbd43e174d.572ed3eb3f94.bda050585a00.9390298f3fb0.bda050585a00",
    "ARCH-03": "3700935a39b8.485ad4f767e3.38cce7a3b9e5.0bf25ec06d7c.bda050585a00.9390298f3fb0.bda050585a00",
    "ARCH-04": "0daf71d9638a.a44d250c7628.b757487a4aec.aba11c510cae.bda050585a00.9390298f3fb0.bda050585a00",
    "ARCH-05": "a4c29cfc88a4.0eb9375dfd33.ff5727d9c886.a4c29cfc88a4.bda050585a00.9390298f3fb0.bda050585a00",
    "ARCH-06": "1039a13cbc79.3de99579f98a.d1df5d8a022e.1039a13cbc79.133d22d98524.8a798890fe93.979d2a1c3d35",
    "ARCH-07": "636fec22662a.a5a07cc2c0af.83df11ad8848.636fec22662a.bda050585a00.9390298f3fb0.bda050585a00",
    "ARCH-08": "3472cc20ecde.2b98a4971bbb.019f3c7c59e9.9836ef97f2fe.78ecfcabf16b.8a798890fe93.255499c8d2e7",
    "REG-01": "9afce4bb4ddd.ee15dadd5fc1.a2a0b5d7990f.3b686d9baf66.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-02": "8851291a18ea.ee616bc65186.91b47fbf02ec.8851291a18ea.ed777df09525.8a798890fe93.4c7546809706",
    "REG-03": "46739600e3aa.e6cd4bb9de95.49185ebface6.9901af2d4b73.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-04": "7b02e057a1f4.8d240f5f740c.99b3afaff7b9.7b02e057a1f4.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-05": "00ff87265672.82aaa29d1402.90d8b41320a1.00ff87265672.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-06": "16b77275c0e4.a2360c8cd0f8.cea4cd09cf57.16b77275c0e4.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-07": "cd1674ed9e54.07205c6f2542.8404e3c7d8fa.f70aa13013a4.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-08": "36a915c98bbb.f603aeb013aa.a67714682961.36a915c98bbb.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-09": "123f9a193907.4e1e315a715b.33557eefc407.94d5ac98e04f.a539dc09c09c.9390298f3fb0.09f38653cd07",
    "REG-10": "d8a133f008aa.9e957995bd43.5594b20b9ae3.d8a133f008aa.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-11": "2aa4e9fd9c24.82b80437d097.e4d35ea771f4.d3c404bc0bbc.acade632f70d.8a798890fe93.0fb674f52df5",
    "REG-12": "25a2bafc572e.efb64032acf6.475c2fe10576.25a2bafc572e.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-13": "5ac8e1306056.a0be2143099e.7718c6781dab.5ac8e1306056.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-14": "51010afb1bf8.7e3162850707.e2961d1e9540.51010afb1bf8.efe2ca6af330.9390298f3fb0.bda050585a00",
    "REG-15": "0562b0504e7c.d3240a8cee0b.7a80a52ffec1.934d7a5eecd5.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-16": "44be79c4758b.117368020848.f21cf13c85e0.9a5722332927.ed777df09525.8a798890fe93.b81e706d465c",
    "REG-17": "ac4f80ca80bd.cf0a67d461f5.027a9e1685f6.5044af1ae9ef.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-18": "a55a21406c93.9ce47b2da463.15b49fba9d7b.70c17c5db4c4.bda050585a00.9390298f3fb0.bda050585a00",
    "REG-19": "e52a08532a45.3de0a38757b7.0d42a18b72d9.e52a08532a45.6731f758068f.8a798890fe93.2e71bf85dbaf",
    "IND-01": "45344277e3d8.9cd5b1ce2b12.581cdb8ed5b6.a650ba82004b.ba9d0f8031fa.8a798890fe93.c80441c1aa5c",
    "IND-02": "d5584bd276b1.4ae2f8dec412.d7b496df724b.6077f84f18cf.bda050585a00.9390298f3fb0.bda050585a00",
    "COLD-01": "aa569cba809e.9effcb79c1f9.8ee90f7ec740.32d88d386866.31c65e049802.8a798890fe93.386195ff7f81",
    "COLD-02": "37273b252a93.8d1f36e9f6cd.67092354fec7.135584b4163f.bda050585a00.9390298f3fb0.bda050585a00",
    "COLD-03": "152f5df3f3f7.96c95a803f87.67092354fec7.152f5df3f3f7.bda050585a00.9390298f3fb0.bda050585a00",
    "COLD-04": "4f8aa2ec8356.8c60ffc82d32.67092354fec7.4f8aa2ec8356.bda050585a00.9390298f3fb0.bda050585a00",
    "COLD-05": "019c8eed7b50.cde718d3f56a.67092354fec7.019c8eed7b50.bda050585a00.9390298f3fb0.bda050585a00",
    "COLD-06": "5495392a35eb.40ce6d9eafe0.67092354fec7.d2b236f5326c.bda050585a00.9390298f3fb0.bda050585a00",
    "COLD-07": "6176f87e3de3.64789b01c94d.b88f0aac8432.ad21ed2d89b7.bda050585a00.9390298f3fb0.bda050585a00",
    "GATE-01": "4d3bbbcc988b.45251b819781.517943edbb5c.df3c78df7cc2.e18b75a07d89.8a798890fe93.87e26cf2dc22",
    "GATE-02": "1e1adcc48077.f8f51b197a29.b2da7bd96a12.a8ffad264e66.12754373b28a.8a798890fe93.47e80c58745f",
    "GATE-03": "3b4c3c560b99.b7acb5f5c829.141502a4d41f.3a559c1a892a.89fe5fe525de.8a798890fe93.179dafcef28e",
    "FIG-01": "708d32901569.524fccb0f809.6969f00b1b8f.708d32901569.bda050585a00.9390298f3fb0.bda050585a00",
    "FIG-02": "ae30d4a0a46d.49e2eb0c879c.6258c57f0cb8.9a7aac665d7b.bda050585a00.9390298f3fb0.bda050585a00",
    "FIG-03": "06e89546331f.b749ad48ee2b.b924d37d808d.93b9133f8a95.bda050585a00.9390298f3fb0.bda050585a00",
    "FIG-04": "494aab1be5ff.10d5783676a1.82adc07c8c81.937826225158.bda050585a00.9390298f3fb0.bda050585a00",
    "FIG-05": "da46be147807.a1e992520364.b924d37d808d.370e9b5b8ac5.09947c6e5a08.8a798890fe93.1fb58675ff63",
    "LT1-01": "c69ec440c21a.0641fd79efe9.d39900e1f13e.17205d0f7a98.ebca3449f77b.8a798890fe93.074d9af3c337",
    "LT1-02": "acff1d9a67b3.691d7b3d4834.f4a1833827c9.19e3a51f3f3e.2fbe87fe5a12.8a798890fe93.a058510d2e35",
    "FTO-01": "2d268e2188d0.c0ec8cb8fe64.1bdd44dca48f.2d268e2188d0.bda050585a00.9390298f3fb0.bda050585a00",
    "FTO-02": "88c395c3a7f3.fbdce0b431ba.1bdd44dca48f.88c395c3a7f3.bda050585a00.9390298f3fb0.bda050585a00",
    "FTO-03": "4945ae081b18.911fc1dddb18.1bdd44dca48f.ee64702ab009.bda050585a00.9390298f3fb0.bda050585a00",
    "FTO-04": "3e643e1c7189.43224e6cfe9f.1bdd44dca48f.3e643e1c7189.bda050585a00.9390298f3fb0.bda050585a00",
    "FTO-05": "2bb3a3ea6285.a2589d6c6372.1bdd44dca48f.2bb3a3ea6285.bda050585a00.9390298f3fb0.bda050585a00",
    "FTO-06": "ed96df13b148.a15c73237ab4.1bdd44dca48f.ed96df13b148.bda050585a00.9390298f3fb0.bda050585a00",
    "DEC-01": "aaa88c4354b8.cd26e8547d8d.2055f7073018.c1c7baacbc77.abb23d0d25cd.9390298f3fb0.433d7ffa90c8",
    "HRV-01": "5f8e665ba762.13a614660b06.8d28dd0a34a1.5f8e665ba762.7a2eca4de16f.9390298f3fb0.6c48482d1617",
    "HRV-02": "e71b6ab0c325.528a4ad85b20.43ad342351f4.89b45ca81b2a.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-03": "2875db3e0aee.15d36d43964e.f5c94c7ffb8b.2875db3e0aee.a539dc09c09c.9390298f3fb0.1097ed74c667",
    "HRV-04": "f8a0bb862e06.b4c4b4ab01f5.1d351f884336.f8a0bb862e06.a539dc09c09c.9390298f3fb0.09f38653cd07",
    "HRV-05": "c174f37f1a7b.770b84444be6.1d351f884336.c174f37f1a7b.2fbe87fe5a12.8a798890fe93.2ba531efd1a7",
    "HRV-06": "b1d8b2fc756f.d550490e2ad8.c4a6ee0b49b2.b1d8b2fc756f.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-07": "b1e8f4496d4c.bbca067bdccd.7c09b4d8d78a.b1e8f4496d4c.fb44388faf2a.8a798890fe93.a36430c37261",
    "HRV-08": "46e318b004fb.5bb27bbd25e0.141502a4d41f.46e318b004fb.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-09": "965a032ffeb7.a34d7bfd9ad4.3c0a138dcb1d.965a032ffeb7.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-10": "3ce6699fcb65.c631e969ab03.0e41697b58a0.0e60a0170cf2.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-11": "72443a5a64c7.7ed69b047f3f.cde953767a87.475b3c7b820c.72443a5a64c7.8a798890fe93.de17b5e1a4da",
    "HRV-12": "9e647e5b9a13.31c3fc916325.91729d5cbade.9e613ccb61a4.fa70213c8aaf.8a798890fe93.e8193fd69620",
    "HRV-13": "da2edb6bd28d.9eeaa388dabe.7254413f89b0.da2edb6bd28d.fa70213c8aaf.9390298f3fb0.bda050585a00",
    "HRV-14": "8fbe7fb6a6bd.2f272c5d6dee.033555daebbb.8fbe7fb6a6bd.22b5894e02a7.9390298f3fb0.bda050585a00",
    "HRV-15": "919858c53430.a5f8bab2b1d0.b87309485bb6.26611ca9c85a.3b7e32ae3b46.8a798890fe93.a43d5d712d8f",
    "HRV-16": "c2832f302a28.599b8952be2d.b6a0e2d29c10.0cb5f881058a.908d72900e24.8a798890fe93.0209abe539b2",
    "HRV-17": "b2695c4ae6dc.568765382f56.b6a0e2d29c10.b2695c4ae6dc.a39ed58c5b0b.8a798890fe93.6928e5dc8c01",
    "HRV-18": "2c60e3953b4c.9164c163de0e.91729d5cbade.2c60e3953b4c.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-19": "a99fac6f6f45.280aa306bc89.91729d5cbade.04a48dea847d.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-20": "352400d42a51.b6df2edfb657.39034ddf782a.c801f9d98d9b.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-21": "2f461612904a.901241c6a2ee.9231830c8f20.190d2ca5a335.e1d53e4f418b.9390298f3fb0.69c1a96d1053",
    "HRV-22": "de1edc054ee0.03a23537a126.39034ddf782a.11e6e530f1bf.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-23": "2bdbb34d3b99.4ebfdbd0d25a.39034ddf782a.a0b98f2203c6.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-24": "868aeaea3064.7b7f1073e65e.d89ce01a1dad.3720c715ebb4.aa482ed186ee.9390298f3fb0.cd795478b76e",
    "HRV-25": "aab245056fcb.172890d79358.30a80ed79fd5.aab245056fcb.e18b75a07d89.8a798890fe93.06cb4bcea601",
    "HRV-26": "6414c71e0922.0e689db70c8c.477cb6490208.6414c71e0922.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-27": "53110bc0046a.088b43f2148d.bfb1273e210c.53110bc0046a.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-28": "cea676d8a58f.2690e3e38516.d97f2ac6b450.f14acf628da5.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-29": "dd178b664468.7450e8b6ca90.141502a4d41f.d4bb179b13d2.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-30": "1e2ef029bb73.e90ce41db4ca.141502a4d41f.1e2ef029bb73.d7cbb6e7dea7.9390298f3fb0.282872747bf5",
    "HRV-31": "f7644bece5b9.732e7894a8f3.3a4b0f9ead80.58b8c6440f07.1ba9aa97fd04.8a798890fe93.adb94f7ea4d9",
    "HRV-32": "62efc47e433a.6396eb3ddd48.82adc07c8c81.8b943484deab.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-33": "cfa72e4a99bc.6f71a595c3c7.4a0ffb80cd0b.ac8890863d7e.09947c6e5a08.8a798890fe93.492c4fa8e233",
    "HRV-34": "bfbe516a48bc.0adf15613391.f9643ded7f32.6e7a11003862.d3bcf7c77f65.8a798890fe93.d9282680ba9d",
    "HRV-35": "438c8a2e6aea.550e3512df10.141502a4d41f.826c270b6978.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-36": "f25c6f7a0081.83aa4a7625d5.141502a4d41f.545c84be8788.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-37": "350c4ef1265b.f7cff2c54ef9.1d68241bcea9.350c4ef1265b.34a44366294a.8a798890fe93.00c2bffa0831",
    "HRV-38": "08458bc79ce4.308982854b97.6f1cce33f7f0.733d298e8d61.6645ca87da63.8a798890fe93.e0b223b1e875",
    "HRV-39": "67e71657c83f.c9dbbd781835.d893b15300d5.f61dd6f1bbac.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-40": "a3a5f5539618.9f6e3248ce64.ee88d8b09d69.6814368af646.7a2eca4de16f.9390298f3fb0.6b810ca2c02f",
    "HRV-41": "6c5693acd4be.a30eb10ac751.a3e3b9ad5b3b.07247f6021b7.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-42": "78c3365d0fab.4ea3f04e28ef.141502a4d41f.78c3365d0fab.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-43": "a11166190cf5.6c975d166371.5b1f25a4fe5e.ab0502a80971.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-44": "468dcd7ad504.4dab9c88cf6a.bcdadad2fbc7.3b90d5b578f0.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-45": "751aa58120e5.9d2c5dda43b9.141502a4d41f.751aa58120e5.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-46": "effcc59e51a1.d8b1cb2b89be.b924d37d808d.6d7af968a571.bda050585a00.9390298f3fb0.bda050585a00",
}

#: Every inventory ID the committed table retires, with the H-NN it retires under (iteration 4, M2).
#: ``retired_ids`` and ``## Retired IDs`` were only compared with each other, so retiring DOC-03 under
#: H-39 in both stayed green. Derived 2026-09-26 at 790ea0c: ``retired_ids(table)`` and the history's
#: list are both exactly this. Asserted by ``retirement_errors``.
RETIRED_IDS = {"PRIN-16": "H-39"}

#: ``OLD_MEANINGS``, frozen per key (iteration 4, S1). Loosening AUT-04-C20's pattern and example
#: together let AUT-04 restate C20's old meaning with the suite green. Held here, not in the support
#: module, so the literals and the snapshot of them never share a file. Each value is
#: ``old_meaning_digest(entry)``: the first 12 hex of sha256 of the raw pattern, example, source and
#: decision, joined by ``.``, so a mismatch names its field. Derived 2026-09-26 from the support module
#: at 790ea0c (54 keys). Asserted both ways by ``old_meaning_digest_errors``; regenerate as for
#: ``TRACEABILITY_ROW_SHA256``.
OLD_MEANING_SHA256 = {
    "AUT-02-C23-override-outside-autonomy": "40ee0124162e.a0a2a0e5f7d0.443a6d782c81.e377a9bf7155",
    "AUT-04-C20-two-purposes-only": "fcf9289cd377.ad92b399c18a.9a371cbdd09e.c9c1dd0d52c9",
    "C01-withhold-not-judgeable-only": "ece30e6d2bca.bce6926526bb.576b340a4e35.132bfbedb168",
    "C02-withhold-against-selected": "4a0e7e6c8041.88bcc0e544f8.576b340a4e35.371e0b6ba48a",
    "C03-return-is-free": "d16d359a47ee.d16d359a47ee.84e7c78e5b2a.0dfab5acbae0",
    "C04-hole-at-least": "4f07aadc1443.9f5c88160919.576b340a4e35.34a44366294a",
    "C05-gate02-worse-rate-reopens": "617c02393a16.7483b505e85d.576b340a4e35.12754373b28a",
    "C06-gate01-one-exception": "ebcd7bc8c59e.75275b4710d1.576b340a4e35.e18b75a07d89",
    "C06-hrv-25-accepted-cost": "a11cbc550188.77a9b4bb08b1.576b340a4e35.e18b75a07d89",
    "C08-arch08-silence-tolerated-freely": "2d05d06f5484.31b6dd220bb2.bf4bbfce9e6f.acade632f70d",
    "C08-reg11-readiness-gate-down-weights": "1612ccf784d8.266bc9b30f23.9386f81ad126.acade632f70d",
    "C09-fig05-idea071-sprint": "82e07b40ba5c.973bd9eadf9c.83e90ba33390.09947c6e5a08",
    "C09-residual-carried-to-idea-071": "89849bbccdd0.e677a7cdc3d8.bf4bbfce9e6f.09947c6e5a08",
    "C10-lone-candidate-never-struck": "5d697fcd9aa1.d4181fae1f2a.f9699c8b2640.908d72900e24",
    "C10-recency-only-rule-that-acts": "6e032fe70942.e0c1bfef36eb.f9699c8b2640.908d72900e24",
    "C12-same-baseline-window": "f32159e95c30.01b0b0473635.576b340a4e35.fa70213c8aaf",
    "C13-era-clip-becomes-hole-clip": "5933dbf510dd.952bdd8429e2.576b340a4e35.6645ca87da63",
    "C14-tier-change-collapses-baseline": "31e37b56e539.3ac56c9d3a23.5b9172a8603f.fafc221218d3",
    "C15-tier-change-called-re-establishment": "eb43eddec2c7.baf9a521ce2f.c9ea2c9d2933.c1f5e527f9d4",
    "C16-hrv21-reads-below-that-band": "79b4138db0e7.9f8dec54b13d.576b340a4e35.1150591c77a1",
    "C17-hrv24-read-on-last": "cf5e3da90d63.39f1ab966669.576b340a4e35.a7314aec43f1",
    "C18-no-tier-from-resolver": "a7915eb5cb64.4ebc7a63f730.e77fba90b5d8.66880a20dae0",
    "C19-hrv-03-tag-and-confidence": "1051957c88ad.201b14aa6fa0.f79e61bfca66.a539dc09c09c",
    "C19-hrv-04-reduced-confidence": "9e3fa949fdc4.7ec3fdac444a.700a5baf5724.aae16350d9be",
    "C21-dec01-bonus-section": "02834eedae78.6bc22b93b0fd.ecc48cc297fe.abb23d0d25cd",
    "C24-arch06-ignores-by-default": "d22a4b5bede7.aeac417e7a2c.16bd7814bc5b.133d22d98524",
    "C26-cold01-hrv-input": "e56a9c2c97b7.d2aeea8a5c8e.d03fa426dc05.31c65e049802",
    "C27-in-activity-hrv-not-computed-at-all": "410e49bf0f47.d1dcfde1a753.84e7c78e5b2a.2fbe87fe5a12",
    "C27-lt1-picked-up-without-amendment": "8c68cf241bd9.3a281279449a.94a35c825035.2fbe87fe5a12",
    "C28-lt1-surrogate-refinement": "008db5a8005c.46ad84e349f4.d0587e831403.9a58d8421888",
    "C30-ctl-rise-row-deferred": "98dda4c9a678.b4ff1eb4d65c.14883507e245.0de00bed0228",
    "C31-ind01-remains-tunable": "5421a47351bb.5421a47351bb.29be2e003f4f.ba9d0f8031fa",
    "C32-band-without-floor": "8545442b8f9e.93c50f9822f4.700a5baf5724.fb44388faf2a",
    "C33-hrv-17-tolerance-not-published": "11cbd5f33443.56bcb20c6d0e.f9699c8b2640.a39ed58c5b0b",
    "C37-gate03-remeasured-not-cited": "234bcca0345f.9d59e0ef188b.576b340a4e35.5a816f13397a",
    "DOC-06-C31-every-number-tunable": "67023c89d734.418fd8dee2b1.8dd82d028b88.ba9d0f8031fa",
    "DOC-09-C38-superseded-text-left-standing": "f74a6dd03624.2d0d80fd956f.f9699c8b2640.625808357ae1",
    "GOAL-02-C22-goal-contract-two-fields": "8e791a826dd9.af406bcf953a.443a6d782c81.0ee73df9932d",
    "HRV-01-R13-four-tier-hierarchy": "e24c73a9ccb2.3bbb4d9f9c89.cebc61292e06.3e0a42a643a3",
    "HRV-11-per-day-collapse-unspecified": "e7af0a519d0b.d524be355a54.576b340a4e35.72443a5a64c7",
    "HRV-40-R13-now-sustaining-tier": "32e01ca7b893.ca8a8ddd8413.c9ea2c9d2933.ef61f8cd3858",
    "PRIN-05-C06-conservative-wins-unscoped": "9a7ed38813f0.187075f13758.31002881e3fe.e18b75a07d89",
    "PRIN-08-C24-sidecar-ignored-by-default": "611f94de932f.aeac417e7a2c.16bd7814bc5b.133d22d98524",
    "PRIN-08-C25-rule-file-short-list": "15bea1a5a8f5.e7b67b07770d.67ae3d3f6543.d0f66c4b6e21",
    "PRIN-10-C19-reduced-confidence": "4a55d90edabf.a241fbcc66d1.0440c34e3192.f5e2ee7f48ed",
    "PRIN-12-C33-tolerance-not-published": "90a523ce3525.56bcb20c6d0e.f9699c8b2640.a39ed58c5b0b",
    "PRIN-12-R13-withheld-response-stays-reproducible": "140471decdb7.965760d0f608.576b340a4e35.bf1999145d8e",
    "PRIN-14-C07-weak-evidence-only": "037bd3251a9c.9fc907922633.5b9172a8603f.ab2c8aca25fc",
    "PRIN-15-C06-accepted-as-priced": "cedf36087186.77a9b4bb08b1.576b340a4e35.e18b75a07d89",
    "PRIN-16-C08-silence-tolerated-freely": "71ccd3cd648e.21241cf65605.5b9172a8603f.acade632f70d",
    "T07-acwr-band": "045461dab097.a7436e9fe478.d6ff9e26863e.bc3dfbf4de86",
    "T07-ctl-rise-band": "a021f2e9a52a.3b0843c5517b.14883507e245.bc3dfbf4de86",
    "T07-tolerance-band": "0a50e1bfac53.3314b0e312c8.576b340a4e35.bc3dfbf4de86",
    "T07-tsb-target-form-band": "00275c8ecc64.2893c573f717.7721e99e1123.bc3dfbf4de86",
}

#: Each meaning-review verdict, bound to the text it judged (iteration 4, M3; the user ruled
#: 2026-09-26: bind the verdicts to the text). Two routes edited only research/00 after the review and
#: stayed green: HRV-24 dropped ``band`` (the AC9 proxy holds only the identifiers its inventory
#: sentence backticks) and DOC-09's text changed (a ``yes`` row is unproxied by design). Keyed by
#: exactly ``required_review_rows`` (789 labels: 246 rule lines, 246 Scope, 246 Not, 51 Why), in its
#: order. Each value is the ``_cell_digest`` (whitespace collapsed, first 12 hex) of the label's block
#: line from ``reviewed_block_lines``, which is the text rounds 1-5 of 00-meaning-review.md last judged
#: ``same``. Derived 2026-09-26 at 229177f: 789 labels, each mapped to exactly one line. Held here, not
#: in the review file, which stays critic-owned and spanless. Asserted both ways by
#: ``reviewed_block_errors``. **Regenerating a digest:** only after a fresh critic has re-reviewed the
#: changed row; the error prints the new digest, and ``frozen_literals()`` prints the whole literal.
REVIEWED_BLOCK_SHA256: dict[str, str] = {
    "PRIN-01": "a1721dba4322",
    "PRIN-01/Scope": "72ad398e0135",
    "PRIN-01/Not": "aa82804667ed",
    "PRIN-02": "a2de0d17d4d8",
    "PRIN-02/Scope": "296b44a68eb1",
    "PRIN-02/Not": "367369441e53",
    "PRIN-03": "4a756a09a4bd",
    "PRIN-03/Scope": "b6c312458fb1",
    "PRIN-03/Not": "b9e9dc98d0c8",
    "ARB-01": "382ead9aea91",
    "ARB-01/Scope": "c92dd57ba3df",
    "ARB-01/Not": "2491d7eef384",
    "ARB-02": "28e86f05d096",
    "ARB-02/Scope": "de1ced5c4372",
    "ARB-02/Not": "399de65afeac",
    "ARB-03": "c6615b02a927",
    "ARB-03/Scope": "a190c9320ac0",
    "ARB-03/Not": "3511f5aca73d",
    "ARB-04": "106b7cb31a1e",
    "ARB-04/Scope": "7516832f2372",
    "ARB-04/Not": "b7a1130ec2a6",
    "ARB-05": "27b5f8185835",
    "ARB-05/Scope": "3dfd2ab2614e",
    "ARB-05/Not": "5dd497e134ce",
    "ARB-06": "b2172e8930a5",
    "ARB-06/Scope": "17a712943ebc",
    "ARB-06/Not": "77bfa64d31b5",
    "ARB-07": "1456ca7202d2",
    "ARB-07/Scope": "f0101b63a46d",
    "ARB-07/Not": "1c70dcde7d24",
    "PRIN-04": "086ca24e1508",
    "PRIN-04/Scope": "de67487d6e01",
    "PRIN-04/Not": "18b24480e4d8",
    "PRIN-05": "ac9d5ecc943a",
    "PRIN-05/Scope": "e863cbf7fbc4",
    "PRIN-05/Not": "5c2e0422deb1",
    "PRIN-05/Why": "bb1d88820551",
    "PRIN-06": "5eb7a4181577",
    "PRIN-06/Scope": "9cac36d140a7",
    "PRIN-06/Not": "47d2d44f133c",
    "PRIN-17": "45379a1b7908",
    "PRIN-17/Scope": "3b5a51a459ad",
    "PRIN-17/Not": "c93966d11465",
    "PRIN-18": "31ef81e47807",
    "PRIN-18/Scope": "aee13726ffd4",
    "PRIN-18/Not": "2b55b40ca339",
    "PRIN-07": "d661923cc81b",
    "PRIN-07/Scope": "0ccdb04b3c57",
    "PRIN-07/Not": "59cc84ae9a2d",
    "PRIN-08": "716ded3fbf51",
    "PRIN-08/Scope": "b79b75ab630b",
    "PRIN-08/Not": "43bf841c503a",
    "PRIN-08/Why": "73f7f3c5b989",
    "PRIN-09": "b8466fcaa59b",
    "PRIN-09/Scope": "fe231d6eb6bf",
    "PRIN-09/Not": "fb88b29ff2c2",
    "PRIN-10": "c9496d34707f",
    "PRIN-10/Scope": "ab4137f32c05",
    "PRIN-10/Not": "83ed8d397c03",
    "PRIN-19": "f8cec5657ba1",
    "PRIN-19/Scope": "564e3f8991a9",
    "PRIN-19/Not": "a929b7e4598d",
    "PRIN-20": "8c6c9850c17d",
    "PRIN-20/Scope": "7a9e36a5b881",
    "PRIN-20/Not": "05d3d79240f5",
    "PRIN-20/Why": "7793b90256be",
    "PRIN-21": "986b1e3ddb62",
    "PRIN-21/Scope": "9f74556c14f5",
    "PRIN-21/Not": "65412f529f2d",
    "PRIN-11": "759ce8fb41ee",
    "PRIN-11/Scope": "d00ae3d76812",
    "PRIN-11/Not": "a7ed8bca22e5",
    "PRIN-12": "b2acf3b170c7",
    "PRIN-12/Scope": "d4f3dac795bd",
    "PRIN-12/Not": "79610d715295",
    "PRIN-12/Why": "9bc596260189",
    "PRIN-22": "40852c4fcdb8",
    "PRIN-22/Scope": "5da0a2823dbe",
    "PRIN-22/Not": "114757cf603b",
    "PRIN-23": "b10b26a50cb7",
    "PRIN-23/Scope": "bd38d855141a",
    "PRIN-23/Not": "6b4725bd80fb",
    "PRIN-24": "5c0af79c297a",
    "PRIN-24/Scope": "0e1a04d2542d",
    "PRIN-24/Not": "f615e9b99379",
    "PRIN-24/Why": "8176401abbe6",
    "PRIN-13": "be8b15fee359",
    "PRIN-13/Scope": "bd00e12e23c6",
    "PRIN-13/Not": "ad93b63bc539",
    "PRIN-14": "088b665342f2",
    "PRIN-14/Scope": "e450c8114047",
    "PRIN-14/Not": "c58d67456d93",
    "PRIN-14/Why": "f42d9eadcb5e",
    "PRIN-15": "1083fa6079ec",
    "PRIN-15/Scope": "5e5800b34562",
    "PRIN-15/Not": "161d4d24e7fd",
    "PRIN-15/Why": "0a62d4ea5ff2",
    "PRIN-25": "ea37aacd156e",
    "PRIN-25/Scope": "59bb14df91e9",
    "PRIN-25/Not": "3642a7e5e60d",
    "PRIN-26": "3bdc3cb84d33",
    "PRIN-26/Scope": "0d2d916961ec",
    "PRIN-26/Not": "55aa769c5980",
    "AUT-01": "cd95606074eb",
    "AUT-01/Scope": "58f1ac7d789f",
    "AUT-01/Not": "96c3c0688555",
    "AUT-02": "addbd874e345",
    "AUT-02/Scope": "65ef6e97a119",
    "AUT-02/Not": "79751ab9e313",
    "AUT-02/Why": "07f0565d189a",
    "AUT-03": "66e5e79be3e2",
    "AUT-03/Scope": "be3bb94d2dcf",
    "AUT-03/Not": "9a4f2e37d224",
    "AUT-04": "b3a6f8cfa18d",
    "AUT-04/Scope": "a27ad0f41415",
    "AUT-04/Not": "94c86a570fb1",
    "AUT-04/Why": "b7abb5d4fcaa",
    "AUT-06": "d89626af80c7",
    "AUT-06/Scope": "3329e678f966",
    "AUT-06/Not": "88707fbcd9cb",
    "AUT-08": "8864fc918068",
    "AUT-08/Scope": "ea146c559b26",
    "AUT-08/Not": "79e4c9a7054e",
    "GOAL-01": "99951128f68d",
    "GOAL-01/Scope": "171f28706199",
    "GOAL-01/Not": "a5fbd6324bed",
    "GOAL-02": "93a49d1ab325",
    "GOAL-02/Scope": "ad7035a5075b",
    "GOAL-02/Not": "17e0c7c0cf1a",
    "GOAL-02/Why": "235e9b105ae0",
    "GOAL-03": "f4f9f535816c",
    "GOAL-03/Scope": "de7470c1127b",
    "GOAL-03/Not": "1d4764d6447f",
    "GOAL-04": "b85929639322",
    "GOAL-04/Scope": "e9009a2b5654",
    "GOAL-04/Not": "4c77344819b1",
    "GOAL-05": "f73dd21161f3",
    "GOAL-05/Scope": "56bbe09c9b91",
    "GOAL-05/Not": "2df06f8262d2",
    "GOAL-06": "b830abfee522",
    "GOAL-06/Scope": "91f64773a345",
    "GOAL-06/Not": "5c10d57266d4",
    "ARCH-00": "8542a3f43cc1",
    "ARCH-00/Scope": "e5167d14e030",
    "ARCH-00/Not": "24f2d975c0f8",
    "ARCH-01": "6f21ab2b6449",
    "ARCH-01/Scope": "1236aec58159",
    "ARCH-01/Not": "7781cd389228",
    "ARCH-02": "03cdd3e4ebc5",
    "ARCH-02/Scope": "30367a2dc0ff",
    "ARCH-02/Not": "e4a1f38e3a13",
    "ARCH-03": "1550a2a8fa65",
    "ARCH-03/Scope": "e21359d928e9",
    "ARCH-03/Not": "965aeb745e17",
    "ARCH-04": "f7e1a3ec8605",
    "ARCH-04/Scope": "9e520f6ea575",
    "ARCH-04/Not": "34570acd9c2f",
    "ARCH-05": "25037da2a64d",
    "ARCH-05/Scope": "bcc9d9fbd09d",
    "ARCH-05/Not": "9217bb2c87c5",
    "ARCH-06": "42178d43c895",
    "ARCH-06/Scope": "ec0efcd7e9d1",
    "ARCH-06/Not": "4d7c770bdafd",
    "ARCH-06/Why": "a26466a69a1d",
    "ARCH-07": "758999269fbd",
    "ARCH-07/Scope": "2b11dc7c7e25",
    "ARCH-07/Not": "7e5e85a559cd",
    "ARCH-08": "c93e91e277dd",
    "ARCH-08/Scope": "b864da726a66",
    "ARCH-08/Not": "5872e8de5975",
    "ARCH-09": "77ecd0cb19fc",
    "ARCH-09/Scope": "4f901d1f7ac1",
    "ARCH-09/Not": "cd1560b358ef",
    "ARCH-10": "03131a7fcde8",
    "ARCH-10/Scope": "b7c02216643c",
    "ARCH-10/Not": "4f3db0ab2a60",
    "ARCH-11": "05cccfcd9356",
    "ARCH-11/Scope": "205eed45e7b3",
    "ARCH-11/Not": "bc274afdd769",
    "ARCH-12": "75ddd061c21e",
    "ARCH-12/Scope": "00434c8be6e5",
    "ARCH-12/Not": "ffb35a021500",
    "ARCH-12/Why": "dc9c3ee6a72e",
    "ARCH-13": "2af31a053cb9",
    "ARCH-13/Scope": "44c5223481ac",
    "ARCH-13/Not": "86f16ce685be",
    "ARCH-13/Why": "312db4cafb2a",
    "DOC-06": "c32d1f3770e5",
    "DOC-06/Scope": "3991f67a274a",
    "DOC-06/Not": "15983729c18a",
    "DOC-06/Why": "1ca51bf6fa3f",
    "DOC-07": "5851626610bf",
    "DOC-07/Scope": "226f8636bffe",
    "DOC-07/Not": "36d64493bffa",
    "DOC-08": "7a48246d8507",
    "DOC-08/Scope": "57ef6cd15cb2",
    "DOC-08/Not": "3f6ee134808b",
    "DOC-17": "80a6f7ba6ddc",
    "DOC-17/Scope": "44b9f0946f17",
    "DOC-17/Not": "5f3befffef78",
    "DOC-18": "0e3a69cf6e2c",
    "DOC-18/Scope": "da14a8687a45",
    "DOC-18/Not": "e8f6dcb5f15d",
    "REG-01": "6f3833ba0dd0",
    "REG-01/Scope": "e37abed58974",
    "REG-01/Not": "f995248410b9",
    "REG-02": "755edc540b20",
    "REG-02/Scope": "c1ed84eb9f5b",
    "REG-02/Not": "07f8143f00de",
    "REG-02/Why": "4e9d40c4717b",
    "REG-03": "0399b8bf3cbc",
    "REG-03/Scope": "469c52680707",
    "REG-03/Not": "600f319ac174",
    "REG-04": "5de66e21d6d6",
    "REG-04/Scope": "89b2daa8c0aa",
    "REG-04/Not": "359d9c37dc96",
    "REG-05": "3b4ab7c65a52",
    "REG-05/Scope": "7344276f15c4",
    "REG-05/Not": "f0fc15cc2e6e",
    "REG-06": "2e00b0aa37ef",
    "REG-06/Scope": "cbc93b1aaca6",
    "REG-06/Not": "2fa50e337c84",
    "REG-07": "5000e4b68ff7",
    "REG-07/Scope": "6768439415c6",
    "REG-07/Not": "146dba16a111",
    "REG-08": "8a28abf117c0",
    "REG-08/Scope": "8b524279ed0a",
    "REG-08/Not": "a2e044e7723c",
    "REG-09": "0946bd3b5b38",
    "REG-09/Scope": "d7ea2281f46e",
    "REG-09/Not": "b5a252f41452",
    "REG-10": "c733ac2b2cb7",
    "REG-10/Scope": "2f185d398ae1",
    "REG-10/Not": "5a61cdc78a3b",
    "REG-11": "eb4e6d6ff070",
    "REG-11/Scope": "afa1e6a2863b",
    "REG-11/Not": "7e20e610b630",
    "REG-12": "23ac94e4156a",
    "REG-12/Scope": "64ee95111eed",
    "REG-12/Not": "0a213a7ce802",
    "REG-13": "4220e7542557",
    "REG-13/Scope": "27aafbded08b",
    "REG-13/Not": "51e4d9e0a4cd",
    "REG-14": "3404191baabf",
    "REG-14/Scope": "2597113b9e3a",
    "REG-14/Not": "2a5ae76c8a0c",
    "REG-14/Why": "10893556559c",
    "REG-15": "9d657e3093c7",
    "REG-15/Scope": "eb731201f4ef",
    "REG-15/Not": "20f73ad5554c",
    "REG-16": "c078809c1fcf",
    "REG-16/Scope": "8b524279ed0a",
    "REG-16/Not": "ce882887273a",
    "REG-16/Why": "c27309e13adc",
    "REG-17": "51e5a31a8900",
    "REG-17/Scope": "ee22b1f0805c",
    "REG-17/Not": "a6bae3038c8f",
    "REG-18": "60fdda546194",
    "REG-18/Scope": "2d10acfb2869",
    "REG-18/Not": "75c1f19c4afb",
    "REG-19": "988e618c732e",
    "REG-19/Scope": "f50128b15d62",
    "REG-19/Not": "45ca2a286190",
    "REG-19/Why": "faa864fb822e",
    "REG-20": "441375824267",
    "REG-20/Scope": "077b2371bad1",
    "REG-20/Not": "90df8f4e71cb",
    "REG-21": "048449ae612b",
    "REG-21/Scope": "967def42737e",
    "REG-21/Not": "d13ebdb994be",
    "REG-22": "8f199d8864f6",
    "REG-22/Scope": "8b929bccecf5",
    "REG-22/Not": "6bb9d4d924db",
    "REG-23": "28c3267230cf",
    "REG-23/Scope": "fa933d22524d",
    "REG-23/Not": "376b0a531347",
    "REG-23/Why": "9863c57335e2",
    "REG-24": "657964a12578",
    "REG-24/Scope": "fb89cd93512f",
    "REG-24/Not": "9a4984daaa6b",
    "REG-25": "0c7063813fc6",
    "REG-25/Scope": "22abaae1e192",
    "REG-25/Not": "630fb574ef1b",
    "REG-25/Why": "6f2abe59b155",
    "REG-26": "b4106c053919",
    "REG-26/Scope": "eb731201f4ef",
    "REG-26/Not": "b162912c2f3b",
    "REG-27": "b7e330866724",
    "REG-27/Scope": "eacfcdd4181b",
    "REG-27/Not": "38300e63fd52",
    "REG-28": "27b294948dc6",
    "REG-28/Scope": "a30c26198a0e",
    "REG-28/Not": "d0213d2fb45d",
    "REG-29": "7fcbe780f50b",
    "REG-29/Scope": "56a541512732",
    "REG-29/Not": "9d74de060409",
    "REG-30": "b83bf4b15095",
    "REG-30/Scope": "fbe1274ce689",
    "REG-30/Not": "18ad2184b368",
    "HRV-07": "b71d3e28bd54",
    "HRV-07/Scope": "bc82056e989c",
    "HRV-07/Not": "0131b7089218",
    "HRV-07/Why": "e6fe5c49d977",
    "IND-01": "143970ffd9b2",
    "IND-01/Scope": "f8426ed87fa1",
    "IND-01/Not": "3dc9e6eddf3a",
    "IND-01/Why": "7be0263b8c70",
    "IND-02": "2bc57924870b",
    "IND-02/Scope": "83b14a72d74c",
    "IND-02/Not": "54077cd23319",
    "IND-03": "580ea3421a9c",
    "IND-03/Scope": "e56209d6609c",
    "IND-03/Not": "5c7f1798a6b6",
    "IND-04": "fa2a44ef7e5f",
    "IND-04/Scope": "3e044002deb6",
    "IND-04/Not": "3fff514ad367",
    "IND-04/Why": "d5251ca9bd89",
    "IND-05": "09b62eb34918",
    "IND-05/Scope": "738691e063a9",
    "IND-05/Not": "f41a082ee374",
    "IND-06": "bc1423423985",
    "IND-06/Scope": "077b2371bad1",
    "IND-06/Not": "f7a192e4e5e5",
    "COLD-01": "2aa1ec96977a",
    "COLD-01/Scope": "11e9e44c3fd9",
    "COLD-01/Not": "c4ea2c71cbab",
    "COLD-01/Why": "1a350bbf89ab",
    "COLD-02": "09ba0e6966f1",
    "COLD-02/Scope": "a23420c693f1",
    "COLD-02/Not": "c92c1455a625",
    "COLD-03": "2e5ec0cb961f",
    "COLD-03/Scope": "a85bf8d6e552",
    "COLD-03/Not": "8b3f230eacad",
    "COLD-04": "8d5efc45444d",
    "COLD-04/Scope": "d36349bd2ae8",
    "COLD-04/Not": "631de712081f",
    "COLD-05": "52179f7bc6dc",
    "COLD-05/Scope": "170d2adb4903",
    "COLD-05/Not": "e00c7482487b",
    "COLD-06": "96a182878172",
    "COLD-06/Scope": "de4246e9b632",
    "COLD-06/Not": "81813d03fc52",
    "COLD-07": "d6ccee83c764",
    "COLD-07/Scope": "d0910925e539",
    "COLD-07/Not": "354940bcff33",
    "COLD-08": "35ed201b33ec",
    "COLD-08/Scope": "7a2cf835fd03",
    "COLD-08/Not": "69324f8c18ec",
    "COLD-08/Why": "cc72f1b6861a",
    "COLD-09": "da63e8170288",
    "COLD-09/Scope": "12082d72200b",
    "COLD-09/Not": "320ede161fd5",
    "COLD-10": "e028732b6ad5",
    "COLD-10/Scope": "b3ecc0e86cf8",
    "COLD-10/Not": "60798272e96a",
    "COLD-11": "d41b03400fab",
    "COLD-11/Scope": "ea92140a5a01",
    "COLD-11/Not": "74b2907b083f",
    "HRV-01": "dc21d8c23680",
    "HRV-01/Scope": "640778d74ec0",
    "HRV-01/Not": "07ee400f7bf5",
    "HRV-02": "d837b07acd64",
    "HRV-02/Scope": "b25bd623b90e",
    "HRV-02/Not": "3bcec8dd953a",
    "HRV-03": "3752dd3d1ea2",
    "HRV-03/Scope": "4f8aad2cd9bc",
    "HRV-03/Not": "a1bbf7952ae8",
    "HRV-04": "d5d6c81f9cd5",
    "HRV-04/Scope": "cba18117d197",
    "HRV-04/Not": "9ccf60368ad0",
    "HRV-04/Why": "c08fa08f3d96",
    "HRV-05": "8f24d1416b41",
    "HRV-05/Scope": "3a8238d1fdc2",
    "HRV-05/Not": "9ab93756282e",
    "HRV-05/Why": "bc80d17fe61c",
    "HRV-06": "42998459e682",
    "HRV-06/Scope": "a923114009e9",
    "HRV-06/Not": "e2979f14609c",
    "HRV-47": "86cf636eceea",
    "HRV-47/Scope": "533612db5bcb",
    "HRV-47/Not": "d6b4475f91a9",
    "LT1-01": "daf677421c99",
    "LT1-01/Scope": "3ff0ff05d128",
    "LT1-01/Not": "3482eebcbcf2",
    "LT1-01/Why": "d1c4db451447",
    "LT1-02": "5c90f1bd927f",
    "LT1-02/Scope": "10613b0ce4d0",
    "LT1-02/Not": "7f5a66c92437",
    "LT1-02/Why": "93fb500c42ae",
    "LT1-03": "6890f261cf1a",
    "LT1-03/Scope": "94cd9258389e",
    "LT1-03/Not": "4dacc32160b5",
    "LT1-04": "847b9b8ea963",
    "LT1-04/Scope": "86a30c3f6aa8",
    "LT1-04/Not": "aadfcd834386",
    "LT1-04/Why": "9df511463e67",
    "LT1-05": "e36de8857cf8",
    "LT1-05/Scope": "d28b10190c3e",
    "LT1-05/Not": "cf1751f9e0e1",
    "FTO-01": "fc7a13ed286a",
    "FTO-01/Scope": "ae49108550b2",
    "FTO-01/Not": "96e017485f90",
    "FTO-02": "0d47c0950804",
    "FTO-02/Scope": "fe98ba59e3ee",
    "FTO-02/Not": "2693f5717ab8",
    "FTO-03": "e667b5749fa7",
    "FTO-03/Scope": "597ad91244fc",
    "FTO-03/Not": "7a10278bc4c1",
    "FTO-04": "eb011d81f78e",
    "FTO-04/Scope": "fbd5c0184e45",
    "FTO-04/Not": "d3b42c036a28",
    "FTO-05": "05dbdb745129",
    "FTO-05/Scope": "f0ab6adae7c3",
    "FTO-05/Not": "c205e9c02929",
    "FTO-06": "40e9316a3c0b",
    "FTO-06/Scope": "d2e5898263c5",
    "FTO-06/Not": "ed4f30b88e01",
    "FTO-07": "d2b967e0cc83",
    "FTO-07/Scope": "95c2643753b9",
    "FTO-07/Not": "3e550f25862e",
    "DOC-15": "ed78409c94a8",
    "DOC-15/Scope": "111c35ff4fed",
    "DOC-15/Not": "a2f9bf38a000",
    "DOC-01": "c4aa7f4cbf34",
    "DOC-01/Scope": "b77303a824c8",
    "DOC-01/Not": "52a701272e8e",
    "DOC-05": "47433d9f0455",
    "DOC-05/Scope": "ec7f8f98c694",
    "DOC-05/Not": "6c2a12e5f0b3",
    "DOC-04": "2e63a0802198",
    "DOC-04/Scope": "b328c6a56ade",
    "DOC-04/Not": "94f2bc92f29f",
    "DOC-03": "e69eee603461",
    "DOC-03/Scope": "569c8d4bae00",
    "DOC-03/Not": "59aa351b16e5",
    "AUT-05": "17e4d487f044",
    "AUT-05/Scope": "d9bca8e40cc5",
    "AUT-05/Not": "5be81b2ebd82",
    "AUT-07": "ebce13317cd1",
    "AUT-07/Scope": "e18e2876886a",
    "AUT-07/Not": "eccc7827e7a9",
    "DEC-01": "78f847f2e066",
    "DEC-01/Scope": "f2fc14b76caf",
    "DEC-01/Not": "10f2456b7931",
    "DEC-01/Why": "70eafde10498",
    "DEC-02": "6dc0a75bd928",
    "DEC-02/Scope": "3a1264dc0348",
    "DEC-02/Not": "8033adec041c",
    "DOC-02": "f4e7cd00c971",
    "DOC-02/Scope": "e3efdc10fb3e",
    "DOC-02/Not": "6d039988c97f",
    "DOC-09": "b7744eea5ac4",
    "DOC-09/Scope": "bd2b75cd13bb",
    "DOC-09/Not": "e782b6707d87",
    "DOC-09/Why": "0c944e9072df",
    "DOC-10": "f63122195aa2",
    "DOC-10/Scope": "093f52b3e5ac",
    "DOC-10/Not": "eaa0beb8e462",
    "DOC-11": "ae401440a712",
    "DOC-11/Scope": "2b700bc6f321",
    "DOC-11/Not": "5f3befffef78",
    "DOC-12": "e200ad42a969",
    "DOC-12/Scope": "8995c71e9cf1",
    "DOC-12/Not": "7a774b2929ca",
    "DOC-13": "3d3abb601575",
    "DOC-13/Scope": "b1a11feb46b0",
    "DOC-13/Not": "1fdbe4f2039c",
    "DOC-14": "22b49e140c9f",
    "DOC-14/Scope": "02ae9d694c5a",
    "DOC-14/Not": "b97510931922",
    "DOC-16": "05a639ee3e66",
    "DOC-16/Scope": "962c3a5043ac",
    "DOC-16/Not": "0d7da4502675",
    "DOC-19": "86d3036b553b",
    "DOC-19/Scope": "7c619c8d34a6",
    "DOC-19/Not": "a980b23791c8",
    "DOC-20": "e1a48bb103c1",
    "DOC-20/Scope": "c19cc1db1320",
    "DOC-20/Not": "b2a2d76c0250",
    "DOC-21": "ae738433775e",
    "DOC-21/Scope": "17a0a45adc83",
    "DOC-21/Not": "bc100b458c08",
    "DOC-22": "a9b88ce051c2",
    "DOC-22/Scope": "6e75621350b4",
    "DOC-22/Not": "f9194ef837ce",
    "HRV-08": "2fcab8f59b81",
    "HRV-08/Scope": "cb792a73f36e",
    "HRV-08/Not": "508b74dc5b50",
    "HRV-09": "fce5807d9a9e",
    "HRV-09/Scope": "1af50204aaf7",
    "HRV-09/Not": "457e2480ba83",
    "HRV-10": "f5e16e6a1feb",
    "HRV-10/Scope": "5c461ec9a509",
    "HRV-10/Not": "a9e228a004ca",
    "HRV-11": "4cda1ac29734",
    "HRV-11/Scope": "e4ebed337951",
    "HRV-11/Not": "b388339d21e7",
    "HRV-11/Why": "04511d556bfa",
    "HRV-12": "cb0e88e71a72",
    "HRV-12/Scope": "9b268f73b3ac",
    "HRV-12/Not": "a432fb49b981",
    "HRV-12/Why": "84e7c4a378ec",
    "HRV-13": "1587a0f2dab4",
    "HRV-13/Scope": "d9225bff1dfe",
    "HRV-13/Not": "a99ee9ad8203",
    "HRV-14": "b1f8092f0d81",
    "HRV-14/Scope": "b7e6a19978f1",
    "HRV-14/Not": "4c884a528ba1",
    "HRV-15": "197ba0bdb526",
    "HRV-15/Scope": "1c82d1491242",
    "HRV-15/Not": "79c3b665344f",
    "HRV-15/Why": "ecc8f60e6f33",
    "HRV-16": "105d1d11ad4e",
    "HRV-16/Scope": "0f11528658f7",
    "HRV-16/Not": "24d82a07b19f",
    "HRV-17": "88bb9be3c36f",
    "HRV-17/Scope": "0f4deffe11ff",
    "HRV-17/Not": "10513b8e8cdb",
    "HRV-17/Why": "93518371ca7c",
    "HRV-18": "48f837c5c59a",
    "HRV-18/Scope": "a91e213ef979",
    "HRV-18/Not": "2e25adb93901",
    "HRV-19": "9cb70e93f3ea",
    "HRV-19/Scope": "b7e6a19978f1",
    "HRV-19/Not": "82e804a03a8a",
    "HRV-20": "5c605a08d865",
    "HRV-20/Scope": "b47bfb7803dc",
    "HRV-20/Not": "9eb30ac70c06",
    "HRV-21": "86933ecc3d8a",
    "HRV-21/Scope": "584aa166a1a9",
    "HRV-21/Not": "a8ce71b5a5e5",
    "HRV-22": "2f4e4a6d092a",
    "HRV-22/Scope": "6eb721eb2245",
    "HRV-22/Not": "6bebbac22003",
    "HRV-23": "c8fabb0283be",
    "HRV-23/Scope": "b5e91934c78b",
    "HRV-23/Not": "7b2fa82bfaf6",
    "HRV-24": "7df54ea30f6f",
    "HRV-24/Scope": "a0ed00bd1565",
    "HRV-24/Not": "f7996d444901",
    "HRV-25": "7cf6a2cfce15",
    "HRV-25/Scope": "d0b0fbb95fb5",
    "HRV-25/Not": "6897f9a6c3b9",
    "HRV-25/Why": "e7ab7840c5b4",
    "HRV-26": "edc7a1f579bc",
    "HRV-26/Scope": "4808f067d08f",
    "HRV-26/Not": "5b3163182454",
    "HRV-27": "420715f26fb3",
    "HRV-27/Scope": "6791a3dc0453",
    "HRV-27/Not": "d18e4bf38442",
    "HRV-28": "abc0fe416b22",
    "HRV-28/Scope": "013edac91507",
    "HRV-28/Not": "e9f0c6c337ff",
    "HRV-29": "b69b4ff0e08b",
    "HRV-29/Scope": "69c776e2aea3",
    "HRV-29/Not": "5bc4ee8d3614",
    "HRV-30": "02edecd95681",
    "HRV-30/Scope": "254f3f55dd17",
    "HRV-30/Not": "4373b4c4b11c",
    "HRV-30/Why": "c2c355809fa8",
    "HRV-31": "9242a3f18bdb",
    "HRV-31/Scope": "8e985d95c336",
    "HRV-31/Not": "65b82f565b0f",
    "HRV-31/Why": "ec24b7456bef",
    "HRV-32": "5d67c2a6ee9e",
    "HRV-32/Scope": "246e108db0e0",
    "HRV-32/Not": "ac986fd4efa9",
    "HRV-33": "91f52dbdba42",
    "HRV-33/Scope": "8a1d67e6db3e",
    "HRV-33/Not": "62214a85e346",
    "HRV-34": "971c7e5bcc9c",
    "HRV-34/Scope": "ba535ddd0900",
    "HRV-34/Not": "72cd6f3af173",
    "HRV-34/Why": "24f8bdaf17bf",
    "HRV-35": "f2439ab89ba7",
    "HRV-35/Scope": "d7d102e0e598",
    "HRV-35/Not": "ecd9249eb8ef",
    "HRV-36": "1565da20257e",
    "HRV-36/Scope": "3aee7fa66ea9",
    "HRV-36/Not": "6bb4ef6fd485",
    "HRV-37": "fcc9ac1ffa9e",
    "HRV-37/Scope": "7c2e5b35a6c0",
    "HRV-37/Not": "7afdffae4735",
    "HRV-38": "8a97e197e256",
    "HRV-38/Scope": "8e985d95c336",
    "HRV-38/Not": "95187aea6b5b",
    "HRV-38/Why": "665edf241bac",
    "HRV-39": "92c8d8704ff3",
    "HRV-39/Scope": "9796d5f55a79",
    "HRV-39/Not": "9ada46e17dc4",
    "HRV-40": "c0ff9587dc39",
    "HRV-40/Scope": "67b78d9e0bea",
    "HRV-40/Not": "43683699a046",
    "HRV-41": "b848653a8c27",
    "HRV-41/Scope": "448302cb0a67",
    "HRV-41/Not": "d5cecaf48594",
    "HRV-42": "01982d9f9255",
    "HRV-42/Scope": "bbe6e01cba0c",
    "HRV-42/Not": "4fd44ca5c335",
    "HRV-43": "b7c950e08cb8",
    "HRV-43/Scope": "959c9fc5908c",
    "HRV-43/Not": "ee6bf9140f60",
    "HRV-44": "9847af628916",
    "HRV-44/Scope": "06f2df4ae640",
    "HRV-44/Not": "19aad97ef788",
    "HRV-45": "b15be3261367",
    "HRV-45/Scope": "5c12d176ca6e",
    "HRV-45/Not": "6872a6ffbe63",
    "HRV-46": "af55a406cafc",
    "HRV-46/Scope": "747dfebc8343",
    "HRV-46/Not": "8767d3899a2d",
    "HRV-48": "f9e0bbb6c195",
    "HRV-48/Scope": "c148047396f9",
    "HRV-48/Not": "04e5fbb71f96",
    "HRV-49": "b79a5737bd9a",
    "HRV-49/Scope": "ea36033a4b28",
    "HRV-49/Not": "3e99d7926e9c",
    "HRV-50": "5cc0031e0d97",
    "HRV-50/Scope": "7d3b2838b6d0",
    "HRV-50/Not": "b613cb59f6a9",
    "HRV-51": "3b302230defa",
    "HRV-51/Scope": "cda7920fb2b4",
    "HRV-51/Not": "c9c686d3bfca",
    "HRV-52": "c41fb808976f",
    "HRV-52/Scope": "712c3a5b3377",
    "HRV-52/Not": "ccd53b6a8d0f",
    "HRV-52/Why": "1dfc2a83afff",
    "HRV-53": "263ff1823729",
    "HRV-53/Scope": "67b78592f736",
    "HRV-53/Not": "219a0590b85f",
    "HRV-54": "f6154aa9f41d",
    "HRV-54/Scope": "0c1cb0374625",
    "HRV-54/Not": "d95932210d23",
    "HRV-55": "328c34e306f7",
    "HRV-55/Scope": "8ea24cd9bcd9",
    "HRV-55/Not": "496b3945cdb0",
    "HRV-56": "7e8f917f2b37",
    "HRV-56/Scope": "584aa166a1a9",
    "HRV-56/Not": "06ba77dbd4a4",
    "HRV-57": "d62f48614c74",
    "HRV-57/Scope": "a8ce153fa251",
    "HRV-57/Not": "0ab5814f6905",
    "HRV-58": "c830847e47df",
    "HRV-58/Scope": "bccc71c96dc8",
    "HRV-58/Not": "b0be3698badc",
    "HRV-59": "049fd70fc221",
    "HRV-59/Scope": "a0ed00bd1565",
    "HRV-59/Not": "f6451310aa87",
    "HRV-60": "ad22e44ca6e8",
    "HRV-60/Scope": "b8ee84c844a5",
    "HRV-60/Not": "cc1f8d717d45",
    "HRV-61": "54cb03c5e1c8",
    "HRV-61/Scope": "cb8660ce5b1b",
    "HRV-61/Not": "d5f6e6854076",
    "HRV-62": "4b4b26b21a52",
    "HRV-62/Scope": "69c776e2aea3",
    "HRV-62/Not": "d4a90baef4bd",
    "HRV-63": "ccca4c86832f",
    "HRV-63/Scope": "09646ecff6e8",
    "HRV-63/Not": "a71995ff572a",
    "HRV-64": "6e63e89349ae",
    "HRV-64/Scope": "246e108db0e0",
    "HRV-64/Not": "8a9b1398f36c",
    "HRV-65": "80763860aa83",
    "HRV-65/Scope": "5b6c48533856",
    "HRV-65/Not": "f3ffb4b6791d",
    "HRV-66": "a033eed1f639",
    "HRV-66/Scope": "5b6c48533856",
    "HRV-66/Not": "3f4c6d8a4c7d",
    "HRV-66/Why": "acfa1c75bab7",
    "HRV-67": "9d9181f153e5",
    "HRV-67/Scope": "1a5256081081",
    "HRV-67/Not": "d58427c04a32",
    "HRV-68": "d3b252a72eb1",
    "HRV-68/Scope": "43da8f2e5709",
    "HRV-68/Not": "963c6a31d317",
    "HRV-69": "3de86501af5a",
    "HRV-69/Scope": "2eb60469c115",
    "HRV-69/Not": "37534c1fbe7d",
    "HRV-70": "eb981c874921",
    "HRV-70/Scope": "6a94b9fc322d",
    "HRV-70/Not": "d5fd042d7605",
    "HRV-71": "2c039c546328",
    "HRV-71/Scope": "6a94b9fc322d",
    "HRV-71/Not": "3d91b8ee5017",
    "HRV-72": "32a3baae0984",
    "HRV-72/Scope": "9d939da807cf",
    "HRV-72/Not": "22880ce38971",
    "HRV-73": "539d8fc58d2a",
    "HRV-73/Scope": "d7d102e0e598",
    "HRV-73/Not": "e93f8cc0b1fe",
    "HRV-74": "f5f45a0eca6d",
    "HRV-74/Scope": "cb3a846ee471",
    "HRV-74/Not": "d1447ec705ea",
    "HRV-75": "9f40075b704f",
    "HRV-75/Scope": "1f064dead565",
    "HRV-75/Not": "a00bd8861bbc",
    "HRV-76": "f8e9908fe968",
    "HRV-76/Scope": "e11924f502db",
    "HRV-76/Not": "9dabe0f5d184",
    "HRV-76/Why": "f85649f41eb5",
    "HRV-77": "326bd25aebd2",
    "HRV-77/Scope": "9796d5f55a79",
    "HRV-77/Not": "8d13d15dadd3",
    "HRV-78": "ef2491f7b96b",
    "HRV-78/Scope": "a2c2bc42caca",
    "HRV-78/Not": "7ccd2a7b28c0",
    "HRV-79": "b76bae6c9a21",
    "HRV-79/Scope": "c709c9d17d24",
    "HRV-79/Not": "177fd24fc7bb",
    "HRV-80": "3162051e2692",
    "HRV-80/Scope": "99f75a3f5771",
    "HRV-80/Not": "96d1c2d9b7ec",
    "HRV-81": "56f22369f36a",
    "HRV-81/Scope": "ecddcb49a653",
    "HRV-81/Not": "02c3b5a00246",
    "HRV-82": "34365140e3e7",
    "HRV-82/Scope": "773b34318f83",
    "HRV-82/Not": "c7e2bf759228",
    "HRV-83": "777c2c9ef7e2",
    "HRV-83/Scope": "06f2df4ae640",
    "HRV-83/Not": "4e6ddbefe243",
    "HRV-84": "2820ebf76eff",
    "HRV-84/Scope": "8b9ec18595a4",
    "HRV-84/Not": "62a2569e03f2",
    "GATE-01": "b9aebd30f317",
    "GATE-01/Scope": "90a6f19e5129",
    "GATE-01/Not": "58c2f5e862e8",
    "GATE-01/Why": "df1c3445f8bb",
    "GATE-02": "d9a1f3ebb626",
    "GATE-02/Scope": "cd72a08bef98",
    "GATE-02/Not": "6a32203f7bb3",
    "GATE-02/Why": "50848c756ce0",
    "GATE-03": "95dfca4ecfa1",
    "GATE-03/Scope": "b80c7771bce6",
    "GATE-03/Not": "a0e77beb7c9c",
    "GATE-03/Why": "3ffd87da959f",
    "GATE-04": "8939c1d99611",
    "GATE-04/Scope": "968d494550ac",
    "GATE-04/Not": "beca759b790a",
    "GATE-04/Why": "d481cc3137f8",
    "GATE-05": "a0a07651a7e6",
    "GATE-05/Scope": "3ede6a012284",
    "GATE-05/Not": "fcd1615cf8ab",
    "GATE-06": "a1951004ee84",
    "GATE-06/Scope": "3ba7592db249",
    "GATE-06/Not": "34e54ca7dd74",
    "GATE-07": "9134cff02b07",
    "GATE-07/Scope": "ea62b21ca2eb",
    "GATE-07/Not": "e56ef81fc2ca",
    "GATE-08": "a28e51347143",
    "GATE-08/Scope": "15cafb59e896",
    "GATE-08/Not": "123250d56e08",
    "FIG-01": "3f61a3eedce2",
    "FIG-01/Scope": "7be1a202f5af",
    "FIG-01/Not": "c07139d9bdef",
    "FIG-02": "707d0e6e1a56",
    "FIG-02/Scope": "b5332a15448b",
    "FIG-02/Not": "9af27532a373",
    "FIG-02/Why": "bd5b399ea058",
    "FIG-03": "81238a4d905e",
    "FIG-03/Scope": "e02700542148",
    "FIG-03/Not": "68de4c4b9ef1",
    "FIG-03/Why": "555c7cec989a",
    "FIG-04": "fd776b7ec6b5",
    "FIG-04/Scope": "cef6c1f70971",
    "FIG-04/Not": "4d8aafecde1b",
    "FIG-05": "55f720f7b0f3",
    "FIG-05/Scope": "52369bbfee66",
    "FIG-05/Not": "f20b7b01c20e",
    "FIG-05/Why": "0863d5e9be03",
    "FIG-06": "63b8d622fefe",
    "FIG-06/Scope": "fa2851738dcf",
    "FIG-06/Not": "872fc4ae82d9",
    "FIG-07": "aed091e72dd3",
    "FIG-07/Scope": "128029b3c378",
    "FIG-07/Not": "03755cf935d9",
    "FIG-08": "b05cff6c1322",
    "FIG-08/Scope": "681daeb806f7",
    "FIG-08/Not": "098713746486",
    "FIG-09": "3525c943d280",
    "FIG-09/Scope": "badcad4990be",
    "FIG-09/Not": "0145199ca1d4",
    "FIG-10": "eb883c3f4c5f",
    "FIG-10/Scope": "503a4cb5b791",
    "FIG-10/Not": "4d8aafecde1b",
    "FIG-11": "62353849ef46",
    "FIG-11/Scope": "a86691c2e599",
    "FIG-11/Not": "27bbfda3defc",
}

#: Iteration 5 (the principle extending the user's M3 ruling): every line of the four research/00 files
#: that carries meaning or structure is bound, so an edit to it needs a visible edit to a frozen literal.
#: Each value below is ``_keyed_digest`` (a ``_cell_digest`` per line: whitespace collapsed, first 12 hex),
#: derived 2026-09-26 at 12348f1 by ``derived_literals()``, which keyed every line and printed its counts;
#: each error names the line and prints the new digest, and ``frozen_literals()`` prints every literal.
#:
#: ``GLOSSARY_SHA256`` (M1): each Glossary definition, keyed by T-NN (33 lines, 33 keys). The T-NN lines
#: are normative IS definitions outside every rule block, so neither the review nor the AC9 proxy read
#: them: T-13's 14 days became 21, T-03's ``>=`` became ``>`` and T-05's HRV Status became a tier, with the
#: suite green. A Glossary change needs a fresh critic. Asserted both ways by ``glossary_digest_errors``.
GLOSSARY_SHA256 = {
    "T-01": "c0c28ddfd4e1",
    "T-02": "9457159e89a3",
    "T-03": "f8e6c46b72c2",
    "T-04": "7825d1c7b3df",
    "T-05": "52637cb6fa06",
    "T-06": "732253a9416f",
    "T-07": "5d646686330b",
    "T-08": "5f4c404a3abf",
    "T-09": "d8ddf1465b2e",
    "T-10": "944baf4f3d04",
    "T-11": "c58b107621a1",
    "T-12": "bc179232d905",
    "T-13": "8f37bec5b094",
    "T-14": "e76fa1508d13",
    "T-15": "8b3b883ad54d",
    "T-16": "3756f4f2a810",
    "T-17": "edbe1b565746",
    "T-18": "44bee810629e",
    "T-19": "1b0e5c3240ea",
    "T-20": "34384310d4b6",
    "T-21": "913cff1fdcd5",
    "T-22": "65e2694c0221",
    "T-23": "7e39c10f35dd",
    "T-24": "0ed2c84fdcf9",
    "T-25": "10f5b0d66534",
    "T-26": "325dd542e443",
    "T-27": "75ec58a85e8e",
    "T-28": "0bac3b383cba",
    "T-29": "e3821e8b5560",
    "T-30": "5b9ce1060c08",
    "T-31": "86f28ce112a1",
    "T-32": "0509a5fb0e65",
    "T-33": "1a3153ddf525",
}

#: ``PINNED_SHA256`` (S1): each rule's Pinned lines, keyed ``<ID>/Pinned`` (246 rules, 249 lines).
#: ``pinned_errors`` only finds the node, so PRIN-26's ``Pinned: none (F009)`` retargeted to an unrelated
#: existing test stayed green. Three rules carry two Pinned lines (PRIN-15, FIG-01, FIG-02); the key stays
#: one per rule and its value is the two lines' digests in order, joined by ``.``, so a mismatch still
#: names its line. Asserted both ways by ``pinned_digest_errors``.
PINNED_SHA256 = {
    "PRIN-01/Pinned": "6f6a2e0e1e2d",
    "PRIN-02/Pinned": "6f6a2e0e1e2d",
    "PRIN-03/Pinned": "6f6a2e0e1e2d",
    "ARB-01/Pinned": "6f6a2e0e1e2d",
    "ARB-02/Pinned": "6f6a2e0e1e2d",
    "ARB-03/Pinned": "6f6a2e0e1e2d",
    "ARB-04/Pinned": "6f6a2e0e1e2d",
    "ARB-05/Pinned": "6f6a2e0e1e2d",
    "ARB-06/Pinned": "6f6a2e0e1e2d",
    "ARB-07/Pinned": "6f6a2e0e1e2d",
    "PRIN-04/Pinned": "6f6a2e0e1e2d",
    "PRIN-05/Pinned": "6f6a2e0e1e2d",
    "PRIN-06/Pinned": "6f6a2e0e1e2d",
    "PRIN-17/Pinned": "6f6a2e0e1e2d",
    "PRIN-18/Pinned": "6f6a2e0e1e2d",
    "PRIN-07/Pinned": "6f6a2e0e1e2d",
    "PRIN-08/Pinned": "6f6a2e0e1e2d",
    "PRIN-09/Pinned": "6f6a2e0e1e2d",
    "PRIN-10/Pinned": "6f6a2e0e1e2d",
    "PRIN-19/Pinned": "6f6a2e0e1e2d",
    "PRIN-20/Pinned": "6f6a2e0e1e2d",
    "PRIN-21/Pinned": "6f6a2e0e1e2d",
    "PRIN-11/Pinned": "6f6a2e0e1e2d",
    "PRIN-12/Pinned": "6b459de01b1d",
    "PRIN-22/Pinned": "6f6a2e0e1e2d",
    "PRIN-23/Pinned": "6f6a2e0e1e2d",
    "PRIN-24/Pinned": "6f6a2e0e1e2d",
    "PRIN-13/Pinned": "6f6a2e0e1e2d",
    "PRIN-14/Pinned": "6f6a2e0e1e2d",
    "PRIN-15/Pinned": "b4566a98f730.e89bfe873864",
    "PRIN-25/Pinned": "b4566a98f730",
    "PRIN-26/Pinned": "e89bfe873864",
    "AUT-01/Pinned": "6f6a2e0e1e2d",
    "AUT-02/Pinned": "6f6a2e0e1e2d",
    "AUT-03/Pinned": "6f6a2e0e1e2d",
    "AUT-04/Pinned": "6f6a2e0e1e2d",
    "AUT-06/Pinned": "6f6a2e0e1e2d",
    "AUT-08/Pinned": "6f6a2e0e1e2d",
    "GOAL-01/Pinned": "6f6a2e0e1e2d",
    "GOAL-02/Pinned": "6f6a2e0e1e2d",
    "GOAL-03/Pinned": "6f6a2e0e1e2d",
    "GOAL-04/Pinned": "6f6a2e0e1e2d",
    "GOAL-05/Pinned": "6f6a2e0e1e2d",
    "GOAL-06/Pinned": "6f6a2e0e1e2d",
    "ARCH-00/Pinned": "6f6a2e0e1e2d",
    "ARCH-01/Pinned": "6f6a2e0e1e2d",
    "ARCH-02/Pinned": "6f6a2e0e1e2d",
    "ARCH-03/Pinned": "6f6a2e0e1e2d",
    "ARCH-04/Pinned": "6f6a2e0e1e2d",
    "ARCH-05/Pinned": "6f6a2e0e1e2d",
    "ARCH-06/Pinned": "6f6a2e0e1e2d",
    "ARCH-07/Pinned": "6f6a2e0e1e2d",
    "ARCH-08/Pinned": "6f6a2e0e1e2d",
    "ARCH-09/Pinned": "6f6a2e0e1e2d",
    "ARCH-10/Pinned": "6f6a2e0e1e2d",
    "ARCH-11/Pinned": "6f6a2e0e1e2d",
    "ARCH-12/Pinned": "6f6a2e0e1e2d",
    "ARCH-13/Pinned": "6f6a2e0e1e2d",
    "DOC-06/Pinned": "6f6a2e0e1e2d",
    "DOC-07/Pinned": "6f6a2e0e1e2d",
    "DOC-08/Pinned": "6f6a2e0e1e2d",
    "DOC-17/Pinned": "6f6a2e0e1e2d",
    "DOC-18/Pinned": "6f6a2e0e1e2d",
    "REG-01/Pinned": "6f6a2e0e1e2d",
    "REG-02/Pinned": "6f6a2e0e1e2d",
    "REG-03/Pinned": "6f6a2e0e1e2d",
    "REG-04/Pinned": "6f6a2e0e1e2d",
    "REG-05/Pinned": "6f6a2e0e1e2d",
    "REG-06/Pinned": "6f6a2e0e1e2d",
    "REG-07/Pinned": "6f6a2e0e1e2d",
    "REG-08/Pinned": "6f6a2e0e1e2d",
    "REG-09/Pinned": "6f6a2e0e1e2d",
    "REG-10/Pinned": "6f6a2e0e1e2d",
    "REG-11/Pinned": "6f6a2e0e1e2d",
    "REG-12/Pinned": "6f6a2e0e1e2d",
    "REG-13/Pinned": "6f6a2e0e1e2d",
    "REG-14/Pinned": "6f6a2e0e1e2d",
    "REG-15/Pinned": "6f6a2e0e1e2d",
    "REG-16/Pinned": "6f6a2e0e1e2d",
    "REG-17/Pinned": "6f6a2e0e1e2d",
    "REG-18/Pinned": "6f6a2e0e1e2d",
    "REG-19/Pinned": "6f6a2e0e1e2d",
    "REG-20/Pinned": "6f6a2e0e1e2d",
    "REG-21/Pinned": "6f6a2e0e1e2d",
    "REG-22/Pinned": "6f6a2e0e1e2d",
    "REG-23/Pinned": "6f6a2e0e1e2d",
    "REG-24/Pinned": "6f6a2e0e1e2d",
    "REG-25/Pinned": "6f6a2e0e1e2d",
    "REG-26/Pinned": "6f6a2e0e1e2d",
    "REG-27/Pinned": "6f6a2e0e1e2d",
    "REG-28/Pinned": "6f6a2e0e1e2d",
    "REG-29/Pinned": "6f6a2e0e1e2d",
    "REG-30/Pinned": "6f6a2e0e1e2d",
    "HRV-07/Pinned": "4094322b60da",
    "IND-01/Pinned": "6f6a2e0e1e2d",
    "IND-02/Pinned": "6f6a2e0e1e2d",
    "IND-03/Pinned": "6f6a2e0e1e2d",
    "IND-04/Pinned": "6f6a2e0e1e2d",
    "IND-05/Pinned": "6f6a2e0e1e2d",
    "IND-06/Pinned": "6f6a2e0e1e2d",
    "COLD-01/Pinned": "6f6a2e0e1e2d",
    "COLD-02/Pinned": "6f6a2e0e1e2d",
    "COLD-03/Pinned": "6f6a2e0e1e2d",
    "COLD-04/Pinned": "6f6a2e0e1e2d",
    "COLD-05/Pinned": "6f6a2e0e1e2d",
    "COLD-06/Pinned": "6f6a2e0e1e2d",
    "COLD-07/Pinned": "6f6a2e0e1e2d",
    "COLD-08/Pinned": "6f6a2e0e1e2d",
    "COLD-09/Pinned": "6f6a2e0e1e2d",
    "COLD-10/Pinned": "6f6a2e0e1e2d",
    "COLD-11/Pinned": "6f6a2e0e1e2d",
    "HRV-01/Pinned": "6f6a2e0e1e2d",
    "HRV-02/Pinned": "6f6a2e0e1e2d",
    "HRV-03/Pinned": "6f6a2e0e1e2d",
    "HRV-04/Pinned": "6f6a2e0e1e2d",
    "HRV-05/Pinned": "6f6a2e0e1e2d",
    "HRV-06/Pinned": "6f6a2e0e1e2d",
    "HRV-47/Pinned": "6f6a2e0e1e2d",
    "LT1-01/Pinned": "6f6a2e0e1e2d",
    "LT1-02/Pinned": "6f6a2e0e1e2d",
    "LT1-03/Pinned": "6f6a2e0e1e2d",
    "LT1-04/Pinned": "6f6a2e0e1e2d",
    "LT1-05/Pinned": "6f6a2e0e1e2d",
    "FTO-01/Pinned": "6f6a2e0e1e2d",
    "FTO-02/Pinned": "6f6a2e0e1e2d",
    "FTO-03/Pinned": "6f6a2e0e1e2d",
    "FTO-04/Pinned": "6f6a2e0e1e2d",
    "FTO-05/Pinned": "6f6a2e0e1e2d",
    "FTO-06/Pinned": "6f6a2e0e1e2d",
    "FTO-07/Pinned": "6f6a2e0e1e2d",
    "DOC-15/Pinned": "6f6a2e0e1e2d",
    "DOC-01/Pinned": "6f6a2e0e1e2d",
    "DOC-05/Pinned": "6f6a2e0e1e2d",
    "DOC-04/Pinned": "6f6a2e0e1e2d",
    "DOC-03/Pinned": "6f6a2e0e1e2d",
    "AUT-05/Pinned": "6f6a2e0e1e2d",
    "AUT-07/Pinned": "6f6a2e0e1e2d",
    "DEC-01/Pinned": "f94658762ab1",
    "DEC-02/Pinned": "6f6a2e0e1e2d",
    "DOC-02/Pinned": "6f6a2e0e1e2d",
    "DOC-09/Pinned": "6f6a2e0e1e2d",
    "DOC-10/Pinned": "6f6a2e0e1e2d",
    "DOC-11/Pinned": "6f6a2e0e1e2d",
    "DOC-12/Pinned": "6f6a2e0e1e2d",
    "DOC-13/Pinned": "6f6a2e0e1e2d",
    "DOC-14/Pinned": "6f6a2e0e1e2d",
    "DOC-16/Pinned": "6f6a2e0e1e2d",
    "DOC-19/Pinned": "6f6a2e0e1e2d",
    "DOC-20/Pinned": "6f6a2e0e1e2d",
    "DOC-21/Pinned": "6f6a2e0e1e2d",
    "DOC-22/Pinned": "6f6a2e0e1e2d",
    "HRV-08/Pinned": "6f6a2e0e1e2d",
    "HRV-09/Pinned": "6f6a2e0e1e2d",
    "HRV-10/Pinned": "6f6a2e0e1e2d",
    "HRV-11/Pinned": "6f6a2e0e1e2d",
    "HRV-12/Pinned": "6f6a2e0e1e2d",
    "HRV-13/Pinned": "6f6a2e0e1e2d",
    "HRV-14/Pinned": "92597a74acb6",
    "HRV-15/Pinned": "51606625db95",
    "HRV-16/Pinned": "6f6a2e0e1e2d",
    "HRV-17/Pinned": "6b459de01b1d",
    "HRV-18/Pinned": "6f6a2e0e1e2d",
    "HRV-19/Pinned": "068d30b2b1f3",
    "HRV-20/Pinned": "6f6a2e0e1e2d",
    "HRV-21/Pinned": "6f6a2e0e1e2d",
    "HRV-22/Pinned": "477b63dda640",
    "HRV-23/Pinned": "6f6a2e0e1e2d",
    "HRV-24/Pinned": "6f6a2e0e1e2d",
    "HRV-25/Pinned": "e89bfe873864",
    "HRV-26/Pinned": "c66a808d4242",
    "HRV-27/Pinned": "aa7abdc0d2c8",
    "HRV-28/Pinned": "55df02f3086b",
    "HRV-29/Pinned": "6f6a2e0e1e2d",
    "HRV-30/Pinned": "54a1a8becdd9",
    "HRV-31/Pinned": "db2eecae9291",
    "HRV-32/Pinned": "6f6a2e0e1e2d",
    "HRV-33/Pinned": "6f6a2e0e1e2d",
    "HRV-34/Pinned": "6f6a2e0e1e2d",
    "HRV-35/Pinned": "6f6a2e0e1e2d",
    "HRV-36/Pinned": "6f6a2e0e1e2d",
    "HRV-37/Pinned": "94532aae8b40",
    "HRV-38/Pinned": "57b75a412632",
    "HRV-39/Pinned": "6f6a2e0e1e2d",
    "HRV-40/Pinned": "6f6a2e0e1e2d",
    "HRV-41/Pinned": "ca11309c6477",
    "HRV-42/Pinned": "6f6a2e0e1e2d",
    "HRV-43/Pinned": "6f6a2e0e1e2d",
    "HRV-44/Pinned": "99e9bf6d0bfa",
    "HRV-45/Pinned": "6f6a2e0e1e2d",
    "HRV-46/Pinned": "6f6a2e0e1e2d",
    "HRV-48/Pinned": "6f6a2e0e1e2d",
    "HRV-49/Pinned": "6f6a2e0e1e2d",
    "HRV-50/Pinned": "6f6a2e0e1e2d",
    "HRV-51/Pinned": "b21037c95648",
    "HRV-52/Pinned": "6f6a2e0e1e2d",
    "HRV-53/Pinned": "6f6a2e0e1e2d",
    "HRV-54/Pinned": "6f6a2e0e1e2d",
    "HRV-55/Pinned": "6f6a2e0e1e2d",
    "HRV-56/Pinned": "6f6a2e0e1e2d",
    "HRV-57/Pinned": "2abe3c626185",
    "HRV-58/Pinned": "6f6a2e0e1e2d",
    "HRV-59/Pinned": "6f6a2e0e1e2d",
    "HRV-60/Pinned": "debeaefe0c53",
    "HRV-61/Pinned": "6f6a2e0e1e2d",
    "HRV-62/Pinned": "6f6a2e0e1e2d",
    "HRV-63/Pinned": "6f6a2e0e1e2d",
    "HRV-64/Pinned": "6f6a2e0e1e2d",
    "HRV-65/Pinned": "6f6a2e0e1e2d",
    "HRV-66/Pinned": "6f6a2e0e1e2d",
    "HRV-67/Pinned": "6f6a2e0e1e2d",
    "HRV-68/Pinned": "6f6a2e0e1e2d",
    "HRV-69/Pinned": "6f6a2e0e1e2d",
    "HRV-70/Pinned": "6f6a2e0e1e2d",
    "HRV-71/Pinned": "6f6a2e0e1e2d",
    "HRV-72/Pinned": "6f6a2e0e1e2d",
    "HRV-73/Pinned": "6f6a2e0e1e2d",
    "HRV-74/Pinned": "6f6a2e0e1e2d",
    "HRV-75/Pinned": "6f6a2e0e1e2d",
    "HRV-76/Pinned": "ba5ce5e828ff",
    "HRV-77/Pinned": "6f6a2e0e1e2d",
    "HRV-78/Pinned": "d006ea69acf9",
    "HRV-79/Pinned": "6f6a2e0e1e2d",
    "HRV-80/Pinned": "6f6a2e0e1e2d",
    "HRV-81/Pinned": "6f6a2e0e1e2d",
    "HRV-82/Pinned": "6f6a2e0e1e2d",
    "HRV-83/Pinned": "6f6a2e0e1e2d",
    "HRV-84/Pinned": "6f6a2e0e1e2d",
    "GATE-01/Pinned": "c372ad0b2a6e",
    "GATE-02/Pinned": "b1d64ea186a2",
    "GATE-03/Pinned": "6f6a2e0e1e2d",
    "GATE-04/Pinned": "c372ad0b2a6e",
    "GATE-05/Pinned": "b4566a98f730",
    "GATE-06/Pinned": "b1d64ea186a2",
    "GATE-07/Pinned": "6f6a2e0e1e2d",
    "GATE-08/Pinned": "6f6a2e0e1e2d",
    "FIG-01/Pinned": "e8d6f5f9382e.9e2fafe435a8",
    "FIG-02/Pinned": "b52b0ae17284.6e87b2c1e576",
    "FIG-03/Pinned": "6f6a2e0e1e2d",
    "FIG-04/Pinned": "6f6a2e0e1e2d",
    "FIG-05/Pinned": "6f6a2e0e1e2d",
    "FIG-06/Pinned": "04591f67f2bc",
    "FIG-07/Pinned": "b52b0ae17284",
    "FIG-08/Pinned": "690b1f78d148",
    "FIG-09/Pinned": "6f6a2e0e1e2d",
    "FIG-10/Pinned": "6f6a2e0e1e2d",
    "FIG-11/Pinned": "6f6a2e0e1e2d",
}

#: ``RESEARCH_STRUCTURE`` (S1): research/00's every heading, Glossary T-NN and rule ID, in document order
#: (303 entries: 24 headings, 33 terms, 246 rules). The heading check reads the headings alone and the
#: rule checks read blocks alone, so moving ``### 1.2`` above PRIN-02 stayed green. Asserted by
#: ``structure_errors``, which names the first position that differs.
RESEARCH_STRUCTURE = (
    "# Design Decisions & Governing Principles",
    "## Glossary",
    "T-01", "T-02", "T-03", "T-04", "T-05", "T-06", "T-07", "T-08", "T-09", "T-10", "T-11", "T-12", "T-13",
    "T-14", "T-15", "T-16", "T-17", "T-18", "T-19", "T-20", "T-21", "T-22", "T-23", "T-24", "T-25", "T-26",
    "T-27", "T-28", "T-29", "T-30", "T-31", "T-32", "T-33",
    "## Part 1 — Principle hierarchy and tie-breakers",
    "### 1.1 The supreme objective",
    "PRIN-01", "PRIN-02",
    "### 1.2 The arbitration ladder",
    "PRIN-03", "ARB-01", "ARB-02", "ARB-03", "ARB-04", "ARB-05", "ARB-06", "ARB-07",
    "### 1.3 The meta-rule",
    "PRIN-04",
    "### 1.4 Conflict resolution between subjective and objective signals",
    "PRIN-05", "PRIN-06", "PRIN-17", "PRIN-18",
    "### 1.5 Raw over derived",
    "PRIN-07", "PRIN-08", "PRIN-09", "PRIN-10", "PRIN-19", "PRIN-20", "PRIN-21",
    "### 1.6 Transparency and explainability",
    "PRIN-11", "PRIN-12", "PRIN-22", "PRIN-23", "PRIN-24",
    "### 1.7 Down-regulate freely, up-regulate cautiously",
    "PRIN-13", "PRIN-14", "PRIN-15", "PRIN-25", "PRIN-26",
    "### 1.8 Autonomy posture",
    "AUT-01", "AUT-02", "AUT-03", "AUT-04", "AUT-06", "AUT-08",
    "### 1.9 System ownership: plan versus goal",
    "GOAL-01", "GOAL-02", "GOAL-03", "GOAL-04", "GOAL-05", "GOAL-06",
    "## Part 2 — Load-bearing findings",
    "ARCH-00", "ARCH-01", "ARCH-02", "ARCH-03", "ARCH-04", "ARCH-05", "ARCH-06", "ARCH-07", "ARCH-08",
    "ARCH-09", "ARCH-10", "ARCH-11", "ARCH-12", "ARCH-13",
    "## Part 3 — Decision register",
    "DOC-06", "DOC-07", "DOC-08", "DOC-17", "DOC-18", "REG-01", "REG-02", "REG-03", "REG-04", "REG-05",
    "REG-06", "REG-07", "REG-08", "REG-09", "REG-10", "REG-11", "REG-12", "REG-13", "REG-14", "REG-15",
    "REG-16", "REG-17", "REG-18", "REG-19", "REG-20", "REG-21", "REG-22", "REG-23", "REG-24", "REG-25",
    "REG-26", "REG-27", "REG-28", "REG-29", "REG-30", "HRV-07",
    "### 3.1 Individualization — the governing rule (resolved)",
    "IND-01", "IND-02", "IND-03", "IND-04", "IND-05", "IND-06",
    "### 3.2 Cold-start — establishing day-one state (resolved, amends `research/05` §3.2)",
    "COLD-01", "COLD-02", "COLD-03", "COLD-04", "COLD-05", "COLD-06", "COLD-07", "COLD-08", "COLD-09",
    "COLD-10", "COLD-11",
    "### 3.3 Resting-HRV source tiering (resolved, amends the data-quality-gating and HRV-gate register rows)",
    "HRV-01", "HRV-02", "HRV-03", "HRV-04", "HRV-05", "HRV-06", "HRV-47",
    "### 3.4 Aerobic-threshold (LT1) determination — recommended path (open, deferred to a future determinant)",
    "LT1-01", "LT1-02", "LT1-03", "LT1-04", "LT1-05",
    "## Part 4 — Design and freedom-to-operate guardrails",
    "FTO-01", "FTO-02", "FTO-03", "FTO-04", "FTO-05", "FTO-06", "FTO-07",
    "## Part 5 — Document map, authority, and decision records",
    "DOC-15",
    "### 5.1 This document's authority",
    "DOC-01", "DOC-05",
    "### 5.2 The mechanism research docs (the evidence this document points to)",
    "DOC-04",
    "### 5.3 Decision records (`decisions/`)",
    "DOC-03", "AUT-05", "AUT-07", "DEC-01", "DEC-02",
    "### 5.4 Reconciliations and amendments",
    "DOC-02", "DOC-09", "DOC-10", "DOC-11", "DOC-12", "DOC-13", "DOC-14", "DOC-16", "DOC-19", "DOC-20",
    "DOC-21", "DOC-22", "HRV-08", "HRV-09", "HRV-10", "HRV-11", "HRV-12", "HRV-13", "HRV-14", "HRV-15",
    "HRV-16", "HRV-17", "HRV-18", "HRV-19", "HRV-20", "HRV-21", "HRV-22", "HRV-23", "HRV-24", "HRV-25",
    "HRV-26", "HRV-27", "HRV-28", "HRV-29", "HRV-30", "HRV-31", "HRV-32", "HRV-33", "HRV-34", "HRV-35",
    "HRV-36", "HRV-37", "HRV-38", "HRV-39", "HRV-40", "HRV-41", "HRV-42", "HRV-43", "HRV-44", "HRV-45",
    "HRV-46", "HRV-48", "HRV-49", "HRV-50", "HRV-51", "HRV-52", "HRV-53", "HRV-54", "HRV-55", "HRV-56",
    "HRV-57", "HRV-58", "HRV-59", "HRV-60", "HRV-61", "HRV-62", "HRV-63", "HRV-64", "HRV-65", "HRV-66",
    "HRV-67", "HRV-68", "HRV-69", "HRV-70", "HRV-71", "HRV-72", "HRV-73", "HRV-74", "HRV-75", "HRV-76",
    "HRV-77", "HRV-78", "HRV-79", "HRV-80", "HRV-81", "HRV-82", "HRV-83", "HRV-84", "GATE-01", "GATE-02",
    "GATE-03", "GATE-04", "GATE-05", "GATE-06", "GATE-07", "GATE-08", "FIG-01", "FIG-02", "FIG-03", "FIG-04",
    "FIG-05", "FIG-06", "FIG-07", "FIG-08", "FIG-09", "FIG-10", "FIG-11",
)

#: ``HISTORY_SHA256`` (S2): every non-blank line of 00-history.md (44: the title, the 41 entries keyed by
#: H-NN, ``## Retired IDs`` and its one line keyed ``PRIN-16 retired``). ``history_errors`` checks the
#: format and the arrows only resolve, so H-06's prose rewritten, or GOAL-06 dropped from its arrow list,
#: stayed green. Asserted both ways by ``history_digest_errors``.
HISTORY_SHA256 = {
    "# research/00 history": "f063580c1056",
    "H-01": "a30b075ea608",
    "H-02": "9141cff19448",
    "H-03": "9e30329874b8",
    "H-04": "dd1558bab4bc",
    "H-05": "7ce9255e0700",
    "H-06": "e9f75c6771e7",
    "H-07": "24bc9091953f",
    "H-08": "c50df375e8a2",
    "H-09": "aee95a74af2f",
    "H-10": "8f4a2c603818",
    "H-11": "b794226b981e",
    "H-12": "4d95ef2aab30",
    "H-13": "0a7b43b825a5",
    "H-14": "553bc3d6a041",
    "H-15": "5d2cd499951f",
    "H-16": "4f9538531353",
    "H-17": "b03bc6373647",
    "H-18": "e14bad09d41e",
    "H-19": "ff4fdedc434f",
    "H-20": "28240cd4a638",
    "H-21": "62a9b4b19be6",
    "H-22": "da73db101476",
    "H-23": "1852a072656e",
    "H-24": "d9086c3a0efa",
    "H-25": "63e6012e3d63",
    "H-26": "13e74a60ee23",
    "H-27": "7f4acb4b4adf",
    "H-28": "95defe788454",
    "H-29": "c68ee8aa7ff4",
    "H-30": "7fd51f643796",
    "H-31": "949cdb78465f",
    "H-32": "1928b509d19d",
    "H-33": "71ffeb27d85a",
    "H-34": "0353a68b5608",
    "H-35": "36ded55f0140",
    "H-36": "60dc60611b45",
    "H-37": "74478c3a736f",
    "H-38": "864b2eb04911",
    "H-39": "7a4d2f9c3018",
    "H-40": "ff2a30c98d83",
    "H-41": "2e10a7076339",
    "## Retired IDs": "df75a433ff9c",
    "PRIN-16 retired": "44a1c18ceea9",
}

#: ``REVIEW_PROSE_SHA256`` (S3): the review file's 14 lines outside its tables, in order (the title, the
#: opening paragraph, the three group headings, ``## Rounds`` and its paragraphs), one digest each.
#: Asserted by ``review_line_errors``, which names the first line that differs; a new round updates it.
REVIEW_PROSE_SHA256 = (
    "3a7b11e58598", "b051f146b589", "e5d55848b69b", "c2b5b175501d", "609e8c7aa461", "96de422a6cb7",
    "66f200076653", "62ca2d783b2d", "7090b17ee6ec", "f936337bcc24", "1e0b83be9b41", "ca0e1376449f",
    "1ab83c31c697", "1ab8f103ac7a",
)

#: ``REVIEW_LINE_SHA256`` (S3): each verdict line of 00-meaning-review.md, label, verdict and reason
#: together, keyed by its row (789 lines, 789 rows). ``review_errors`` caught a flipped verdict, not a
#: reason: PRIN-01's rewritten to "Not reviewed." stayed green. A new round updates these digests
#: deliberately, alongside ``REVIEWED_BLOCK_SHA256``. Asserted both ways by ``review_line_errors``.
REVIEW_LINE_SHA256 = {
    "PRIN-01": "a7e1c9e52032",
    "PRIN-01/Scope": "b8c31674851e",
    "PRIN-01/Not": "633e4fdf0a6b",
    "PRIN-02": "7e7935d9b077",
    "PRIN-02/Scope": "2077b2d51054",
    "PRIN-02/Not": "6e98a3e36aac",
    "PRIN-03": "0bcab07e0359",
    "PRIN-03/Scope": "0d0411dade0b",
    "PRIN-03/Not": "d3efb51ef0a8",
    "ARB-01": "b28821c11359",
    "ARB-01/Scope": "b387f96f1dce",
    "ARB-01/Not": "df2f60ae2161",
    "ARB-02": "5b3b39c2dfde",
    "ARB-02/Scope": "fd758a35d579",
    "ARB-02/Not": "3e08c611f7a5",
    "ARB-03": "01da60071810",
    "ARB-03/Scope": "8a50de3e713d",
    "ARB-03/Not": "8b5a2e9127f8",
    "ARB-04": "e513514c9e32",
    "ARB-04/Scope": "f07f98bd844c",
    "ARB-04/Not": "6b2d9f78703d",
    "ARB-05": "9807463efb49",
    "ARB-05/Scope": "7f7b2a9c4f70",
    "ARB-05/Not": "f78f6fa7af7d",
    "ARB-06": "c057eccca70b",
    "ARB-06/Scope": "fac6d9f906c7",
    "ARB-06/Not": "ef52516a97a2",
    "ARB-07": "87a8e1ac85aa",
    "ARB-07/Scope": "da44a0631c43",
    "ARB-07/Not": "4ebb0af8c724",
    "PRIN-04": "12a88f322953",
    "PRIN-04/Scope": "d7eff621c712",
    "PRIN-04/Not": "61bdb62f0342",
    "PRIN-05": "5ac648632b4a",
    "PRIN-05/Scope": "4960363ca352",
    "PRIN-05/Not": "ead17aeaed30",
    "PRIN-05/Why": "98da67b0367a",
    "PRIN-06": "067648bf7c54",
    "PRIN-06/Scope": "cb0a1a1b398e",
    "PRIN-06/Not": "bb8ee110d715",
    "PRIN-17": "ff96d4c21c47",
    "PRIN-17/Scope": "4ec9b2dd2684",
    "PRIN-17/Not": "c83d3fa9346d",
    "PRIN-18": "a4e845f57059",
    "PRIN-18/Scope": "c2df6ceb25f4",
    "PRIN-18/Not": "8ee2d3bafbaa",
    "PRIN-07": "91bab25e9ff4",
    "PRIN-07/Scope": "83c95add581e",
    "PRIN-07/Not": "1127a4a32fb4",
    "PRIN-08": "8d800a363981",
    "PRIN-08/Scope": "5f582a01b5c0",
    "PRIN-08/Not": "e598d956ac25",
    "PRIN-08/Why": "ff5bc9d69b9b",
    "PRIN-09": "4fed292634db",
    "PRIN-09/Scope": "5a4b9de1186c",
    "PRIN-09/Not": "7c5765328ea0",
    "PRIN-10": "0cff3b3a0ebe",
    "PRIN-10/Scope": "0e0211fc00ce",
    "PRIN-10/Not": "1fdd85e8265c",
    "PRIN-19": "66551f730f19",
    "PRIN-19/Scope": "0e575dbde0e9",
    "PRIN-19/Not": "d399e82564af",
    "PRIN-20": "06f762dfe95d",
    "PRIN-20/Scope": "1396942ad85d",
    "PRIN-20/Not": "9ba23913c260",
    "PRIN-20/Why": "1d2fcbb63f91",
    "PRIN-21": "3cf8e16273a9",
    "PRIN-21/Scope": "38e54c9d40f6",
    "PRIN-21/Not": "22b05435ca2c",
    "PRIN-11": "036278ed407c",
    "PRIN-11/Scope": "16b3b1ebd125",
    "PRIN-11/Not": "2e05e8b2f7d9",
    "PRIN-12": "d43b69d3acfc",
    "PRIN-12/Scope": "cec103472cfe",
    "PRIN-12/Not": "2eb6768b6d94",
    "PRIN-12/Why": "5aa73bbf537a",
    "PRIN-22": "f030adf027ba",
    "PRIN-22/Scope": "979276a0ec8a",
    "PRIN-22/Not": "fece653ccdca",
    "PRIN-23": "b0bc226ef078",
    "PRIN-23/Scope": "f143b94d64f7",
    "PRIN-23/Not": "3a2ddbaaaf1d",
    "PRIN-24": "2286e09a7138",
    "PRIN-24/Scope": "a677162a2e36",
    "PRIN-24/Not": "93a897daff62",
    "PRIN-24/Why": "839ffa2c58af",
    "PRIN-13": "918e80b7e2e0",
    "PRIN-13/Scope": "7a022539d67d",
    "PRIN-13/Not": "38816d71b561",
    "PRIN-14": "c2bec84c9aa5",
    "PRIN-14/Scope": "f97f2dde58f7",
    "PRIN-14/Not": "05161c2d8617",
    "PRIN-14/Why": "265de8961edc",
    "PRIN-15": "1a90b1ed8470",
    "PRIN-15/Scope": "e1c2a9b9cfca",
    "PRIN-15/Not": "6c94998a409f",
    "PRIN-15/Why": "cac0a6bc6653",
    "PRIN-25": "3f5014eeb82c",
    "PRIN-25/Scope": "429576235a91",
    "PRIN-25/Not": "1f907675c2a6",
    "PRIN-26": "383ff1e57a73",
    "PRIN-26/Scope": "8bd1c510e18a",
    "PRIN-26/Not": "18076fc74ff9",
    "AUT-01": "6d6211390e9e",
    "AUT-01/Scope": "f834f219bdfe",
    "AUT-01/Not": "18eb96c68ce3",
    "AUT-02": "504fbce65d38",
    "AUT-02/Scope": "2658d358e77e",
    "AUT-02/Not": "24c1bba149a1",
    "AUT-02/Why": "1011c048fb9e",
    "AUT-03": "934e3158fcba",
    "AUT-03/Scope": "16e1a9448835",
    "AUT-03/Not": "249c3bed5417",
    "AUT-04": "4aa1f9b89a29",
    "AUT-04/Scope": "1bb7b83722d5",
    "AUT-04/Not": "1ecd662e8baa",
    "AUT-04/Why": "157a1309daf1",
    "AUT-06": "3d0534fcb9a5",
    "AUT-06/Scope": "040543437c11",
    "AUT-06/Not": "116ebbba7bb1",
    "AUT-08": "8e73f0a323e3",
    "AUT-08/Scope": "abbdda4a9146",
    "AUT-08/Not": "0368cb396adf",
    "GOAL-01": "4e0593e931d1",
    "GOAL-01/Scope": "21e000dadbcb",
    "GOAL-01/Not": "17d0ea1bc127",
    "GOAL-02": "4a4845200d98",
    "GOAL-02/Scope": "33bba95a6cd7",
    "GOAL-02/Not": "3ec4a566707e",
    "GOAL-02/Why": "71f7439abbf8",
    "GOAL-03": "c8bbebd1a803",
    "GOAL-03/Scope": "4a69f9707825",
    "GOAL-03/Not": "588ab55fd4ff",
    "GOAL-04": "3bae3ab25411",
    "GOAL-04/Scope": "feb4d8da5737",
    "GOAL-04/Not": "6aaec14b43f2",
    "GOAL-05": "49540af01d15",
    "GOAL-05/Scope": "c67b94e19768",
    "GOAL-05/Not": "b364ad30ace9",
    "GOAL-06": "1d85f242ac24",
    "GOAL-06/Scope": "53fcace94c0b",
    "GOAL-06/Not": "3b39f58295b2",
    "DOC-06": "33a509761f6f",
    "DOC-06/Scope": "1f2d32b9da9d",
    "DOC-06/Not": "c26c8dc22f78",
    "DOC-06/Why": "4b1c2fca872d",
    "DOC-07": "14c4d97a1528",
    "DOC-07/Scope": "1a56b88c936d",
    "DOC-07/Not": "f3332c7cd92a",
    "DOC-08": "619debe4d74b",
    "DOC-08/Scope": "71f64d9a6b87",
    "DOC-08/Not": "bfce02fb3b5a",
    "DOC-17": "dbf2f1ce4e9a",
    "DOC-17/Scope": "6f7381317af7",
    "DOC-17/Not": "e923bd7b7299",
    "DOC-18": "6f8e114fc4f8",
    "DOC-18/Scope": "5cd2b8157d9c",
    "DOC-18/Not": "ee0909710f85",
    "DOC-15": "1fb309d90439",
    "DOC-15/Scope": "a34e1462b1ee",
    "DOC-15/Not": "ed215664c186",
    "DOC-01": "093a90ed5d15",
    "DOC-01/Scope": "c3e2a29574bf",
    "DOC-01/Not": "3b7bce4db26a",
    "DOC-05": "dafe4ae7fc89",
    "DOC-05/Scope": "536b6780b2c0",
    "DOC-05/Not": "0da4625294ac",
    "DOC-04": "d6e0aef16f8c",
    "DOC-04/Scope": "13d2772e3517",
    "DOC-04/Not": "ebb4b57eef09",
    "DOC-03": "5af3a7b12c07",
    "DOC-03/Scope": "965c11aa271e",
    "DOC-03/Not": "a2ccf0f37dc2",
    "AUT-05": "ac39b28f638c",
    "AUT-05/Scope": "21217082f9d6",
    "AUT-05/Not": "419f158be6c1",
    "AUT-07": "a8e0f3b15409",
    "AUT-07/Scope": "89db91628172",
    "AUT-07/Not": "fc5e09f81916",
    "DOC-02": "1b483e199bca",
    "DOC-02/Scope": "8f87defbc725",
    "DOC-02/Not": "c40d47ae298e",
    "DOC-09": "13af1023d38d",
    "DOC-09/Scope": "d797d8bb024f",
    "DOC-09/Not": "e978be6eb916",
    "DOC-09/Why": "e7013880deaa",
    "DOC-10": "c637fed4fe24",
    "DOC-10/Scope": "9714f260a1cb",
    "DOC-10/Not": "ba034b0df311",
    "DOC-11": "7773dfc55fdd",
    "DOC-11/Scope": "34e7af556807",
    "DOC-11/Not": "46860a953358",
    "DOC-12": "73cac9aeb846",
    "DOC-12/Scope": "bfe59905b3bc",
    "DOC-12/Not": "4192afec55bf",
    "DOC-13": "c47e6749ce33",
    "DOC-13/Scope": "384a22f07295",
    "DOC-13/Not": "643c862f58fd",
    "DOC-14": "1d5ee6dece7d",
    "DOC-14/Scope": "63925b9f67ff",
    "DOC-14/Not": "bf1872addd81",
    "DOC-16": "80b15977bcf4",
    "DOC-16/Scope": "4a1830e458cb",
    "DOC-16/Not": "6a8a761b9fb6",
    "DOC-19": "406421b2c509",
    "DOC-19/Scope": "67de6ccbb150",
    "DOC-19/Not": "08baef24bb41",
    "DOC-20": "f2ebd36e3b2f",
    "DOC-20/Scope": "ab16caaea04a",
    "DOC-20/Not": "01e51481336c",
    "DOC-21": "ce4cb22267ef",
    "DOC-21/Scope": "946ebd29957c",
    "DOC-21/Not": "61096c26a6d8",
    "DOC-22": "102fa9eee0d4",
    "DOC-22/Scope": "046f947ad70b",
    "DOC-22/Not": "a0311a62015b",
    "ARCH-00": "397e2c6cba93",
    "ARCH-00/Scope": "3cc44232ba4b",
    "ARCH-00/Not": "cd9c340465f6",
    "ARCH-01": "a622d41f4c0a",
    "ARCH-01/Scope": "d72c21d61d9f",
    "ARCH-01/Not": "3fa7e1970c88",
    "ARCH-02": "adc993567067",
    "ARCH-02/Scope": "43c08e768b31",
    "ARCH-02/Not": "a1a84dcf86f7",
    "ARCH-03": "865921b210c0",
    "ARCH-03/Scope": "f18b7bc2fb4a",
    "ARCH-03/Not": "cda5c68eda87",
    "ARCH-04": "4af7797a639a",
    "ARCH-04/Scope": "78bfafbd3ac5",
    "ARCH-04/Not": "deacb6a72f7f",
    "ARCH-05": "94575478b7e1",
    "ARCH-05/Scope": "1203fd557cd4",
    "ARCH-05/Not": "537e8457c58c",
    "ARCH-06": "d095de8ec568",
    "ARCH-06/Scope": "236b64f07198",
    "ARCH-06/Not": "006ba6bdf848",
    "ARCH-06/Why": "36c99e6944d4",
    "ARCH-07": "123704542f0e",
    "ARCH-07/Scope": "995eb604def8",
    "ARCH-07/Not": "5b566cc5dc5a",
    "ARCH-08": "8e4e842334ad",
    "ARCH-08/Scope": "95368b38e340",
    "ARCH-08/Not": "d51f5b16dc5f",
    "ARCH-09": "8d6b1c014338",
    "ARCH-09/Scope": "34017829837a",
    "ARCH-09/Not": "7c427bd29b5b",
    "ARCH-10": "450392214909",
    "ARCH-10/Scope": "edc51d09bc4b",
    "ARCH-10/Not": "fe8fe38f1940",
    "ARCH-11": "4b7d11bab263",
    "ARCH-11/Scope": "f4ca8d795b65",
    "ARCH-11/Not": "f4b0812fa6da",
    "ARCH-12": "4573152888f6",
    "ARCH-12/Scope": "6e35c639f2fa",
    "ARCH-12/Not": "a661f26e0e0b",
    "ARCH-12/Why": "9a57c6d7e058",
    "ARCH-13": "824ccfd262b9",
    "ARCH-13/Scope": "2fc2aa20b84d",
    "ARCH-13/Not": "f16b30747463",
    "ARCH-13/Why": "6c3a9cc47f00",
    "REG-01": "fc9b831ba165",
    "REG-01/Scope": "885324bd8305",
    "REG-01/Not": "fef65748815f",
    "REG-02": "f0bdb504255e",
    "REG-02/Scope": "8e36b1089214",
    "REG-02/Not": "107287078ebc",
    "REG-02/Why": "efb1dd92f02c",
    "REG-03": "eea0f92d4ec3",
    "REG-03/Scope": "c2835884fb2b",
    "REG-03/Not": "7348f56c0c54",
    "REG-04": "c6937368d1c3",
    "REG-04/Scope": "39b6c5c3ec94",
    "REG-04/Not": "706f90c27fe0",
    "REG-05": "1dbfb87969d6",
    "REG-05/Scope": "3878db6cc9d4",
    "REG-05/Not": "8f68cc200934",
    "REG-06": "4268e21ce79b",
    "REG-06/Scope": "a5a72855d2e1",
    "REG-06/Not": "da838d5184a2",
    "REG-07": "16115063c169",
    "REG-07/Scope": "6edf229706a1",
    "REG-07/Not": "6a1a4aab7bd3",
    "REG-08": "fb1e7748f923",
    "REG-08/Scope": "3e11683d0bf9",
    "REG-08/Not": "0e71d88a6dbd",
    "REG-09": "1cc3e8a1e966",
    "REG-09/Scope": "3c0da97e815f",
    "REG-09/Not": "e84b58c28380",
    "REG-10": "231006dfca36",
    "REG-10/Scope": "1b2d69ec9d37",
    "REG-10/Not": "0ecafa448ca6",
    "REG-11": "bbb6e18b035c",
    "REG-11/Scope": "f091343a86bf",
    "REG-11/Not": "8d599cefef8f",
    "REG-12": "026c632b34c4",
    "REG-12/Scope": "681f58b449c7",
    "REG-12/Not": "8d91260db691",
    "REG-13": "166dc65d8c81",
    "REG-13/Scope": "6ae029155cb9",
    "REG-13/Not": "a9a49ecbcbc2",
    "REG-14": "8e463f67cf64",
    "REG-14/Scope": "e56cf17d0b4c",
    "REG-14/Not": "c95d297289ad",
    "REG-14/Why": "1f2264bb59d5",
    "REG-15": "0f493156f5c7",
    "REG-15/Scope": "33ec4f13fbe0",
    "REG-15/Not": "bc2932f0d882",
    "REG-16": "53f300c13860",
    "REG-16/Scope": "acba03485cf9",
    "REG-16/Not": "1c021432df78",
    "REG-16/Why": "cdc4b729d788",
    "REG-17": "2cb31ee6013f",
    "REG-17/Scope": "c2eaf6f568cb",
    "REG-17/Not": "fca2230313fd",
    "REG-18": "af17d4eadb13",
    "REG-18/Scope": "0ddf0ddbca69",
    "REG-18/Not": "2602815a2516",
    "REG-19": "2fb6948f2b9d",
    "REG-19/Scope": "4e9a04375c46",
    "REG-19/Not": "bd6350425805",
    "REG-19/Why": "11fe0751c6eb",
    "REG-20": "42b2920ceb91",
    "REG-20/Scope": "54894df7346f",
    "REG-20/Not": "3efa7303334c",
    "REG-21": "971a56959af4",
    "REG-21/Scope": "3055c9a74df2",
    "REG-21/Not": "d33aff3f3cb7",
    "REG-22": "20be62dee0b7",
    "REG-22/Scope": "fee0206c753d",
    "REG-22/Not": "30eeb2beaf8d",
    "REG-23": "9067c4860b14",
    "REG-23/Scope": "9d6aa44d3874",
    "REG-23/Not": "db4dc045287d",
    "REG-23/Why": "0b52ff45fe17",
    "REG-24": "34c0ba381588",
    "REG-24/Scope": "aeab9a9ab97c",
    "REG-24/Not": "86ded5cfaf48",
    "REG-25": "4660318590b9",
    "REG-25/Scope": "e724b216a47a",
    "REG-25/Not": "bacad9a86f47",
    "REG-25/Why": "fbf051780125",
    "REG-26": "670447f4d78a",
    "REG-26/Scope": "6258a68946f3",
    "REG-26/Not": "a76bead3872c",
    "REG-27": "b733ab3defb3",
    "REG-27/Scope": "d2626c0b87ba",
    "REG-27/Not": "d5f2afc0819a",
    "REG-28": "792b0904177b",
    "REG-28/Scope": "a3787e1729cb",
    "REG-28/Not": "b53fec544235",
    "REG-29": "46864d8ea53f",
    "REG-29/Scope": "a04d4660ffcf",
    "REG-29/Not": "17e38012fb0b",
    "REG-30": "9c3314eff14a",
    "REG-30/Scope": "7d2c9497c323",
    "REG-30/Not": "7b6d5523d9bc",
    "IND-01": "52219b8f6a28",
    "IND-01/Scope": "215167a50d1e",
    "IND-01/Not": "a7560951b55a",
    "IND-01/Why": "c1407a096f64",
    "IND-02": "9defd6e62d0c",
    "IND-02/Scope": "5d9fbd017b4d",
    "IND-02/Not": "b761a05d8139",
    "IND-03": "dde2b5816a1a",
    "IND-03/Scope": "1c4698331703",
    "IND-03/Not": "a6efb2583d96",
    "IND-04": "b222c56256e3",
    "IND-04/Scope": "d5cbe13c8d11",
    "IND-04/Not": "7e5ece068a84",
    "IND-04/Why": "a90e25b0a018",
    "IND-05": "0f68d42734bf",
    "IND-05/Scope": "9a9e82d53180",
    "IND-05/Not": "102830668744",
    "IND-06": "9658fa2bb906",
    "IND-06/Scope": "cbe4b5823768",
    "IND-06/Not": "3b9d093d2e72",
    "COLD-01": "dcaedb6f315d",
    "COLD-01/Scope": "1b1a0b0d2688",
    "COLD-01/Not": "528bb887f0a7",
    "COLD-01/Why": "3a68fea5676a",
    "COLD-02": "4e3adc6b140d",
    "COLD-02/Scope": "86096139e31a",
    "COLD-02/Not": "2d426a2353c2",
    "COLD-03": "a5d06b8a566f",
    "COLD-03/Scope": "c6853e441e7b",
    "COLD-03/Not": "5f35932750e0",
    "COLD-04": "7476a7b3466a",
    "COLD-04/Scope": "fbc44d653c71",
    "COLD-04/Not": "f622d7a532d1",
    "COLD-05": "d15455e1e701",
    "COLD-05/Scope": "02555a54e278",
    "COLD-05/Not": "89a6e9bcb23d",
    "COLD-06": "e66c8bedf8e1",
    "COLD-06/Scope": "8e462587d5a7",
    "COLD-06/Not": "f7b25de38116",
    "COLD-07": "9a5407ad2781",
    "COLD-07/Scope": "13ff5703008d",
    "COLD-07/Not": "b1df269da013",
    "COLD-08": "6db06e3bc143",
    "COLD-08/Scope": "3b1e83c79d11",
    "COLD-08/Not": "d550ac4bd361",
    "COLD-08/Why": "bf40f82b9071",
    "COLD-09": "eac4225d7a46",
    "COLD-09/Scope": "640e0620a6ea",
    "COLD-09/Not": "b03f8eb94dda",
    "COLD-10": "34221e881ece",
    "COLD-10/Scope": "b08170f22e99",
    "COLD-10/Not": "b3d2199d8b26",
    "COLD-11": "d087624f1281",
    "COLD-11/Scope": "12b5119702bf",
    "COLD-11/Not": "614cd10c1663",
    "LT1-01": "3ca570f563aa",
    "LT1-01/Scope": "8153037c43c4",
    "LT1-01/Not": "2fca6dcec846",
    "LT1-01/Why": "6746fe2aeea6",
    "LT1-02": "d69c196e414d",
    "LT1-02/Scope": "35c1199d2bfb",
    "LT1-02/Not": "718466622e40",
    "LT1-02/Why": "a7d200503987",
    "LT1-03": "9a37c327a327",
    "LT1-03/Scope": "b6d3e8676a2a",
    "LT1-03/Not": "de02c79c3a22",
    "LT1-04": "be91d6db700d",
    "LT1-04/Scope": "9162e08e9404",
    "LT1-04/Not": "af7b7cdb7eb1",
    "LT1-04/Why": "eb22e333a0c5",
    "LT1-05": "4c34de26a13c",
    "LT1-05/Scope": "b3eb8809b7c4",
    "LT1-05/Not": "0df680790d95",
    "FTO-01": "856c4680f544",
    "FTO-01/Scope": "5b05dba06932",
    "FTO-01/Not": "67f0a958895d",
    "FTO-02": "cb100b2a334a",
    "FTO-02/Scope": "8de126dabd7b",
    "FTO-02/Not": "cd5b09b33443",
    "FTO-03": "31f3a927676e",
    "FTO-03/Scope": "013186775c26",
    "FTO-03/Not": "f093f8b0bdfa",
    "FTO-04": "6efdca82f90e",
    "FTO-04/Scope": "f1db5ad843c6",
    "FTO-04/Not": "2d9e82dac9d1",
    "FTO-05": "5587d54de12a",
    "FTO-05/Scope": "abe540feab66",
    "FTO-05/Not": "90cd179ff7ca",
    "FTO-06": "0bd9426fae30",
    "FTO-06/Scope": "1a275b587cd1",
    "FTO-06/Not": "a9603ea56922",
    "FTO-07": "6e5d80c47948",
    "FTO-07/Scope": "b7d5ffc2f360",
    "FTO-07/Not": "afe728c8b9d7",
    "DEC-01": "e715743956d1",
    "DEC-01/Scope": "f3d9b36049b1",
    "DEC-01/Not": "b98e711c6154",
    "DEC-01/Why": "7abb667d874f",
    "DEC-02": "307fdcb12cde",
    "DEC-02/Scope": "7178949aa269",
    "DEC-02/Not": "ea4554c706b7",
    "GATE-01": "8e9b24b92042",
    "GATE-01/Scope": "1f7210536a59",
    "GATE-01/Not": "208b93fb7fcc",
    "GATE-01/Why": "8e85a89fee4d",
    "GATE-02": "88e7c9c3a570",
    "GATE-02/Scope": "a7a0b2e95f83",
    "GATE-02/Not": "70daa8cf205c",
    "GATE-02/Why": "3990886c7a79",
    "GATE-03": "f961d77c6e3e",
    "GATE-03/Scope": "af58df63ca56",
    "GATE-03/Not": "b1677c495a51",
    "GATE-03/Why": "aaac61f70996",
    "GATE-04": "aee1deb8e197",
    "GATE-04/Scope": "43fe740d5518",
    "GATE-04/Not": "bcc819540fba",
    "GATE-04/Why": "d3d4237ddf1d",
    "GATE-05": "872a55a1b893",
    "GATE-05/Scope": "92fc72cb47fe",
    "GATE-05/Not": "d318c48e39dc",
    "GATE-06": "42410cd199c6",
    "GATE-06/Scope": "5f1eed559c52",
    "GATE-06/Not": "1647fa66267b",
    "GATE-07": "fe6821b930ea",
    "GATE-07/Scope": "d698289e1703",
    "GATE-07/Not": "467a31cfe950",
    "GATE-08": "f4c8be183dc0",
    "GATE-08/Scope": "d697df08e1dc",
    "GATE-08/Not": "ae41e8435324",
    "FIG-01": "b5c12d9bdbd2",
    "FIG-01/Scope": "c58097b55f3e",
    "FIG-01/Not": "f3f11ba16e37",
    "FIG-02": "b619af5a7092",
    "FIG-02/Scope": "02efc7bc6dd7",
    "FIG-02/Not": "adc994dd097e",
    "FIG-02/Why": "0f5411ff5f86",
    "FIG-03": "899a892cdf22",
    "FIG-03/Scope": "8c97876088c9",
    "FIG-03/Not": "15b71f964425",
    "FIG-03/Why": "2ffd4e0a1e27",
    "FIG-04": "886dcad4e900",
    "FIG-04/Scope": "46a5a4e94653",
    "FIG-04/Not": "2516923c8617",
    "FIG-05": "cc45651d2b72",
    "FIG-05/Scope": "46812980d3e9",
    "FIG-05/Not": "c9a78569f2d3",
    "FIG-05/Why": "60a63eeb9364",
    "FIG-06": "8675204773d1",
    "FIG-06/Scope": "5beb549b8a87",
    "FIG-06/Not": "2d91147cef05",
    "FIG-07": "a75151053167",
    "FIG-07/Scope": "14f8689a1891",
    "FIG-07/Not": "dd2052c43c50",
    "FIG-08": "657a33bc2b34",
    "FIG-08/Scope": "d1aca9e9e38c",
    "FIG-08/Not": "c7f478b86cb0",
    "FIG-09": "2163af2aa820",
    "FIG-09/Scope": "95922d74a55f",
    "FIG-09/Not": "3307db0bcd59",
    "FIG-10": "a341e9bce887",
    "FIG-10/Scope": "52aac7147607",
    "FIG-10/Not": "72eefa43719d",
    "FIG-11": "ccab1350effa",
    "FIG-11/Scope": "f6398a7f6e8c",
    "FIG-11/Not": "f2ce2dfaf210",
    "HRV-07": "30ec98f1150c",
    "HRV-07/Scope": "2b3fef73d397",
    "HRV-07/Not": "8ce5a2dca89c",
    "HRV-07/Why": "56945f1c5e8d",
    "HRV-01": "f815245776d6",
    "HRV-01/Scope": "d3f77a0357cd",
    "HRV-01/Not": "1a716e599a21",
    "HRV-02": "7a311bbefec9",
    "HRV-02/Scope": "d551bc66ce7e",
    "HRV-02/Not": "edeb20720902",
    "HRV-03": "2f83eb911729",
    "HRV-03/Scope": "a0a01a0f4a58",
    "HRV-03/Not": "0fe603994d7c",
    "HRV-04": "132d6da76e9c",
    "HRV-04/Scope": "b5bd331401d9",
    "HRV-04/Not": "8bb68cb8ac91",
    "HRV-04/Why": "8b359444e85b",
    "HRV-05": "ea4c46054ac1",
    "HRV-05/Scope": "77aa5e51d6c8",
    "HRV-05/Not": "8688b2ef7670",
    "HRV-05/Why": "df40c7e563cb",
    "HRV-06": "769466eceac9",
    "HRV-06/Scope": "ddf359caed55",
    "HRV-06/Not": "dd777a1a594a",
    "HRV-47": "898fe27a045f",
    "HRV-47/Scope": "e6169fa0e4e1",
    "HRV-47/Not": "0a9c5b611210",
    "HRV-08": "5bb1b041bcc2",
    "HRV-08/Scope": "1ba8bc27f154",
    "HRV-08/Not": "1f1d7dde6006",
    "HRV-09": "cdbcdbf2bf0a",
    "HRV-09/Scope": "880344c615e0",
    "HRV-09/Not": "d4427f1f35c5",
    "HRV-10": "5909d77ed821",
    "HRV-10/Scope": "320c02dc6e31",
    "HRV-10/Not": "bfc6db128bac",
    "HRV-11": "e38439d854d0",
    "HRV-11/Scope": "93dff64ecdec",
    "HRV-11/Not": "45b1b35bef1e",
    "HRV-11/Why": "afb34323500a",
    "HRV-12": "1cd388af2a7c",
    "HRV-12/Scope": "157c6ba72a32",
    "HRV-12/Not": "27bc45607f67",
    "HRV-12/Why": "f149efc16217",
    "HRV-13": "d115191f4377",
    "HRV-13/Scope": "29b2f59e50d1",
    "HRV-13/Not": "527bc0bf7863",
    "HRV-14": "6e5161ebce2f",
    "HRV-14/Scope": "3193e90b933b",
    "HRV-14/Not": "49c9aa4abacd",
    "HRV-15": "1c572aae0657",
    "HRV-15/Scope": "66ac3da09707",
    "HRV-15/Not": "31749cbde698",
    "HRV-15/Why": "57168bb3858b",
    "HRV-16": "d5942746b7ce",
    "HRV-16/Scope": "c13f091f983e",
    "HRV-16/Not": "e22d8a47e175",
    "HRV-17": "a1907990d8d0",
    "HRV-17/Scope": "23cc14df012c",
    "HRV-17/Not": "648e3985459a",
    "HRV-17/Why": "3cb121691c71",
    "HRV-18": "11e581bf0787",
    "HRV-18/Scope": "611b04a0d50b",
    "HRV-18/Not": "3a4207c9c090",
    "HRV-19": "50332ed21eaa",
    "HRV-19/Scope": "3a071be637f4",
    "HRV-19/Not": "7d1d15e95a47",
    "HRV-20": "7fbc49972bb8",
    "HRV-20/Scope": "28afb334b3f5",
    "HRV-20/Not": "af96169c6e8a",
    "HRV-21": "d7eda81e6dcf",
    "HRV-21/Scope": "ff9afca2e6c3",
    "HRV-21/Not": "bbbf63ffab64",
    "HRV-22": "579f4d00358b",
    "HRV-22/Scope": "32f4900585a9",
    "HRV-22/Not": "c7ef5afe54a6",
    "HRV-23": "a07e72848158",
    "HRV-23/Scope": "78f7a3ad9fa0",
    "HRV-23/Not": "403389dfee3d",
    "HRV-24": "4e052216bb2d",
    "HRV-24/Scope": "cbcb81a7caba",
    "HRV-24/Not": "bc3bdfd60e9c",
    "HRV-25": "d20244217c8a",
    "HRV-25/Scope": "2bdd10f769c0",
    "HRV-25/Not": "9ef570ef327c",
    "HRV-25/Why": "5f8e9ab40ea3",
    "HRV-26": "fd9a52c43aab",
    "HRV-26/Scope": "b3d3cccc9c8e",
    "HRV-26/Not": "b20822e807ab",
    "HRV-27": "c1c7de946fd7",
    "HRV-27/Scope": "7cfc7829b43f",
    "HRV-27/Not": "4d573093cdc3",
    "HRV-28": "c48ad5f55afa",
    "HRV-28/Scope": "7bee76da6077",
    "HRV-28/Not": "d9d455a42c91",
    "HRV-29": "f67e14c37608",
    "HRV-29/Scope": "8759935c5122",
    "HRV-29/Not": "90dbc4b33d34",
    "HRV-30": "d6b7fb8d6382",
    "HRV-30/Scope": "ea3ebec0d5ef",
    "HRV-30/Not": "2b2be3c0cc7e",
    "HRV-30/Why": "84a404030228",
    "HRV-31": "a812f6472798",
    "HRV-31/Scope": "7e8481362239",
    "HRV-31/Not": "1354e77a464c",
    "HRV-31/Why": "47bf5a21f957",
    "HRV-32": "5887880b6e47",
    "HRV-32/Scope": "4b0574719e47",
    "HRV-32/Not": "50f12623ca55",
    "HRV-33": "53cc14aaeda9",
    "HRV-33/Scope": "9b5a79f635a0",
    "HRV-33/Not": "88f7a0747066",
    "HRV-34": "1ccd6c90baca",
    "HRV-34/Scope": "a8128537c98c",
    "HRV-34/Not": "c3654af2b0fa",
    "HRV-34/Why": "80096d38abb2",
    "HRV-35": "709fa13b448d",
    "HRV-35/Scope": "c187df2ccaab",
    "HRV-35/Not": "8a9f5c8d129b",
    "HRV-36": "ce4e2aeabf9a",
    "HRV-36/Scope": "b2a4fc721398",
    "HRV-36/Not": "8e3e8f21c537",
    "HRV-37": "c3353edb53ea",
    "HRV-37/Scope": "1f8660641fee",
    "HRV-37/Not": "effef8ba300d",
    "HRV-38": "14e16dca1564",
    "HRV-38/Scope": "82ff50adde0f",
    "HRV-38/Not": "605d503e07d9",
    "HRV-38/Why": "f531dcbbbc6c",
    "HRV-39": "9b2971a79da4",
    "HRV-39/Scope": "40cd5c299e0d",
    "HRV-39/Not": "960a7feb45ca",
    "HRV-40": "83cba3ff9ba6",
    "HRV-40/Scope": "c8efd0c7a25b",
    "HRV-40/Not": "0f9b6533f7e2",
    "HRV-41": "299e631231ae",
    "HRV-41/Scope": "a25084238bd6",
    "HRV-41/Not": "5e0fd9396b00",
    "HRV-42": "dd6f5bf18b2f",
    "HRV-42/Scope": "5ab98d3c01e1",
    "HRV-42/Not": "fcb4f1b83213",
    "HRV-43": "cb686202ec2f",
    "HRV-43/Scope": "6ff47f315991",
    "HRV-43/Not": "de957e0947f9",
    "HRV-44": "2c3b46af25a8",
    "HRV-44/Scope": "b0d84a5f0d32",
    "HRV-44/Not": "154b3b6d9570",
    "HRV-45": "1d9414115d6a",
    "HRV-45/Scope": "a2d5de6df01d",
    "HRV-45/Not": "f0fab6529dfa",
    "HRV-46": "cf4d07cf1afb",
    "HRV-46/Scope": "97704c1e0a4e",
    "HRV-46/Not": "5bfa1175cb54",
    "HRV-48": "e486e351cdf1",
    "HRV-48/Scope": "f31d8baf8f87",
    "HRV-48/Not": "5daac0c3666c",
    "HRV-49": "f9c449e4c73d",
    "HRV-49/Scope": "d63da2ca80ae",
    "HRV-49/Not": "03850988250c",
    "HRV-50": "5c4beaa41b2f",
    "HRV-50/Scope": "6d33d8ccf312",
    "HRV-50/Not": "f6cd0d428416",
    "HRV-51": "0adba7ab127a",
    "HRV-51/Scope": "b4093382ba0c",
    "HRV-51/Not": "f4c16e7485e9",
    "HRV-52": "0e4b4af1aa2a",
    "HRV-52/Scope": "5b00c7ac957b",
    "HRV-52/Not": "9aeb76814a6b",
    "HRV-52/Why": "fadeb34616c1",
    "HRV-53": "65175129f193",
    "HRV-53/Scope": "90a953e95e85",
    "HRV-53/Not": "bbad89512a68",
    "HRV-54": "78ba0006ac35",
    "HRV-54/Scope": "2dc434d2c718",
    "HRV-54/Not": "e4053b62655a",
    "HRV-55": "4d0d7f410f85",
    "HRV-55/Scope": "d11eb9a65ad0",
    "HRV-55/Not": "c18e2cb12818",
    "HRV-56": "ad07a9fc9db2",
    "HRV-56/Scope": "3c9747d0211a",
    "HRV-56/Not": "2ec20ef52de1",
    "HRV-57": "cc820ffe4d60",
    "HRV-57/Scope": "054e8d29d9db",
    "HRV-57/Not": "2ba664040992",
    "HRV-58": "a70a6a272a21",
    "HRV-58/Scope": "35378b260dcd",
    "HRV-58/Not": "3828126233ec",
    "HRV-59": "215c67ac65d9",
    "HRV-59/Scope": "5ce98fd1ebdd",
    "HRV-59/Not": "7db55d5c40fb",
    "HRV-60": "3d881192a408",
    "HRV-60/Scope": "3f1df1cdc767",
    "HRV-60/Not": "332b550f0d26",
    "HRV-61": "a85610871d0e",
    "HRV-61/Scope": "581ec7130a4f",
    "HRV-61/Not": "ccd4439a1a73",
    "HRV-62": "99863abd1b91",
    "HRV-62/Scope": "e437f785de4d",
    "HRV-62/Not": "6a7597cabaa2",
    "HRV-63": "94ed9da3b373",
    "HRV-63/Scope": "2e972e1111ad",
    "HRV-63/Not": "ca867b76a7b8",
    "HRV-64": "d7d3cb5fe163",
    "HRV-64/Scope": "1a945d4bc95e",
    "HRV-64/Not": "47d7077ac4f8",
    "HRV-65": "cf7b520c85f7",
    "HRV-65/Scope": "2dad71fb72c8",
    "HRV-65/Not": "f540ad7e6ad6",
    "HRV-66": "2ad40f24b637",
    "HRV-66/Scope": "cc6b02632845",
    "HRV-66/Not": "d9c0c35484a6",
    "HRV-66/Why": "7ebff3d18794",
    "HRV-67": "33e38497583d",
    "HRV-67/Scope": "1b2b5203d343",
    "HRV-67/Not": "29c58c4f3b80",
    "HRV-68": "efdd286e3d8a",
    "HRV-68/Scope": "35b54c7b3071",
    "HRV-68/Not": "6efdabb9c119",
    "HRV-69": "55acf8a042f4",
    "HRV-69/Scope": "7d9623d2ef14",
    "HRV-69/Not": "eed996a003c1",
    "HRV-70": "fbeb3bcd2e28",
    "HRV-70/Scope": "6f88400a5e6d",
    "HRV-70/Not": "4e100cb8e0c8",
    "HRV-71": "b0e0ca28047c",
    "HRV-71/Scope": "6352b63d1b52",
    "HRV-71/Not": "a3fa6323ee6a",
    "HRV-72": "20e3683f1be2",
    "HRV-72/Scope": "26b9a6cbbb56",
    "HRV-72/Not": "c8874e90a40d",
    "HRV-73": "0034274ce381",
    "HRV-73/Scope": "1ec55740235d",
    "HRV-73/Not": "b11005e68815",
    "HRV-74": "a70625c57214",
    "HRV-74/Scope": "efb405135126",
    "HRV-74/Not": "bfead98db590",
    "HRV-75": "3fb05a1d4d4d",
    "HRV-75/Scope": "a3cc698e5d26",
    "HRV-75/Not": "547cca626472",
    "HRV-76": "ba07a11a1764",
    "HRV-76/Scope": "d2c099f62812",
    "HRV-76/Not": "620ef40f7348",
    "HRV-76/Why": "98331c4ae013",
    "HRV-77": "b788d07d30f2",
    "HRV-77/Scope": "2aa1c3b50961",
    "HRV-77/Not": "b34c6e089041",
    "HRV-78": "04e36cc9dace",
    "HRV-78/Scope": "d467a697ab0e",
    "HRV-78/Not": "cbe4e2086e50",
    "HRV-79": "261c718fdb0c",
    "HRV-79/Scope": "00aced749eb5",
    "HRV-79/Not": "34085666d1ba",
    "HRV-80": "ee3799b950db",
    "HRV-80/Scope": "973bc9cb3e88",
    "HRV-80/Not": "632b1dae3cb3",
    "HRV-81": "8ea5bda3a3b9",
    "HRV-81/Scope": "c5e388f04c81",
    "HRV-81/Not": "8b169b4bf947",
    "HRV-82": "f22d24b04aa3",
    "HRV-82/Scope": "9910b5b5813c",
    "HRV-82/Not": "babb335012cb",
    "HRV-83": "e027b137bb5e",
    "HRV-83/Scope": "c9a6738d2d2b",
    "HRV-83/Not": "27a6aaa39eb1",
    "HRV-84": "a35d592babae",
    "HRV-84/Scope": "1a74d3f2ad98",
    "HRV-84/Not": "e835e93a58ef",
}

#: The decisions reference's two tables, for the H-NN a merged-away ID retires under (R11).
GROUP_A = frozenset({
    "C01", "C02", "C03", "C04", "C10", "C11", "C12", "C13", "C14", "C15", "C16", "C17", "C18", "C19",
    "C21", "C24", "C25", "C26", "C29",
})
GROUP_B = frozenset({
    "C05", "C06", "C07", "C08", "C09", "C20", "C22", "C23", "C27", "C28", "C30", "C31", "C32", "C33",
    "C37", "C38",
})

#: Who writes each C-number's ``yes`` row (R11). The explicit table first; otherwise the group of the
#: C-number's first inventory row, read from each row's Status cell (``C09``'s first is HRV-33, whose
#: status homes its open question in C09). C38 cites no inventory row: its decision text names DOC-09.
YES_ROW_OWNER = {
    "C01": "hrv", "C02": "hrv", "C03": "hrv", "C04": "hrv", "C05": "arch-dec",
    "C06": "doc-goal",  # R11: PRIN-15
    "C07": "doc-goal",  # R11: PRIN-14
    "C08": "arch-dec",  # R11: ARCH-08 (the first row, PRIN-16, is doc-goal)
    "C09": "hrv", "C10": "hrv", "C11": "hrv", "C12": "hrv", "C13": "hrv", "C14": "hrv", "C15": "hrv",
    "C16": "hrv", "C17": "hrv", "C18": "hrv", "C19": "doc-goal", "C20": "doc-goal", "C21": "arch-dec",
    "C22": "doc-goal", "C23": "doc-goal", "C24": "doc-goal", "C25": "doc-goal", "C26": "arch-dec",
    "C27": "hrv",  # R11: HRV-05
    "C28": "arch-dec", "C29": "arch-dec",
    "C30": "arch-dec",  # R11: REG-19 (the first row, DOC-07, is doc-goal)
    "C31": "doc-goal", "C32": "hrv",
    "C33": "doc-goal",  # R11: PRIN-12
    "C37": "arch-dec", "C38": "doc-goal",
}

#: F008 AC7: the rule each decision is stated in, and the strings that rule's block must carry after
#: ``normalize()``. C05's absence half ("worse rate reopens") is built from ``OLD_MEANINGS``, never
#: from a literal (R11).
OPERATIVE = {
    "C01": ("HRV-31", ("could not have been selected: not judgeable, or skipped",)),
    "C02": ("HRV-31", ("the dataset being judged",)),
    "C04": ("HRV-37", ("more than",)),
    "C05": ("GATE-02", ("MUST NOT add hysteresis",)),
    "C06": ("PRIN-15", (
        "may not grow", "F005-parity", "DEFERRED_EXCEPTION", "IDEA-087", "HRV-25", "IDEA-099",
        "Pinned: none (F009)",
    )),
    "C07": ("PRIN-14", ("forbidden direction",)),
    "C32": ("HRV-07", ("max(0.5 · SD(ln rMSSD), 0.01)",)),
    "C33": ("PRIN-12", ("recency_tolerance_days", "Pinned: none (F010)")),
    "C38": ("DOC-09", ("only current rules", "dated summary")),
}

#: F008 decisions reference section 7: the traceability table's header, exactly.
TRACE_HEADER = (
    "| inventory ID | inventory sentence | Cited today as | new ID(s) | decision | meaning changed "
    "| old-meaning key |"
)
TRACE_COLUMNS = tuple(cell.strip() for cell in TRACE_HEADER.strip().strip("|").split("|"))

#: R11: the three source_change anchors and COST_SITES / spec_cost's FIG-01 lead.
ANCHOR_RULES = ("HRV-01", "HRV-06", "HRV-10", "FIG-01")
PER_TIER_ANCHORS = ("HRV-01", "HRV-06", "HRV-10")

#: R6's AC9 quantifiers.
QUANTIFIERS = ("strictly", "more than", "at least", "every", "only", "never")

ADDITION = "—"


# ===========================================================================
# The checkers. Every *_errors function returns a list of error strings, each prefixed with the
# check that raised it, and never raises on bad text.
# ===========================================================================

_P = "|".join(PREFIX_ORDER)
_RULE_ID = re.compile(rf"\b(?:{_P})-\d{{2,3}}\b")
_H_ID = re.compile(r"\bH-\d{2}\b")
_C_ID = re.compile(r"\bC\d{2}\b")
_ISO_DATE = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}")
_RULE_LINE = re.compile(rf"^\*\*(?P<id>(?:{_P})-\d{{2,3}})\.\*\* (?P<body>\S.*)$")
_SUB_LINE = re.compile(r"^(?P<kind>Scope|Not|Pinned|Why): \S")
_PINNED = re.compile(r"^Pinned: (?:none|none \(F0\d\d\)|(?P<path>[^\s`:]+)::(?P<node>[^\s`]+))$")
_CODE_SPAN = re.compile(r"`[^`]*`")
_ABBREVIATIONS = re.compile(r"\b(?:e\.g|i\.e|vs|cf)\.", re.IGNORECASE)
#: R6: after the ID, an interior sentence terminator.
_TERMINATOR = re.compile(r"[.!?](?=\s+[A-Z(])")
_GLOSSARY_LINE = re.compile(r"^- \*\*(?P<id>T-\d{2}) (?P<term>[^*]+?)\*\* IS (?P<definition>\S.*)\.$")
_HISTORY_ENTRY = re.compile(
    r"^- \*\*(?P<id>H-\d{2})\*\* \((?:20\d{2}-\d{2}-\d{2}(?:, 20\d{2}-\d{2}-\d{2})*|undated)\) (?P<what>\S.*) → "
    rf"(?P<ids>(?:{_P})-\d{{2,3}}(?:, (?:{_P})-\d{{2,3}})*)$"
)
_RETIRED_LINE = re.compile(rf"^- \*\*(?P<id>(?:{_P})-\d{{2,3}})\*\* retired → (?P<h>H-\d{{2}})$")
_MAX_LINE = 400
_SUB_ORDER = {"Scope": 0, "Not": 1, "Pinned": 2, "Why": 3}
_RETIRED_HEADING = "## Retired IDs"
_GROUP_OF = {prefix: group for group, prefixes in GROUPS.items() for prefix in prefixes}
_SOURCE = re.compile(r"^[^\s:]+:\d+(?:-\d+)?@4e47d0e$")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
#: A decision token in a decision cell or an ``OldMeaning.decision``: a C-number, the critique-round
#: ``HRV-11`` call, an R-refinement or a Glossary T-number (as ``_decision_group_entry`` reads them).
_DECISION_TOKEN = re.compile(r"\bC\d{2}\b|\bHRV-11\b|\bR\d+\b|\bT-\d{2}\b")


def _lead_decision(decision: str) -> str | None:
    """The decision an ``OldMeaning.decision`` records: its first ``_DECISION_TOKEN``. The rest is
    prose (R9's T07 keys mention ``R9``; R13's keys mention ``T-05``, ``T-13``, ``T-26``), and a row
    citing a token from that prose must not be able to borrow the key (iteration 2, M1)."""
    m = _DECISION_TOKEN.search(decision)
    return m.group(0) if m else None


def _prefix(rule_id: str) -> str:
    return rule_id.rsplit("-", 1)[0]


def _order_key(rule_id: str) -> tuple[int, int]:
    return PREFIX_ORDER.index(_prefix(rule_id)), int(rule_id.rsplit("-", 1)[1])


def _is_blank(cell: str) -> bool:
    return cell.strip() in ("", ADDITION, "-")


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").split("\n")


def _glossary_span(lines: list[str]) -> tuple[int, int] | None:
    """The [start, end) line range of the Glossary section's body, or None."""
    if GLOSSARY_HEADING not in lines:
        return None
    start = lines.index(GLOSSARY_HEADING) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("#")), len(lines))
    return start, end


# ---------------------------------------------------------------------------
# Parsers and extractors
# ---------------------------------------------------------------------------


def rule_ids(text: str) -> list[str]:
    """The ID of every rule line, in document order (duplicates kept, so they can be counted)."""
    return [m.group("id") for line in _lines(text) if (m := _RULE_LINE.match(line))]


def rule_blocks(text: str) -> dict[str, str]:
    """``{rule_id: block}``: the rule line plus the Scope, Not, Pinned and Why lines directly under it."""
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in _lines(text):
        m = _RULE_LINE.match(line)
        if m:
            current = blocks.setdefault(m.group("id"), [])
            current.append(line)
        elif current is not None and _SUB_LINE.match(line):
            current.append(line)
        else:
            current = None
    return {rule_id: "\n".join(lines) for rule_id, lines in blocks.items()}


def history_ids(text: str) -> list[str]:
    """The ``H-NN`` of every history entry line, in order (duplicates kept)."""
    return re.findall(r"^- \*\*(H-\d{2})\*\* \(", text.replace("\r\n", "\n"), re.M)


def _history_arrows(text: str) -> dict[str, list[str]]:
    arrows: dict[str, list[str]] = {}
    for line in _lines(text):
        m = _HISTORY_ENTRY.match(line)
        if m:
            arrows[m.group("id")] = m.group("ids").split(", ")
    return arrows


def _retired_listed(history_text: str) -> dict[str, str]:
    """``{ID: H-NN}`` as listed under ``## Retired IDs``."""
    if _RETIRED_HEADING not in history_text:
        return {}
    section = history_text.replace("\r\n", "\n").split(_RETIRED_HEADING, 1)[1]
    return {m.group("id"): m.group("h") for line in section.split("\n") if (m := _RETIRED_LINE.match(line))}


def _split_cells(line: str) -> list[str]:
    body = line.strip().removeprefix("|")
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body)]


#: One cell of a markdown table's separator line: ``---``, ``:---``, ``---:`` or ``:---:``.
_SEPARATOR_CELL = re.compile(r":?-{3,}:?")


def _is_separator(cells: list[str]) -> bool:
    """Whether a line's ``_split_cells`` are a table separator: every cell is ``_SEPARATOR_CELL``."""
    return all(_SEPARATOR_CELL.fullmatch(c) for c in cells)


def parse_traceability(text: str) -> list[dict[str, str]]:
    """The table's rows, each keyed by ``TRACE_COLUMNS``. Takes the committed table (with its exact
    header and separator) or a header-less draft fragment. Raises ``ValueError`` on a row with the
    wrong number of columns or a header that is not ``TRACE_HEADER``."""
    rows = []
    for number, line in enumerate(_lines(text), 1):
        if not line.strip().startswith("|"):
            continue
        cells = _split_cells(line)
        if _is_separator(cells):
            continue
        if cells and cells[0] == TRACE_COLUMNS[0]:
            if tuple(cells) != TRACE_COLUMNS:
                raise ValueError(f"line {number}: a header that is not TRACE_HEADER: {line.strip()!r}")
            continue
        if len(cells) != len(TRACE_COLUMNS):
            raise ValueError(f"line {number}: {len(cells)} columns, not {len(TRACE_COLUMNS)}: {line.strip()[:120]!r}")
        rows.append(dict(zip(TRACE_COLUMNS, cells, strict=True)))
    return rows


def load_meanings_jsonl(text: str) -> dict[str, OldMeaning]:
    """One JSON object per line (``key, pattern, example, source, decision``) into ``{key: OldMeaning}``.
    Raises ``ValueError`` on bad JSON, missing fields or a duplicate key."""
    entries: dict[str, OldMeaning] = {}
    need = {"key", "pattern", "example", "source", "decision"}
    for number, line in enumerate(_lines(text), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {number}: not JSON: {exc}") from exc
        if not isinstance(obj, dict) or set(obj) != need:
            raise ValueError(f"line {number}: fields must be exactly {sorted(need)}, got {sorted(obj) if isinstance(obj, dict) else obj!r}")
        if obj["key"] in entries:
            raise ValueError(f"line {number}: duplicate key {obj['key']!r}")
        entries[obj["key"]] = OldMeaning(obj["pattern"], obj["example"], obj["source"], obj["decision"])
    return entries


def inventory_sentences(path) -> dict[str, str]:
    """``{ID: verbatim sentence}`` from the inventory's Section 1 tables (the "Current rule" cell).
    It reads the data dir, so probes and ``fragment_errors`` use it; the committed tests never do."""
    sentences = {}
    for line in _lines(Path(path).read_text(encoding="utf-8")):
        m = re.match(rf"^\| ((?:{_P})-\d{{2,3}}) \| ", line)
        if m:
            sentences[m.group(1)] = _split_cells(line)[1]
    return sentences


def public_names(path) -> set[str]:
    """Top-level ``ClassDef``, ``FunctionDef`` and ``Assign``/``AnnAssign`` targets without a leading
    underscore, read from the AST (R11). Imports are not definitions and are not counted."""
    names: set[str] = set()
    for node in ast.parse(Path(path).read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if not name.startswith("_")}


def _new_rule_ids(row: dict[str, str]) -> list[str]:
    return _RULE_ID.findall(row["new ID(s)"])


def retired_ids(rows: list[dict[str, str]]) -> dict[str, str | None]:
    """``{inventory ID: H-NN}`` for every ID that no longer names a rule (AC3, R6, R11):
    - a row whose new-ID cell does not name its own ID retires with the first ``H-NN`` in that cell;
    - otherwise (merged away) with its decision group's entry: H-38 for a Group A C-number, H-39 for
      Group B, H-40 for the critique-round ``HRV-11`` call, H-41 for an R-refinement or a T-number
      (R9's T-07 renames). The first decision in the cell decides.
    ``None`` marks a merged-away ID with no decision to retire under; ``traceability_errors`` flags it.
    A malformed inventory-ID cell (not one rule ID, e.g. "PRIN-15, IND-99") retires nothing: it is
    reported, never raised -- ``traceability_errors`` names it as not an inventory ID and ``assemble``
    as malformed (T176's robustness finding)."""
    retired: dict[str, str | None] = {}
    for row in rows:
        inv = row["inventory ID"]
        if _is_blank(inv) or not _RULE_ID.fullmatch(inv) or inv in _new_rule_ids(row):
            continue
        h = _H_ID.findall(row["new ID(s)"])
        if h:
            retired[inv] = h[0]
            continue
        retired[inv] = _decision_group_entry(row["decision"])
    return retired


def _decision_group_entry(cell: str) -> str | None:
    for token in re.findall(r"\bC\d{2}\b|\bHRV-11\b|\bR\d+\b|\bT-\d{2}\b", cell):
        if token in GROUP_A:
            return "H-38"
        if token in GROUP_B:
            return "H-39"
        if token == "HRV-11":
            return "H-40"
        if token.startswith(("R", "T-")):
            return "H-41"
    return None


# ---------------------------------------------------------------------------
# Grammar and structure
# ---------------------------------------------------------------------------


def _one_sentence(body: str) -> bool:
    plain = _ABBREVIATIONS.sub("ABBR", _CODE_SPAN.sub("CODE", body))
    return not _TERMINATOR.search(plain)


def rule_grammar_errors(text: str) -> list[str]:
    """AC1, AC2, AC3 and R6 over research/00 or a rules draft: no ISO date; no line over 400 characters;
    headings only from ``HEADINGS`` (or the Glossary's); every other line outside the Glossary is a
    blank line, a rule line in obligation or definition form holding one sentence, or a Scope, Not,
    Pinned or Why line under it -- exactly one Scope, one Not, one or more Pinned and at most one Why,
    in that order, with no blank line inside a block; rule IDs unique."""
    errors: list[str] = []
    flat = " ".join(text.split())
    for date in sorted(set(_ISO_DATE.findall(flat))):
        errors.append(f"[grammar] an ISO date remains: {date}")
    lines = _lines(text)
    glossary = _glossary_span(lines)
    state: dict | None = None

    def close() -> None:
        if state is None or state["closed"]:
            return
        state["closed"] = True
        seen = state["seen"]
        for kind in ("Scope", "Not", "Pinned"):
            if kind not in seen:
                errors.append(f"[grammar] {state['id']} (line {state['line']}): no {kind}: line")
        for kind in ("Scope", "Not"):
            if seen.count(kind) > 1:
                errors.append(f"[grammar] {state['id']} (line {state['line']}): {seen.count(kind)} {kind}: lines, not one")

    for number, line in enumerate(lines, 1):
        if len(line) > _MAX_LINE:
            errors.append(f"[grammar] line {number}: {len(line)} characters, over {_MAX_LINE}")
        if glossary and glossary[0] <= number - 1 < glossary[1]:
            continue
        if line.startswith("#"):
            close()
            state = None
            if line not in HEADINGS and line != GLOSSARY_HEADING:
                errors.append(f"[grammar] line {number}: a heading not in HEADINGS: {line[:100]!r}")
            continue
        if not line.strip():
            close()
            continue
        m = _RULE_LINE.match(line)
        if m:
            close()
            body = m.group("body")
            state = {"id": m.group("id"), "line": number, "seen": [], "closed": False}
            if not line.endswith("."):
                errors.append(f"[grammar] {m.group('id')} (line {number}): a rule line must end in '.'")
            elif not _one_sentence(body):
                errors.append(f"[grammar] {m.group('id')} (line {number}): more than one sentence (R6)")
            if not (re.search(r"\bMUST\b|\bMAY\b", body) or " IS " in f" {body} "):
                errors.append(f"[grammar] {m.group('id')} (line {number}): no MUST, MUST NOT, MAY or IS")
            continue
        sub = _SUB_LINE.match(line)
        if sub and state is not None and not state["closed"]:
            kind, seen = sub.group("kind"), state["seen"]
            if seen and _SUB_ORDER[kind] < _SUB_ORDER[seen[-1]]:
                errors.append(f"[grammar] {state['id']} (line {number}): {kind}: out of order after {seen[-1]}:")
            elif kind == "Why" and "Why" in seen:
                errors.append(f"[grammar] {state['id']} (line {number}): a second Why: line is out of order")
            elif kind in ("Scope", "Not") and kind in seen:
                errors.append(f"[grammar] {state['id']} (line {number}): a second {kind}: line is out of order")
            if kind == "Pinned" and not _PINNED.match(line):
                errors.append(f"[grammar] {state['id']} (line {number}): Pinned: must be plain path::name, "
                              f"'none' or 'none (F0NN)': {line[:120]!r}")
            seen.append(kind)
            continue
        errors.append(f"[grammar] line {number}: not a rule, Scope, Not, Pinned, Why, heading or blank line: {line[:100]!r}")
    close()
    counts = Counter(rule_ids(text))
    errors += [f"[grammar] duplicate rule ID {i} ({n} rule lines)" for i, n in counts.items() if n > 1]
    return errors


def glossary_errors(text: str, complete: bool = True) -> list[str]:
    """R6's glossary format over the Glossary section of research/00 (or a whole glossary draft):
    ``- **T-NN <term>** IS <definition>.``, at most 400 characters, each term once, T-25's definition
    exactly ``OPEN (IDEA-088)``, and, when ``complete``, exactly the 33 terms T-01..T-33."""
    lines = _lines(text)
    span = _glossary_span(lines)
    body = lines[span[0]:span[1]] if span else [line for line in lines if not line.startswith("#")]
    errors, seen = [], []
    for line in body:
        if not line.strip():
            continue
        if len(line) > _MAX_LINE:
            errors.append(f"[glossary] {len(line)} characters, over {_MAX_LINE}: {line[:60]!r}")
        m = _GLOSSARY_LINE.match(line)
        if not m:
            errors.append(f"[glossary] not in the format '- **T-NN <term>** IS <definition>.': {line[:80]!r}")
            continue
        seen.append(m.group("id"))
        if m.group("id") == "T-25" and m.group("definition") != "OPEN (IDEA-088)":
            errors.append(f"[glossary] T-25 must be defined as exactly 'OPEN (IDEA-088)', not {m.group('definition')!r}")
    errors += [f"[glossary] duplicate term {t}" for t, n in Counter(seen).items() if n > 1]
    if complete and sorted(set(seen)) != [f"T-{i:02d}" for i in range(1, 34)]:
        errors.append(f"[glossary] {len(set(seen))} distinct terms, not the 33 T-01..T-33")
    return errors


def glossary_term_errors(text: str) -> list[str]:
    """AC4 and AC7 C07 over research/00's Glossary (sprint-007 review iteration 1, S4): each ID names
    its ``GLOSSARY_TERMS`` term, each term is defined once, and T-24's definition carries every
    ``FORBIDDEN_DIRECTION_CLAUSES`` clause after ``normalize()``; and each clause occurs exactly once
    in the whole of ``text`` after ``normalize()``, so no rule restates the direction (iteration 2,
    S3)."""
    lines = _lines(text)
    span = _glossary_span(lines)
    body = lines[span[0]:span[1]] if span else []
    found = [(m.group("id"), m.group("term"), m.group("definition"))
             for line in body if (m := _GLOSSARY_LINE.match(line))]
    errors = [f"[glossary] {tid} names the term {term!r}, not {GLOSSARY_TERMS.get(tid)!r}"
              for tid, term, _d in found if GLOSSARY_TERMS.get(tid) != term]
    errors += [f"[glossary] the term {term!r} is defined {n} times, not once"
               for term, n in Counter(term for _t, term, _d in found).items() if n > 1]
    errors += [f"[glossary] {tid} ({term!r}) is not defined" for tid, term in GLOSSARY_TERMS.items()
               if tid not in {t for t, _term, _d in found}]
    t24 = [d for tid, _term, d in found if tid == "T-24"]
    flat = normalize(text)
    for clause in FORBIDDEN_DIRECTION_CLAUSES:
        if not any(normalize(clause) in normalize(d) for d in t24):
            errors.append(f"[glossary] T-24 does not carry {clause!r} (after normalize)")
        # C07 "defined once, in the Glossary" is a claim about the whole file, not the Glossary's
        # lines: a narrower restatement anywhere else is a second definition (iteration 2, S3).
        if (n := flat.count(normalize(clause))) != 1:
            errors.append(f"[glossary] {clause!r} occurs {n} times in the whole file after normalize, "
                          "not once (C07: the forbidden direction is defined once, in the Glossary)")
    return errors


def _sentences(line: str) -> list[str]:
    guarded = _ABBREVIATIONS.sub(lambda m: m.group(0).replace(".", "\0"), line)
    return [s.replace("\0", ".") for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", guarded)]


def band_errors(text: str) -> list[str]:
    """AC4 and R6, case-insensitive: every ``\\bbands?\\b`` sits in a sentence that, with its rule-ID
    tokens removed, also contains SWC, HRV or rMSSD. "Band" is reserved for the HRV SWC band."""
    errors = []
    for number, line in enumerate(_lines(text), 1):
        for sentence in _sentences(line):
            if re.search(r"\bbands?\b", sentence, re.IGNORECASE):
                rest = _RULE_ID.sub(" ", sentence)
                if not re.search(r"swc|hrv|rmssd", rest, re.IGNORECASE):
                    errors.append(f"[band] line {number}: 'band' outside the HRV SWC band: {sentence.strip()[:120]!r}")
    return errors


def history_errors(text: str) -> list[str]:
    """AC5 and R6 over the history file or its draft: every entry is
    ``- **H-NN** (<date>[, <date>...] | undated) <what changed> → <rule IDs>``, each H-NN once; no rule
    line; no uppercase MUST or MAY; a ``## Retired IDs`` section whose lines are
    ``- **<ID>** retired → H-NN``."""
    errors = []
    lines = _lines(text)
    if _RETIRED_HEADING not in lines:
        errors.append("[history] no '## Retired IDs' section")
    in_retired = False
    for number, line in enumerate(lines, 1):
        if line.startswith("#"):
            in_retired = line == _RETIRED_HEADING
            continue
        if _RULE_LINE.match(line):
            errors.append(f"[history] line {number}: a rule line: {line[:80]!r}")
        for modal in re.findall(r"\b(MUST|MAY)\b", line):
            errors.append(f"[history] line {number}: uppercase {modal}")
        if in_retired:
            if line.strip() and not _RETIRED_LINE.match(line):
                errors.append(f"[history] line {number}: not '- **<ID>** retired → H-NN': {line[:80]!r}")
        elif line.startswith("- **") and not _HISTORY_ENTRY.match(line):
            errors.append(f"[history] line {number}: an entry not in the format "
                          f"'- **H-NN** (<date>[, <date>…] | undated) <what> → <rule IDs>': {line[:100]!r}")
    errors += [f"[history] duplicate entry {h}" for h, n in Counter(history_ids(text)).items() if n > 1]
    return errors


# ---------------------------------------------------------------------------
# Traceability and decisions
# ---------------------------------------------------------------------------


def _keys(row: dict[str, str]) -> list[str]:
    cell = row["old-meaning key"]
    return [] if _is_blank(cell) else [k.strip() for k in cell.split(",") if k.strip()]


def traceability_errors(rows: list[dict[str, str]], research_text: str, history_text: str, meanings) -> list[str]:
    """AC3 and AC6, both directions: the inventory-ID column is the 149 IDs with no duplicates; every
    new-ID cell names at least one ID and each resolves (a rule line, or a history entry); every rule
    ID appears in some new-ID cell; no retired ID names a rule; every key exists in ``meanings``."""
    errors = []
    ids = [r["inventory ID"] for r in rows if not _is_blank(r["inventory ID"])]
    errors += [f"[trace] duplicate inventory ID {i}" for i, n in Counter(ids).items() if n > 1]
    errors += [f"[trace] {i} is not an inventory ID" for i in sorted(set(ids) - INVENTORY_IDS)]
    missing = sorted(INVENTORY_IDS - set(ids), key=_order_key)
    if missing:
        errors.append(f"[trace] {len(missing)} inventory IDs missing from the table: {missing[:10]}")
    rules, entries = set(rule_ids(research_text)), set(history_ids(history_text))
    named: set[str] = set()
    for row in rows:
        label = row["inventory ID"] if not _is_blank(row["inventory ID"]) else f"addition {row['new ID(s)']}"
        new_rules, new_h = _new_rule_ids(row), _H_ID.findall(row["new ID(s)"])
        if not new_rules and not new_h:
            errors.append(f"[trace] {label}: names no new ID: {row['new ID(s)']!r}")
        named.update(new_rules)
        errors += [f"[trace] {label}: new ID {i} is not a rule line of research/00" for i in new_rules if i not in rules]
        errors += [f"[trace] {label}: {h} is not a history entry" for h in new_h if h not in entries]
        errors += [f"[trace] {label}: no such old-meaning key {k!r}" for k in _keys(row) if k not in meanings]
        for token in re.findall(r"\b[A-Z][A-Z0-9]*-\d+\b", row["new ID(s)"]):
            if not _RULE_ID.fullmatch(token) and not _H_ID.fullmatch(token):
                errors.append(f"[trace] {label}: {token} is neither a rule ID nor an H-NN")
    errors += [f"[trace] rule {i} is not named by any traceability row" for i in sorted(rules - named, key=_order_key)]
    retired = retired_ids(rows)
    errors += [f"[trace] {i}: merged away with no decision to retire under; cite its H-NN in the new-ID cell"
               for i, h in retired.items() if h is None]
    listed = _retired_listed(history_text)
    for i in sorted((set(retired) | set(listed)) & rules, key=_order_key):
        errors.append(f"[trace] retired ID {i} is reused by a rule line")
    return errors


def decision_column_errors(rows: list[dict[str, str]], complete: bool, meanings=None) -> list[str]:
    """R3 over the decision, meaning and key columns. Always: ``meaning changed`` is yes or no; every
    C-number is one of ``DECISIONS`` (a further one is a stop-and-ask); a ``yes`` row is authorized by
    at least one C-number outside NO_ONLY or a ``NON_C_AUTHORITIES`` token whose frozen set holds the
    row's inventory ID (M1) -- a no-only C-number
    never justifies a meaning change on its own, but may share an authorized cell (R3 "A shared cell",
    e.g. PRIN-08's "C24, C25"), and a cell of ``—`` authorizes nothing (M2); every ``yes`` row names an
    old-meaning key (F008 AC6); every row citing C19 or C25 names a key, a ``no`` row one whose decision
    is that C-number, and any row citing C25 C25's own key (R3; S5); the decision every key a row
    names records -- its **leading** token (``_lead_decision``), not any token its prose mentions --
    is cited in the row's decision cell, so a key cannot be borrowed from another row's change (M2,
    M1); and a key in ``KEY_OWNERS`` is named only by a row its frozen set holds, so a row citing the
    same decision still cannot borrow it (iteration 3, S1); a ``yes`` row whose keys all exist names
    at least one whose leading decision itself authorizes the yes (``_key_authorizes``), so a keyed
    ``no`` row cannot turn ``yes`` by adding an unrelated authorizing C-number beside its no-only one
    (iteration 4, M1); an addition row cites a decision. The key checks read ``meanings``
    (default: the support module's ``OLD_MEANINGS``); a key absent from it is
    ``traceability_errors``' finding. With ``complete``, every decision appears and every decision
    outside NO_ONLY backs at least one ``yes`` row. Group ownership is coverage, not exclusivity (R3),
    so a ``yes`` row may cite a C-number another group owns; the owner's duty is
    ``group_coverage_errors``."""
    meanings = _OM.OLD_MEANINGS if meanings is None else meanings
    errors = []
    for row in rows:
        label = row["inventory ID"] if not _is_blank(row["inventory ID"]) else f"addition {row['new ID(s)']}"
        meaning, cs = row["meaning changed"], _C_ID.findall(row["decision"])
        cell_tokens = set(_DECISION_TOKEN.findall(row["decision"]))
        if meaning not in ("yes", "no"):
            errors.append(f"[decision] {label}: meaning changed must be yes or no, not {meaning!r}")
        errors += [f"[decision] {label}: {c} is not a decision in the decisions reference (R3: stop and ask)"
                   for c in cs if c not in YES_ROW_OWNER]
        if meaning == "yes":
            no_only = [c for c in cs if c in NO_ONLY]
            misplaced = sorted(t for t in cell_tokens & NON_C_AUTHORITIES.keys()
                               if row["inventory ID"] not in NON_C_AUTHORITIES[t])
            authorized = any(c in YES_ROW_OWNER and c not in NO_ONLY for c in cs) or any(
                row["inventory ID"] in NON_C_AUTHORITIES[t] for t in cell_tokens & NON_C_AUTHORITIES.keys())
            if no_only and not authorized:
                errors.append(f"[decision] {label}: {', '.join(no_only)} never authorizes a yes on its own; "
                              "cite the C-number that changes the meaning, or mark the row no (R3)")
            elif misplaced and not authorized:
                errors += [f"[decision] {label}: {t} authorizes a yes only on {sorted(NON_C_AUTHORITIES[t])}, "
                           f"not on {label} (R3, M1: NON_C_AUTHORITIES is frozen)" for t in misplaced]
            elif not authorized:
                errors.append(f"[decision] {label}: a yes row cites no decision that authorizes a meaning "
                              f"change ({row['decision']!r}); cite a C-number outside NO_ONLY or one of "
                              f"{sorted(NON_C_AUTHORITIES)} (R3, M2)")
            if _is_blank(row["old-meaning key"]):
                errors.append(f"[decision] {label}: a yes row must name an old-meaning key (F008 AC6)")
        keys = _keys(row)
        key_lead = {k: _lead_decision(meanings[k].decision) for k in keys if k in meanings}
        if (meaning == "yes" and keys and all(k in meanings for k in keys)
                and not any(_key_authorizes(lead, row["inventory ID"]) for lead in key_lead.values())):
            errors.append(f"[decision] {label}: a yes row names no old-meaning key whose decision authorizes "
                          f"the yes ({', '.join(f'{k} -> {key_lead[k]}' for k in dict.fromkeys(keys))}); "
                          "name the key of the C-number outside NO_ONLY, or of the NON_C_AUTHORITIES token, "
                          "that changes the meaning (R3, iteration 4 M1)")
        for c in KEYED_NO_ONLY:
            if c not in cs:
                continue
            if not keys:
                errors.append(f"[decision] {label}: cites {c}, whose old meaning is still stated downstream, "
                              "and names no old-meaning key (R3, F008 AC6)")
            elif ((meaning == "no" or c == "C25") and len(key_lead) == len(keys)
                  and c not in key_lead.values()):
                errors.append(f"[decision] {label}: cites {c} and names no old-meaning key whose decision is "
                              f"{c}: {keys} (R3, F008 AC6)")
        for k in dict.fromkeys(keys):
            if k in key_lead and key_lead[k] not in cell_tokens:
                errors.append(f"[decision] {label}: old-meaning key {k!r} records decision {key_lead[k]!r}, "
                              f"which the row's cell {row['decision']!r} does not cite (M2, M1: a key "
                              "cannot be borrowed)")
            elif k in KEY_OWNERS and row["inventory ID"] not in KEY_OWNERS[k]:
                errors.append(f"[decision] {label}: old-meaning key {k!r} belongs to {sorted(KEY_OWNERS[k])}, "
                              f"not to {label} (S1: KEY_OWNERS is frozen; a key cannot be borrowed)")
        if _is_blank(row["inventory ID"]) and _is_blank(row["decision"]):
            errors.append(f"[decision] {label}: an addition row must cite a decision")
    if complete:
        cited = {c for r in rows for c in _C_ID.findall(r["decision"])}
        yes = {c for r in rows if r["meaning changed"] == "yes" for c in _C_ID.findall(r["decision"])}
        errors += [f"[decision] {c} appears in no decision cell (R3)" for c in DECISIONS if c not in cited]
        errors += [f"[decision] {c} backs no yes row (R3; YES_ROW_OWNER gives it to {YES_ROW_OWNER[c]})"
                   for c in DECISIONS if c not in NO_ONLY and c in cited and c not in yes]
    return errors


def _key_authorizes(lead: str | None, inventory_id: str) -> bool:
    """Whether a key's leading decision authorizes a ``yes`` on ``inventory_id`` by itself: a C-number
    of ``DECISIONS`` outside NO_ONLY, or a ``NON_C_AUTHORITIES`` token whose frozen set holds the row
    (iteration 4, M1)."""
    if lead is None:
        return False
    return (lead in YES_ROW_OWNER and lead not in NO_ONLY) or inventory_id in NON_C_AUTHORITIES.get(lead, ())


def group_coverage_errors(rows: list[dict[str, str]], group: str) -> list[str]:
    """R3 "Ownership is coverage, not exclusivity" (user, 2026-09-25): for every C-number
    ``YES_ROW_OWNER`` gives to ``group``, some row in ``rows`` is ``yes`` and cites it. ``yes`` rows
    citing C-numbers other groups own are allowed. NO_ONLY C-numbers never authorize a ``yes``, so
    no group owes one for them."""
    yes = {c for r in rows if r["meaning changed"] == "yes" for c in _C_ID.findall(r["decision"])}
    return [f"[coverage] group {group} owns {c} (YES_ROW_OWNER) and writes no yes row citing it (R3)"
            for c in DECISIONS if YES_ROW_OWNER[c] == group and c not in NO_ONLY and c not in yes]


def operative_string_errors(research_text: str, rows: list[dict[str, str]], meanings=None,
                            group: str | None = None) -> list[str]:
    """AC7: for each decision in ``OPERATIVE`` that some row cites, the rule ``OPERATIVE`` names for it
    carries each operative string after ``normalize()``. Only decisions the rows cite are checked, so
    it runs per group; with ``group`` set, a named rule outside that group is left to the assembled
    check. C05's absence half is built from the ``OLD_MEANINGS`` entries whose decision is C05
    (``meanings`` defaults to the support module's): each pattern must match nowhere in the text."""
    meanings = _OM.OLD_MEANINGS if meanings is None else meanings
    errors = []
    cited = {c for r in rows for c in _C_ID.findall(r["decision"])}
    blocks = rule_blocks(research_text)
    for decision, (rule_id, strings) in OPERATIVE.items():
        if decision not in cited:
            continue
        if rule_id not in blocks:
            if group is None or _GROUP_OF[_prefix(rule_id)] == group:
                errors.append(f"[operative] {decision}: its rule {rule_id} is not in the text")
            continue
        flat = normalize(blocks[rule_id])
        errors += [f"[operative] {decision}: {rule_id} does not state {s!r} (after normalize)"
                   for s in strings if normalize(s) not in flat]
    if "C05" in cited:
        c05 = {k: m for k, m in meanings.items() if re.search(r"\bC05\b", m.decision)}
        if not c05:
            errors.append("[operative] C05: no OLD_MEANINGS entry with decision C05, so the absence of the "
                          "reopened-decision wording cannot be asserted")
        flat = normalize(research_text)
        for key, entry in c05.items():
            try:
                if re.search(entry.pattern, flat):
                    errors.append(f"[operative] C05: the old wording of {key!r} is still stated")
            except re.error as exc:
                errors.append(f"[operative] C05: {key!r} does not compile: {exc}")
    return errors


def proxy_errors(sentence: str, block: str) -> list[str]:
    """AC9 and R6: an unchanged row's inventory sentence survives in its mapped block(s). Date
    expressions are removed first, and the block must then cite an ``H-NN``. Then every number
    (``\\d+(?:\\.\\d+)?``, after ``−`` and ``–`` fold to ``-``), every backticked identifier (exactly)
    and each quantifier (``QUANTIFIERS``, whole words, any case) must appear in the block."""
    errors = []
    undated, removed = _ISO_DATE.subn(" ", sentence)
    if removed and not _H_ID.search(block):
        errors.append("[proxy] the sentence carries a date, and the block cites no H-NN for it")
    fold = str.maketrans({"−": "-", "–": "-"})
    have = set(_NUMBER.findall(block.translate(fold)))
    lost = [n for n in dict.fromkeys(_NUMBER.findall(_CODE_SPAN.sub(" ", undated).translate(fold))) if n not in have]
    for span in re.findall(r"`([^`]*)`", undated):
        if span not in block:
            errors.append(f"[proxy] identifier `{span}` is lost")
        lost += [n for n in _NUMBER.findall(span.translate(fold)) if n not in have and n not in lost]
    errors += [f"[proxy] number {n} is lost" for n in lost]
    for q in QUANTIFIERS:
        words = q.replace(" ", r"\s+")
        pattern = rf"\b{words}\b"
        if re.search(pattern, undated, re.IGNORECASE) and not re.search(pattern, block, re.IGNORECASE):
            errors.append(f"[proxy] quantifier {q!r} is lost")
    return errors


def pinned_errors(research_text: str, repo_root) -> list[str]:
    """AC6: every ``Pinned: path::name`` names a file under ``repo_root`` in which ``def name`` is
    found; a parametrised ``name[id]`` is looked up by ``name``."""
    errors = []
    for line in _lines(research_text):
        m = _PINNED.match(line)
        if not m or not m.group("path"):
            continue
        path = Path(repo_root) / m.group("path")
        name = m.group("node").split("::")[-1].split("[", 1)[0]
        if not path.is_file():
            errors.append(f"[pinned] {m.group('path')} does not exist")
        elif not re.search(rf"^\s*(?:async\s+)?def {re.escape(name)}\(", path.read_text(encoding="utf-8"), re.M):
            errors.append(f"[pinned] no def {name} in {m.group('path')}")
    return errors


def old_meaning_errors(entries, research_text: str) -> list[str]:
    """R4, for each entry: the pattern compiles, matches ``normalize(example)``, and matches nowhere
    in ``normalize(research_text)``; ``source`` is ``path:line@4e47d0e``."""
    errors = []
    flat = normalize(research_text)
    for key, entry in entries.items():
        if not _SOURCE.match(entry.source):
            errors.append(f"[old-meaning] {key}: source {entry.source!r} is not path:line@4e47d0e")
        try:
            pattern = re.compile(entry.pattern)
        except re.error as exc:
            errors.append(f"[old-meaning] {key}: pattern does not compile: {exc}")
            continue
        if not pattern.search(normalize(entry.example)):
            errors.append(f"[old-meaning] {key}: pattern does not match its normalized example")
        if pattern.search(flat):
            errors.append(f"[old-meaning] {key}: pattern matches research/00 (the old meaning is still stated)")
    return errors


# ---------------------------------------------------------------------------
# Review scope (R7)
# ---------------------------------------------------------------------------


def required_review_rows(research_text: str, rows: list[dict[str, str]], group: str | None = None) -> list[str]:
    """R7's row IDs, in document order: the block of every rule a ``no`` or ``yes`` row names
    (``<ID>``), and every rule's ``<ID>/Scope``, ``<ID>/Not`` and ``<ID>/Why`` line. ``group`` keeps
    only the rules whose prefix is in that group."""
    blocks = rule_blocks(research_text)
    named = {i for r in rows if r["meaning changed"] in ("yes", "no") for i in _new_rule_ids(r)}
    need = []
    for rule_id, block in blocks.items():
        if group is not None and _GROUP_OF.get(_prefix(rule_id)) != group:
            continue
        if rule_id in named:
            need.append(rule_id)
        kinds = [line.split(":", 1)[0] for line in block.splitlines()[1:]]
        need += [f"{rule_id}/{k}" for k in ("Scope", "Not", "Why") if k in kinds]
    return need


# ---------------------------------------------------------------------------
# Assembly (R11) and the checks that span groups
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assembly:
    research: str
    history: str
    table: str
    meanings: dict
    problems: tuple[str, ...]


def _draft_blocks(text: str, name: str, problems: list[str]) -> dict[str, list[tuple[str, str]]]:
    """``{heading: [(rule_id, block)]}`` from one rules draft."""
    placed: dict[str, list[tuple[str, str]]] = {}
    heading, current = None, None
    for number, line in enumerate(_lines(text), 1):
        if line.startswith("#"):
            heading, current = line, None
            if line not in HEADINGS[1:]:
                problems.append(f"[assemble] {name} line {number}: a heading not in HEADINGS: {line[:100]!r}")
            continue
        m = _RULE_LINE.match(line)
        if m:
            if heading is None:
                problems.append(f"[assemble] {name} line {number}: {m.group('id')} sits under no heading")
                current = None
                continue
            current = [line]
            placed.setdefault(heading, []).append((m.group("id"), current))
        elif line.strip() and current is not None:
            current.append(line)
        elif line.strip():
            problems.append(f"[assemble] {name} line {number}: a line outside any rule block: {line[:100]!r}")
        else:
            current = None
    return {h: [(i, "\n".join(b)) for i, b in blocks] for h, blocks in placed.items()}


def _read(drafts: Path, name: str, problems: list[str]) -> str:
    path = drafts / name
    if not path.is_file():
        problems.append(f"[assemble] no draft {name}")
        return ""
    return path.read_text(encoding="utf-8")


def assemble(drafts_dir) -> Assembly:
    """Build research/00, the history, the traceability table and ``OLD_MEANINGS`` from the drafts
    (R11): the title, the Glossary directly before the first Part, then every heading of ``HEADINGS``
    in order, each holding the rules the drafts placed under it (merged across drafts) in
    ``PREFIX_ORDER`` then by number; ``## Retired IDs`` filled from ``retired_ids``. A missing group
    draft is a problem, not an exception, so a partial set still assembles for inspection."""
    drafts, problems = Path(drafts_dir), []
    glossary = [line for line in _lines(_read(drafts, "glossary.txt", problems))
                if line.strip() and line != GLOSSARY_HEADING]
    placed: dict[str, list[tuple[str, str]]] = {}
    table_rows, meanings = [], {}
    for group in GROUPS:
        if not (drafts / f"{group}.rules.txt").is_file():
            continue
        for heading, blocks in _draft_blocks(_read(drafts, f"{group}.rules.txt", problems), f"{group}.rules.txt", problems).items():
            placed.setdefault(heading, []).extend(blocks)
        table_rows += [line for line in _lines(_read(drafts, f"{group}.trace.txt", problems)) if line.strip().startswith("|")]
        if (drafts / f"{group}.meanings.txt").is_file():
            try:
                for key, entry in load_meanings_jsonl(_read(drafts, f"{group}.meanings.txt", problems)).items():
                    if key in meanings:
                        problems.append(f"[assemble] duplicate old-meaning key {key!r} ({group})")
                    meanings[key] = entry
            except ValueError as exc:
                problems.append(f"[assemble] {group}.meanings.txt: {exc}")
    out = [HEADINGS[0], "", GLOSSARY_HEADING, "", *glossary, ""]
    for heading in HEADINGS[1:]:
        out += [heading, ""]
        for _rule_id, block in sorted(placed.get(heading, []), key=lambda b: _order_key(b[0])):
            out += [block, ""]
    table = "\n".join([TRACE_HEADER, "|" + "---|" * len(TRACE_COLUMNS), *table_rows]) + "\n"
    try:
        parsed = parse_traceability(table)
        problems += [f"[assemble] traceability: a malformed inventory-ID cell {r['inventory ID']!r} (one rule ID "
                     f"or '{ADDITION}')" for r in parsed
                     if not _is_blank(r["inventory ID"]) and not _RULE_ID.fullmatch(r["inventory ID"])]
        retired = retired_ids(parsed)
    except ValueError as exc:
        problems.append(f"[assemble] traceability: {exc}")
        retired = {}
    history = _read(drafts, "history.txt", problems).replace("\r\n", "\n").rstrip("\n")
    if _RETIRED_HEADING in history:
        history = history.split(_RETIRED_HEADING, 1)[0].rstrip("\n")
    else:
        problems.append("[assemble] history.txt has no '## Retired IDs' section")
    listed = [f"- **{i}** retired → {h}" for i, h in sorted(retired.items(), key=lambda kv: _order_key(kv[0])) if h]
    history = "\n".join([history, "", _RETIRED_HEADING, "", *listed]) + "\n"
    return Assembly("\n".join(out).rstrip("\n") + "\n", history, table, meanings, tuple(problems))


def _anchor_errors(research_text: str) -> list[str]:
    """R11: each anchor rule (the three source_change anchors and the FIG-01 lead) exists once, its
    ID plus opening words is unique in the flattened file, and the three HRV anchors' paragraphs say
    "per-tier dataset"."""
    errors, flat, blocks = [], normalize(research_text), rule_blocks(research_text)
    for rule_id in ANCHOR_RULES:
        if rule_id not in blocks:
            errors.append(f"[anchor] {rule_id} carries an anchor and has no rule line")
            continue
        lead = " ".join(normalize(blocks[rule_id].splitlines()[0]).split()[:6])
        if flat.count(lead) != 1:
            errors.append(f"[anchor] {rule_id}'s lead {lead!r} occurs {flat.count(lead)} times in the flattened file, not once")
        if rule_id in PER_TIER_ANCHORS and "per-tier dataset" not in normalize(blocks[rule_id]):
            errors.append(f"[anchor] {rule_id}'s paragraph does not say 'per-tier dataset' (R11)")
    return errors


def _sentence_digest(cell: str) -> str:
    """The frozen form of an inventory sentence: sha256 of the cell with every run of whitespace
    collapsed to one space. Backticks, underscores, digits, quotes and case are kept, since the AC9
    proxy reads backticked identifiers and numbers exactly (iteration 2, S1)."""
    return hashlib.sha256(" ".join(cell.split()).encode("utf-8")).hexdigest()


def inventory_sentence_errors(rows: list[dict[str, str]]) -> list[str]:
    """AC9's proxy input, frozen (sprint-007 review iteration 1, S3): every non-addition row's
    "inventory sentence" cell is non-blank and its ``_sentence_digest`` is
    ``INVENTORY_SENTENCE_SHA256[ID]``. Without it, a cell set to ``—`` left the proxy, and a cell
    edited alongside its rule passed it. The digest is not taken after ``normalize()``, which let a
    cell drop its backticks and the proxy its identifier (iteration 2, S1)."""
    errors = []
    for row in rows:
        inv = row["inventory ID"]
        if _is_blank(inv) or inv not in INVENTORY_SENTENCE_SHA256:
            continue
        cell = row["inventory sentence"]
        if _is_blank(cell):
            errors.append(f"[inventory] {inv}: the inventory sentence cell is blank ({cell!r})")
        elif _sentence_digest(cell) != INVENTORY_SENTENCE_SHA256[inv]:
            errors.append(f"[inventory] {inv}: the inventory sentence cell is not the frozen inventory "
                          f"sentence: {cell[:120]!r}")
    return errors


#: The hex digits of one cell's or one field's digest in ``TRACEABILITY_ROW_SHA256`` and
#: ``OLD_MEANING_SHA256`` (48 bits each; the row or entry value is all of them).
_DIGEST_HEX = 12
_OLD_MEANING_FIELDS = ("pattern", "example", "source", "decision")


def _cell_digest(cell: str) -> str:
    """One traceability cell's frozen form: the first 12 hex of ``_sentence_digest`` (iteration 4, M1)."""
    return _sentence_digest(cell)[:_DIGEST_HEX]


def _row_key(row: dict[str, str]) -> str:
    """A row's key in ``TRACEABILITY_ROW_SHA256``: its inventory ID, or ``addition <new ID(s)>``."""
    inv = row["inventory ID"]
    return inv if not _is_blank(inv) else f"addition {' '.join(row['new ID(s)'].split())}"


def row_digest(row: dict[str, str]) -> str:
    """The row's frozen form: each cell's ``_cell_digest``, in ``TRACE_COLUMNS`` order, joined by ``.``."""
    return ".".join(_cell_digest(row[column]) for column in TRACE_COLUMNS)


def traceability_row_errors(rows: list[dict[str, str]], frozen: dict[str, str] | None = None) -> list[str]:
    """The table is the frozen table (iteration 4, M1 and M2), both ways: each row key occurs once; every
    frozen row is in the table and every table row is frozen (no row added, removed or re-keyed); and
    each row's ``row_digest`` is its frozen digest. Each message names the row and the cell that
    differs, and prints the row's new digest for a deliberate regeneration."""
    frozen = TRACEABILITY_ROW_SHA256 if frozen is None else frozen
    keys = [_row_key(r) for r in rows]
    errors = [f"[frozen-table] row {k} occurs {n} times, not once" for k, n in Counter(keys).items() if n > 1]
    errors += [f"[frozen-table] row {k} is frozen in TRACEABILITY_ROW_SHA256 and is not in the table"
               for k in sorted(frozen.keys() - set(keys))]
    for row, key in zip(rows, keys, strict=True):
        digest = row_digest(row)
        if key not in frozen:
            errors.append(f"[frozen-table] row {key} is not in TRACEABILITY_ROW_SHA256 (a row was added or "
                          f"its key cell changed); its digest is {digest!r}")
            continue
        for column, have, want in zip(TRACE_COLUMNS, digest.split("."), frozen[key].split("."), strict=False):
            if have != want:
                errors.append(f"[frozen-table] {key}: the {column!r} cell is not the frozen cell (now "
                              f"{row[column][:120]!r}); once the edit is reviewed, "
                              f"TRACEABILITY_ROW_SHA256[{key!r}] = {digest!r}")
    return errors


#: The committed table's separator line, exactly: one ``---`` per column of ``TRACE_HEADER``.
TRACE_SEPARATOR = "|" + "---|" * len(TRACE_COLUMNS)


def table_line_errors(table_text: str) -> list[str]:
    """Every line of 00-traceability.md is bound (iteration 6, S1): line 1 is ``TRACE_HEADER``, line 2 is
    ``TRACE_SEPARATOR``, every later line is a row ``parse_traceability`` returns, and nothing follows
    but the final newline. ``parse_traceability`` skips any line that does not start with ``|``, so a
    ``Correction (C38): ... read its row as yes`` line after the last row (U1c), a blockquoted
    ``> | DOC-03 | ... | yes |`` row (U1b) and a deleted separator (U2) each left every row, and so every
    frozen digest, unchanged, with the suite green. Each message names the line."""
    lines = _lines(table_text)
    lines = lines[:-1] if lines and lines[-1] == "" else lines
    errors = []
    for number, line in enumerate(lines, 1):
        if number == 1:
            if line != TRACE_HEADER:
                errors.append(f"[frozen-table] line 1: the header is not TRACE_HEADER (now {line[:120]!r})")
            continue
        if number == 2:
            if line != TRACE_SEPARATOR:
                errors.append(f"[frozen-table] line 2: the separator is not {TRACE_SEPARATOR!r} (now {line[:120]!r})")
            continue
        cells = _split_cells(line)
        is_row = (line.startswith("|") and len(cells) == len(TRACE_COLUMNS) and cells[0] != TRACE_COLUMNS[0]
                  and not _is_separator(cells))
        if not is_row:
            errors.append(f"[frozen-table] line {number}: a table line that is not a row cannot be bound: "
                          f"{line[:120]!r}")
    return errors


def retirement_errors(rows: list[dict[str, str]], history_text: str,
                      frozen: dict[str, str] | None = None) -> list[str]:
    """Iteration 4, M2, over the committed table and history. The IDs the table retires
    (``retired_ids``) and the IDs ``## Retired IDs`` lists each equal ``RETIRED_IDS``. And a row that
    retires under an H-NN it names is ``yes`` and cites a decision whose ``_decision_group_entry`` is
    that H-NN (PRIN-16: C08 is Group B, H-39). A ``no`` row naming only an H-NN names no rule, so the
    AC9 proxy (``_proxy_rows``) never reads it: DOC-03 retired under H-39 with its changed text
    re-homed on an addition row stayed green. Not part of ``traceability_errors``: R11 lets a draft
    move a row to a history entry (the synthetic world's DOC-15 -> H-01); the committed table has no
    such row, and this holds it to that."""
    frozen = RETIRED_IDS if frozen is None else frozen
    errors = []
    for source, got in (("the table (retired_ids)", retired_ids(rows)),
                        ("## Retired IDs", _retired_listed(history_text))):
        for i in sorted(got.keys() | frozen.keys()):
            if got.get(i) != frozen.get(i):
                errors.append(f"[retired] {source} retires {i} under {got.get(i)}, and RETIRED_IDS says "
                              f"{frozen.get(i)} (M2: RETIRED_IDS is frozen)")
    for row in rows:
        inv, h = row["inventory ID"], _H_ID.findall(row["new ID(s)"])
        if _is_blank(inv) or not _RULE_ID.fullmatch(inv) or inv in _new_rule_ids(row) or not h:
            continue
        entries = {t: _decision_group_entry(t) for t in _DECISION_TOKEN.findall(row["decision"])}
        if row["meaning changed"] != "yes":
            errors.append(f"[retired] {inv}: retires under {h[0]} on a {row['meaning changed']!r} row; a rule "
                          "retired under an H-NN changes meaning, so the row is yes (M2)")
        if h[0] not in entries.values():
            errors.append(f"[retired] {inv}: retires under {h[0]}, and no decision its cell cites "
                          f"({row['decision']!r}) retires under {h[0]}: {entries} (M2)")
    return errors


def old_meaning_digest(entry) -> str:
    """An ``OldMeaning``'s frozen form: the first 12 hex of sha256 of each raw field, in
    ``_OLD_MEANING_FIELDS`` order, joined by ``.`` (iteration 4, S1)."""
    return ".".join(hashlib.sha256(getattr(entry, name).encode("utf-8")).hexdigest()[:_DIGEST_HEX]
                    for name in _OLD_MEANING_FIELDS)


def old_meaning_digest_errors(meanings, frozen: dict[str, str] | None = None) -> list[str]:
    """``meanings`` is the frozen ``OLD_MEANINGS`` (iteration 4, S1), both ways: every frozen key is
    present, every key is frozen, and each entry's ``old_meaning_digest`` is its frozen digest. Each
    message names the key and the field that differs, and prints the entry's new digest."""
    frozen = OLD_MEANING_SHA256 if frozen is None else frozen
    errors = [f"[frozen-meanings] {k} is frozen in OLD_MEANING_SHA256 and is not in OLD_MEANINGS"
              for k in sorted(frozen.keys() - meanings.keys())]
    for key in sorted(meanings):
        entry, digest = meanings[key], old_meaning_digest(meanings[key])
        if key not in frozen:
            errors.append(f"[frozen-meanings] {key} is not in OLD_MEANING_SHA256 (a key was added or renamed); "
                          f"its digest is {digest!r}")
            continue
        for name, have, want in zip(_OLD_MEANING_FIELDS, digest.split("."), frozen[key].split("."), strict=False):
            if have != want:
                errors.append(f"[frozen-meanings] {key}: the {name!r} field is not the frozen field (now "
                              f"{getattr(entry, name)[:120]!r}); once the edit is reviewed, "
                              f"OLD_MEANING_SHA256[{key!r}] = {digest!r}")
    return errors


def reviewed_block_lines(research_text: str, rows: list[dict[str, str]]) -> tuple[dict[str, str], list[str]]:
    """``({label: line}, problems)`` for each ``required_review_rows`` label (iteration 4, M3): ``<ID>`` is
    the rule's own line and ``<ID>/Scope``, ``/Not``, ``/Why`` its line of that kind, from
    ``rule_blocks``. A label whose block has no such line, or more than one (a repeated rule ID merges
    two blocks), is a problem, not a line."""
    blocks = {rule_id: block.split("\n") for rule_id, block in rule_blocks(research_text).items()}
    lines, problems = {}, []
    for label in required_review_rows(research_text, rows):
        rule_id, _, kind = label.partition("/")
        block = blocks.get(rule_id, [])
        hits = [line for line in block if (_RULE_LINE.match(line) if not kind
                                           else (m := _SUB_LINE.match(line)) and m.group("kind") == kind)]
        if len(hits) == 1:
            lines[label] = hits[0]
        else:
            problems.append(f"[reviewed-block] {label}: {len(hits)} block lines, not one")
    return lines, problems


def reviewed_block_errors(research_text: str, rows: list[dict[str, str]],
                          frozen: dict[str, str] | None = None) -> list[str]:
    """Each meaning-review verdict is bound to the text it judged (iteration 4, M3, ruled 2026-09-26),
    both ways: the labels of ``reviewed_block_lines`` are exactly the frozen keys, and each line's
    ``_cell_digest`` is its frozen digest. A mismatch names the row, says its block line changed after
    its verdict, and prints the new digest to paste once a fresh critic has re-reviewed it."""
    frozen = REVIEWED_BLOCK_SHA256 if frozen is None else frozen
    lines, errors = reviewed_block_lines(research_text, rows)
    required = set(required_review_rows(research_text, rows))
    errors += [f"[reviewed-block] {label} is frozen in REVIEWED_BLOCK_SHA256 and is not a required review row"
               for label in sorted(frozen.keys() - required)]
    for label, line in lines.items():
        digest = _cell_digest(line)
        if label not in frozen:
            errors.append(f"[reviewed-block] {label} is a required review row and is not in "
                          f"REVIEWED_BLOCK_SHA256, so no verdict is bound to its text; once a critic has "
                          f"reviewed it, REVIEWED_BLOCK_SHA256[{label!r}] = {digest!r}")
        elif digest != frozen[label]:
            errors.append(f"[reviewed-block] {label}: the block line changed after its meaning-review verdict "
                          f"(now {line[:120]!r}); a fresh critic must re-review {label} in "
                          f"00-meaning-review.md, then REVIEWED_BLOCK_SHA256[{label!r}] = {digest!r}")
    return errors


#: A Glossary line's key: its T-NN, read before the format check, so an edit that breaks the format is
#: still named by its term (``glossary_errors`` reports the format).
_GLOSSARY_KEY = re.compile(r"^- \*\*(?P<id>T-\d{2})\b")
#: A history entry's key: its H-NN, read before the format check (``history_errors`` reports the format).
_HISTORY_KEY = re.compile(r"^- \*\*(?P<id>H-\d{2})\*\* ")


def _keyed_digest(lines: list[str]) -> str:
    """A keyed entry's frozen form (iteration 5): each line's ``_cell_digest``, in order, joined by ``.``.
    Every key has one line except ``<ID>/Pinned`` on a rule with more than one Pinned line (PRIN-15,
    FIG-01, FIG-02), so a mismatch still names its line."""
    return ".".join(_cell_digest(line) for line in lines)


def _first_difference(have, want) -> int:
    """The first index at which two sequences differ; the shorter length when one is a prefix of the other."""
    return next((i for i, (a, b) in enumerate(zip(have, want)) if a != b), min(len(have), len(want)))


def keyed_line_errors(tag: str, literal: str, where: str, entries: list[tuple[str, list[str]]],
                      frozen: dict[str, str], then: str) -> list[str]:
    """The iteration-5 binding, both ways, shared by ``GLOSSARY_SHA256``, ``PINNED_SHA256``,
    ``HISTORY_SHA256`` and ``REVIEW_LINE_SHA256``: each key occurs once, every frozen key is in
    ``where`` and every key in ``where`` is frozen, and each key's ``_keyed_digest`` is its frozen
    digest. A mismatch names the key and the first of its lines that differs; every message says what
    must happen first (``then``) and prints the new digest to paste."""
    keys = [key for key, _lines_of_key in entries]
    errors = [f"[{tag}] {key} occurs {n} times in {where}, not once" for key, n in Counter(keys).items() if n > 1]
    errors += [f"[{tag}] {key} is frozen in {literal} and is not in {where}" for key in sorted(frozen.keys() - set(keys))]
    for key, lines in entries:
        digest = _keyed_digest(lines)
        if key not in frozen:
            errors.append(f"[{tag}] {key} is not in {literal} (a line was added or re-keyed); {then}, "
                          f"{literal}[{key!r}] = {digest!r}")
        elif digest != frozen[key]:
            have, want = digest.split("."), frozen[key].split(".")
            first = _first_difference(have, want)
            now = repr(lines[first][:120]) if first < len(lines) else "no such line: a line was removed"
            errors.append(f"[{tag}] {key}: the line changed (now {now}); {then}, {literal}[{key!r}] = {digest!r}")
    return errors


def glossary_entries(research_text: str) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """``([(T-NN, [line])], problems)`` over the Glossary section, in order (iteration 5, M1). A
    non-blank line with no T-NN key is a problem, so no Glossary line is unbound."""
    lines = _lines(research_text)
    span = _glossary_span(lines)
    if span is None:
        return [], [f"[frozen-glossary] research/00 has no {GLOSSARY_HEADING!r} section"]
    entries, problems = [], []
    for number in range(*span):
        line = lines[number]
        if not line.strip():
            continue
        if m := _GLOSSARY_KEY.match(line):
            entries.append((m.group("id"), [line]))
        else:
            problems.append(f"[frozen-glossary] line {number + 1}: a Glossary line with no T-NN key cannot be "
                            f"bound: {line[:120]!r}")
    return entries, problems


def glossary_digest_errors(research_text: str, frozen: dict[str, str] | None = None) -> list[str]:
    """Each Glossary definition is the frozen definition (iteration 5, M1), both ways. The T-NN lines are
    normative IS definitions outside every rule block, so neither the review nor the proxy read them:
    T-13's 14 days became 21, T-03's ``>=`` became ``>`` and T-05's HRV Status became a tier, with the
    suite green, though R13 states T-05, T-13 and T-26 unchanged."""
    frozen = GLOSSARY_SHA256 if frozen is None else frozen
    entries, problems = glossary_entries(research_text)
    return problems + keyed_line_errors(
        "frozen-glossary", "GLOSSARY_SHA256", "the Glossary", entries, frozen,
        "a Glossary change needs a fresh critic (R13 holds T-05, T-13 and T-26 unchanged): once one has reviewed it")


def pinned_entries(research_text: str) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """``([(<ID>/Pinned, [lines])], problems)`` in document order (iteration 5, S1): each rule's Pinned
    lines, in order, under one key. A Pinned line outside a rule block is a problem."""
    entries: dict[str, list[str]] = {}
    problems, current = [], None
    for number, line in enumerate(_lines(research_text), 1):
        if m := _RULE_LINE.match(line):
            current = m.group("id")
        elif current is not None and _SUB_LINE.match(line):
            if line.startswith("Pinned:"):
                entries.setdefault(f"{current}/Pinned", []).append(line)
        else:
            current = None
            if line.startswith("Pinned:"):
                problems.append(f"[frozen-pinned] line {number}: a Pinned line outside a rule block cannot be "
                                f"bound: {line[:120]!r}")
    return list(entries.items()), problems


def pinned_digest_errors(research_text: str, frozen: dict[str, str] | None = None) -> list[str]:
    """Each rule's Pinned lines are the frozen lines (iteration 5, S1), both ways. ``pinned_errors`` only
    finds the node: PRIN-26's ``Pinned: none (F009)`` retargeted to an unrelated existing test stayed
    green."""
    frozen = PINNED_SHA256 if frozen is None else frozen
    entries, problems = pinned_entries(research_text)
    return problems + keyed_line_errors(
        "frozen-pinned", "PINNED_SHA256", "research/00", entries, frozen,
        "a Pinned change needs a review that the node pins the rule: once reviewed")


def research_structure(research_text: str) -> tuple[str, ...]:
    """research/00's structure (iteration 5, S1): every heading line, Glossary T-NN and rule ID, in
    document order."""
    out = []
    for line in _lines(research_text):
        if line.startswith("#"):
            out.append(line)
        elif m := (_RULE_LINE.match(line) or _GLOSSARY_KEY.match(line)):
            out.append(m.group("id"))
    return tuple(out)


def structure_errors(research_text: str, frozen: tuple[str, ...] | None = None) -> list[str]:
    """research/00's structure is ``RESEARCH_STRUCTURE`` (iteration 5, S1). The heading check reads the
    headings alone and the rule checks read blocks alone, so moving ``### 1.2`` above PRIN-02 stayed
    green. The message names the first position that differs."""
    frozen = RESEARCH_STRUCTURE if frozen is None else frozen
    have = research_structure(research_text)
    if have == frozen:
        return []
    i = _first_difference(have, frozen)
    got, want = (have[i] if i < len(have) else "<end>"), (frozen[i] if i < len(frozen) else "<end>")
    after = have[i - 1] if i else "<start>"
    return [(f"[frozen-structure] position {i}, after {after!r}: research/00 has {got!r} where RESEARCH_STRUCTURE "
             f"has {want!r} ({len(have)} entries, {len(frozen)} frozen): a heading, term or rule moved, was added "
             f"or was removed; once the move is reviewed, paste RESEARCH_STRUCTURE from frozen_literals()")]


def history_entries(history_text: str) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """``([(key, [line])], problems)`` over every non-blank line of the history (iteration 5, S2): an
    entry is keyed by its H-NN, a ``## Retired IDs`` line by ``<ID> retired`` and a heading by its own
    text. Any other line is a problem, so no history line is unbound."""
    entries, problems = [], []
    for number, line in enumerate(_lines(history_text), 1):
        if not line.strip():
            continue
        if line.startswith("#"):
            entries.append((line, [line]))
        elif m := _HISTORY_KEY.match(line):
            entries.append((m.group("id"), [line]))
        elif m := _RETIRED_LINE.match(line):
            entries.append((f"{m.group('id')} retired", [line]))
        else:
            problems.append(f"[frozen-history] line {number}: a history line with no key cannot be bound: "
                            f"{line[:120]!r}")
    return entries, problems


def history_digest_errors(history_text: str, frozen: dict[str, str] | None = None) -> list[str]:
    """Each history line is the frozen line (iteration 5, S2), both ways. ``history_errors`` checks the
    format and the arrows only resolve: H-06's prose rewritten, or GOAL-06 dropped from its arrow list,
    stayed green."""
    frozen = HISTORY_SHA256 if frozen is None else frozen
    entries, problems = history_entries(history_text)
    return problems + keyed_line_errors(
        "frozen-history", "HISTORY_SHA256", "00-history.md", entries, frozen,
        "a history change is reviewed like the rules it records: once reviewed")


def example_source_errors(meanings, show: Callable[[str], str]) -> list[str]:
    """R4's verbatim check: each ``example``, after ``normalize()``, is a substring of
    ``normalize(show(path))`` for its ``source`` path (``show`` stands for ``git show 4e47d0e:<path>``).
    ``fragment_errors`` runs it on the drafts; iteration 4, S1 runs it on the committed module."""
    errors, cache = [], {}
    for key, entry in meanings.items():
        path = entry.source.split(":", 1)[0]
        if path not in cache:
            cache[path] = normalize(show(path))
        if normalize(entry.example) not in cache[path]:
            errors.append(f"[old-meaning] {key}: example is not verbatim in {path} at 4e47d0e")
    return errors


def _proxy_rows(rows: list[dict[str, str]], research_text: str, only_within: set[str] | None = None) -> list[str]:
    """The AC9 proxy on every ``no`` row with an inventory sentence, over the blocks it names. With
    ``only_within``, a row naming a rule outside that set is left to the assembled check."""
    errors, blocks = [], rule_blocks(research_text)
    for row in rows:
        new = _new_rule_ids(row)
        if row["meaning changed"] != "no" or _is_blank(row["inventory sentence"]) or not new:
            continue
        if only_within is not None and not set(new) <= only_within:
            continue
        mapped = "\n".join(blocks[i] for i in new if i in blocks)
        errors += [f"{e} ({row['inventory ID']})" for e in proxy_errors(row["inventory sentence"], mapped)]
    return errors


def assemble_check(drafts_dir) -> list[str]:
    """Every check over ``assemble()``'s output with ``complete=True``, plus what no single group can
    see: the whole-document R4 negative (glossary included), history arrows resolving to a rule or a
    retired ID, the uniqueness of the anchors, and the AC9 proxy on every ``no`` row."""
    a = assemble(drafts_dir)
    errors = list(a.problems)
    try:
        rows = parse_traceability(a.table)
    except ValueError as exc:
        return errors + [f"[trace] {exc}"]
    errors += rule_grammar_errors(a.research) + glossary_errors(a.research) + band_errors(a.research)
    errors += history_errors(a.history)
    errors += traceability_errors(rows, a.research, a.history, a.meanings)
    errors += decision_column_errors(rows, True, meanings=a.meanings)
    errors += operative_string_errors(a.research, rows, meanings=a.meanings)
    errors += pinned_errors(a.research, _REPO_ROOT)
    errors += old_meaning_errors(a.meanings, a.research)
    resolvable = set(rule_ids(a.research)) | set(_retired_listed(a.history))
    for h, ids in _history_arrows(a.history).items():
        errors += [f"[history] {h}'s arrow cites {i}, neither a rule nor a retired ID" for i in ids if i not in resolvable]
    errors += _anchor_errors(a.research)
    errors += _proxy_rows(rows, a.research)
    return errors


def _git_show(path: str) -> str:
    done = subprocess.run(["git", "show", f"4e47d0e:{path}"], capture_output=True, text=True, encoding="utf-8",
                          cwd=_REPO_ROOT, check=False)
    return done.stdout


def fragment_text_errors(rules: str, trace: str, meanings_text: str, group: str, sentences: dict[str, str],
                         show: Callable[[str], str] | None = None, coverage: bool = True) -> list[str]:
    """``fragment_errors`` over text already read. ``coverage=False`` skips the whole-group coverage --
    the group's inventory IDs and ``group_coverage_errors`` (a ``yes`` row for each C-number the group
    owns) -- so a single real block can be checked (T170 AC4); ``show=None`` skips the verbatim check."""
    errors = rule_grammar_errors(rules) + band_errors(rules)
    try:
        rows = parse_traceability(trace)
    except ValueError as exc:
        return errors + [f"[trace] {group}.trace.txt: {exc}"]
    try:
        meanings = load_meanings_jsonl(meanings_text)
    except ValueError as exc:
        errors.append(f"[old-meaning] {group}.meanings.txt: {exc}")
        meanings = {}
    prefixes = GROUPS[group]
    ids = [r["inventory ID"] for r in rows if not _is_blank(r["inventory ID"])]
    errors += [f"[trace] duplicate inventory ID {i}" for i, n in Counter(ids).items() if n > 1]
    errors += [f"[trace] {i} is outside group {group}'s prefixes" for i in ids if _prefix(i) not in prefixes]
    if coverage:
        want = {i for i in INVENTORY_IDS if _prefix(i) in prefixes}
        missing = sorted(want - set(ids), key=_order_key)
        if missing:
            errors.append(f"[trace] {len(missing)} of group {group}'s inventory IDs have no row: {missing[:10]}")
    here = set(rule_ids(rules))
    errors += [f"[grammar] rule {i} has a prefix outside group {group}" for i in sorted(here) if _prefix(i) not in prefixes]
    named: set[str] = set()
    for row in rows:
        label = row["inventory ID"] if not _is_blank(row["inventory ID"]) else f"addition {row['new ID(s)']}"
        new = _new_rule_ids(row)
        named.update(new)
        if not new and not _H_ID.findall(row["new ID(s)"]):
            errors.append(f"[trace] {label}: names no new ID: {row['new ID(s)']!r}")
        errors += [f"[trace] {label}: new ID {i} is not a rule line of the {group} draft"
                   for i in new if _prefix(i) in prefixes and i not in here]
        errors += [f"[trace] {label}: {h} is outside the history's H-01..H-41"
                   for h in _H_ID.findall(row["new ID(s)"]) if not 1 <= int(h[2:]) <= 41]
        errors += [f"[trace] {label}: no such old-meaning key {k!r} in {group}.meanings.txt"
                   for k in _keys(row) if k not in meanings]
        inv = row["inventory ID"]
        if _is_blank(inv):
            if not _is_blank(row["inventory sentence"]):
                errors.append(f"[trace] {label}: an addition's inventory sentence must be '{ADDITION}'")
        elif inv in sentences and row["inventory sentence"] != sentences[inv]:
            errors.append(f"[trace] {inv}: the inventory sentence cell is not the inventory's sentence verbatim")
        elif inv not in sentences and _prefix(inv) in prefixes:
            errors.append(f"[trace] {inv}: not in the inventory")
    errors += [f"[trace] rule {i} is not named by any {group} row" for i in sorted(here - named, key=_order_key)]
    errors += decision_column_errors(rows, False, meanings=meanings)
    if coverage:
        errors += group_coverage_errors(rows, group)
    errors += operative_string_errors(rules, rows, meanings=meanings, group=group)
    errors += _proxy_rows(rows, rules, only_within=here)
    errors += old_meaning_errors(meanings, rules)
    if show is not None:
        errors += example_source_errors(meanings, show)
    return errors


def fragment_errors(drafts_dir, group: str, inventory_path, show: Callable[[str], str] | None = _git_show) -> list[str]:
    """What the T172-T174 probes run over ``<group>.rules.txt``, ``.trace.txt`` and ``.meanings.txt``:
    grammar and band; the group's inventory IDs, each once; new IDs resolving within the group's
    prefixes or to an H-NN (cross-group IDs are left to ``assemble_check``); each inventory-sentence
    cell equal to ``inventory_sentences()``; R3 per group, including a ``yes`` row for each C-number the group owns; AC7 for the group's decisions; the AC9 proxy
    on every ``no`` row; R4 against the group's rules; and each example verbatim in
    ``git show 4e47d0e:<source>``."""
    drafts, errors = Path(drafts_dir), []
    if group not in GROUPS:
        return [f"[fragment] unknown group {group!r}; one of {sorted(GROUPS)}"]
    texts = {}
    for kind in ("rules", "trace", "meanings"):
        path = drafts / f"{group}.{kind}.txt"
        if not path.is_file():
            errors.append(f"[fragment] no draft {path.name}")
        texts[kind] = path.read_text(encoding="utf-8") if path.is_file() else ""
    return errors + fragment_text_errors(texts["rules"], texts["trace"], texts["meanings"], group,
                                         inventory_sentences(inventory_path), show=show)


# ===========================================================================
# Tests
# ===========================================================================

# ---------------------------------------------------------------------------
# The old-meanings support module (T170 AC1, AC5)
# ---------------------------------------------------------------------------


def test_the_old_meanings_module_exposes_exactly_its_four_public_names() -> None:
    """R4 and R11: the module the endpoint walk holds out by name may define only these four, so
    nothing else can shelter in the one unscanned literals file. Read from the AST, real path."""
    names = public_names(_SUPPORT)
    print(f"[slice compared] public names of {_SUPPORT.name}: {sorted(names)}")
    assert names == {"OldMeaning", "OLD_MEANINGS", "EXCEPTIONS", "normalize"}
    assert isinstance(_OM.OLD_MEANINGS, dict) and isinstance(_OM.EXCEPTIONS, tuple)
    tree = ast.parse(_SUPPORT.read_text(encoding="utf-8"))
    imported = [a.asname or a.name for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom) for a in n.names]
    assert imported and all(name.startswith("_") for name in imported), f"unaliased import: {imported}"
    assert set(OldMeaning.__dataclass_fields__) == {"pattern", "example", "source", "decision"}
    with pytest.raises(AttributeError):
        OldMeaning("a", "b", "c", "d").pattern = "x"  # frozen


@pytest.mark.parametrize(
    ("raw", "flat"),
    [
        pytest.param("A  b\n\tC", "a b c", id="whitespace-collapsed-and-casefolded"),
        pytest.param("“quoted” ‘single’ \"straight\" 'apos'", "quoted single straight apos", id="all-quotes-dropped"),
        pytest.param("`min_window_readings` **bold** *em*", "minwindowreadings bold em", id="code-emphasis-underscore-dropped"),
        pytest.param("≥ ≤ — – −", ">= <= -- -- -", id="typography-folded"),
        pytest.param("ＲＭＳＳＤ", "rmssd", id="nfkc-before-folding"),
        pytest.param("Straße", "strasse", id="casefold-not-lower"),
        pytest.param("0.5 · SD(ln rMSSD)", "0.5 · sd(ln rmssd)", id="middle-dot-kept"),
    ],
)
def test_normalize_is_flat_plus_nfkc_casefold_and_curly_quotes(raw: str, flat: str) -> None:
    print(f"[slice compared] {raw!r} -> {normalize(raw)!r}")
    assert normalize(raw) == flat


def test_the_module_loads_by_path_as_every_probe_loads_it() -> None:
    """The T171-T180 probes load this file with ``spec_from_file_location`` and never register it in
    ``sys.modules``. A dataclass under string annotations looks its module up there and fails, which
    is why this module carries no ``from __future__ import annotations``."""
    spec = importlib.util.spec_from_file_location("t", Path(__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.assemble is not None and len(module.INVENTORY_IDS) == 149


def test_public_names_reads_classes_functions_and_assignments_only(tmp_path) -> None:
    module = tmp_path / "m.py"
    module.write_text(
        "import os as _os\nfrom x import y\nclass A: pass\ndef f(): pass\nB = 1\nC: int = 2\n_d = 3\n"
        "def _g(): pass\nif True:\n    Z = 1\n",
        encoding="utf-8",
    )
    assert public_names(module) == {"A", "f", "B", "C"}


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_the_frozen_constants_have_the_inventorys_shape() -> None:
    per_prefix = Counter(i.split("-")[0] for i in INVENTORY_IDS)
    print(f"[slice compared] {len(INVENTORY_IDS)} IDs {dict(per_prefix)}")
    assert len(INVENTORY_IDS) == 149
    assert per_prefix == {"DOC": 15, "PRIN": 16, "ARB": 6, "AUT": 6, "GOAL": 6, "ARCH": 9, "REG": 19, "IND": 2,
                          "COLD": 7, "HRV": 46, "GATE": 3, "FIG": 5, "LT1": 2, "FTO": 6, "DEC": 1}
    assert "ARCH-00" in INVENTORY_IDS and "ARCH-09" not in INVENTORY_IDS
    assert sorted(p for ps in GROUPS.values() for p in ps) == sorted(PREFIX_ORDER)
    sizes = {g: sum(1 for i in INVENTORY_IDS if i.split("-")[0] in ps) for g, ps in GROUPS.items()}
    assert sizes == {"doc-goal": 49, "arch-dec": 54, "hrv": 46}
    assert len(HEADINGS) == 23 and len(set(HEADINGS)) == 23
    assert not any(re.search(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", h) for h in HEADINGS)
    assert HEADINGS[1].startswith("## Part 1") and HEADINGS[-1] == "### 5.4 Reconciliations and amendments"
    assert set(YES_ROW_OWNER) == set(DECISIONS) and set(YES_ROW_OWNER.values()) == set(GROUPS)
    assert {YES_ROW_OWNER[c] for c in ("C06", "C07", "C33")} == {"doc-goal"}
    assert (YES_ROW_OWNER["C08"], YES_ROW_OWNER["C30"], YES_ROW_OWNER["C27"]) == ("arch-dec", "arch-dec", "hrv")
    assert GROUP_A | GROUP_B == set(DECISIONS) and not GROUP_A & GROUP_B
    assert NO_ONLY <= GROUP_A


# ---------------------------------------------------------------------------
# Grammar, glossary, band and history
# ---------------------------------------------------------------------------


def _block(rule_id: str, body: str | None = None, *, scope: str = "Scope: every synthetic case.",
           not_: str = "Not: anything else.", pinned: tuple[str, ...] = ("Pinned: none",),
           why: str | None = None) -> str:
    lines = [f"**{rule_id}.** {body or f'Rule {rule_id} MUST hold every day.'}", scope, not_, *pinned]
    if why:
        lines.append(why)
    return "\n".join(lines)


_GOOD = "\n\n".join([
    "### 1.1 The supreme objective",
    _block("PRIN-01"),
    _block("PRIN-02", "A green day IS not a hard day, and the ladder (`R+0 .. R+19`) MAY say so.", why="Why: C07."),
    _block("PRIN-03", "The system MUST NOT stop, e.g. on one reading."),
])


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        pytest.param(_GOOD.replace("every day", "every day on 2026-09-18", 1), "ISO date", id="iso-date"),
        pytest.param(_GOOD.replace("Rule PRIN-01 MUST hold every day.", "Rule PRIN-01 MUST " + "x" * 400 + "."), "characters", id="line-over-400"),
        pytest.param(_GOOD.replace("MUST hold every day.", "MUST hold. It does.", 1), "one sentence", id="two-sentences"),
        pytest.param(_GOOD.replace("Rule PRIN-01 MUST hold every day.", "Rule PRIN-01 holds every day."), "MUST", id="no-obligation-or-definition"),
        pytest.param(_GOOD.replace("Rule PRIN-01 MUST hold every day.", "Rule PRIN-01 MUST hold every day"), "end in", id="no-final-period"),
        pytest.param(_GOOD.replace("Scope: every synthetic case.\nNot: anything else.", "Not: anything else.\nScope: every synthetic case.", 1), "order", id="not-before-scope"),
        pytest.param(_GOOD.replace("Scope: every synthetic case.\n", "", 1), "Scope", id="missing-scope"),
        pytest.param(_GOOD.replace("Not: anything else.\n", "", 1), "Not", id="missing-not"),
        pytest.param(_GOOD.replace("Pinned: none\nWhy: C07.", "Why: C07."), "Pinned", id="missing-pinned"),
        pytest.param(_GOOD.replace("Why: C07.", "Why: C07.\nWhy: again."), "order", id="two-why"),
        pytest.param(_GOOD.replace("Pinned: none", "Pinned: `tests/test_x.py::test_y`", 1), "Pinned", id="pinned-as-code-span"),
        pytest.param(_GOOD.replace("Pinned: none", "Pinned: nothing yet", 1), "Pinned", id="pinned-malformed"),
        pytest.param(_GOOD.replace("### 1.1 The supreme objective", "### 1.1 The supreme objectives"), "heading", id="unknown-heading"),
        pytest.param(_GOOD + "\n\nSome prose the grammar does not allow.", "not a rule", id="stray-prose"),
        pytest.param(_GOOD + "\n\n" + _block("PRIN-01"), "duplicate", id="duplicate-id"),
        pytest.param(_GOOD.replace("**PRIN-01.**", "**PRIN-1.**"), "not a rule", id="bad-id-format"),
        pytest.param(_GOOD.replace("**PRIN-01.**", "**XYZ-01.**"), "not a rule", id="unknown-prefix"),
        pytest.param(_GOOD.replace("Scope: every synthetic case.", "Scope: every synthetic case.\n", 1), "not a rule", id="blank-inside-block"),
    ],
)
def test_rule_grammar_errors_turns_red_on_each_violation(text: str, fragment: str) -> None:
    errors = rule_grammar_errors(text)
    print(f"[slice compared] {errors}")
    assert any(fragment in e for e in errors), errors


def test_rule_grammar_errors_is_green_on_valid_blocks_with_the_r6_exemptions() -> None:
    """Code spans are skipped (FIG-01's ``R+0 .. R+19``), ``e.g.`` is not a terminator, a definition
    form and a MUST NOT pass, and the Glossary's lines are left to ``glossary_errors``."""
    glossary = f"{GLOSSARY_HEADING}\n\n- **T-01 judgeable** IS a dataset. It has two sentences.\n"
    text = f"{HEADINGS[0]}\n\n{glossary}\n{HEADINGS[1]}\n\n{_GOOD}\n"
    errors = rule_grammar_errors(text)
    print(f"[slice compared] {errors}")
    assert errors == []
    assert rule_ids(text) == ["PRIN-01", "PRIN-02", "PRIN-03"]


def _glossary(n: int = 33, **override: str) -> str:
    lines = []
    for i in range(1, n + 1):
        tid = f"T-{i:02d}"
        definition = "OPEN (IDEA-088)" if tid == "T-25" else f"the synthetic definition {i}"
        lines.append(override.get(tid, f"- **{tid} term{i}** IS {definition}."))
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    ("text", "complete", "fragment"),
    [
        pytest.param(_glossary(32), True, "33", id="thirty-two-terms"),
        pytest.param(_glossary() + "- **T-01 again** IS twice.\n", True, "duplicate", id="duplicate-term"),
        pytest.param(_glossary(**{"T-03": "- T-03 established IS n days."}), True, "format", id="bad-format"),
        pytest.param(_glossary(**{"T-03": "- **T-03 established** means n days."}), True, "format", id="no-IS"),
        pytest.param(_glossary(**{"T-25": "- **T-25 rate** IS per cell."}), True, "T-25", id="t25-not-open"),
        pytest.param(_glossary(**{"T-04": "- **T-04 stale** IS " + "y" * 400 + "."}), True, "characters", id="over-400"),
        pytest.param("- **T-25 rate** IS per cell.\n", False, "T-25", id="partial-t25-not-open"),
    ],
)
def test_glossary_errors_turns_red(text: str, complete: bool, fragment: str) -> None:
    errors = glossary_errors(text, complete=complete)
    print(f"[slice compared] {errors}")
    assert any(fragment in e for e in errors), errors


def test_glossary_errors_is_green_complete_partial_and_inside_research() -> None:
    assert glossary_errors(_glossary()) == []
    assert glossary_errors(_glossary(3), complete=False) == []
    research = f"{HEADINGS[0]}\n\n{GLOSSARY_HEADING}\n\n{_glossary()}\n{HEADINGS[1]}\n\n{_block('PRIN-01')}\n"
    assert glossary_errors(research) == []


@pytest.mark.parametrize(
    ("text", "red"),
    [
        pytest.param("**HRV-07.** The SWC band MUST be computed.", False, id="swc-sentence"),
        pytest.param("The band of rMSSD readings is wide.", False, id="rmssd-sentence"),
        pytest.param("- **T-07 band** IS the hrv_trend band.", False, id="lowercase-hrv"),
        pytest.param("**REG-02.** ACWR MUST keep a wide band.", True, id="acwr-band"),
        pytest.param("**HRV-40.** The BANDS MUST be clipped.", True, id="only-hrv-is-the-rule-id"),
        pytest.param("The SWC reading holds. The band is wide.", True, id="other-sentence-carries-swc"),
        pytest.param("test_band_reconciliation.py::test_x holds.", False, id="identifier-not-a-word"),
    ],
)
def test_band_errors(text: str, red: bool) -> None:
    errors = band_errors(text)
    print(f"[slice compared] {text!r}: {errors}")
    assert bool(errors) == red, errors


def _history(n: int = 41, retired: str = "") -> str:
    entries = [f"- **H-{i:02d}** (2026-09-{(i % 28) + 1:02d}) Synthetic change {i}. → DOC-01" for i in range(1, n + 1)]
    return "# research/00 history\n\n" + "\n".join(entries) + "\n\n## Retired IDs\n" + retired


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        pytest.param(_history().replace("(2026-09-02)", "(2 Sept)"), "format", id="bad-date"),
        pytest.param(_history().replace("Synthetic change 3. → DOC-01", "Synthetic change 3."), "format", id="no-arrow"),
        pytest.param(_history().replace("→ DOC-01", "→ nothing", 1), "format", id="arrow-cites-no-id"),
        pytest.param(_history().replace("Synthetic change 5.", "The system MUST change 5."), "MUST", id="uppercase-must"),
        pytest.param(_history().replace("Synthetic change 5.", "It MAY change."), "MAY", id="uppercase-may"),
        pytest.param(_history().replace("## Retired IDs\n", ""), "Retired IDs", id="no-retired-section"),
        pytest.param(_history(retired="- **DOC-15** retired\n"), "retired", id="bad-retired-line"),
        pytest.param(_history() + "\n**DOC-01.** A rule MUST hold.\n", "rule line", id="rule-line"),
        pytest.param(_history().replace("- **H-02**", "- **H-01**"), "duplicate", id="duplicate-entry"),
    ],
)
def test_history_errors_turns_red(text: str, fragment: str) -> None:
    errors = history_errors(text)
    print(f"[slice compared] {errors}")
    assert any(fragment in e for e in errors), errors


def test_history_errors_is_green_with_multiple_dates_undated_and_lowercase_modals() -> None:
    text = _history(retired="- **DOC-15** retired → H-01\n").replace(
        "(2026-09-03)", "(2026-08-31, 2026-09-16)").replace("(2026-09-05)", "(undated)").replace(
        "Synthetic change 6.", "The rule must change and may stay.").replace("→ DOC-01", "→ DOC-01, HRV-31", 1)
    errors = history_errors(text)
    print(f"[slice compared] {errors}")
    assert errors == []
    assert history_ids(text) == [f"H-{i:02d}" for i in range(1, 42)]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _row(inv: str, sentence: str, new: str, decision: str = "—", meaning: str = "no",
         key: str = "—", cited: str = "§5.4") -> str:
    return f"| {inv} | {sentence} | {cited} | {new} | {decision} | {meaning} | {key} |"


def _as_row(line: str) -> dict[str, str]:
    return parse_traceability(line)[0]


def test_parse_traceability_reads_the_header_form_and_a_header_less_fragment() -> None:
    body = (_row("HRV-37", "A band \\| pipe.", "HRV-37", "C04", "yes", "C04-at-least") + "\n"
            + _row(ADDITION, ADDITION, "HRV-47", "C03"))
    full = f"{TRACE_HEADER}\n|---|---|---|---|---|---|---|\n{body}\n"
    for text in (full, body):
        rows = parse_traceability(text)
        print(f"[slice compared] {rows}")
        assert [r["inventory ID"] for r in rows] == ["HRV-37", ADDITION]
        assert set(rows[0]) == set(TRACE_COLUMNS)
        assert rows[0]["inventory sentence"] == "A band | pipe." and rows[0]["old-meaning key"] == "C04-at-least"
    with pytest.raises(ValueError, match="columns"):
        parse_traceability("| HRV-01 | too | few |")
    with pytest.raises(ValueError, match="header"):
        parse_traceability("| inventory ID | sentence | cited | new | decision | meaning | key |")


def test_load_meanings_jsonl_builds_old_meanings_and_rejects_bad_lines() -> None:
    line = {"key": "k1", "pattern": "a", "example": "a", "source": "x.md:1@4e47d0e", "decision": "C05"}
    entries = load_meanings_jsonl(json.dumps(line) + "\n\n")
    assert entries == {"k1": OldMeaning("a", "a", "x.md:1@4e47d0e", "C05")}
    with pytest.raises(ValueError, match="duplicate"):
        load_meanings_jsonl(json.dumps(line) + "\n" + json.dumps(line))
    with pytest.raises(ValueError, match="fields"):
        load_meanings_jsonl(json.dumps({"key": "k1", "pattern": "a"}))
    with pytest.raises(ValueError, match="JSON"):
        load_meanings_jsonl("{not json")


def test_inventory_sentences_reads_the_current_rule_cell_verbatim(tmp_path) -> None:
    inv = tmp_path / "inventory.md"
    inv.write_text(
        "| ID | Current rule | Assembled from | Cited today as | Status |\n|---|---|---|---|---|\n"
        "| HRV-09 | Every count is in distinct local days, with `min_window_readings` = 3. | L217 | §5.4 | SETTLED |\n"
        "| C01 | not a rule row | x | y | z |\n",
        encoding="utf-8",
    )
    assert inventory_sentences(inv) == {"HRV-09": "Every count is in distinct local days, with `min_window_readings` = 3."}


def test_rule_blocks_and_ids_parse_each_block_with_its_lines() -> None:
    blocks = rule_blocks(_GOOD)
    assert list(blocks) == ["PRIN-01", "PRIN-02", "PRIN-03"]
    assert blocks["PRIN-02"].splitlines()[-1] == "Why: C07."
    assert blocks["PRIN-01"].splitlines()[1:] == ["Scope: every synthetic case.", "Not: anything else.", "Pinned: none"]


# ---------------------------------------------------------------------------
# A complete synthetic world: every draft, all 149 rows, every decision
# ---------------------------------------------------------------------------

#: The row each decision sits on in the synthetic world. PRIN-08 holds the shared cell "C24, C25"
#: (R3 "A shared cell"): C24 authorizes its ``yes``, and C25, no-only, rides along.
_WORLD_DECISION_ROWS = {
    "C01": "HRV-31", "C02": "HRV-31", "C03": "HRV-34", "C04": "HRV-37", "C05": "GATE-02", "C06": "PRIN-15",
    "C07": "PRIN-14", "C08": "ARCH-08", "C09": "HRV-33", "C10": "HRV-15", "C11": "HRV-14", "C12": "HRV-12",
    "C13": "HRV-38", "C14": "HRV-34", "C15": "HRV-34", "C16": "HRV-21", "C17": "HRV-24", "C18": "HRV-30",
    "C19": "PRIN-10", "C20": "AUT-04", "C21": "DEC-01", "C22": "GOAL-02", "C23": "ARB-02", "C24": "PRIN-08",
    "C25": "PRIN-08", "C26": "COLD-01", "C27": "HRV-05", "C28": "LT1-01", "C29": "FTO-06", "C30": "REG-19",
    "C31": "DOC-06", "C32": "HRV-07", "C33": "PRIN-12", "C37": "GATE-03", "C38": "DOC-09",
}

_WORLD_BODIES = {
    "DOC-09": ("Rule DOC-09 MUST hold every day, so research/00 states only current rules and a dated summary "
               "goes to the history file."),
    "PRIN-12": "Rule PRIN-12 MUST hold every day and serves `recency_tolerance_days`.",
    "PRIN-14": "Rule PRIN-14 MUST hold every day against the forbidden direction.",
    "PRIN-15": ("Rule PRIN-15 MUST hold every day, and the F005-parity population, `DEFERRED_EXCEPTION` (IDEA-087) "
                "and the HRV-25 population (IDEA-099) may not grow."),
    "HRV-07": "Rule HRV-07 MUST hold every day with the half-width `max(0.5 · SD(ln rMSSD), 0.01)`.",
    "HRV-31": ("Rule HRV-31 MUST hold every day for a dataset that could not have been selected: not judgeable, "
               "or skipped, against the dataset being judged."),
    "HRV-37": "Rule HRV-37 MUST hold every day after more than 21 silent days.",
    "GATE-02": "Rule GATE-02 MUST hold every day, and the system MUST NOT add hysteresis.",
}
_WORLD_PINNED = {
    "PRIN-12": ("Pinned: none (F010)",),
    "PRIN-15": ("Pinned: none (F009)",),
    "GATE-02": (("Pinned: runcoach-api/tests/test_hrv_no_regression_gate.py::"
                "test_the_ac23_flip_rate_comparison_is_asserted_and_its_worsened_cells_are_pinned"),),
}
_WORLD_HEADINGS = {"doc-goal": HEADINGS[2], "arch-dec": HEADINGS[11], "hrv": HEADINGS[-1]}
_WORLD_C05 = {"key": "C05-reopens", "pattern": "worse rate reopens",
              "example": "a worse rate reopens the deferred hysteresis decision",
              "source": "specification/research/00-design-decisions.md:230@4e47d0e", "decision": "C05"}
#: F008 AC6: every ``yes`` row names an old-meaning key. Each group's keys copy one entry, one key per
#: decision (``_world_key``; M1: a key records one decision); arch-dec's copy C05's, and the other two
#: quote ``_old_show``'s text so the verbatim check holds.
_WORLD_OLD = {
    "doc-goal": {"key": "doc-goal-old", "pattern": "flip rate is measured", "example": "the flip rate is measured",
                 "source": "specification/research/00-design-decisions.md:230@4e47d0e", "decision": "C24"},
    "arch-dec": _WORLD_C05,
    "hrv": {"key": "hrv-old", "pattern": "deferred hysteresis decision [(]f006[)]",
            "example": "the deferred hysteresis decision (F006)",
            "source": "specification/research/00-design-decisions.md:230@4e47d0e", "decision": "C01"},
}
#: DOC-15 retires to its history entry; FTO-06 merges into FTO-05 under C29 (Group A, so H-38).
_WORLD_NEW_ID = {"DOC-15": "H-01 (moved to history)", "FTO-06": "FTO-05"}
_WORLD_SENTENCE = {"FTO-06": "The rule holds every day."}


def _sorted_ids(ids) -> list[str]:
    return sorted(ids, key=lambda i: (PREFIX_ORDER.index(i.rsplit("-", 1)[0]), int(i.rsplit("-", 1)[1])))


def _world_sentence(inv: str) -> str:
    return _WORLD_SENTENCE.get(inv, f"Rule {inv} holds every day.")


def _world_key(group: str, decision: str) -> str:
    """The world's old-meaning key for ``decision``: the group's own key for its own decision (so
    ``C05-reopens`` stays C05's), a suffixed copy for any other."""
    base = _WORLD_OLD[group]
    return base["key"] if decision == base["decision"] else f"{base['key']}-{decision.lower()}"


def _world_files() -> dict[str, str]:
    decisions: dict[str, list[str]] = {}
    for c, row_id in _WORLD_DECISION_ROWS.items():
        decisions.setdefault(row_id, []).append(c)
    files = {"glossary.txt": _glossary(), "history.txt": _history().replace("→ DOC-01", "→ FTO-06", 1)}
    for group, prefixes in GROUPS.items():
        ids = _sorted_ids(i for i in INVENTORY_IDS if i.split("-")[0] in prefixes)
        blocks, rows, meanings, keyed = [_WORLD_HEADINGS[group]], [], [], []
        for inv in ids:
            new = _WORLD_NEW_ID.get(inv, inv)
            if new == inv:
                scope = "Scope: every per-tier dataset." if inv in PER_TIER_ANCHORS else "Scope: every synthetic case."
                pinned = _WORLD_PINNED.get(inv, ("Pinned: none",))
                blocks.append(_block(inv, _WORLD_BODIES.get(inv), scope=scope, pinned=pinned))
            cs = decisions.get(inv, [])
            meaning = "yes" if any(c not in NO_ONLY for c in cs) else "no"
            # AC6 and R3: a yes row, and a row citing C19 or C25, names a key (S5).
            has_key = meaning == "yes" or any(c in KEYED_NO_ONLY for c in cs)
            # M1: a key records one decision, its leading token, and the row cites it. A yes row names
            # the key of the decision that authorizes it (iteration 4, M1), and a C25 or C19 row also
            # names that C-number's key (S5): PRIN-08's "C24, C25" names both.
            leads = [next(c for c in cs if c not in NO_ONLY)] if meaning == "yes" else []
            leads += [c for c in ("C25", "C19") if c in cs] if has_key else []
            keyed += leads
            key = ", ".join(_world_key(group, c) for c in leads) or ADDITION
            rows.append(_row(inv, _world_sentence(inv), new, ", ".join(cs) or ADDITION, meaning, key))
        # One old meaning per recorded decision, each quoting the group's verbatim example.
        for c in dict.fromkeys([_WORLD_OLD[group]["decision"], *keyed]):
            meanings.append(json.dumps(dict(_WORLD_OLD[group], key=_world_key(group, c), decision=c),
                                       ensure_ascii=False))
        files[f"{group}.rules.txt"] = "\n\n".join(blocks) + "\n"
        files[f"{group}.trace.txt"] = "\n".join(rows) + "\n"
        files[f"{group}.meanings.txt"] = "\n".join(meanings) + ("\n" if meanings else "")
    return files


def _write(tmp_path: Path, files: dict[str, str]) -> Path:
    drafts = tmp_path / "drafts"
    drafts.mkdir(exist_ok=True)
    for name, text in files.items():
        (drafts / name).write_text(text, encoding="utf-8")
    return drafts


def _inventory(tmp_path: Path) -> Path:
    lines = ["| ID | Current rule | Assembled from | Cited today as | Status |", "|---|---|---|---|---|"]
    lines += [f"| {i} | {_world_sentence(i)} | L1 | §5.4 | SETTLED |" for i in _sorted_ids(INVENTORY_IDS)]
    path = tmp_path / "inventory.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _old_show(path: str) -> str:
    """Stands in for ``git show 4e47d0e:<path>``: the committed suite runs where there is no full clone."""
    return "... the flip rate is measured, and a worse rate reopens the deferred hysteresis decision (F006) ..."


def _world(tmp_path: Path):
    a = assemble(_write(tmp_path, _world_files()))
    return a, parse_traceability(a.table)


def test_the_synthetic_world_passes_assemble_check_so_the_checkers_compose(tmp_path) -> None:
    """Green on a complete world proves the checkers do not contradict each other; every red case
    below is one mutation of it."""
    errors = assemble_check(_write(tmp_path, _world_files()))
    print(f"[slice compared] assemble_check over the synthetic world: {errors[:10]}")
    assert errors == []


def test_assemble_orders_headings_rules_and_fills_the_retired_ids(tmp_path) -> None:
    files = {
        "glossary.txt": _glossary(2),
        "history.txt": _history(2),
        "hrv.rules.txt": f"{HEADINGS[-1]}\n\n{_block('HRV-10')}\n\n{_block('HRV-02')}\n\n{HEADINGS[2]}\n\n{_block('HRV-01')}\n",
        "hrv.trace.txt": "\n".join(_row(i, "s", i) for i in ("HRV-01", "HRV-02", "HRV-10")) + "\n"
                         + _row("HRV-03", "s", "H-02 (history)") + "\n",
        "doc-goal.rules.txt": f"{HEADINGS[-1]}\n\n{_block('DOC-02')}\n\n{_block('PRIN-01')}\n",
        "doc-goal.trace.txt": _row("DOC-02", "s", "DOC-02") + "\n" + _row("PRIN-01", "s", "PRIN-01") + "\n"
                              + _row("DOC-01", "s", "DOC-02", "C38", "yes") + "\n",
    }
    a = assemble(_write(tmp_path, files))
    heads = [line for line in a.research.splitlines() if line.startswith("#")]
    order = rule_ids(a.research)
    print(f"[slice compared] headings {heads[:4]} ... rules {order}")
    assert heads == [HEADINGS[0], GLOSSARY_HEADING, *HEADINGS[1:]]
    assert order == ["HRV-01", "DOC-02", "PRIN-01", "HRV-02", "HRV-10"]
    retired = a.history.split("## Retired IDs", 1)[1]
    assert re.findall(r"^- \*\*(\S+)\*\* retired → (H-\d{2})$", retired, re.M) == [("DOC-01", "H-39"), ("HRV-03", "H-02")]
    assert len(parse_traceability(a.table)) == 7 and a.table.startswith(TRACE_HEADER)


def test_retired_ids_follow_r11s_h_nn_rule() -> None:
    rows = parse_traceability("\n".join([
        _row("DOC-15", "s", "H-06 (moved to history)"),
        _row("DOC-06", "s", "IND-01", "C31", "yes"),
        _row("DOC-13", "s", "DOC-14", "C29"),
        _row("HRV-12", "s", "HRV-13", "HRV-11", "yes"),
        _row("REG-02", "s", "REG-03", "T-07, R9", "yes"),
        _row("FIG-05", "s", "FIG-04"),
        _row("HRV-15", "s", "HRV-15, H-17", "C10", "yes"),
    ]))
    retired = retired_ids(rows)
    print(f"[slice compared] {retired}")
    assert retired == {"DOC-15": "H-06", "DOC-06": "H-39", "DOC-13": "H-38", "HRV-12": "H-40", "REG-02": "H-41",
                       "FIG-05": None}


# ---------------------------------------------------------------------------
# Traceability, both directions (AC6), and the four "proving it" mutations
# ---------------------------------------------------------------------------


def test_traceability_errors_is_green_on_the_world(tmp_path) -> None:
    a, rows = _world(tmp_path)
    assert traceability_errors(rows, a.research, a.history, a.meanings) == []


def _drop_rule(research: str, rule_id: str) -> str:
    return "\n".join(line for line in research.splitlines() if not line.startswith(f"**{rule_id}.**"))


@pytest.mark.parametrize(
    "mutation",
    ["delete-a-rule-line", "add-an-unmapped-rule", "point-a-row-at-HRV-999", "key-pattern-misses-its-example"],
)
def test_ac6_proving_it_each_mutation_turns_the_check_red(tmp_path, mutation: str) -> None:
    a, rows = _world(tmp_path)
    research, meanings = a.research, dict(a.meanings)
    if mutation == "delete-a-rule-line":
        research = _drop_rule(research, "HRV-09")
    elif mutation == "add-an-unmapped-rule":
        research = research.replace(_block("HRV-09"), _block("HRV-09") + "\n\n" + _block("HRV-47"))
    elif mutation == "point-a-row-at-HRV-999":
        rows = [dict(r, **{"new ID(s)": "HRV-999"}) if r["inventory ID"] == "HRV-09" else r for r in rows]
    else:
        meanings["C05-reopens"] = OldMeaning("worse rate re-opens", _WORLD_C05["example"], _WORLD_C05["source"], "C05")
    errors = traceability_errors(rows, research, a.history, meanings) + old_meaning_errors(meanings, research)
    print(f"[slice compared] {mutation}: {errors}")
    assert errors, f"{mutation} left the traceability check green"


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        ("drop-row", "missing"), ("duplicate-row", "duplicate"), ("unknown-id", "not an inventory ID"),
        ("empty-new-id", "names no new ID"), ("history-unresolved", "H-99"), ("reuse-retired", "reused"),
        ("missing-key", "no such old-meaning key"), ("merged-without-decision", "retire"),
    ],
)
def test_traceability_errors_turns_red(tmp_path, mutation: str, fragment: str) -> None:
    a, rows = _world(tmp_path)
    research = a.research
    first = rows[0]["inventory ID"]
    if mutation == "drop-row":
        rows = rows[1:]
    elif mutation == "duplicate-row":
        rows = rows + [rows[0]]
    elif mutation == "unknown-id":
        rows = rows + [dict(rows[0], **{"inventory ID": "HRV-47"})]
    elif mutation == "empty-new-id":
        rows = [dict(rows[0], **{"new ID(s)": "(dropped)"})] + rows[1:]
    elif mutation == "history-unresolved":
        rows = [dict(r, **{"new ID(s)": "H-99"}) if r["inventory ID"] == "DOC-15" else r for r in rows]
    elif mutation == "reuse-retired":
        research = research.replace(_block("DOC-14"), _block("DOC-14") + "\n\n" + _block("DOC-15"))
        rows = rows + [_as_row(_row(ADDITION, ADDITION, "DOC-15", "C38", "yes"))]
    elif mutation == "missing-key":
        rows = [dict(r, **{"old-meaning key": "nope"}) if r["inventory ID"] == first else r for r in rows]
    else:
        rows = [dict(r, decision=ADDITION) if r["inventory ID"] == "FTO-06" else r for r in rows]
    errors = traceability_errors(rows, research, a.history, a.meanings)
    print(f"[slice compared] {mutation}: {errors}")
    assert any(fragment in e for e in errors), errors


# ---------------------------------------------------------------------------
# The decision column (R3)
# ---------------------------------------------------------------------------


def test_decision_column_errors_is_green_on_the_world_complete_and_per_group(tmp_path) -> None:
    _a, rows = _world(tmp_path)
    assert decision_column_errors(rows, True, meanings=_a.meanings) == []
    for prefixes in GROUPS.values():
        group_rows = [r for r in rows if r["inventory ID"].split("-")[0] in prefixes]
        assert decision_column_errors(group_rows, False, meanings=_a.meanings) == []


@pytest.mark.parametrize(
    ("rows", "complete", "fragment"),
    [
        pytest.param([_row("HRV-14", "s", "HRV-14", "C11", "yes", "k")], False, "never authorizes", id="no-only-on-yes"),
        pytest.param([_row("PRIN-08", "s", "PRIN-08", "C25", "yes", "k")], False, "never authorizes", id="no-only-alone-on-yes"),
        pytest.param([_row("PRIN-08", "s", "PRIN-08", "C19, C25", "yes", "k")], False, "never authorizes", id="no-only-pair-on-yes"),
        pytest.param([_row(ADDITION, ADDITION, "HRV-47", ADDITION, "yes", "k")], False, "cite a decision", id="addition-without-decision"),
        pytest.param([_row("HRV-14", "s", "HRV-14", "C34", "yes", "k")], False, "not a decision", id="further-c-number"),
        pytest.param([_row("HRV-14", "s", "HRV-14", "C11", "maybe")], False, "yes or no", id="bad-meaning"),
        pytest.param([_row("HRV-11", "s", "HRV-11", "C01", "yes")], False, "old-meaning key", id="yes-row-without-key"),
        pytest.param([_row("HRV-11", "s", "HRV-11", "C01", "yes", "")], False, "old-meaning key", id="yes-row-with-empty-key"),
        pytest.param([_row("HRV-31", "s", "HRV-31", "C01, C02", "yes", "k")], True, "C03", id="complete-missing-decision"),
    ],
)
def test_decision_column_errors_turns_red(rows: list[str], complete: bool, fragment: str) -> None:
    errors = decision_column_errors([_as_row(r) for r in rows], complete)
    print(f"[slice compared] {errors}")
    assert any(fragment in e for e in errors), errors


@pytest.mark.parametrize(
    "row",
    [
        # R3 "A shared cell": C24 authorizes the yes, and C25 rides along with its key for F011.
        pytest.param(_row("PRIN-08", "s", "PRIN-08", "C24, C25", "yes", "C25-quarantine-list"), id="shared-cell-prin-08"),
        # R3 "Ownership is coverage, not exclusivity": C06's owner is doc-goal, and GATE-01 is arch-dec.
        pytest.param(_row("GATE-01", "s", "GATE-01", "C06", "yes", "C06-one-exception"), id="cross-group-gate-01"),
        pytest.param(_row("HRV-25", "s", "HRV-25", "C06", "yes", "C06-hrv-25"), id="cross-group-hrv-25"),
        pytest.param(_row("HRV-14", "s", "HRV-14", "C11", "no"), id="no-only-on-a-no-row-without-key"),
        pytest.param(_row("PRIN-10", "s", "PRIN-10", "C19", "no", "C19-schemas"), id="no-row-with-key"),
    ],
)
def test_decision_column_errors_is_green_on_shared_cells_cross_group_yes_rows_and_keyed_rows(row: str) -> None:
    errors = decision_column_errors([_as_row(row)], False)
    print(f"[slice compared] {row} -> {errors}")
    assert errors == []


def test_group_coverage_errors_names_each_owned_decision_without_a_yes_row() -> None:
    """R3 (user, 2026-09-25): the owner group writes at least one ``yes`` row per C-number it owns;
    other groups' ``yes`` rows for it are allowed. NO_ONLY C-numbers never back a ``yes``, so no
    group owes one."""
    owned = sorted(c for c, g in YES_ROW_OWNER.items() if g == "doc-goal" and c not in NO_ONLY)
    only_c24 = group_coverage_errors([_as_row(_row("PRIN-08", "s", "PRIN-08", "C24, C25", "yes", "k"))], "doc-goal")
    print(f"[slice compared] doc-goal owns {owned}; with only PRIN-08 yes C24, C25 -> {only_c24}")
    for c in owned:
        assert any(c in e for e in only_c24) == (c != "C24"), (c, only_c24)
    assert not any("C25" in e or "C19" in e for e in only_c24), only_c24
    # a no row, or a yes row in the wrong group only, does not cover
    as_no = group_coverage_errors([_as_row(_row("PRIN-15", "s", "PRIN-15", "C06", "no"))], "doc-goal")
    assert any("C06" in e for e in as_no), as_no
    elsewhere = group_coverage_errors([_as_row(_row("GATE-01", "s", "GATE-01", "C06", "yes", "k"))], "arch-dec")
    assert not any("C06" in e for e in elsewhere), elsewhere
    covered = group_coverage_errors([_as_row(_row("PRIN-15", "s", "PRIN-15", "C06", "yes", "k"))], "doc-goal")
    assert not any("C06" in e for e in covered), covered
    every = [_as_row(_row(f"DOC-{i:02d}", "s", f"DOC-{i:02d}", c, "yes", "k")) for i, c in enumerate(owned, 1)]
    assert group_coverage_errors(every, "doc-goal") == []


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        # M2: scanner A's DOC-03 case -- a yes row with no decision, and a key borrowed from another row.
        pytest.param(
            _row("DOC-03", "s", "DOC-03", ADDITION, "yes", "DOC-06-C31-every-number-tunable"),
            [("[decision] DOC-03: a yes row cites no decision that authorizes a meaning change ('—'); cite a "
             "C-number outside NO_ONLY or one of ['HRV-11', 'R13', 'T-07'] (R3, M2)"),
             ("[decision] DOC-03: old-meaning key 'DOC-06-C31-every-number-tunable' records decision 'C31', "
             "which the row's cell '—' does not cite (M2, M1: a key cannot be borrowed)")],
            id="m2-yes-without-decision-and-borrowed-key"),
        # M2: an authorized yes row still may not borrow another decision's key.
        pytest.param(
            _row("DOC-03", "s", "DOC-03", "C38", "yes", "C04-hole-at-least"),
            [("[decision] DOC-03: old-meaning key 'C04-hole-at-least' records decision 'C04', which the row's "
             "cell 'C38' does not cite (M2, M1: a key cannot be borrowed)")],
            id="m2-borrowed-key-on-an-authorized-row"),
        # M2: a non-C token outside NON_C_AUTHORITIES authorizes nothing.
        pytest.param(
            _row("REG-02", "s", "REG-02", "T-08", "yes", "T07-acwr-band"),
            [("[decision] REG-02: a yes row cites no decision that authorizes a meaning change ('T-08'); cite a "
             "C-number outside NO_ONLY or one of ['HRV-11', 'R13', 'T-07'] (R3, M2)"),
             ("[decision] REG-02: old-meaning key 'T07-acwr-band' records decision 'T-07', which the row's "
             "cell 'T-08' does not cite (M2, M1: a key cannot be borrowed)")],
            id="m2-unfrozen-non-c-token"),
        # Iteration 2, M1: scanner A's DOC-03 case. "1–4" -> "1–3" under R13, with an R13 key: R13
        # authorizes a yes on PRIN-12 alone, and the key is not borrowed (its decision is R13).
        pytest.param(
            _row("DOC-03", "s", "DOC-03", "R13", "yes", "HRV-01-R13-four-tier-hierarchy"),
            # Iteration 3, S1: the key records R13, which the cell cites, but it is HRV-01's key.
            [("[decision] DOC-03: R13 authorizes a yes only on ['PRIN-12'], not on DOC-03 (R3, M1: "
              "NON_C_AUTHORITIES is frozen)"),
             # Iteration 4, M1: and its one key records R13, which does not authorize a yes on DOC-03.
             ("[decision] DOC-03: a yes row names no old-meaning key whose decision authorizes the yes "
              "(HRV-01-R13-four-tier-hierarchy -> R13); name the key of the C-number outside NO_ONLY, or of "
              "the NON_C_AUTHORITIES token, that changes the meaning (R3, iteration 4 M1)"),
             ("[decision] DOC-03: old-meaning key 'HRV-01-R13-four-tier-hierarchy' belongs to ['HRV-01'], "
              "not to DOC-03 (S1: KEY_OWNERS is frozen; a key cannot be borrowed)")],
            id="m1-r13-yes-on-a-row-r13-does-not-rule-on"),
        # Iteration 3, S1: scanner's DOC-03 case. "1–4" -> "1–3" under C38 with DOC-09's key: C38
        # authorizes the yes and the key's decision is cited, but the key is DOC-09's.
        pytest.param(
            _row("DOC-03", "s", "DOC-03", "C38", "yes", "DOC-09-C38-superseded-text-left-standing"),
            [("[decision] DOC-03: old-meaning key 'DOC-09-C38-superseded-text-left-standing' belongs to "
              "['DOC-09'], not to DOC-03 (S1: KEY_OWNERS is frozen; a key cannot be borrowed)")],
            id="s1-c38-row-borrows-doc-09s-key"),
        # Iteration 3, S1: the shared key is named by its two owners and by no third row.
        pytest.param(
            _row("PRIN-10", "s", "PRIN-10", "C19", "no",
                 "PRIN-10-C19-reduced-confidence, C19-hrv-04-reduced-confidence"),
            [("[decision] PRIN-10: old-meaning key 'C19-hrv-04-reduced-confidence' belongs to ['HRV-04', "
              "'REG-09'], not to PRIN-10 (S1: KEY_OWNERS is frozen; a key cannot be borrowed)")],
            id="s1-shared-key-on-a-third-row"),
        # Iteration 2, M1: T-26 appears only in the HRV-01 key's prose; the key records R13.
        pytest.param(
            _row("DOC-09", "s", "DOC-09", "C38, T-26", "yes",
                 "DOC-09-C38-superseded-text-left-standing, HRV-01-R13-four-tier-hierarchy"),
            [("[decision] DOC-09: old-meaning key 'HRV-01-R13-four-tier-hierarchy' records decision 'R13', "
             "which the row's cell 'C38, T-26' does not cite (M2, M1: a key cannot be borrowed)")],
            id="m1-borrow-through-a-token-in-the-hrv-01-keys-prose"),
        # Iteration 2, M1: R9 appears only in the T07 keys' prose; they record T-07.
        pytest.param(
            _row("DOC-09", "s", "DOC-09", "C38, R9", "yes",
                 "DOC-09-C38-superseded-text-left-standing, T07-acwr-band"),
            [("[decision] DOC-09: old-meaning key 'T07-acwr-band' records decision 'T-07', which the row's "
             "cell 'C38, R9' does not cite (M2, M1: a key cannot be borrowed)")],
            id="m1-borrow-through-r9-in-the-t07-keys-prose"),
        # Iteration 4, M1: the scanner's PRIN-10 route. The keyed no row turns yes by adding C38, which
        # authorizes a yes, beside C19; its one key records C19, which never does.
        pytest.param(
            _row("PRIN-10", "s", "PRIN-10, PRIN-21", "C19, C38", "yes", "PRIN-10-C19-reduced-confidence"),
            [("[decision] PRIN-10: a yes row names no old-meaning key whose decision authorizes the yes "
              "(PRIN-10-C19-reduced-confidence -> C19); name the key of the C-number outside NO_ONLY, or of "
              "the NON_C_AUTHORITIES token, that changes the meaning (R3, iteration 4 M1)")],
            id="m1-iter4-prin-10-c19-c38-yes-on-its-c19-key"),
        # Iteration 4, M1: the scanner's REG-09 route, on the key REG-09 shares with HRV-04.
        pytest.param(
            _row("REG-09", "s", "REG-09", "C19, C06", "yes", "C19-hrv-04-reduced-confidence"),
            [("[decision] REG-09: a yes row names no old-meaning key whose decision authorizes the yes "
              "(C19-hrv-04-reduced-confidence -> C19); name the key of the C-number outside NO_ONLY, or of "
              "the NON_C_AUTHORITIES token, that changes the meaning (R3, iteration 4 M1)")],
            id="m1-iter4-reg-09-c19-c06-yes-on-its-shared-c19-key"),
        # S5: a C19 no row with its key blanked.
        pytest.param(
            _row("HRV-03", "s", "HRV-03", "C19", "no"),
            [("[decision] HRV-03: cites C19, whose old meaning is still stated downstream, and names no "
             "old-meaning key (R3, F008 AC6)")],
            id="s5-c19-no-row-without-key"),
        # S5: PRIN-08's shared cell with C25's key dropped, C24's kept.
        pytest.param(
            _row("PRIN-08", "s", "PRIN-08", "C24, C25", "yes", "PRIN-08-C24-sidecar-ignored-by-default"),
            [("[decision] PRIN-08: cites C25 and names no old-meaning key whose decision is C25: "
             "['PRIN-08-C24-sidecar-ignored-by-default'] (R3, F008 AC6)")],
            id="s5-prin-08-without-its-c25-key"),
    ],
)
def test_decision_column_errors_names_an_unauthorized_yes_a_borrowed_key_and_a_missing_c19_c25_key(
        row: str, expected: list[str]) -> None:
    """Sprint-007 review iteration 1, M2 and S5, against the committed ``OLD_MEANINGS``: each message
    exactly, so a check that fires for another reason does not pass."""
    errors = decision_column_errors([_as_row(row)], False, meanings=_OM.OLD_MEANINGS)
    print(f"[slice compared] {row} -> {errors}")
    assert errors == expected


def test_decision_column_errors_is_green_on_every_non_c_authority() -> None:
    """The other side of M2 and M1: each frozen non-C token authorizes a ``yes`` row by itself on
    every inventory ID its set holds, and on no other."""
    for token, ids in sorted(NON_C_AUTHORITIES.items()):
        for inv in sorted(ids):
            errors = [e for e in decision_column_errors([_as_row(_row(inv, "s", inv, token, "yes", "k"))],
                                                        False, meanings={}) if "authorizes" in e]
            print(f"[slice compared] {token} on {inv} -> {errors}")
            assert errors == [], (token, inv, errors)
        outside = next(i for i in sorted(INVENTORY_IDS, key=_order_key) if i not in ids)
        errors = decision_column_errors([_as_row(_row(outside, "s", outside, token, "yes", "k"))],
                                        False, meanings={})
        print(f"[slice compared] {token} on {outside} -> {errors}")
        assert errors == [(f"[decision] {outside}: {token} authorizes a yes only on {sorted(ids)}, not on "
                           f"{outside} (R3, M1: NON_C_AUTHORITIES is frozen)")], (token, errors)


#: A second copy of ``NON_C_AUTHORITIES``, which the pin test compares the map against (sprint-007
#: review iteration 3, S2). Checking the map only against the table let the two widen together:
#: DOC-03 added to T-07's set with a ``T-07 | yes | T07-acwr-band`` row stayed green.
_NON_C_AUTHORITIES_PIN = {
    "HRV-11": frozenset({"HRV-11"}),
    "R13": frozenset({"PRIN-12"}),
    "T-07": frozenset({"GATE-03", "REG-02", "REG-16", "REG-19"}),
}


#: A second copy of ``KEY_OWNERS``, which the pin test compares the map against (sprint-007 review
#: iteration 4, S2), as ``_NON_C_AUTHORITIES_PIN`` does for its map: checked only against the table,
#: the map and the table could widen together. Derived 2026-09-26 from the committed table at
#: 790ea0c: 54 keys, all of ``OLD_MEANINGS``; one shared, by HRV-04 and REG-09.
_KEY_OWNERS_PIN: dict[str, frozenset[str]] = {
    "AUT-02-C23-override-outside-autonomy": frozenset({"AUT-02"}),
    "AUT-04-C20-two-purposes-only": frozenset({"AUT-04"}),
    "C01-withhold-not-judgeable-only": frozenset({"HRV-31"}),
    "C02-withhold-against-selected": frozenset({"HRV-31"}),
    "C03-return-is-free": frozenset({"HRV-34"}),
    "C04-hole-at-least": frozenset({"HRV-37"}),
    "C05-gate02-worse-rate-reopens": frozenset({"GATE-02"}),
    "C06-gate01-one-exception": frozenset({"GATE-01"}),
    "C06-hrv-25-accepted-cost": frozenset({"HRV-25"}),
    "C08-arch08-silence-tolerated-freely": frozenset({"ARCH-08"}),
    "C08-reg11-readiness-gate-down-weights": frozenset({"REG-11"}),
    "C09-fig05-idea071-sprint": frozenset({"FIG-05"}),
    "C09-residual-carried-to-idea-071": frozenset({"HRV-33"}),
    "C10-lone-candidate-never-struck": frozenset({"HRV-15"}),
    "C10-recency-only-rule-that-acts": frozenset({"HRV-16"}),
    "C12-same-baseline-window": frozenset({"HRV-12"}),
    "C13-era-clip-becomes-hole-clip": frozenset({"HRV-38"}),
    "C14-tier-change-collapses-baseline": frozenset({"HRV-34"}),
    "C15-tier-change-called-re-establishment": frozenset({"HRV-34"}),
    "C16-hrv21-reads-below-that-band": frozenset({"HRV-21"}),
    "C17-hrv24-read-on-last": frozenset({"HRV-24"}),
    "C18-no-tier-from-resolver": frozenset({"HRV-30"}),
    "C19-hrv-03-tag-and-confidence": frozenset({"HRV-03"}),
    "C19-hrv-04-reduced-confidence": frozenset({"HRV-04", "REG-09"}),
    "C21-dec01-bonus-section": frozenset({"DEC-01"}),
    "C24-arch06-ignores-by-default": frozenset({"ARCH-06"}),
    "C26-cold01-hrv-input": frozenset({"COLD-01"}),
    "C27-in-activity-hrv-not-computed-at-all": frozenset({"HRV-05"}),
    "C27-lt1-picked-up-without-amendment": frozenset({"LT1-02"}),
    "C28-lt1-surrogate-refinement": frozenset({"LT1-01"}),
    "C30-ctl-rise-row-deferred": frozenset({"REG-19"}),
    "C31-ind01-remains-tunable": frozenset({"IND-01"}),
    "C32-band-without-floor": frozenset({"HRV-07"}),
    "C33-hrv-17-tolerance-not-published": frozenset({"HRV-17"}),
    "C37-gate03-remeasured-not-cited": frozenset({"GATE-03"}),
    "DOC-06-C31-every-number-tunable": frozenset({"DOC-06"}),
    "DOC-09-C38-superseded-text-left-standing": frozenset({"DOC-09"}),
    "GOAL-02-C22-goal-contract-two-fields": frozenset({"GOAL-02"}),
    "HRV-01-R13-four-tier-hierarchy": frozenset({"HRV-01"}),
    "HRV-11-per-day-collapse-unspecified": frozenset({"HRV-11"}),
    "HRV-40-R13-now-sustaining-tier": frozenset({"HRV-40"}),
    "PRIN-05-C06-conservative-wins-unscoped": frozenset({"PRIN-05"}),
    "PRIN-08-C24-sidecar-ignored-by-default": frozenset({"PRIN-08"}),
    "PRIN-08-C25-rule-file-short-list": frozenset({"PRIN-08"}),
    "PRIN-10-C19-reduced-confidence": frozenset({"PRIN-10"}),
    "PRIN-12-C33-tolerance-not-published": frozenset({"PRIN-12"}),
    "PRIN-12-R13-withheld-response-stays-reproducible": frozenset({"PRIN-12"}),
    "PRIN-14-C07-weak-evidence-only": frozenset({"PRIN-14"}),
    "PRIN-15-C06-accepted-as-priced": frozenset({"PRIN-15"}),
    "PRIN-16-C08-silence-tolerated-freely": frozenset({"PRIN-16"}),
    "T07-acwr-band": frozenset({"REG-02"}),
    "T07-ctl-rise-band": frozenset({"REG-19"}),
    "T07-tolerance-band": frozenset({"GATE-03"}),
    "T07-tsb-target-form-band": frozenset({"REG-16"}),
}


def test_the_non_c_authority_map_is_the_committed_tables_yes_rows() -> None:
    """M1's map, frozen from the table at adcb66d: the map equals its pinned copy,
    ``_NON_C_AUTHORITIES_PIN``, for all three tokens; every committed ``yes`` row a non-C token
    authorizes is in that token's set; and every ID in a set is a committed ``yes`` row citing the
    token. A row cannot enter a set without the map, its pinned copy and the table all changing
    together (iteration 3, S2)."""
    _research, _history, rows = _real()
    cited = {t: {r["inventory ID"] for r in rows if r["meaning changed"] == "yes"
                 and t in _DECISION_TOKEN.findall(r["decision"])} for t in NON_C_AUTHORITIES}
    print(f"[slice compared] yes rows citing each token: {cited}; map {NON_C_AUTHORITIES}; "
          f"pin {_NON_C_AUTHORITIES_PIN}")
    drift = {t: sorted(NON_C_AUTHORITIES.get(t, frozenset()) ^ _NON_C_AUTHORITIES_PIN.get(t, frozenset()))
             for t in sorted(NON_C_AUTHORITIES.keys() | _NON_C_AUTHORITIES_PIN.keys())}
    assert NON_C_AUTHORITIES == _NON_C_AUTHORITIES_PIN, (
        f"NON_C_AUTHORITIES differs from its pinned copy (IDs in one and not the other): {drift}")
    assert {t: ids <= cited[t] for t, ids in NON_C_AUTHORITIES.items()} == dict.fromkeys(NON_C_AUTHORITIES, True)
    # PRIN-15 cites R13 for S9's counts and is yes under C06, not R13 (R13, S6).
    assert cited["R13"] - NON_C_AUTHORITIES["R13"] == {"PRIN-15"}
    assert all(cited[t] == ids for t, ids in NON_C_AUTHORITIES.items() if t != "R13")


def test_the_key_owner_map_is_the_committed_tables_key_column() -> None:
    """Iteration 3, S1, both ways: the owners the committed table gives each key equal
    ``KEY_OWNERS`` -- every key named only by its owners, and every owner naming it -- and the
    keys are exactly the committed ``OLD_MEANINGS``. One key is shared, by REG-09 and HRV-04; a
    second shared key is a change to review, not to freeze."""
    _research, _history, rows = _real()
    drift = {k: sorted(KEY_OWNERS.get(k, frozenset()) ^ _KEY_OWNERS_PIN.get(k, frozenset()))
             for k in sorted(KEY_OWNERS.keys() | _KEY_OWNERS_PIN.keys())
             if KEY_OWNERS.get(k) != _KEY_OWNERS_PIN.get(k)}
    print(f"[slice compared] KEY_OWNERS ({len(KEY_OWNERS)} keys) against _KEY_OWNERS_PIN "
          f"({len(_KEY_OWNERS_PIN)} keys): drift {drift}")
    assert KEY_OWNERS == _KEY_OWNERS_PIN, (
        f"KEY_OWNERS differs from its pinned copy (key: IDs in one and not the other): {drift}")
    derived: dict[str, set[str]] = {}
    for r in rows:
        for k in _keys(r):
            derived.setdefault(k, set()).add(r["inventory ID"])
    shared = {k: sorted(v) for k, v in derived.items() if len(v) > 1}
    print(f"[slice compared] {len(derived)} keys named by the table, {len(KEY_OWNERS)} frozen, "
          f"{len(_OM.OLD_MEANINGS)} in OLD_MEANINGS; shared {shared}")
    extra = {k: sorted(v - KEY_OWNERS.get(k, frozenset())) for k, v in derived.items()
             if v - KEY_OWNERS.get(k, frozenset())}
    missing = {k: sorted(v - derived.get(k, set())) for k, v in KEY_OWNERS.items() if v - derived.get(k, set())}
    print(f"[slice compared] rows naming a key they do not own {extra}; owners not naming their key {missing}")
    assert extra == {} and missing == {}
    assert {k: frozenset(v) for k, v in derived.items()} == KEY_OWNERS
    assert set(KEY_OWNERS) == set(_OM.OLD_MEANINGS)
    assert shared == {"C19-hrv-04-reduced-confidence": ["HRV-04", "REG-09"]}


def test_glossary_term_errors_names_a_renamed_term_a_cut_clause_and_a_twice_defined_term() -> None:
    """S4 on synthetic glossaries built from the frozen terms: green on the full glossary, and each
    mutation scanner B ran on the real file reds with its own message."""
    t24 = ("- **T-24 forbidden direction** IS asserting `hrv_normal` on evidence the system reports as "
           "insufficient, or while any dataset reported in the same response reads below its own HRV SWC band, "
           "whether or not that dataset is judgeable.")
    lines = {tid: f"- **{tid} {term}** IS a definition." for tid, term in GLOSSARY_TERMS.items()}
    lines["T-24"] = t24

    def text(*after: str, **override: str) -> str:
        return "\n".join([GLOSSARY_HEADING, "", *{**lines, **override}.values(), "", HEADINGS[1], *after]) + "\n"

    cut = t24.replace(", or while any dataset reported in the same response reads below its own HRV SWC band", "")
    # Iteration 2, S3: scanner C's narrower restatement of T-24, appended to PRIN-14's Not line.
    restated = ("Not: the manufacture of hard work on a green day, which PRIN-13 forbids separately, the "
                "forbidden direction being asserting `hrv_normal` on evidence the system reports as\n"
                "insufficient.")
    cases = {
        "full": (text(), []),
        "renamed": (text(**{"T-24": t24.replace("forbidden direction", "unsafe direction", 1)}),
                    ["[glossary] T-24 names the term 'unsafe direction', not 'forbidden direction'"]),
        "cut-disjunct": (text(**{"T-24": cut}),
                         [("[glossary] T-24 does not carry 'or while any dataset reported in the same response "
                          "reads below its own HRV SWC band' (after normalize)"),
                          ("[glossary] 'or while any dataset reported in the same response reads below its own "
                          "HRV SWC band' occurs 0 times in the whole file after normalize, not once (C07: the "
                          "forbidden direction is defined once, in the Glossary)")]),
        "restated-outside-the-glossary": (
            text("", restated),
            [("[glossary] 'asserting `hrv_normal` on evidence the system reports as insufficient' occurs 2 "
              "times in the whole file after normalize, not once (C07: the forbidden direction is defined "
              "once, in the Glossary)")]),
        "twice": (text(**{"T-23": "- **T-23 forbidden direction** IS a definition."}),
                  ["[glossary] T-23 names the term 'forbidden direction', not 'count'",
                   "[glossary] the term 'forbidden direction' is defined 2 times, not once"]),
    }
    for name, (glossary, expected) in cases.items():
        errors = glossary_term_errors(glossary)
        print(f"[slice compared] {name}: {errors}")
        assert errors == expected, (name, errors)


def test_inventory_sentence_errors_names_a_blank_and_an_edited_cell() -> None:
    """S3: the frozen hashes, not the table, say what DOC-03's inventory sentence is."""
    sentence = ("Decision records conform to Parts 1–4. Where a record and research/00 conflict, research/00 "
                "governs until the record is reconciled here.")
    rows = [_as_row(_row("DOC-03", cell, "DOC-03")) for cell in
            (sentence, ADDITION, sentence.replace("Parts 1–4", "Parts 1–3"))]
    rows.append(_as_row(_row(ADDITION, ADDITION, "DOC-19", "C38", "yes", "k")))
    errors = [inventory_sentence_errors([row]) for row in rows]
    print(f"[slice compared] {errors}")
    assert errors == [
        [],
        ["[inventory] DOC-03: the inventory sentence cell is blank ('—')"],
        [(f"[inventory] DOC-03: the inventory sentence cell is not the frozen inventory sentence: "
         f"{sentence.replace('Parts 1–4', 'Parts 1–3')[:120]!r}")],
        [],
    ]


def test_inventory_sentence_errors_names_a_cell_that_lost_its_backticks() -> None:
    """Iteration 2, S1, on scanner B's DOC-13 case: the cell with ``spec_outline.md`` un-backticked
    hashed the same after ``normalize()``, so the AC9 proxy stopped reading the identifier and the
    rule could drop it. The raw digest keeps backticks, so the cell is red; with them, green."""
    sentence = ("`spec_outline.md` Section 3 states only the band and the window. It is swept for band "
                "restatements and exempt from tier-rule sweeps.")
    bare = sentence.replace("`", "")
    rows = [_as_row(_row("DOC-13", cell, "DOC-13")) for cell in (sentence, bare, sentence.replace(" ", "  "))]
    errors = [inventory_sentence_errors([row]) for row in rows]
    print(f"[slice compared] {_sentence_digest(sentence)} vs {_sentence_digest(bare)}: {errors}")
    assert normalize(bare) == normalize(sentence), "the case no longer exercises what normalize() drops"
    assert errors == [
        [],
        [f"[inventory] DOC-13: the inventory sentence cell is not the frozen inventory sentence: {bare[:120]!r}"],
        [],
    ]


def test_decision_column_errors_no_longer_checks_ownership() -> None:
    """Mutation witness for Q1: the old exclusivity check named YES_ROW_OWNER on a cross-group row."""
    errors = decision_column_errors([_as_row(_row("GATE-01", "s", "GATE-01", "C06", "yes", "k"))], False)
    assert not any("YES_ROW_OWNER" in e for e in errors), errors


def test_decision_column_errors_needs_a_yes_row_for_every_other_decision(tmp_path) -> None:
    _a, rows = _world(tmp_path)
    rows = [dict(r, **{"meaning changed": "no"}) if "C22" in r["decision"] else r for r in rows]
    errors = decision_column_errors(rows, True, meanings=_a.meanings)
    print(f"[slice compared] {errors}")
    assert any("C22" in e and "yes row" in e for e in errors), errors


# ---------------------------------------------------------------------------
# AC7 operative strings, the AC9 proxy, pins and old meanings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", sorted(OPERATIVE))
def test_operative_string_errors_is_red_when_the_named_rule_loses_its_string(tmp_path, decision: str) -> None:
    a, rows = _world(tmp_path)
    assert operative_string_errors(a.research, rows, meanings=a.meanings) == []
    rule_id, strings = OPERATIVE[decision]
    block = rule_blocks(a.research)[rule_id]
    for s in strings:
        gutted = a.research.replace(block, block.replace(s, "zzz"))
        errors = operative_string_errors(gutted, rows, meanings=a.meanings)
        print(f"[slice compared] {decision} without {s!r} in {rule_id}: {errors}")
        assert any(decision in e and rule_id in e for e in errors), (s, errors)


def test_operative_string_errors_builds_the_c05_absence_from_old_meanings(tmp_path) -> None:
    a, rows = _world(tmp_path)
    reopened = a.research.replace("the system MUST NOT add hysteresis.",
                                  "the system MUST NOT add hysteresis, and a worse rate reopens it.")
    errors = operative_string_errors(reopened, rows, meanings=a.meanings)
    print(f"[slice compared] {errors}")
    assert any("C05" in e and "C05-reopens" in e for e in errors), errors
    errors = operative_string_errors(a.research, rows, meanings={})
    assert any("C05" in e and "OLD_MEANINGS" in e for e in errors), errors


def test_operative_string_errors_checks_only_the_decisions_its_rows_cite(tmp_path) -> None:
    a, rows = _world(tmp_path)
    hrv = [r for r in rows if r["inventory ID"].startswith("HRV-")]
    hrv_text = "\n\n".join(b for i, b in rule_blocks(a.research).items() if i.startswith("HRV-"))
    assert operative_string_errors(hrv_text, hrv, meanings={}, group="hrv") == []
    borrowed = hrv + [_as_row(_row("HRV-25", "s", "HRV-25", "C06", "yes"))]
    assert operative_string_errors(hrv_text, borrowed, meanings={}, group="hrv") == []
    assert any("PRIN-15" in e for e in operative_string_errors(hrv_text, borrowed, meanings={}))


@pytest.mark.parametrize(
    ("sentence", "block", "fragment"),
    [
        pytest.param("A gap of 21 days resets.", "**HRV-35.** A gap of 22 days MUST reset.", "21", id="number-lost"),
        pytest.param("Uses `min_window_readings`.", "**HRV-13.** It MUST use min window readings.", "min_window_readings", id="identifier-lost"),
        pytest.param("It is strictly below.", "**HRV-26.** It MUST be below.", "strictly", id="strictly-lost"),
        pytest.param("It holds more than 21.", "**HRV-37.** It MUST hold over 21.", "more than", id="more-than-lost"),
        pytest.param("Every count is 3 at least.", "**HRV-09.** A count MUST be 3 at least.", "every", id="every-lost"),
        pytest.param("It is 3 at least.", "**HRV-09.** It MUST be 3.", "at least", id="at-least-lost"),
        pytest.param("It is never CV.", "**HRV-07.** It MUST NOT be CV.", "never", id="never-lost"),
        pytest.param("It changes only here.", "**DOC-04.** It MUST change here.", "only", id="only-lost"),
        pytest.param("Left unchanged (2026-09-18).", "**HRV-33.** It MUST stay unchanged.", "H-NN", id="date-without-history-cite"),
    ],
)
def test_proxy_errors_turns_red_on_each_lost_token(sentence: str, block: str, fragment: str) -> None:
    errors = proxy_errors(sentence, block)
    print(f"[slice compared] {sentence!r} vs {block!r}: {errors}")
    assert any(fragment in e for e in errors), errors


def test_proxy_errors_is_green_when_every_token_survives() -> None:
    sentence = ("Every count uses `min_baseline_readings` = 14, 21 does not reset (2026-09-18), and it is never CV "
                "− only 0.5·SD.")
    block = ("**HRV-09.** Every count MUST use `min_baseline_readings` = 14, so 21 does not reset and it is never CV, "
             "only 0.5·SD.\nScope: every count.\nNot: the 2 CV forms.\nPinned: none\nWhy: user decision H-18.")
    errors = proxy_errors(sentence, block)
    print(f"[slice compared] {errors}")
    assert errors == []
    assert proxy_errors("Every day is judged – not 7−1.", "**HRV-08.** Every day MUST be judged, 7-1 excepted.") == []
    assert proxy_errors("Everything is judged.", "**HRV-08.** All is judged.") == []


def test_pinned_errors_finds_real_nodes_and_rejects_missing_ones() -> None:
    here = f"runcoach-api/tests/{Path(__file__).name}"
    good = (f"Pinned: {here}::test_pinned_errors_finds_real_nodes_and_rejects_missing_ones\n"
            f"Pinned: {here}::test_band_errors[acwr-band]\nPinned: none\nPinned: none (F009)")
    assert pinned_errors(good, _REPO_ROOT) == []
    errors = pinned_errors(f"Pinned: runcoach-api/tests/no_such_file.py::test_x\nPinned: {here}::test_no_such_def",
                           _REPO_ROOT)
    print(f"[slice compared] {errors}")
    assert len(errors) == 2 and "no_such_file" in errors[0] and "test_no_such_def" in errors[1]


@pytest.mark.parametrize(
    ("entry", "research", "fragment"),
    [
        pytest.param(OldMeaning("(unclosed", "x", "a.md:1@4e47d0e", "C05"), "", "compile", id="pattern-does-not-compile"),
        pytest.param(OldMeaning("worse rate re-opens", "a worse rate reopens it", "a.md:1@4e47d0e", "C05"), "", "example", id="pattern-misses-example"),
        pytest.param(OldMeaning("worse rate reopens", "a worse rate reopens it", "a.md:1@4e47d0e", "C05"), "A **worse** rate `reopens`.", "research/00", id="pattern-matches-research"),
        pytest.param(OldMeaning("worse rate reopens", "a worse rate reopens it", "a.md", "C05"), "", "source", id="source-malformed"),
    ],
)
def test_old_meaning_errors_turns_red(entry, research: str, fragment: str) -> None:
    errors = old_meaning_errors({"k": entry}, research)
    print(f"[slice compared] {errors}")
    assert any(fragment in e for e in errors), errors


def test_old_meaning_errors_is_green_and_matches_the_normalized_example() -> None:
    entry = OldMeaning("capture hole of at least gapresetdays", "an internal capture hole of at least `gap_reset_days`",
                       "specification/research/00-design-decisions.md:230@4e47d0e", "C04")
    assert old_meaning_errors({"k": entry}, "A capture hole of more than `gap_reset_days` (21) days.") == []


# ---------------------------------------------------------------------------
# Review scope (R7)
# ---------------------------------------------------------------------------


def test_required_review_rows_is_every_block_and_every_scope_not_and_why_line() -> None:
    research = "\n\n".join([_block("DOC-01", why="Why: C38."), _block("HRV-01"), _block("HRV-02")])
    rows = [_as_row(_row("DOC-01", "s", "DOC-01", "C38", "yes")), _as_row(_row("HRV-01", "s", "HRV-01, HRV-02")),
            _as_row(_row("HRV-03", "s", "H-05 (history)"))]
    need = required_review_rows(research, rows)
    print(f"[slice compared] {need}")
    assert need == ["DOC-01", "DOC-01/Scope", "DOC-01/Not", "DOC-01/Why", "HRV-01", "HRV-01/Scope", "HRV-01/Not",
                    "HRV-02", "HRV-02/Scope", "HRV-02/Not"]
    assert required_review_rows(research, rows, group="hrv") == need[4:]
    assert required_review_rows(research, rows, group="arch-dec") == []


# ---------------------------------------------------------------------------
# assemble_check and fragment_errors, each red case one mutation of the green world
# ---------------------------------------------------------------------------


def _sub(name: str, old: str, new: str, count: int = -1):
    def mutate(files: dict[str, str]) -> None:
        assert old in files[name], f"the mutation's target {old!r} is not in {name}"
        files[name] = files[name].replace(old, new, count)
    return mutate


def _put(name: str, text: str, *, append: bool = False):
    def mutate(files: dict[str, str]) -> None:
        files[name] = (files.get(name, "") if append else "") + text
    return mutate


_C05_LINE = json.dumps(_WORLD_C05, ensure_ascii=False)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        pytest.param(_sub("hrv.rules.txt", "Rule HRV-02 MUST", "The band MUST"), "band", id="band-outside-hrv"),
        pytest.param(_sub("history.txt", "→ FTO-06", "→ FTO-09"), "arrow", id="history-arrow-unresolved"),
        pytest.param(_sub("hrv.rules.txt", "Rule HRV-02 MUST hold every day.", "Rule HRV-02 MUST hold. Rule HRV-02 MUST hold every day."), "one sentence", id="grammar-after-merge"),
        pytest.param(_sub("hrv.rules.txt", "Scope: every per-tier dataset.", "Scope: every dataset.", 1), "per-tier dataset", id="anchor-scope-lost"),
        pytest.param(_sub("arch-dec.rules.txt", "**FIG-02.** Rule FIG-02 MUST hold every day.", "**FIG-02.** FIG-01. Rule FIG-01 MUST hold every day and FIG-02."), "anchor", id="fig01-lead-duplicated"),
        pytest.param(_sub("hrv.trace.txt", "| Rule HRV-09 holds every day. |", "| Rule HRV-09 holds 40 days. |"), "40", id="proxy-on-a-no-row"),
        pytest.param(_put("glossary.txt", "- **T-01 again** IS twice.\n", append=True), "glossary", id="glossary-duplicate"),
        pytest.param(_put("doc-goal.rules.txt", "\n" + _block("DOC-16") + "\n", append=True), "not named", id="unmapped-rule"),
        pytest.param(_put("hrv.meanings.txt", _C05_LINE.replace("worse rate reopens", "worse rate") + "\n"), "duplicate", id="duplicate-key-across-groups"),
        pytest.param(_put("doc-goal.meanings.txt", json.dumps(dict(_WORLD_C05, key="k2", pattern="synthetic definition", example="the synthetic definition")) + "\n"), "research/00", id="r4-negative-over-the-glossary"),
        pytest.param(_sub("arch-dec.rules.txt", "test_the_ac23_flip", "test_no_ac23_flip"), "def", id="pinned-node-missing"),
        pytest.param(_sub("doc-goal.rules.txt", "### 1.1 The supreme objective", "### 1.1 A heading of our own"), "heading", id="unknown-heading"),
    ],
)
def test_assemble_check_turns_red_on_each_cross_group_finding(tmp_path, mutate, fragment: str) -> None:
    files = _world_files()
    mutate(files)
    errors = assemble_check(_write(tmp_path, files))
    print(f"[slice compared] {errors[:8]}")
    assert any(fragment in e for e in errors), errors


def test_fragment_errors_is_green_on_each_world_group(tmp_path) -> None:
    drafts, inventory = _write(tmp_path, _world_files()), _inventory(tmp_path)
    for group in GROUPS:
        errors = fragment_errors(drafts, group, inventory, show=_old_show)
        print(f"[slice compared] {group}: {errors[:5]}")
        assert errors == []


def _drop_file(name: str):
    def mutate(files: dict[str, str]) -> None:
        del files[name]
    return mutate


def _drop_first_row(files: dict[str, str]) -> None:
    files["hrv.trace.txt"] = files["hrv.trace.txt"].split("\n", 1)[1]


@pytest.mark.parametrize(
    ("group", "mutate", "fragment"),
    [
        pytest.param("hrv", _drop_first_row, "have no row: ['HRV-01']", id="missing-group-id"),
        pytest.param("hrv", _put("hrv.trace.txt", _row("HRV-02", "Rule HRV-02 holds every day.", "HRV-02") + "\n", append=True), "duplicate", id="duplicate-group-id"),
        pytest.param("hrv", _put("hrv.trace.txt", _row("DOC-01", "Rule DOC-01 holds every day.", "DOC-01") + "\n", append=True), "outside", id="row-from-another-group"),
        pytest.param("hrv", _sub("hrv.trace.txt", "| §5.4 | HRV-09 |", "| §5.4 | HRV-48 |"), "HRV-48", id="in-group-id-unresolved"),
        pytest.param("hrv", _sub("hrv.trace.txt", "Rule HRV-09 holds every day.", "Rule HRV-09 holds each day."), "inventory sentence", id="sentence-not-verbatim"),
        pytest.param("hrv", _put("hrv.rules.txt", "\n" + _block("DOC-16") + "\n", append=True), "prefix", id="rule-outside-group-prefixes"),
        pytest.param("hrv", _sub("hrv.rules.txt", "could not have been selected", "was not selected"), "C01", id="operative-string-lost"),
        pytest.param("hrv", _sub("hrv.trace.txt", "| Rule HRV-09 holds every day. |", "| Rule HRV-09 holds 40 days. |"), "40", id="proxy-no-row"),
        pytest.param("arch-dec", _put("arch-dec.meanings.txt", _C05_LINE.replace("worse rate reopens", "every day") + "\n"), "research/00", id="r4-negative-over-group"),
        pytest.param("arch-dec", _put("arch-dec.meanings.txt", _C05_LINE.replace("hysteresis decision\"", "hysteresis decision, invented\"") + "\n"), "4e47d0e", id="example-not-verbatim"),
        pytest.param("hrv", _sub("hrv.trace.txt", "| C11 | no |", "| C11 | yes |"), "never authorizes", id="decision-column"),
        pytest.param("hrv", _sub("hrv.trace.txt", "| C01, C02 | yes |", "| C01, C02 | no |"), "owns C01", id="group-coverage"),
        pytest.param("doc-goal", _sub("doc-goal.trace.txt", "| C24, C25 | yes | doc-goal-old, doc-goal-old-c25 |", "| C24, C25 | yes | — |"), "old-meaning key", id="yes-row-without-key"),
        pytest.param("hrv", _sub("hrv.rules.txt", "Rule HRV-02 MUST", "The band MUST"), "band", id="band"),
        pytest.param("hrv", _drop_file("hrv.meanings.txt"), "hrv.meanings.txt", id="missing-draft"),
        pytest.param("hrv", _sub("hrv.trace.txt", "| no | — |", "| no | k9 |", 1), "no such old-meaning key", id="key-not-in-group-meanings"),
        pytest.param("hrv", _sub("hrv.rules.txt", "Rule HRV-02 MUST hold every day.", "Rule HRV-02 MUST hold. It does."), "one sentence", id="grammar"),
    ],
)
def test_fragment_errors_turns_red(tmp_path, group: str, mutate, fragment: str) -> None:
    files = _world_files()
    mutate(files)
    drafts, inventory = _write(tmp_path, files), _inventory(tmp_path)
    errors = fragment_errors(drafts, group, inventory, show=_old_show)
    print(f"[slice compared] {group}: {errors[:8]}")
    assert any(fragment in e for e in errors), errors


# ---------------------------------------------------------------------------
# Real-block sanity (T170 AC4): one hand-written rule block, trace row and old meaning per group,
# written from its inventory row. The fragment-level checks must report 0 on each: the proof that
# no checker is too strict for real text. Each example is verbatim research/00 at 4e47d0e; the
# git-show half of that check needs a full clone, so it runs in the probe, not here.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RealCase:
    group: str
    heading: str
    rules: str
    rows: tuple[str, ...]
    sentences: dict[str, str]
    meanings: dict[str, dict[str, str]] = field(default_factory=dict)


_GOAL_02 = ("The goal contract belongs to the athlete. The system may propose a change but never applies one, and a "
            "change takes effect only as an athlete-supplied input, exactly as at program start.")
_GATE_02 = 'The dataset-flip rate is measured the same way, and a worse rate "reopens the deferred hysteresis decision".'
_FIG_01 = ("After a coverage-gap re-establishment the athlete traverses 20 days beneath `min_baseline_readings` "
           "(`R+0 .. R+19`).")
_HRV_37 = "A dataset's band is also clipped, unreported, at an internal capture hole in its own baseline window."
_HRV_09 = ("Every count in the rule is in distinct local days, with `min_baseline_readings` = 14 and "
           "`min_window_readings` = 3.")

REAL_CASES = (
    RealCase(
        "doc-goal", "### 1.9 System ownership: plan versus goal",
        "\n".join([
            ("**GOAL-02.** The goal contract IS the three athlete-owned fields `goal_pace_target`, `race_date` and "
            "`distance_m`, which the system MAY propose to change but MUST never change itself, so a change takes "
            "effect only as an athlete-supplied input, exactly as at program start."),
            "Scope: every goal-contract field, and every proposal the system makes about one.",
            "Not: the order in which the system proposes remedies, which GOAL-03 sets.",
            "Pinned: none",
            "Why: decision C22 bundles the three fields that two of the three sources already bundle.",
        ]),
        (_row("GOAL-02", _GOAL_02, "GOAL-02", "C22", "yes", "C22-goal-contract-two-fields", "§1.9"),),
        {"GOAL-02": _GOAL_02},
        {"C22-goal-contract-two-fields": {
            "pattern": r"goal contract -- the declared target pace and the race date",
            "example": "The *goal contract* — the declared target pace and the race date — is the athlete's.",
            "source": "specification/research/00-design-decisions.md:65@4e47d0e", "decision": "C22"}},
    ),
    RealCase(
        "arch-dec", "### 5.4 Reconciliations and amendments",
        "\n".join([
            ("**GATE-02.** The system MUST NOT add hysteresis to dataset selection, and the worsened dataset-flip set "
            "MUST remain the 80 pinned `walk_flips` cells at `car_density = 2wk`."),
            "Scope: the dataset-flip rate, measured against shipped F005 on every sweep the same way as GATE-01.",
            "Not: a worsened cell outside that pinned set, which fails the gate.",
            ("Pinned: runcoach-api/tests/test_hrv_no_regression_gate.py::"
            "test_the_ac23_flip_rate_comparison_is_asserted_and_its_worsened_cells_are_pinned"),
            ("Why: the no-hysteresis decision is recorded at H-37 and is revisited only if the unequal-dispersion "
            "measurement of IDEA-089 (b) shows harm."),
            "",
            ("**FIG-01.** After a coverage-gap re-establishment the athlete traverses 20 days beneath "
            "`min_baseline_readings` (`R+0 .. R+19`), and the spec MUST publish that figure."),
            "Scope: a coverage-gap re-establishment of the series (HRV-35), not a source-tier change.",
            "Not: the cost of a source-tier change, which FIG-02 states.",
            "Pinned: none",
        ]),
        (_row("GATE-02", _GATE_02, "GATE-02", "C05", "yes", "C05-worse-rate-reopens", "§5.4, AC23"),
         _row("FIG-01", _FIG_01, "FIG-01", ADDITION, "no", ADDITION, "§5.4 [T116/T126] (test-anchored)")),
        {"GATE-02": _GATE_02, "FIG-01": _FIG_01},
        {"C05-worse-rate-reopens": {
            "pattern": r"worse rate reopens",
            "example": "a worse rate reopens the deferred hysteresis decision",
            "source": "specification/research/00-design-decisions.md:230@4e47d0e", "decision": "C05"}},
    ),
    RealCase(
        "hrv", "### 5.4 Reconciliations and amendments",
        "\n".join([
            ("**HRV-37.** A per-tier dataset's SWC band MUST also be clipped, unreported, at an internal capture hole "
            "in its own baseline window, meaning more than `gap_reset_days` (21) silent local days of its tier, so 21 "
            "does not clip and 22 does."),
            "Scope: every per-tier dataset, over the silent local days of its own tier inside its dataset baseline window.",
            "Not: a silence of the whole series, which the coverage gap of HRV-35 resets.",
            "Pinned: none",
            "",
            ("**HRV-09.** Every count in the HRV rule MUST be in distinct local days, with `min_baseline_readings` = 14 "
            "and `min_window_readings` = 3."),
            "Scope: every count HRV-08 to HRV-46 take, in every per-tier dataset.",
            "Not: a count of captures, since a second capture on one local day adds no day.",
            "Pinned: none",
        ]),
        (_row("HRV-37", _HRV_37, "HRV-37", "C04", "yes", "C04-hole-at-least", "§5.4 (v), AC17"),
         _row("HRV-09", _HRV_09, "HRV-09", ADDITION, "no", ADDITION, "§5.4, (i)")),
        {"HRV-37": _HRV_37, "HRV-09": _HRV_09},
        {"C04-hole-at-least": {
            "pattern": r"capture hole of at least gapresetdays",
            "example": "an internal capture hole of at least `gap_reset_days`",
            "source": "specification/research/00-design-decisions.md:230@4e47d0e", "decision": "C04"}},
    ),
)


def _meanings_text(case: RealCase) -> str:
    return "\n".join(json.dumps(dict(v, key=k), ensure_ascii=False) for k, v in case.meanings.items())


@pytest.mark.parametrize("case", REAL_CASES, ids=lambda c: c.group)
def test_real_blocks_pass_every_fragment_level_check(case: RealCase) -> None:
    rules = f"{case.heading}\n\n{case.rules}\n"
    errors = fragment_text_errors(rules, "\n".join(case.rows), _meanings_text(case), case.group, case.sentences,
                                  coverage=False)
    errors += pinned_errors(rules, _REPO_ROOT)
    print(f"[slice compared] {case.group}: rules {rule_ids(rules)} rows {len(case.rows)} "
          f"keys {list(case.meanings)} -> {errors}")
    assert errors == []


def test_the_real_old_meaning_patterns_miss_their_new_rules() -> None:
    """R4's hard half: each pattern matches its old example and misses the correct new statement,
    e.g. C04's "at least" pattern against HRV-37's "more than"."""
    for case in REAL_CASES:
        for key, entry in load_meanings_jsonl(_meanings_text(case)).items():
            print(f"[slice compared] {key}: {entry.pattern!r} vs {normalize(entry.example)!r}")
            assert re.search(entry.pattern, normalize(entry.example)), key
            assert not re.search(entry.pattern, normalize(case.rules)), key


# ---------------------------------------------------------------------------
# The real paths (T175): the committed research/00, history and traceability table, and the
# committed OLD_MEANINGS, held to the same complete check the drafts passed under assemble_check.
# ---------------------------------------------------------------------------

_REAL_RESEARCH = _REPO_ROOT / "specification" / "research" / "00-design-decisions.md"
_REAL_HISTORY = _REPO_ROOT / "specification" / "research" / "00-history.md"
_REAL_TABLE = _REPO_ROOT / "specification" / "research" / "00-traceability.md"


def _real() -> tuple[str, str, list[dict[str, str]]]:
    research = _REAL_RESEARCH.read_text(encoding="utf-8")
    history = _REAL_HISTORY.read_text(encoding="utf-8")
    return research, history, parse_traceability(_REAL_TABLE.read_text(encoding="utf-8"))


def test_real_path_ac1_ac2_ac3_every_line_of_research00_is_grammar() -> None:
    """AC1 (no ISO date, no line over 400 characters, one sentence per rule line), AC2 (every line
    outside the headings and the Glossary is a rule, Scope, Not, Pinned, Why or blank line) and AC3's
    unique, well-formed IDs, over the committed file."""
    research, _history, _rows = _real()
    errors = rule_grammar_errors(research)
    ids = rule_ids(research)
    longest = max(len(line) for line in _lines(research))
    print(f"[slice compared] {_REAL_RESEARCH.name}: {len(ids)} rule lines, longest line {longest}, "
          f"errors {errors[:10]}")
    assert errors == []
    assert len(ids) >= 120 and len(set(ids)) == len(ids)
    assert not _ISO_DATE.search(" ".join(research.split()))
    # AC2 / R8: the Part and section headings are kept, in order, with the Glossary directly before
    # Part 1 (S6). ``rule_grammar_errors`` checks membership only.
    heads = [line for line in _lines(research) if line.startswith("#")]
    want = [HEADINGS[0], GLOSSARY_HEADING, *HEADINGS[1:]]
    print(f"[slice compared] {len(heads)} headings in order: {heads}")
    assert heads == want, (
        f"the headings are not R8's, in order: missing {[h for h in want if h not in heads]}, "
        f"first difference at {next((i for i, (a, b) in enumerate(zip(heads, want)) if a != b), None)}"
    )


def test_real_path_ac4_glossary_and_band() -> None:
    research, _history, _rows = _real()
    errors = glossary_errors(research) + band_errors(research) + glossary_term_errors(research)
    t24 = next((line for line in _lines(research) if line.startswith("- **T-24 ")), None)
    print(f"[slice compared] glossary and band over {_REAL_RESEARCH.name}: {errors[:10]}; T-24: {t24!r}")
    assert errors == []
    assert GLOSSARY_HEADING in _lines(research)


def test_real_path_ac3_ac5_history_entries_retired_ids_and_arrows() -> None:
    """AC5's entry format and AC3's retired IDs: ``## Retired IDs`` lists exactly what
    ``retired_ids`` derives from the committed table, no retired ID names a rule, and every
    history arrow resolves to a rule or a retired ID."""
    research, history, rows = _real()
    errors = history_errors(history)
    listed = _retired_listed(history)
    derived = {i: h for i, h in retired_ids(rows).items() if h}
    resolvable = set(rule_ids(research)) | set(listed)
    for h, ids in _history_arrows(history).items():
        errors += [f"[history] {h}'s arrow cites {i}, neither a rule nor a retired ID" for i in ids if i not in resolvable]
    print(f"[slice compared] {len(history_ids(history))} entries, retired listed {listed}, derived {derived}: "
          f"{errors[:10]}")
    assert errors == []
    assert history_ids(history) == [f"H-{i:02d}" for i in range(1, 42)]
    assert listed == derived and listed
    assert not set(listed) & set(rule_ids(research))


def test_real_path_ac6_traceability_both_directions_and_pins() -> None:
    """AC6 over the committed table: the 149 inventory IDs, every new ID resolving, every rule named,
    every key in the committed ``OLD_MEANINGS``, every ``Pinned:`` node found; and R11's anchors
    (the three source_change anchors and the FIG-01 lead) each unique."""
    research, history, rows = _real()
    header = _REAL_TABLE.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n", 1)[0]
    errors = (traceability_errors(rows, research, history, _OM.OLD_MEANINGS)
              + pinned_errors(research, _REPO_ROOT) + _anchor_errors(research))
    print(f"[slice compared] {len(rows)} rows, {len(rule_ids(research))} rules, "
          f"{len(_OM.OLD_MEANINGS)} keys: {errors[:10]}")
    assert header == TRACE_HEADER
    assert errors == []


def test_real_path_decision_column_is_complete() -> None:
    _research, _history, rows = _real()
    errors = decision_column_errors(rows, True, meanings=_OM.OLD_MEANINGS)
    yes = [(r["inventory ID"], r["decision"]) for r in rows if r["meaning changed"] == "yes"]
    print(f"[slice compared] R3 over {len(rows)} rows, {len(yes)} yes rows {yes}: {errors[:10]}")
    assert errors == []


def test_real_path_ac7_every_decision_is_stated_in_its_rule() -> None:
    research, _history, rows = _real()
    errors = operative_string_errors(research, rows)
    cited = sorted({c for r in rows for c in _C_ID.findall(r["decision"])} & set(OPERATIVE))
    print(f"[slice compared] operative strings for {cited}: {errors[:10]}")
    assert errors == []
    assert cited == sorted(OPERATIVE)


def test_real_path_ac9_proxy_on_every_unchanged_row() -> None:
    research, _history, rows = _real()
    errors = _proxy_rows(rows, research) + inventory_sentence_errors(rows)
    checked = sum(1 for r in rows if r["meaning changed"] == "no" and not _is_blank(r["inventory sentence"]))
    frozen = sum(1 for r in rows if r["inventory ID"] in INVENTORY_SENTENCE_SHA256)
    print(f"[slice compared] AC9 proxy over {checked} no rows, {frozen} sentence cells against the frozen "
          f"hashes: {errors[:10]}")
    assert errors == []
    assert checked > 0
    assert frozen == len(INVENTORY_SENTENCE_SHA256) == 149


def _real_row_swap(rows: list[dict[str, str]], inv: str, **cells: str) -> list[dict[str, str]]:
    assert sum(r["inventory ID"] == inv for r in rows) == 1, inv
    return [dict(r, **cells) if r["inventory ID"] == inv else r for r in rows]


def test_real_path_every_traceability_row_is_the_frozen_row() -> None:
    """Iteration 4, M1 and M2: the committed table is ``TRACEABILITY_ROW_SHA256``, row by row and cell
    by cell, with no row added or removed."""
    _research, _history, rows = _real()
    errors = traceability_row_errors(rows)
    keys = [_row_key(r) for r in rows]
    prin16 = next(r for r in rows if r["inventory ID"] == "PRIN-16")
    print(f"[slice compared] {len(rows)} table rows, {len(set(keys))} keys, {len(TRACEABILITY_ROW_SHA256)} "
          f"frozen; PRIN-16 {row_digest(prin16)} vs {TRACEABILITY_ROW_SHA256['PRIN-16']}: {errors[:5]}")
    assert errors == []
    assert set(keys) == set(TRACEABILITY_ROW_SHA256) == INVENTORY_IDS and len(keys) == 149


def test_real_path_every_traceability_line_is_the_header_the_separator_or_a_row() -> None:
    """Iteration 6, S1: the committed table is ``TRACE_HEADER``, ``TRACE_SEPARATOR`` and one parsed row per
    later line, and nothing else, so no line of it sits outside ``TRACEABILITY_ROW_SHA256``."""
    text = _REAL_TABLE.read_text(encoding="utf-8")
    lines = _lines(text)
    errors = table_line_errors(text)
    rows = parse_traceability(text)
    print(f"[slice compared] {_REAL_TABLE.name}: {len(lines)} split lines, last {lines[-1]!r}, {len(rows)} rows; "
          f"line 2 {lines[1]!r}; line {len(lines) - 1} {lines[-2][:60]!r}: {errors[:5]}")
    assert errors == []
    assert lines[-1] == "" and len(lines) - 3 == len(rows) == 149


def test_table_line_errors_names_a_line_that_is_not_a_row() -> None:
    """Iteration 6, S1, on synthetic text shaped like the scanner's routes, each message exactly: a
    correction line after the last row (U1c), a blockquoted contradicting row (U1b), the separator deleted
    (U2), and the header edited. ``parse_traceability`` returns the same rows for each route, so no row
    check can see them."""
    rows = [_row("DOC-03", "Decision records conform to Parts 1-4.", "DOC-03"),
            _row("HRV-46", "The rule keys on `hrv_source_tier` alone.", "HRV-46, HRV-84")]
    base = "\r\n".join([TRACE_HEADER, TRACE_SEPARATOR, *rows]) + "\r\n"
    assert table_line_errors(base) == [] and len(parse_traceability(base)) == 2
    u1c = ("Correction (C38): DOC-03's meaning changed; read its row as yes, key "
           "DOC-09-C38-superseded-text-left-standing.")
    u1b = "> " + _row("DOC-03", "(see above)", "DOC-03", "C38", "yes", "DOC-09-C38-superseded-text-left-standing",
                      cited=ADDITION)
    routes = {
        "u1c": base + u1c + "\r\n",
        "u1b": _one_edit(base, rows[0] + "\r\n", rows[0] + "\r\n" + u1b + "\r\n"),
        "u2": _one_edit(base, TRACE_SEPARATOR + "\r\n", ""),
        "header": _one_edit(base, "| old-meaning key |", "| key |"),
    }
    for name in ("u1c", "u1b"):
        assert parse_traceability(routes[name]) == parse_traceability(base), name
    _check_cases(table_line_errors, {
        "u1c-a-correction-after-the-last-row": ((routes["u1c"],), [
            f"[frozen-table] line 5: a table line that is not a row cannot be bound: {u1c[:120]!r}"]),
        "u1b-a-blockquoted-row": ((routes["u1b"],), [
            f"[frozen-table] line 4: a table line that is not a row cannot be bound: {u1b[:120]!r}"]),
        "u2-the-separator-deleted": ((routes["u2"],), [
            f"[frozen-table] line 2: the separator is not '|---|---|---|---|---|---|---|' (now {rows[0][:120]!r})"]),
        "the-header-edited": ((routes["header"],), [
            f"[frozen-table] line 1: the header is not TRACE_HEADER (now {TRACE_HEADER.replace('old-meaning key', 'key')[:120]!r})"]),
    })


def test_traceability_row_errors_names_the_changed_row_and_cell() -> None:
    """Iteration 4, M1 and M2, on the scanner's routes, each message exactly: M1's PRIN-10 row turned
    ``yes`` under ``C19, C38``; M2's DOC-03 retired under H-39 with an addition row DOC-23; a row removed;
    a row repeated. Iteration 6, S2: the routes run over the synthetic world's table (``_world_files()``:
    PRIN-10 ``no`` under C19, DOC-03 ``no`` with no decision), not the committed one, so a reviewed edit
    to the committed PRIN-10, DOC-03 or HRV-09 row cannot change what this test asserts; the frozen side is
    derived from the unmutated rows and each expected digest from the mutated row (iteration 5, S5)."""
    world = _world_files()
    rows = parse_traceability("".join(world[f"{group}.trace.txt"] for group in GROUPS))
    frozen = {_row_key(r): row_digest(r) for r in rows}
    before = {r["inventory ID"]: (r["decision"], r["meaning changed"]) for r in rows if r["inventory ID"] in ("PRIN-10", "DOC-03")}
    print(f"[slice compared] world rows {len(rows)}; before the routes {before}")
    assert before == {"PRIN-10": ("C19", "no"), "DOC-03": (ADDITION, "no")}
    m1 = _real_row_swap(rows, "PRIN-10", decision="C19, C38", **{"meaning changed": "yes"})
    m2 = _real_row_swap(rows, "DOC-03", **{"new ID(s)": "H-39"}) + [_as_row(_row(ADDITION, ADDITION, "DOC-23", "C38",
                                                                                   cited=ADDITION))]
    prin10 = row_digest(next(r for r in m1 if r["inventory ID"] == "PRIN-10"))
    doc03 = row_digest(next(r for r in m2 if r["inventory ID"] == "DOC-03"))
    doc23 = row_digest(m2[-1])
    assert traceability_row_errors(rows, frozen) == []
    cases = {
        "m1-prin-10": (m1, [
            (f"[frozen-table] PRIN-10: the 'decision' cell is not the frozen cell (now 'C19, C38'); once the "
             f"edit is reviewed, TRACEABILITY_ROW_SHA256['PRIN-10'] = '{prin10}'"),
            (f"[frozen-table] PRIN-10: the 'meaning changed' cell is not the frozen cell (now 'yes'); once the "
             f"edit is reviewed, TRACEABILITY_ROW_SHA256['PRIN-10'] = '{prin10}'")]),
        "m2-doc-03": (m2, [
            (f"[frozen-table] DOC-03: the 'new ID(s)' cell is not the frozen cell (now 'H-39'); once the edit "
             f"is reviewed, TRACEABILITY_ROW_SHA256['DOC-03'] = '{doc03}'"),
            (f"[frozen-table] row addition DOC-23 is not in TRACEABILITY_ROW_SHA256 (a row was added or its "
             f"key cell changed); its digest is '{doc23}'")]),
        "removed": ([r for r in rows if r["inventory ID"] != "HRV-09"],
                    ["[frozen-table] row HRV-09 is frozen in TRACEABILITY_ROW_SHA256 and is not in the table"]),
        "repeated": (rows + [next(r for r in rows if r["inventory ID"] == "HRV-09")],
                     ["[frozen-table] row HRV-09 occurs 2 times, not once"]),
    }
    for name, (mutated, expected) in cases.items():
        errors = traceability_row_errors(mutated, frozen)
        print(f"[slice compared] {name}: {errors}")
        assert errors == expected, (name, errors)


def test_real_path_retired_ids_are_frozen_and_each_retires_under_its_decision() -> None:
    """Iteration 4, M2: the table retires exactly ``RETIRED_IDS``, the history lists exactly it, and
    PRIN-16, the one row retired under an H-NN, is ``yes`` under C08, whose entry is H-39."""
    _research, history, rows = _real()
    errors = retirement_errors(rows, history)
    prin16 = next(r for r in rows if r["inventory ID"] == "PRIN-16")
    print(f"[slice compared] retired_ids {retired_ids(rows)}, listed {_retired_listed(history)}, frozen "
          f"{RETIRED_IDS}; PRIN-16 {prin16['new ID(s)']!r} {prin16['decision']!r} {prin16['meaning changed']!r} "
          f"-> {_decision_group_entry(prin16['decision'])}: {errors}")
    assert errors == []


def test_retirement_errors_names_a_no_row_retired_under_an_h_nn() -> None:
    """Iteration 4, M2, on the scanner's route: DOC-03 retired under H-39 in the table and in the
    history, its row still ``no`` with no decision, each message exactly. Iteration 6, S2: on synthetic
    rows and a synthetic history shaped like the committed ones (PRIN-16 ``yes`` under C08, retired under
    H-39; DOC-03 ``no`` with no decision), with their own frozen map, so a reviewed edit to the committed
    DOC-03 row, the history or ``RETIRED_IDS`` cannot change what this test asserts."""
    rows = [_as_row(_row("PRIN-16", "Silence is tolerated freely.", "H-39 (dropped by C08)", "C08", "yes",
                         "PRIN-16-C08-silence-tolerated-freely")),
            _as_row(_row("DOC-03", "Decision records conform to Parts 1-4.", "DOC-03"))]
    history = _history(retired="- **PRIN-16** retired → H-39\n")
    frozen = {"PRIN-16": "H-39"}
    assert retirement_errors(rows, history, frozen) == []
    m2 = _real_row_swap(rows, "DOC-03", **{"new ID(s)": "H-39"})
    listed = _one_edit(history, "- **PRIN-16** retired → H-39\n", "- **PRIN-16** retired → H-39\n- **DOC-03** retired → H-39\n")
    errors = retirement_errors(m2, listed, frozen)
    print(f"[slice compared] {errors}")
    assert errors == [
        "[retired] the table (retired_ids) retires DOC-03 under H-39, and RETIRED_IDS says None (M2: RETIRED_IDS is frozen)",
        "[retired] ## Retired IDs retires DOC-03 under H-39, and RETIRED_IDS says None (M2: RETIRED_IDS is frozen)",
        "[retired] DOC-03: retires under H-39 on a 'no' row; a rule retired under an H-NN changes meaning, so the row is yes (M2)",
        "[retired] DOC-03: retires under H-39, and no decision its cell cites ('—') retires under H-39: {} (M2)",
    ]
    # The retire rule alone, with RETIRED_IDS widened to fit: a yes row under a Group A decision (H-38).
    group_a = _real_row_swap(m2, "DOC-03", decision="C03", **{"meaning changed": "yes"})
    errors = retirement_errors(group_a, listed, frozen={**frozen, "DOC-03": "H-39"})
    print(f"[slice compared] widened: {errors}")
    assert errors == [("[retired] DOC-03: retires under H-39, and no decision its cell cites ('C03') retires under "
                       "H-39: {'C03': 'H-38'} (M2)")]


def test_real_path_old_meanings_are_the_frozen_literals() -> None:
    """Iteration 4, S1: the committed ``OLD_MEANINGS`` is ``OLD_MEANING_SHA256``, key by key and field
    by field, with no key added or removed."""
    errors = old_meaning_digest_errors(_OM.OLD_MEANINGS)
    key = "AUT-04-C20-two-purposes-only"
    print(f"[slice compared] {len(_OM.OLD_MEANINGS)} keys, {len(OLD_MEANING_SHA256)} frozen; {key} "
          f"{old_meaning_digest(_OM.OLD_MEANINGS[key])} vs {OLD_MEANING_SHA256[key]}: {errors[:5]}")
    assert errors == []
    assert set(OLD_MEANING_SHA256) == set(_OM.OLD_MEANINGS) and len(OLD_MEANING_SHA256) == 54


def test_old_meaning_digest_errors_names_the_changed_key_and_field() -> None:
    """Iteration 4, S1, on the scanner's route over synthetic entries shaped like AUT-04-C20's: the
    pattern and example loosened together, so AUT-04 could restate C20's old meaning; and a key added and
    one removed. Each message exactly. The frozen side is derived from the unmutated entries and each
    expected digest from the mutated entry (iteration 5, S5), so an F009 or F011 edit to the committed
    ``OLD_MEANINGS`` leaves this test green; the committed literal is the real-path test's."""
    key = "AUT-04-C20-two-purposes-only"
    meanings = {
        key: OldMeaning("serves two purposes only", "Chat serves two purposes only",
                        "specification/research/00-design-decisions.md:57@4e47d0e", "C20"),
        "C05-reopens": OldMeaning("worse rate reopens", "a worse rate reopens the deferred hysteresis decision",
                                  "specification/research/00-design-decisions.md:230@4e47d0e", "C05"),
    }
    frozen = {k: old_meaning_digest(e) for k, e in meanings.items()}
    assert old_meaning_digest_errors(meanings, frozen) == []
    loosened = dict(meanings)
    loosened[key] = OldMeaning("chat serves exactly two purposes", "Chat serves exactly two purposes",
                               loosened[key].source, loosened[key].decision)
    new = old_meaning_digest(loosened[key])
    renamed = {("AUT-04-C20-renamed" if k == key else k): v for k, v in meanings.items()}
    cases = {
        "loosened": (loosened, [
            (f"[frozen-meanings] {key}: the 'pattern' field is not the frozen field (now 'chat serves exactly two "
             f"purposes'); once the edit is reviewed, OLD_MEANING_SHA256['{key}'] = '{new}'"),
            (f"[frozen-meanings] {key}: the 'example' field is not the frozen field (now 'Chat serves exactly two "
             f"purposes'); once the edit is reviewed, OLD_MEANING_SHA256['{key}'] = '{new}'")]),
        "renamed": (renamed, [
            f"[frozen-meanings] {key} is frozen in OLD_MEANING_SHA256 and is not in OLD_MEANINGS",
            (f"[frozen-meanings] AUT-04-C20-renamed is not in OLD_MEANING_SHA256 (a key was added or renamed); "
             f"its digest is '{frozen[key]}'")]),
    }
    for name, (mutated, expected) in cases.items():
        errors = old_meaning_digest_errors(mutated, frozen)
        print(f"[slice compared] {name}: {errors}")
        assert errors == expected, (name, errors)


def _commit_4e47d0e_is_here() -> bool:
    return subprocess.run(["git", "cat-file", "-e", "4e47d0e^{commit}"], capture_output=True, cwd=_REPO_ROOT,
                          check=False).returncode == 0


def test_real_path_every_old_meaning_example_is_verbatim_at_4e47d0e() -> None:
    """Iteration 4, S1: each committed ``example``, after ``normalize()``, is in ``git show
    4e47d0e:<source path>`` after ``normalize()`` (``example_source_errors`` over ``_git_show``). The
    suite carries no skip (R11), so a clone without 4e47d0e reds here by name rather than passing
    silently; CI checks out with ``fetch-depth: 0`` (``.github/workflows/test-suite.yml``)."""
    assert _commit_4e47d0e_is_here(), (
        "4e47d0e is not in this clone (a shallow checkout?): the example-at-source check needs the full "
        "history -- fetch it (git fetch --unshallow), do not skip this test")
    errors = example_source_errors(_OM.OLD_MEANINGS, _git_show)
    paths = sorted({e.source.split(":", 1)[0] for e in _OM.OLD_MEANINGS.values()})
    print(f"[slice compared] {len(_OM.OLD_MEANINGS)} examples against git show 4e47d0e of {paths}: {errors[:5]}")
    assert errors == []


def test_example_source_errors_names_an_example_not_at_its_source() -> None:
    """The red side of the check above, on ``_old_show``'s text: a verbatim example is green, and the
    scanner's loosened AUT-04 example is named."""
    meanings = {
        "k-verbatim": OldMeaning("worse rate reopens", "a worse rate reopens the deferred hysteresis decision",
                                 "specification/research/00-design-decisions.md:230@4e47d0e", "C05"),
        "k-invented": OldMeaning("chat serves exactly two purposes", "Chat serves exactly two purposes",
                                 "specification/research/00-design-decisions.md:57@4e47d0e", "C20"),
    }
    errors = example_source_errors(meanings, _old_show)
    print(f"[slice compared] {errors}")
    assert errors == [("[old-meaning] k-invented: example is not verbatim in "
                       "specification/research/00-design-decisions.md at 4e47d0e")]


def test_real_path_old_meanings_miss_the_whole_of_research00() -> None:
    """R4 over the committed ``OLD_MEANINGS`` and the whole of research/00, glossary included; every
    key the table names exists, and ``EXCEPTIONS`` stays empty until F011."""
    research, _history, rows = _real()
    errors = old_meaning_errors(_OM.OLD_MEANINGS, research)
    named = {k for r in rows for k in _keys(r)}
    print(f"[slice compared] {len(_OM.OLD_MEANINGS)} keys, {len(named)} named by the table: {errors[:10]}")
    assert errors == []
    assert _OM.OLD_MEANINGS and named <= set(_OM.OLD_MEANINGS)
    orphans = sorted(set(_OM.OLD_MEANINGS) - named)
    assert not orphans, f"OLD_MEANINGS keys no traceability row names (S5): {orphans}"
    assert _OM.EXCEPTIONS == ()


# ---------------------------------------------------------------------------
# The meaning review (T180, R7/AC9): one verdict per required row, under its group, none differs
# ---------------------------------------------------------------------------

_REAL_REVIEW = _REPO_ROOT / "specification" / "research" / "00-meaning-review.md"
REVIEW_HEADER = "| row | verdict | reason |"
REVIEW_HEADINGS = {"doc-goal": "## DOC–GOAL", "arch-dec": "## ARCH–DEC", "hrv": "## HRV"}
_VERDICTS = ("same", "differs")


def review_errors(review_text: str, required: list[str]) -> list[str]:
    """R7's coverage over a review file: every ``required`` row has exactly one verdict line, no
    verdict line names a row outside ``required``, every verdict is ``same`` or ``differs`` and none
    is ``differs``, every line sits under its group's heading, and each group's table opens with
    ``REVIEW_HEADER``."""
    errors: list[str] = []
    group_of_heading = {h: g for g, h in REVIEW_HEADINGS.items()}
    heading, counts, headed = None, Counter(), set()
    for number, line in enumerate(_lines(review_text), 1):
        if line.startswith("#"):
            heading = line.strip()
            continue
        if not line.startswith("|"):
            continue
        if line.strip() == REVIEW_HEADER:
            headed.add(heading)
            continue
        cells = _split_cells(line)
        if _is_separator(cells):
            continue
        if len(cells) != 3:
            errors.append(f"[review] line {number}: {len(cells)} cells, not 3: {line[:100]!r}")
            continue
        row, verdict, reason = cells
        counts[row] += 1
        if verdict not in _VERDICTS:
            errors.append(f"[review] {row}: verdict {verdict!r} is neither same nor differs")
        if verdict == "differs":
            errors.append(f"[review] {row} differs: {reason[:160]}")
        if not reason:
            errors.append(f"[review] {row}: no reason")
        want = REVIEW_HEADINGS.get(_GROUP_OF.get(_prefix(row.split("/", 1)[0]), ""))
        if heading != want:
            errors.append(f"[review] {row} sits under {heading!r}, not {want!r}")
    need = Counter(required)
    errors += [f"[review] {row} has no verdict" for row in need if counts[row] == 0]
    errors += [f"[review] {row} has {n} verdicts, not one" for row, n in counts.items() if n > 1]
    errors += [f"[review] {row} is not a required review row" for row in counts if row not in need]
    for heading_text in group_of_heading:
        if heading_text not in headed:
            errors.append(f"[review] {heading_text} has no {REVIEW_HEADER} table")
    return errors


def review_entries(review_text: str) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """``([(row, [line])], prose)`` over the review file (iteration 5, S3): each verdict line keyed by its
    row, and every other non-blank line that is not a table header or separator (the headings, the
    opening paragraph and the ``## Rounds`` paragraphs), in order. ``review_errors`` holds the table
    headers and the cell count."""
    entries, prose = [], []
    for line in _lines(review_text):
        if not line.strip() or line.strip() == REVIEW_HEADER:
            continue
        if line.startswith("|"):
            cells = _split_cells(line)
            if not _is_separator(cells):
                entries.append((cells[0], [line]))
        else:
            prose.append(line)
    return entries, prose


def review_line_errors(review_text: str, frozen: dict[str, str] | None = None,
                       frozen_prose: tuple[str, ...] | None = None) -> list[str]:
    """Each verdict line, label, verdict and reason together, is the frozen line (iteration 5, S3), both
    ways, and the file's prose is ``REVIEW_PROSE_SHA256``. ``review_errors`` caught a flipped verdict,
    not a reason: PRIN-01's rewritten to "Not reviewed." stayed green. A new round updates these
    literals deliberately, alongside ``REVIEWED_BLOCK_SHA256``."""
    frozen = REVIEW_LINE_SHA256 if frozen is None else frozen
    frozen_prose = REVIEW_PROSE_SHA256 if frozen_prose is None else frozen_prose
    entries, prose = review_entries(review_text)
    errors = keyed_line_errors(
        "frozen-review", "REVIEW_LINE_SHA256", "00-meaning-review.md", entries, frozen,
        "a verdict or its reason changes only in a fresh critic's round recorded under ## Rounds: once recorded")
    have = tuple(_cell_digest(line) for line in prose)
    if have != frozen_prose:
        i = _first_difference(have, frozen_prose)
        now = repr(prose[i][:120]) if i < len(prose) else "no such line: a line was removed"
        errors.append(f"[frozen-review] prose line {i + 1} of {len(prose)} (the headings, the opening paragraph and "
                      f"## Rounds) is not the frozen line (now {now}); a round is recorded only with the review it "
                      f"records: once it is, REVIEW_PROSE_SHA256 = {have!r}")
    return errors


def _synthetic_review(required: list[str], drop: str | None = None) -> str:
    parts = []
    for group, heading in REVIEW_HEADINGS.items():
        parts += [heading, "", REVIEW_HEADER, "| --- | --- | --- |"]
        parts += [f"| {row} | same | A synthetic reason. |" for row in required
                  if row != drop and _GROUP_OF[_prefix(row.split("/", 1)[0])] == group]
        parts.append("")
    return "\n".join(parts)


def test_review_errors_turns_red_on_a_missing_verdict_row() -> None:
    """The synthetic red case: the same file with one required row's verdict line removed."""
    required = ["DOC-01", "DOC-01/Scope", "ARCH-01/Not", "HRV-07", "HRV-07/Why"]
    full = _synthetic_review(required)
    missing = _synthetic_review(required, drop="ARCH-01/Not")
    print(f"[slice compared] full {review_errors(full, required)}; missing {review_errors(missing, required)}")
    assert review_errors(full, required) == []
    assert review_errors(missing, required) == ["[review] ARCH-01/Not has no verdict"]


def test_real_path_meaning_review_covers_exactly_the_required_rows_with_no_differs() -> None:
    """AC9 over the committed review: every row of ``required_review_rows(research/00, table)`` has
    exactly one verdict line, there is no row outside that set, and none is ``differs``."""
    research, _history, rows = _real()
    required = required_review_rows(research, rows)
    review = _REAL_REVIEW.read_text(encoding="utf-8")
    errors = review_errors(review, required)
    verdicts = [line for line in _lines(review) if re.match(r"^\| [^|]+ \| (same|differs) \|", line)]
    differs = [line[:80] for line in verdicts if "| differs |" in line]
    print(f"[slice compared] {_REAL_REVIEW.name}: {len(required)} required rows, {len(verdicts)} verdict "
          f"lines, differs {differs}, errors {errors[:10]}")
    assert errors == []
    assert len(verdicts) == len(required) > 0
    assert differs == []


def test_real_path_every_reviewed_block_line_is_the_text_its_verdict_judged() -> None:
    """Iteration 4, M3 (ruled 2026-09-26): ``REVIEWED_BLOCK_SHA256`` is keyed by exactly
    ``required_review_rows``, in its order, each label maps to one block line, and each line is the
    frozen text its ``same`` verdict judged."""
    research, _history, rows = _real()
    required = required_review_rows(research, rows)
    lines, problems = reviewed_block_lines(research, rows)
    errors = reviewed_block_errors(research, rows)
    print(f"[slice compared] {len(required)} required rows, {len(lines)} mapped, {len(REVIEWED_BLOCK_SHA256)} "
          f"frozen, problems {problems}; HRV-24 {_cell_digest(lines['HRV-24'])} vs "
          f"{REVIEWED_BLOCK_SHA256['HRV-24']}, DOC-09 {_cell_digest(lines['DOC-09'])} vs "
          f"{REVIEWED_BLOCK_SHA256['DOC-09']}: {errors[:5]}")
    assert problems == [] and errors == []
    assert list(lines) == list(REVIEWED_BLOCK_SHA256) == required and len(required) == 789


def test_reviewed_block_errors_names_a_row_whose_text_changed_after_its_verdict() -> None:
    """Iteration 4, M3, on the scanner's two routes, each editing only research/00 after the review and
    each message exactly: RA3, HRV-24 drops ``band`` (a ``no`` row; the AC9 proxy holds only the
    identifiers its inventory sentence backticks); RC, DOC-09's text changes (a ``yes`` row, unproxied
    by design). Then the key set both ways, and a label with two lines of its kind.

    Iteration 5, S5: the frozen side is derived from the unmutated text, each route's edit is applied to
    the derived line (RA3 and RC each drop the line's last backticked span: HRV-24's ``band``, DOC-09's
    history path), and each expected "now" text and digest comes from the mutated line. A reviewed
    rewording of HRV-24 or DOC-09 leaves this test green; the committed literal is the real-path test's.

    Iteration 6, S2: the blocks and rows are synthetic, shaped like the committed ones (HRV-24 a ``no`` row
    whose rule line ends in the ``band`` span, DOC-09 a ``yes`` row whose rule line ends in the history
    path), so a reviewed edit to the committed HRV-24 or DOC-09 (a row turned ``yes``, a rule line with no
    code span) cannot change what this test asserts."""
    research = "\n\n".join([
        HEADINGS[-1],
        _block("DOC-09", "research/00 MUST state only current rules, and a dated summary of what changed MUST go "
                         "to the history file, `specification/research/00-history.md`."),
        _block("HRV-24", "When nothing is selected, the HRV verdict MUST be unavailable and `baseline`/`band` MUST "
                         "be populated for presentation only."),
    ]) + "\n"
    rows = [_as_row(_row("DOC-09", "A dated summary goes to the history file.", "DOC-09", "C38", "yes",
                         "DOC-09-C38-superseded-text-left-standing")),
            _as_row(_row("HRV-24", "The band is populated for presentation only.", "HRV-24", "C17", "no",
                         "C17-hrv24-read-on-last"))]
    lines, _problems = reviewed_block_lines(research, rows)
    assert _problems == [] and list(lines) == ["DOC-09", "DOC-09/Scope", "DOC-09/Not", "HRV-24", "HRV-24/Scope",
                                               "HRV-24/Not"]
    frozen = {label: _cell_digest(line) for label, line in lines.items()}
    blocks = rule_blocks(research)

    def edit(rule_id: str, old: str, new: str) -> str:
        block = blocks[rule_id]
        assert research.count(block) == 1 and block.count(old) == 1, (rule_id, old)
        return research.replace(block, block.replace(old, new))

    def drop_last_code_span(line: str) -> str:
        span = list(_CODE_SPAN.finditer(line))[-1]
        return line[:span.start()] + line[span.end():]

    ra3_line, rc_line = drop_last_code_span(lines["HRV-24"]), drop_last_code_span(lines["DOC-09"])
    ra3, rc = edit("HRV-24", lines["HRV-24"], ra3_line), edit("DOC-09", lines["DOC-09"], rc_line)
    scope = lines["HRV-24/Scope"]
    doubled = edit("HRV-24", scope, scope + "\nScope: every day.")
    assert {r["meaning changed"] for r in rows if r["inventory ID"] in ("HRV-24", "DOC-09")} == {"no", "yes"}
    assert reviewed_block_errors(research, rows, frozen) == []
    narrowed = {k: v for k, v in frozen.items() if k != "HRV-24/Scope"} | {"HRV-99": "000000000000"}
    cases = {
        "ra3-hrv-24": (ra3, frozen, [
            (f"[reviewed-block] HRV-24: the block line changed after its meaning-review verdict (now "
             f"{ra3_line[:120]!r}); a fresh critic must re-review HRV-24 in 00-meaning-review.md, then "
             f"REVIEWED_BLOCK_SHA256['HRV-24'] = {_cell_digest(ra3_line)!r}")]),
        "rc-doc-09": (rc, frozen, [
            (f"[reviewed-block] DOC-09: the block line changed after its meaning-review verdict (now "
             f"{rc_line[:120]!r}); a fresh critic must re-review DOC-09 in 00-meaning-review.md, then "
             f"REVIEWED_BLOCK_SHA256['DOC-09'] = {_cell_digest(rc_line)!r}")]),
        "key-set": (research, narrowed, [
            "[reviewed-block] HRV-99 is frozen in REVIEWED_BLOCK_SHA256 and is not a required review row",
            ("[reviewed-block] HRV-24/Scope is a required review row and is not in REVIEWED_BLOCK_SHA256, so no "
             "verdict is bound to its text; once a critic has reviewed it, "
             f"REVIEWED_BLOCK_SHA256['HRV-24/Scope'] = {frozen['HRV-24/Scope']!r}")]),
        "doubled": (doubled, frozen, ["[reviewed-block] HRV-24/Scope: 2 block lines, not one"]),
    }
    wrong = {}
    for name, (text, frozen, expected) in cases.items():
        errors = reviewed_block_errors(text, rows, frozen)
        print(f"[slice compared] {name}: {errors}")
        if errors != expected:
            wrong[name] = errors
    assert wrong == {}, wrong


# ---------------------------------------------------------------------------
# Iteration 5: the Glossary, the Pinned lines, the structure, the history and the review lines are bound
# ---------------------------------------------------------------------------


def _check_cases(check: Callable[..., list[str]], cases: dict[str, tuple]) -> None:
    """Run ``check(*args)`` for each case and compare its errors with the expected list, exactly."""
    wrong = {}
    for name, (args, expected) in cases.items():
        errors = check(*args)
        print(f"[slice compared] {name}: {errors}")
        if errors != expected:
            wrong[name] = errors
    assert wrong == {}, wrong


def _one_edit(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


def test_real_path_every_glossary_definition_is_the_frozen_definition() -> None:
    """Iteration 5, M1: the committed Glossary is ``GLOSSARY_SHA256``, term by term, both ways; every
    Glossary line has a T-NN key, and the keys are ``GLOSSARY_TERMS``'s, in order."""
    research, _history, _rows = _real()
    entries, problems = glossary_entries(research)
    errors = glossary_digest_errors(research)
    t13 = dict(entries)["T-13"]
    print(f"[slice compared] {len(entries)} Glossary lines, {len(GLOSSARY_SHA256)} frozen, problems {problems}; "
          f"T-13 {_keyed_digest(t13)} vs {GLOSSARY_SHA256['T-13']}: {errors[:5]}")
    assert errors == []
    assert [k for k, _ in entries] == list(GLOSSARY_SHA256) == list(GLOSSARY_TERMS)


def test_glossary_digest_errors_names_a_changed_definition() -> None:
    """Iteration 5, M1, on synthetic text shaped like the scanner's routes, each message exactly: T-13's
    14 days made 21 (N1), T-05's HRV Status made a tier (N3), then a term replaced by another, a term
    repeated and a line with no key. The frozen side is derived from the unmutated text (S5)."""
    t13 = "- **T-13 sustains** IS the highest-fidelity tier with at least 14 distinct days in a window."
    t05 = "- **T-05 tier** IS a value of `hrv_source_tier`; HRV Status is a sidecar metric and not a tier."
    base = f"{HEADINGS[0]}\n\n{GLOSSARY_HEADING}\n\n{_glossary(**{'T-13': t13, 'T-05': t05})}\n{HEADINGS[1]}\n"
    entries, problems = glossary_entries(base)
    frozen = {k: _keyed_digest(lines) for k, lines in entries}
    assert problems == [] and len(frozen) == 33 and glossary_digest_errors(base, frozen) == []
    t13_21 = t13.replace("at least 14", "at least 21")
    t05_tier = t05.replace("and not a tier", "and a tier")
    t33 = "- **T-33 term33** IS the synthetic definition 33."
    t34 = "- **T-34 term34** IS a new term."
    _check_cases(glossary_digest_errors, {
        "n1-t13": ((_one_edit(base, t13, t13_21), frozen), [
            ("[frozen-glossary] T-13: the line changed (now '- **T-13 sustains** IS the highest-fidelity tier with "
             "at least 21 distinct days in a window.'); a Glossary change needs a fresh critic (R13 holds T-05, "
             f"T-13 and T-26 unchanged): once one has reviewed it, GLOSSARY_SHA256['T-13'] = {_cell_digest(t13_21)!r}")]),
        "n3-t05": ((_one_edit(base, t05, t05_tier), frozen), [
            ("[frozen-glossary] T-05: the line changed (now '- **T-05 tier** IS a value of `hrv_source_tier`; HRV "
             "Status is a sidecar metric and a tier.'); a Glossary change needs a fresh critic (R13 holds T-05, "
             f"T-13 and T-26 unchanged): once one has reviewed it, GLOSSARY_SHA256['T-05'] = {_cell_digest(t05_tier)!r}")]),
        "re-keyed": ((_one_edit(base, t33, t34), frozen), [
            "[frozen-glossary] T-33 is frozen in GLOSSARY_SHA256 and is not in the Glossary",
            ("[frozen-glossary] T-34 is not in GLOSSARY_SHA256 (a line was added or re-keyed); a Glossary change "
             "needs a fresh critic (R13 holds T-05, T-13 and T-26 unchanged): once one has reviewed it, "
             f"GLOSSARY_SHA256['T-34'] = {_cell_digest(t34)!r}")]),
        "repeated-and-unkeyed": ((_one_edit(base, t33, t33 + "\n- **T-01 again** IS twice.\nSome prose."), frozen), [
            "[frozen-glossary] line 39: a Glossary line with no T-NN key cannot be bound: 'Some prose.'",
            "[frozen-glossary] T-01 occurs 2 times in the Glossary, not once",
            ("[frozen-glossary] T-01: the line changed (now '- **T-01 again** IS twice.'); a Glossary change needs "
             "a fresh critic (R13 holds T-05, T-13 and T-26 unchanged): once one has reviewed it, "
             f"GLOSSARY_SHA256['T-01'] = {_cell_digest('- **T-01 again** IS twice.')!r}")]),
    })


def test_real_path_every_pinned_line_is_the_frozen_line() -> None:
    """Iteration 5, S1: each committed rule's Pinned lines are ``PINNED_SHA256``, both ways, one key per
    rule in document order; no Pinned line sits outside a rule block."""
    research, _history, _rows = _real()
    entries, problems = pinned_entries(research)
    errors = pinned_digest_errors(research)
    multi = {k: len(v) for k, v in entries if len(v) > 1}
    print(f"[slice compared] {len(entries)} rules, {sum(len(v) for _, v in entries)} Pinned lines, "
          f"{len(PINNED_SHA256)} frozen, more than one {multi}, problems {problems}; PRIN-26 "
          f"{_keyed_digest(dict(entries)['PRIN-26/Pinned'])} vs {PINNED_SHA256['PRIN-26/Pinned']}: {errors[:5]}")
    assert errors == []
    assert [k for k, _ in entries] == list(PINNED_SHA256) == [f"{i}/Pinned" for i in rule_ids(research)]


def test_pinned_digest_errors_names_a_retargeted_pin() -> None:
    """Iteration 5, S1, on synthetic blocks, each message exactly: PRIN-26's ``Pinned: none (F009)``
    retargeted to an unrelated existing node (N5); the second of PRIN-15's two Pinned lines edited, then
    dropped; a rule removed; a Pinned line outside a block. The frozen side is derived (S5)."""
    two = ("Pinned: runcoach-api/tests/test_a.py::test_a", "Pinned: none (F009)")
    base = "\n\n".join(["### 1.7 Down-regulate freely, up-regulate cautiously",
                        _block("PRIN-15", pinned=two), _block("PRIN-25", pinned=("Pinned: none (F009)",)),
                        _block("PRIN-26", pinned=("Pinned: none (F009)",))]) + "\n"
    entries, problems = pinned_entries(base)
    frozen = {k: _keyed_digest(lines) for k, lines in entries}
    assert problems == [] and glossary_entries(base)[0] == [] and pinned_digest_errors(base, frozen) == []
    retarget = "Pinned: runcoach-api/tests/test_hrv_trend_band.py::test_the_floor_fires_for_a_degenerate_baseline"
    prin26 = _block("PRIN-26", pinned=("Pinned: none (F009)",))
    prin15 = _block("PRIN-15", pinned=two)
    then = "a Pinned change needs a review that the node pins the rule: once reviewed"
    _check_cases(pinned_digest_errors, {
        "n5-prin-26": ((_one_edit(base, prin26, _block("PRIN-26", pinned=(retarget,))), frozen), [
            (f"[frozen-pinned] PRIN-26/Pinned: the line changed (now {retarget!r}); {then}, "
             f"PINNED_SHA256['PRIN-26/Pinned'] = {_cell_digest(retarget)!r}")]),
        "second-edited": ((_one_edit(base, prin15, _block("PRIN-15", pinned=(two[0], "Pinned: none (F011)"))), frozen), [
            (f"[frozen-pinned] PRIN-15/Pinned: the line changed (now 'Pinned: none (F011)'); {then}, "
             f"PINNED_SHA256['PRIN-15/Pinned'] = {_keyed_digest([two[0], 'Pinned: none (F011)'])!r}")]),
        "n4-second-dropped": ((_one_edit(base, prin15, _block("PRIN-15", pinned=two[:1])), frozen), [
            (f"[frozen-pinned] PRIN-15/Pinned: the line changed (now no such line: a line was removed); {then}, "
             f"PINNED_SHA256['PRIN-15/Pinned'] = {_cell_digest(two[0])!r}")]),
        "rule-removed": ((_one_edit(base, "\n\n" + prin26, ""), frozen), [
            "[frozen-pinned] PRIN-26/Pinned is frozen in PINNED_SHA256 and is not in research/00"]),
        "outside-a-block": ((base + "\nPinned: none\n", frozen), [
            "[frozen-pinned] line 19: a Pinned line outside a rule block cannot be bound: 'Pinned: none'"]),
    })


def test_real_path_research00_structure_is_the_frozen_structure() -> None:
    """Iteration 5, S1: research/00's headings, Glossary terms and rules sit where ``RESEARCH_STRUCTURE``
    puts them."""
    research, _history, _rows = _real()
    have = research_structure(research)
    errors = structure_errors(research)
    heads = [e for e in have if e.startswith("#")]
    print(f"[slice compared] {len(have)} entries ({len(heads)} headings), {len(RESEARCH_STRUCTURE)} frozen; "
          f"around 1.2 {have[have.index(HEADINGS[3]) - 2:have.index(HEADINGS[3]) + 2]}: {errors}")
    assert errors == []
    assert heads == [HEADINGS[0], GLOSSARY_HEADING, *HEADINGS[1:]]


def test_structure_errors_names_the_first_position_that_differs() -> None:
    """Iteration 5, S1, on synthetic text, each message exactly: ``### 1.2`` moved above PRIN-02 (N6), and
    the last rule removed. The frozen side is derived (S5)."""
    one, two = "### 1.1 The supreme objective", "### 1.2 The arbitration ladder"
    base = "\n\n".join([one, _block("PRIN-01"), _block("PRIN-02"), two, _block("PRIN-03")]) + "\n"
    moved = "\n\n".join([one, _block("PRIN-01"), two, _block("PRIN-02"), _block("PRIN-03")]) + "\n"
    frozen = research_structure(base)
    assert frozen == (one, "PRIN-01", "PRIN-02", two, "PRIN-03") and structure_errors(base, frozen) == []
    _check_cases(structure_errors, {
        "n6-heading-moved": ((moved, frozen), [
            ("[frozen-structure] position 2, after 'PRIN-01': research/00 has '### 1.2 The arbitration ladder' "
             "where RESEARCH_STRUCTURE has 'PRIN-02' (5 entries, 5 frozen): a heading, term or rule moved, was "
             "added or was removed; once the move is reviewed, paste RESEARCH_STRUCTURE from frozen_literals()")]),
        "last-removed": ((_one_edit(base, "\n\n" + _block("PRIN-03"), ""), frozen), [
            ("[frozen-structure] position 4, after '### 1.2 The arbitration ladder': research/00 has '<end>' where "
             "RESEARCH_STRUCTURE has 'PRIN-03' (4 entries, 5 frozen): a heading, term or rule moved, was added or "
             "was removed; once the move is reviewed, paste RESEARCH_STRUCTURE from frozen_literals()")]),
    })


def test_real_path_every_history_line_is_the_frozen_line() -> None:
    """Iteration 5, S2: every non-blank line of the committed history is ``HISTORY_SHA256``, both ways,
    and every one has a key."""
    _research, history, _rows = _real()
    entries, problems = history_entries(history)
    errors = history_digest_errors(history)
    print(f"[slice compared] {len(entries)} history lines, {len(HISTORY_SHA256)} frozen, problems {problems}; "
          f"H-06 {_keyed_digest(dict(entries)['H-06'])} vs {HISTORY_SHA256['H-06']}: {errors[:5]}")
    assert errors == []
    assert [k for k, _ in entries] == list(HISTORY_SHA256)
    assert [k for k in HISTORY_SHA256 if k.startswith("H-")] == history_ids(history)


def test_history_digest_errors_names_a_changed_entry() -> None:
    """Iteration 5, S2, on a synthetic history, each message exactly: H-06's prose rewritten (N9), GOAL-06
    dropped from its arrows (N10), the retired line re-pointed, an entry removed and a line with no key.
    The frozen side is derived (S5)."""
    h06 = "- **H-06** (undated) A clarification fixed the order as pace first and distance never. → GOAL-03, GOAL-06"
    base = _one_edit(_history(retired="- **PRIN-16** retired → H-39\n"), "- **H-06** (2026-09-07) Synthetic change 6. → DOC-01", h06)
    entries, problems = history_entries(base)
    frozen = {k: _keyed_digest(lines) for k, lines in entries}
    assert problems == [] and len(frozen) == 44 and history_digest_errors(base, frozen) == []
    n9, n10 = h06.replace("distance never", "distance last"), h06.replace(", GOAL-06", "")
    retired = "- **PRIN-16** retired → H-38"
    then = "a history change is reviewed like the rules it records: once reviewed"
    _check_cases(history_digest_errors, {
        "n9-h-06-prose": ((_one_edit(base, h06, n9), frozen), [
            f"[frozen-history] H-06: the line changed (now {n9!r}); {then}, HISTORY_SHA256['H-06'] = {_cell_digest(n9)!r}"]),
        "n10-h-06-arrows": ((_one_edit(base, h06, n10), frozen), [
            f"[frozen-history] H-06: the line changed (now {n10!r}); {then}, HISTORY_SHA256['H-06'] = {_cell_digest(n10)!r}"]),
        "retired-re-pointed": ((_one_edit(base, "- **PRIN-16** retired → H-39", retired), frozen), [
            (f"[frozen-history] PRIN-16 retired: the line changed (now {retired!r}); {then}, "
             f"HISTORY_SHA256['PRIN-16 retired'] = {_cell_digest(retired)!r}")]),
        "removed-and-unkeyed": ((_one_edit(base, "- **H-41** (2026-09-14) Synthetic change 41. → DOC-01",
                                           "A paragraph with no key."), frozen), [
            "[frozen-history] line 43: a history line with no key cannot be bound: 'A paragraph with no key.'",
            "[frozen-history] H-41 is frozen in HISTORY_SHA256 and is not in 00-history.md"]),
    })


def test_real_path_every_review_line_and_the_rounds_are_frozen() -> None:
    """Iteration 5, S3: each committed verdict line, label, verdict and reason together, is
    ``REVIEW_LINE_SHA256``, both ways, over exactly the rows ``REVIEWED_BLOCK_SHA256`` binds; and the
    file's prose, ``## Rounds`` included, is ``REVIEW_PROSE_SHA256``."""
    review = _REAL_REVIEW.read_text(encoding="utf-8")
    entries, prose = review_entries(review)
    errors = review_line_errors(review)
    print(f"[slice compared] {len(entries)} verdict lines, {len(REVIEW_LINE_SHA256)} frozen, {len(prose)} prose "
          f"lines; PRIN-01 {_keyed_digest(dict(entries)['PRIN-01'])} vs {REVIEW_LINE_SHA256['PRIN-01']}: {errors[:5]}")
    assert errors == []
    assert [k for k, _ in entries] == list(REVIEW_LINE_SHA256)
    assert set(REVIEW_LINE_SHA256) == set(REVIEWED_BLOCK_SHA256) and len(REVIEW_LINE_SHA256) == 789
    assert "## Rounds" in prose and len(prose) == len(REVIEW_PROSE_SHA256)


def test_review_line_errors_names_a_rewritten_reason_and_a_changed_round() -> None:
    """Iteration 5, S3, on a synthetic review, each message exactly: PRIN-01's reason rewritten to "Not
    reviewed." (N12), its verdict flipped (N11), a row dropped, and a ``## Rounds`` paragraph edited. The
    frozen side is derived (S5)."""
    required = ["PRIN-01", "PRIN-01/Scope", "ARCH-01/Not", "HRV-07"]
    base = _synthetic_review(required) + "## Rounds\n\nRound 1: a synthetic critic reviewed 4 rows.\n"
    entries, prose = review_entries(base)
    frozen, frozen_prose = {k: _keyed_digest(lines) for k, lines in entries}, tuple(_cell_digest(p) for p in prose)
    assert len(frozen) == 4 and len(prose) == 5 and review_line_errors(base, frozen, frozen_prose) == []
    row = "| PRIN-01 | same | A synthetic reason. |"
    n12, n11 = "| PRIN-01 | same | Not reviewed. |", "| PRIN-01 | differs | A synthetic reason. |"
    rounds = "Round 1: a synthetic critic reviewed 5 rows."
    edited = _one_edit(base, "Round 1: a synthetic critic reviewed 4 rows.", rounds)
    then = "a verdict or its reason changes only in a fresh critic's round recorded under ## Rounds: once recorded"
    _check_cases(review_line_errors, {
        "n12-reason": ((_one_edit(base, row, n12), frozen, frozen_prose), [
            (f"[frozen-review] PRIN-01: the line changed (now '| PRIN-01 | same | Not reviewed. |'); {then}, "
             f"REVIEW_LINE_SHA256['PRIN-01'] = {_cell_digest(n12)!r}")]),
        "n11-verdict": ((_one_edit(base, row, n11), frozen, frozen_prose), [
            (f"[frozen-review] PRIN-01: the line changed (now '| PRIN-01 | differs | A synthetic reason. |'); "
             f"{then}, REVIEW_LINE_SHA256['PRIN-01'] = {_cell_digest(n11)!r}")]),
        "row-dropped": ((_one_edit(base, "| HRV-07 | same | A synthetic reason. |\n", ""), frozen, frozen_prose), [
            "[frozen-review] HRV-07 is frozen in REVIEW_LINE_SHA256 and is not in 00-meaning-review.md"]),
        "round-edited": ((edited, frozen, frozen_prose), [
            ("[frozen-review] prose line 5 of 5 (the headings, the opening paragraph and ## Rounds) is not the "
             "frozen line (now 'Round 1: a synthetic critic reviewed 5 rows.'); a round is recorded only with the "
             f"review it records: once it is, REVIEW_PROSE_SHA256 = {(*frozen_prose[:4], _cell_digest(rounds))!r}")]),
    })


# ---------------------------------------------------------------------------
# A malformed inventory-ID cell is reported, not raised (T176's robustness finding)
# ---------------------------------------------------------------------------


def test_a_malformed_inventory_id_cell_is_reported_not_raised(tmp_path) -> None:
    files = _world_files()
    _sub("doc-goal.trace.txt", "| PRIN-15 | Rule PRIN-15", "| PRIN-15, IND-99 | Rule PRIN-15")(files)
    drafts = _write(tmp_path, files)
    a = assemble(drafts)
    rows = parse_traceability(a.table)
    retired = retired_ids(rows)
    errors = assemble_check(drafts)
    print(f"[slice compared] problems {list(a.problems)}; retired {retired}; errors {errors[:6]}")
    assert any("PRIN-15, IND-99" in p and "malformed" in p for p in a.problems), a.problems
    assert "PRIN-15, IND-99" not in retired
    assert any("PRIN-15, IND-99" in e for e in errors), errors


# ---------------------------------------------------------------------------
# The endpoint walk holds the literals module out (T170 AC6); read from its source, not imported
# ---------------------------------------------------------------------------


def test_the_endpoint_walk_declares_and_applies_the_literals_exclusion() -> None:
    source = _ENDPOINT_SUITE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    value = next(
        ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SCAN_EXCLUDED_LITERALS" for t in node.targets)
    )
    print(f"[slice compared] SCAN_EXCLUDED_LITERALS = {value}")
    assert value[:2] == (0, "runcoach-api/tests/support/research00_old_meanings.py") and value[2]
    scanned = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_scanned_files")
    assert "SCAN_EXCLUDED_LITERALS" in ast.get_source_segment(source, scanned)
    # Applied, not only named (sprint-007 review iteration 1, S7): deleting the two exclusion lines in
    # ``_scanned_files`` left the name in its source and this test green. Run the walk itself.
    endpoint = _load_module("hrv_trend_endpoint_walk", _ENDPOINT_SUITE)
    literals = _REPO_ROOT / value[1]
    candidates, walked = endpoint._candidate_files(value[0]), endpoint._scanned_files(value[0])
    print(f"[slice compared] {value[1]}: in _candidate_files({value[0]}) {literals in candidates} "
          f"({len(candidates)} files), in _scanned_files({value[0]}) {literals in walked} ({len(walked)} files)")
    assert literals in candidates, (
        f"{value[1]} is not among the walk's candidates, so its exclusion is tested over nothing"
    )
    assert literals not in walked, (
        f"{value[1]} is read by the endpoint scan: SCAN_EXCLUDED_LITERALS is declared but not applied"
    )


# ---------------------------------------------------------------------------
# Regenerating the frozen digests deliberately (iteration 4)
# ---------------------------------------------------------------------------


#: Every frozen literal ``frozen_literals()`` prints, in its order (iteration 5, S4). Each is a snapshot of
#: the committed files, so each has one regeneration path, and
#: ``test_frozen_literals_prints_every_frozen_literal_as_committed`` proves the path gives the committed
#: value. ``HEADINGS`` and ``INVENTORY_IDS`` are not here: they are the 4e47d0e inventory's (R11), inputs
#: no edit regenerates, and ``RESEARCH_STRUCTURE`` carries where each heading sits.
FROZEN_LITERALS = (
    "INVENTORY_SENTENCE_SHA256", "GLOSSARY_TERMS", "NON_C_AUTHORITIES", "_NON_C_AUTHORITIES_PIN", "KEY_OWNERS",
    "_KEY_OWNERS_PIN", "TRACEABILITY_ROW_SHA256", "RETIRED_IDS", "OLD_MEANING_SHA256", "REVIEWED_BLOCK_SHA256",
    "GLOSSARY_SHA256", "PINNED_SHA256", "RESEARCH_STRUCTURE", "HISTORY_SHA256", "REVIEW_LINE_SHA256",
    "REVIEW_PROSE_SHA256",
)


def derived_literals() -> tuple[dict[str, object], list[str]]:
    """``({name: value}, notes)``: each of ``FROZEN_LITERALS`` as the committed files now give it, and
    what the derivation found (counts, and every line it could not key). The two maps are rulings drawn
    from the table: ``NON_C_AUTHORITIES`` holds each ``yes`` row citing a non-C token under a key whose
    ``_lead_decision`` is that token (PRIN-15 cites R13 for S9's counts under a C06 key only), and
    ``KEY_OWNERS`` each key's naming rows; each pin is the same value, pasted twice after review."""
    research, history, rows = _real()
    review = _REAL_REVIEW.read_text(encoding="utf-8")
    inventory = [r for r in rows if not _is_blank(r["inventory ID"])]
    leads = {k: _lead_decision(e.decision) for k, e in _OM.OLD_MEANINGS.items()}
    tokens = sorted({t for t in leads.values() if t and not _C_ID.fullmatch(t)})
    authorities = {t: frozenset(r["inventory ID"] for r in inventory if r["meaning changed"] == "yes"
                                and t in _DECISION_TOKEN.findall(r["decision"])
                                and any(leads.get(k) == t for k in _keys(r))) for t in tokens}
    owners: dict[str, set[str]] = {}
    for r in rows:
        for k in _keys(r):
            owners.setdefault(k, set()).add(r["inventory ID"])
    glossary, glossary_problems = glossary_entries(research)
    pinned, pinned_problems = pinned_entries(research)
    history_lines, history_problems = history_entries(history)
    review_lines, prose = review_entries(review)
    blocks, block_problems = reviewed_block_lines(research, rows)
    values: dict[str, object] = {
        "INVENTORY_SENTENCE_SHA256": {r["inventory ID"]: _sentence_digest(r["inventory sentence"]) for r in inventory},
        "GLOSSARY_TERMS": {m.group("id"): m.group("term") for _k, (line,) in glossary
                           if (m := _GLOSSARY_LINE.match(line))},
        "NON_C_AUTHORITIES": authorities,
        "_NON_C_AUTHORITIES_PIN": authorities,
        "KEY_OWNERS": {k: frozenset(v) for k, v in sorted(owners.items())},
        "_KEY_OWNERS_PIN": {k: frozenset(v) for k, v in sorted(owners.items())},
        "TRACEABILITY_ROW_SHA256": {_row_key(r): row_digest(r) for r in rows},
        "RETIRED_IDS": dict(sorted((i, h) for i, h in retired_ids(rows).items() if h)),
        "OLD_MEANING_SHA256": {k: old_meaning_digest(e) for k, e in sorted(_OM.OLD_MEANINGS.items())},
        "REVIEWED_BLOCK_SHA256": {label: _cell_digest(line) for label, line in blocks.items()},
        "GLOSSARY_SHA256": {k: _keyed_digest(lines) for k, lines in glossary},
        "PINNED_SHA256": {k: _keyed_digest(lines) for k, lines in pinned},
        "RESEARCH_STRUCTURE": research_structure(research),
        "HISTORY_SHA256": {k: _keyed_digest(lines) for k, lines in history_lines},
        "REVIEW_LINE_SHA256": {k: _keyed_digest(lines) for k, lines in review_lines},
        "REVIEW_PROSE_SHA256": tuple(_cell_digest(line) for line in prose),
    }
    multi = [k for k, lines in pinned if len(lines) > 1]
    notes = [
        f"{len(inventory)} inventory rows, {len(rows)} table rows; non-C tokens {tokens}; {len(owners)} keys",
        f"retired: table {retired_ids(rows)!r}, listed {_retired_listed(history)!r}",
        f"reviewed_block_lines: {len(blocks)} labels mapped, problems {block_problems}",
        (f"Glossary: {len(glossary)} keyed lines, {len({k for k, _ in glossary})} unique keys, "
         f"unkeyable {glossary_problems}"),
        (f"Pinned: {len(pinned)} rules, {sum(len(v) for _, v in pinned)} lines, more than one line {multi}, "
         f"unkeyable {pinned_problems}"),
        f"structure: {len(values['RESEARCH_STRUCTURE'])} entries",
        (f"history: {len(history_lines)} keyed lines, {len({k for k, _ in history_lines})} unique keys, "
         f"unkeyable {history_problems}"),
        (f"review: {len(review_lines)} verdict lines, {len({k for k, _ in review_lines})} unique rows, "
         f"{len(prose)} prose lines"),
    ]
    problems = glossary_problems + pinned_problems + history_problems + block_problems
    problems += [f"{k} occurs more than once" for entries in (glossary, history_lines, review_lines)
                 for k, n in Counter(k for k, _ in entries).items() if n > 1]
    return values, notes + [f"problems {problems}"]


def _literal_source(name: str, value: object) -> list[str]:
    """``name = value`` as Python source, one entry per line (a structure run per line, wrapped)."""
    def item(v: object) -> str:
        if isinstance(v, frozenset):
            return "frozenset({" + ", ".join(f'"{x}"' for x in sorted(v)) + "})"
        return json.dumps(v, ensure_ascii=False)
    if isinstance(value, dict):
        return [f"{name} = {{", *[f"    {item(k)}: {item(v)}," for k, v in value.items()], "}"]
    if name == "RESEARCH_STRUCTURE":
        out, run = [f"{name} = ("], []
        for entry in (*value, None):
            if entry is None or entry.startswith("#"):
                out += textwrap.wrap(" ".join(f"{item(x)}," for x in run), 112, initial_indent="    ",
                                     subsequent_indent="    ", break_on_hyphens=False, break_long_words=False)
                run = []
                if entry is not None:
                    out.append(f"    {item(entry)},")
            else:
                run.append(entry)
        return [*out, ")"]
    return [f"{name} = (", *textwrap.wrap(" ".join(f"{item(x)}," for x in value), 112, initial_indent="    ",
                                          subsequent_indent="    "), ")"]


def frozen_literals() -> str:
    """The source of every literal in ``FROZEN_LITERALS``, as the committed files now give it, headed by
    what the derivation found. For a reviewed edit only: paste the entries the edit changed, and no
    others, so the diff of the literal shows what was approved. Run
    ``uv run --package runcoach-api python runcoach-api/tests/test_research00_traceability.py``."""
    values, notes = derived_literals()
    out = [f"# {note}" for note in notes]
    for name in FROZEN_LITERALS:
        out += ["", *_literal_source(name, values[name])]
    return "\n".join(out)


def test_frozen_literals_prints_every_frozen_literal_as_committed() -> None:
    """Iteration 5, S4: ``frozen_literals()`` prints every frozen literal, and its printed source, run,
    gives each committed literal exactly, so the one regeneration path is proven to match. Every module
    name ending ``_SHA256`` or ``_PIN`` is in ``FROZEN_LITERALS``, and the derivation keyed every line."""
    source = frozen_literals()
    printed: dict[str, object] = {}
    exec(source, {}, printed)  # noqa: S102 -- the module's own generated source, run to prove it
    declared = {n for n in globals() if n.endswith(("_SHA256", "_PIN"))}
    wrong = {}
    for name in FROZEN_LITERALS:
        have, want = printed.get(name), globals()[name]
        if have != want:
            wrong[name] = (sorted(k for k in set(have) | set(want) if have.get(k) != want.get(k))[:5]
                           if isinstance(want, dict) and isinstance(have, dict) else "differs")
    notes = [line for line in source.splitlines() if line.startswith("# ")]
    print(f"[slice compared] {len(printed)} printed, {len(FROZEN_LITERALS)} frozen, declared {sorted(declared)}; "
          f"{notes}; differing {wrong}")
    assert set(printed) == set(FROZEN_LITERALS) and declared <= set(FROZEN_LITERALS)
    assert notes[-1] == "# problems []"
    assert wrong == {}


if __name__ == "__main__":
    print(frozen_literals())
