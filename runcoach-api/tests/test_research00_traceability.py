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
import difflib
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
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
#:   ``no``-row restorations (HRV-01, HRV-40). Since T223 (D6, sprint-009): HRV-31's ``yes`` row, already
#:   ``yes`` under C01 and C02, cites R13 for its old-meaning key, and ARB-01's ``no`` row cites ``T-27``,
#:   whose set is empty (no ``yes`` row cites it); both regenerated by the documented command.
#: A ``yes`` row authorized by no C-number outside NO_ONLY and by no token whose set holds its ID is
#: a meaning change no decision authorized. Do not widen a set to fit a row.
NON_C_AUTHORITIES = {
    "HRV-11": frozenset({"HRV-11"}),
    "R13": frozenset({"HRV-31", "PRIN-12"}),
    "T-07": frozenset({"GATE-03", "REG-02", "REG-16", "REG-19"}),
    "T-27": frozenset({}),
}

#: R3: the no-only decisions whose old meaning is still stated downstream, so every row citing one
#: names an old-meaning key (F008 AC6; S5).
KEYED_NO_ONLY = ("C19", "C25")

#: Each old-meaning key mapped to the inventory row(s) that may name it (sprint-007 review iteration
#: 3, S1). Checking only that a key's leading decision token is cited let a row borrow any key whose
#: decision it also cites: DOC-03's "1–4" became "1–3" under ``C38 | yes |
#: DOC-09-C38-superseded-text-left-standing`` and the suite stayed green, the third route in one
#: family (iteration 1 M2, iteration 2 M1). Derived 2026-09-26 from the committed table at d8c8275
#: (54 keys, all 54 of ``OLD_MEANINGS``; 58 since T223 added F012's four, D6-D8), then frozen. Every key
#: has one owner row except
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
    "HRV-31-R13-broad-withhold-of-any-verdict": frozenset({"HRV-31"}),
    "HRV-42-R13-reset-in-force-persists-through-it": frozenset({"HRV-42"}),
    "PRIN-05-C06-conservative-wins-unscoped": frozenset({"PRIN-05"}),
    "PRIN-08-C24-sidecar-ignored-by-default": frozenset({"PRIN-08"}),
    "PRIN-08-C25-rule-file-short-list": frozenset({"PRIN-08"}),
    "PRIN-10-C19-reduced-confidence": frozenset({"PRIN-10"}),
    "PRIN-12-C33-tolerance-not-published": frozenset({"PRIN-12"}),
    "PRIN-12-R13-withheld-response-stays-reproducible": frozenset({"PRIN-12"}),
    "PRIN-12-R13-reproducible-by-hand-without-exceptions": frozenset({"PRIN-12"}),
    "PRIN-14-C07-weak-evidence-only": frozenset({"PRIN-14"}),
    "PRIN-15-C06-accepted-as-priced": frozenset({"PRIN-15"}),
    "PRIN-16-C08-silence-tolerated-freely": frozenset({"PRIN-16"}),
    "T07-acwr-band": frozenset({"REG-02"}),
    "T07-ctl-rise-band": frozenset({"REG-19"}),
    "T07-tolerance-band": frozenset({"GATE-03"}),
    "T07-tsb-target-form-band": frozenset({"REG-16"}),
    "T-27-ladder-order-for-the-loop": frozenset({"ARB-01"}),
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
    "PRIN-12": "d191ef3229ab.6cb6ac81454f.4fef567a5a58.b019a19ba8c3.0e01f0971f82.8a798890fe93.03f6d70b24ee",
    "PRIN-13": "e47004f3f0c5.d7c14355883f.369c49596864.e47004f3f0c5.bda050585a00.9390298f3fb0.bda050585a00",
    "PRIN-14": "4fc6fb4664dc.8d6698e35470.494da81eb714.4fc6fb4664dc.1751f998fdad.8a798890fe93.ac919b316567",
    "PRIN-15": "d5452db028f5.ac46c591281b.a3e3b9ad5b3b.a6260e38dafd.8d05991678e9.8a798890fe93.ebf2f0bfa089",
    "PRIN-16": "e716bfb58948.141ac72b92ba.0208c460b4e9.e19449ddac9a.acade632f70d.8a798890fe93.d174652dd329",
    "ARB-01": "9ab809362800.cc3f97b16818.78fb27e49156.9ab809362800.9795e70cfc3d.9390298f3fb0.f119cae80e94",
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
    "HRV-31": "f7644bece5b9.732e7894a8f3.3a4b0f9ead80.58b8c6440f07.53effaf38cb0.8a798890fe93.3135a375c05f",
    "HRV-32": "62efc47e433a.6396eb3ddd48.82adc07c8c81.8b943484deab.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-33": "cfa72e4a99bc.6f71a595c3c7.4a0ffb80cd0b.ac8890863d7e.09947c6e5a08.8a798890fe93.492c4fa8e233",
    "HRV-34": "bfbe516a48bc.0adf15613391.f9643ded7f32.6e7a11003862.d3bcf7c77f65.8a798890fe93.d9282680ba9d",
    "HRV-35": "438c8a2e6aea.550e3512df10.141502a4d41f.ecb9510f3b2e.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-36": "f25c6f7a0081.83aa4a7625d5.141502a4d41f.545c84be8788.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-37": "350c4ef1265b.f7cff2c54ef9.1d68241bcea9.350c4ef1265b.34a44366294a.8a798890fe93.00c2bffa0831",
    "HRV-38": "08458bc79ce4.308982854b97.6f1cce33f7f0.733d298e8d61.6645ca87da63.8a798890fe93.e0b223b1e875",
    "HRV-39": "67e71657c83f.c9dbbd781835.d893b15300d5.f61dd6f1bbac.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-40": "a3a5f5539618.9f6e3248ce64.ee88d8b09d69.6814368af646.7a2eca4de16f.9390298f3fb0.6b810ca2c02f",
    "HRV-41": "6c5693acd4be.a30eb10ac751.a3e3b9ad5b3b.07247f6021b7.bda050585a00.9390298f3fb0.bda050585a00",
    "HRV-42": "78c3365d0fab.4ea3f04e28ef.141502a4d41f.78c3365d0fab.7a2eca4de16f.9390298f3fb0.5ed388d3b2c9",
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
#: at 790ea0c (54 keys; 58 since T223). Asserted both ways by ``old_meaning_digest_errors``; regenerate as for
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
    "C19-hrv-04-reduced-confidence": "f45786026b4c.7ec3fdac444a.700a5baf5724.aae16350d9be",
    "C21-dec01-bonus-section": "02834eedae78.6bc22b93b0fd.ecc48cc297fe.abb23d0d25cd",
    "C24-arch06-ignores-by-default": "d22a4b5bede7.aeac417e7a2c.16bd7814bc5b.133d22d98524",
    "C26-cold01-hrv-input": "1c4bef7f91ca.d2aeea8a5c8e.d03fa426dc05.31c65e049802",
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
    "HRV-31-R13-broad-withhold-of-any-verdict": "01457eeff28c.85e3b6f4dcf9.576b340a4e35.1a590f2ca696",
    "HRV-40-R13-now-sustaining-tier": "32e01ca7b893.ca8a8ddd8413.c9ea2c9d2933.ef61f8cd3858",
    "HRV-42-R13-reset-in-force-persists-through-it": "86936d6317e2.d5e290c91073.97afffe3bb25.5bd1b73f6fca",
    "PRIN-05-C06-conservative-wins-unscoped": "9a7ed38813f0.187075f13758.31002881e3fe.e18b75a07d89",
    "PRIN-08-C24-sidecar-ignored-by-default": "611f94de932f.aeac417e7a2c.16bd7814bc5b.133d22d98524",
    "PRIN-08-C25-rule-file-short-list": "15bea1a5a8f5.e7b67b07770d.67ae3d3f6543.d0f66c4b6e21",
    "PRIN-10-C19-reduced-confidence": "4a55d90edabf.a241fbcc66d1.0440c34e3192.f5e2ee7f48ed",
    "PRIN-12-C33-tolerance-not-published": "90a523ce3525.56bcb20c6d0e.f9699c8b2640.a39ed58c5b0b",
    "PRIN-12-R13-reproducible-by-hand-without-exceptions": "d1b5387ebb59.fd76c1b74970.e77fba90b5d8.fbf57c0d412b",
    "PRIN-12-R13-withheld-response-stays-reproducible": "140471decdb7.965760d0f608.576b340a4e35.bf1999145d8e",
    "PRIN-14-C07-weak-evidence-only": "037bd3251a9c.9fc907922633.5b9172a8603f.ab2c8aca25fc",
    "PRIN-15-C06-accepted-as-priced": "cedf36087186.77a9b4bb08b1.576b340a4e35.e18b75a07d89",
    "PRIN-16-C08-silence-tolerated-freely": "71ccd3cd648e.21241cf65605.5b9172a8603f.acade632f70d",
    "T-27-ladder-order-for-the-loop": "c738533c88ad.1f952a09b594.512103a0ef0b.658dc846be7e",
    "T07-acwr-band": "045461dab097.a7436e9fe478.d6ff9e26863e.bc3dfbf4de86",
    "T07-ctl-rise-band": "a021f2e9a52a.3b0843c5517b.14883507e245.bc3dfbf4de86",
    "T07-tolerance-band": "0a50e1bfac53.3314b0e312c8.576b340a4e35.bc3dfbf4de86",
    "T07-tsb-target-form-band": "00275c8ecc64.2893c573f717.7721e99e1123.bc3dfbf4de86",
}

#: Each meaning-review verdict is bound to the text it judged (iteration 4, M3, ruled 2026-09-26; made
#: mechanical by T192 under R13). The binding lives in 00-meaning-review.md, not here: each verdict line
#: carries the ``_cell_digest`` of the block line it judged, and ``reviewed_block_errors`` compares the
#: current line with that cell. The former ``REVIEWED_BLOCK_SHA256`` literal is retired: a digest pasted
#: into this file cleared a changed rule with no new verdict (the sprint-007 HRV-24 MUST to MAY route).

#: Iteration 5 (the principle extending the user's M3 ruling): every line of the four research/00 files
#: that carries meaning or structure is bound, so an edit to it needs a visible edit to a frozen literal.
#: Each value below is ``_keyed_digest`` (a ``_cell_digest`` per line: whitespace collapsed, first 12 hex),
#: derived 2026-09-26 at 12348f1 by ``derived_literals()``, which keyed every line and printed its counts;
#: each error names the line and prints the new digest, and ``frozen_literals()`` prints every literal.
#:
#: T192 step C1 retired ``GLOSSARY_SHA256`` (M1): each Glossary definition is now a required review row
#: (``T-NN``), bound by the digest its verdict line in 00-meaning-review.md records, as a block line is.

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
    "PRIN-12/Pinned": "397791298134",
    "PRIN-22/Pinned": "6f6a2e0e1e2d",
    "PRIN-23/Pinned": "6f6a2e0e1e2d",
    "PRIN-24/Pinned": "6f6a2e0e1e2d",
    "PRIN-27/Pinned": "6f6a2e0e1e2d",
    "PRIN-13/Pinned": "6f6a2e0e1e2d",
    "PRIN-14/Pinned": "6f6a2e0e1e2d",
    "PRIN-15/Pinned": "b4566a98f730.7da163e13d9d",
    "PRIN-25/Pinned": "b4566a98f730",
    "PRIN-26/Pinned": "7da163e13d9d",
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
    "DEC-01/Pinned": "f6f6ea7ed100",
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
    "HRV-17/Pinned": "397791298134",
    "HRV-18/Pinned": "6f6a2e0e1e2d",
    "HRV-19/Pinned": "068d30b2b1f3",
    "HRV-20/Pinned": "6f6a2e0e1e2d",
    "HRV-21/Pinned": "6f6a2e0e1e2d",
    "HRV-22/Pinned": "477b63dda640",
    "HRV-23/Pinned": "6f6a2e0e1e2d",
    "HRV-24/Pinned": "6f6a2e0e1e2d",
    "HRV-25/Pinned": "7da163e13d9d",
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
    "HRV-85/Pinned": "6f6a2e0e1e2d",
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
    "PRIN-11", "PRIN-12", "PRIN-22", "PRIN-23", "PRIN-24", "PRIN-27",
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
    "HRV-77", "HRV-78", "HRV-79", "HRV-80", "HRV-81", "HRV-82", "HRV-83", "HRV-84", "HRV-85", "GATE-01",
    "GATE-02", "GATE-03", "GATE-04", "GATE-05", "GATE-06", "GATE-07", "GATE-08", "FIG-01", "FIG-02", "FIG-03",
    "FIG-04", "FIG-05", "FIG-06", "FIG-07", "FIG-08", "FIG-09", "FIG-10", "FIG-11",
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
    "H-16": "0cbf563fb825",
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
    "H-39": "ea77338acc5e",
    "H-40": "ff2a30c98d83",
    "H-41": "cb75714fb871",
    "## Retired IDs": "df75a433ff9c",
    "PRIN-16 retired": "44a1c18ceea9",
}

#: ``REVIEW_PROSE_SHA256`` (S3): the review file's lines outside its tables, in order (the title, the
#: opening paragraph, the three group headings and the Glossary's, ``## Rounds``, its ``Round N:``
#: paragraphs and the Final line), one digest each. Asserted by ``review_line_errors``, which names the
#: first line that differs; each new round adds its entry before Final's and changes Final's (regenerated
#: through ``frozen_literals()`` at T192 for round 7, at T194 for rounds 8 and 9, and in review cycle 2
#: for round 10 at iteration 1 and round 11 at iteration 2).
REVIEW_PROSE_SHA256 = (
    "3a7b11e58598", "f191ca7ee5d3", "e5d55848b69b", "c2b5b175501d", "609e8c7aa461", "5fefbc585347",
    "96de422a6cb7", "66f200076653", "62ca2d783b2d", "7090b17ee6ec", "f936337bcc24", "1e0b83be9b41",
    "ca0e1376449f", "1ab83c31c697", "9129d180bcf2", "394aca2810e8", "4bab88c420df", "a27a119979ce",
    "a4e5252f9b6e", "4cf8113a2480", "a99cc87f595b", "76a184b14c1b", "dc852e60c644", "0734e515a8d0",
    "6e818f8a9193", "b42d87f9dcd2", "d13bb5c915cb",
)

#: ``FROZEN_ROUNDS`` (review cycle 2, S1): each ``Round N:`` paragraph under ``## Rounds`` that a commit
#: has frozen, keyed by its name, with its ``_cell_digest``. A round is frozen by its name, so an edited
#: frozen round names no row (``unfrozen_rounds``) and reds on its own (``frozen_round_errors``);
#: ``round_literal``, what ``frozen_literals()`` prints, keeps every committed entry and adds only a new
#: name, so regenerating clears neither an edit nor a removal. Asserted equal to ``_FROZEN_ROUNDS_PIN``.
FROZEN_ROUNDS = {
    "1": "66f200076653",
    "2": "62ca2d783b2d",
    "3": "7090b17ee6ec",
    "3b": "f936337bcc24",
    "4": "1e0b83be9b41",
    "4b": "ca0e1376449f",
    "5": "1ab83c31c697",
    "6": "9129d180bcf2",
    "7": "394aca2810e8",
    "8": "4bab88c420df",
    "9": "a27a119979ce",
    "10": "a4e5252f9b6e",
    "11": "4cf8113a2480",
    "12": "a99cc87f595b",
    "13": "76a184b14c1b",
    "14": "dc852e60c644",
    "15": "0734e515a8d0",
    "16": "6e818f8a9193",
    "17": "b42d87f9dcd2",
}

#: A second copy of ``FROZEN_ROUNDS``, as ``_NON_C_AUTHORITIES_PIN`` is of its map: a hand edit that
#: re-freezes an edited round must change both.
_FROZEN_ROUNDS_PIN = {
    "1": "66f200076653",
    "2": "62ca2d783b2d",
    "3": "7090b17ee6ec",
    "3b": "f936337bcc24",
    "4": "1e0b83be9b41",
    "4b": "ca0e1376449f",
    "5": "1ab83c31c697",
    "6": "9129d180bcf2",
    "7": "394aca2810e8",
    "8": "4bab88c420df",
    "9": "a27a119979ce",
    "10": "a4e5252f9b6e",
    "11": "4cf8113a2480",
    "12": "a99cc87f595b",
    "13": "76a184b14c1b",
    "14": "dc852e60c644",
    "15": "0734e515a8d0",
    "16": "6e818f8a9193",
    "17": "b42d87f9dcd2",
}

#: ``REVIEW_LINE_SHA256`` (S3): each verdict line of 00-meaning-review.md, label, verdict, judged digest
#: and reason together, keyed by its row (one per ``required_review_rows`` row: the rule rows, then T-01 to
#: T-33, added at T192 step C1 from round 6). ``review_errors`` caught a flipped verdict, not a reason:
#: PRIN-01's rewritten to "Not reviewed." stayed green. It also makes an edit to a verdict line's digest
#: cell visible when the verdict and reason stay (T192). A new round updates these digests deliberately
#: (``review_line_literal``): a changed or added entry only for a row whose verdict line's reason begins
#: with that round's ``Round N:`` and whose paragraph names the row (``_rejudged``), and a dropped entry
#: only for a row the paragraph says "; removed <row>" or ". Removed <row>" of, as its own clause
#: (``round_removals``, T227); the verdict line's own
#: digest cell is what binds the text. Asserted both ways by ``review_line_errors``.
REVIEW_LINE_SHA256 = {
    "PRIN-01": "02d5c6065e90",
    "PRIN-01/Scope": "69d7dc23734b",
    "PRIN-01/Not": "a4ff1861b774",
    "PRIN-02": "d8c4bd30b2b4",
    "PRIN-02/Scope": "12a0264d64b4",
    "PRIN-02/Not": "37c82774a823",
    "PRIN-03": "85d062fa7a28",
    "PRIN-03/Scope": "aaa7d226da39",
    "PRIN-03/Not": "6c165cafd5d8",
    "ARB-01": "259d2280c936",
    "ARB-01/Scope": "be6eeb743496",
    "ARB-01/Not": "c46c30792083",
    "ARB-02": "91e93dac8c93",
    "ARB-02/Scope": "9b6a83190e9c",
    "ARB-02/Not": "9b8ec5859c7c",
    "ARB-03": "3e40f125d342",
    "ARB-03/Scope": "e4b6d7c22188",
    "ARB-03/Not": "9f474fb0f37c",
    "ARB-04": "893cc8fa2e4d",
    "ARB-04/Scope": "96add20250d0",
    "ARB-04/Not": "cb831b7f1be4",
    "ARB-05": "b0ff3d597149",
    "ARB-05/Scope": "a59d7fa8e92f",
    "ARB-05/Not": "a19c9993e9cb",
    "ARB-06": "859c03abaaab",
    "ARB-06/Scope": "4fe8ef37b23f",
    "ARB-06/Not": "36ad84afec37",
    "ARB-07": "3a5c67e2a9a4",
    "ARB-07/Scope": "cbd7d33a6b0e",
    "ARB-07/Not": "db4ceb453152",
    "PRIN-04": "95d6d45567c7",
    "PRIN-04/Scope": "d34eabce814c",
    "PRIN-04/Not": "92b40d5e15fe",
    "PRIN-05": "61f5617959b4",
    "PRIN-05/Scope": "4b2d412af0f8",
    "PRIN-05/Not": "afaa03434705",
    "PRIN-05/Why": "176d251c375f",
    "PRIN-06": "ba8410637975",
    "PRIN-06/Scope": "0550b5d650a1",
    "PRIN-06/Not": "84bb2db9fadd",
    "PRIN-17": "8dcf28478188",
    "PRIN-17/Scope": "8103f108bd29",
    "PRIN-17/Not": "8a4a695902e8",
    "PRIN-18": "acaf948c7dbd",
    "PRIN-18/Scope": "9d1072c5849c",
    "PRIN-18/Not": "9ee7f8e2399d",
    "PRIN-07": "92d3592a12d6",
    "PRIN-07/Scope": "297923eeec1b",
    "PRIN-07/Not": "a7995f06a008",
    "PRIN-08": "4e78d38cf492",
    "PRIN-08/Scope": "5ed2bb8a5b09",
    "PRIN-08/Not": "f8266aa2a2ef",
    "PRIN-08/Why": "cd08a720c075",
    "PRIN-09": "bf022260d245",
    "PRIN-09/Scope": "9908a42e8959",
    "PRIN-09/Not": "82d01a78c104",
    "PRIN-10": "7dfdd89d5026",
    "PRIN-10/Scope": "cb16899cd058",
    "PRIN-10/Not": "9125da803dd8",
    "PRIN-19": "877ecac351f2",
    "PRIN-19/Scope": "17fac90aef50",
    "PRIN-19/Not": "34627042ac60",
    "PRIN-20": "58aa5d7fe734",
    "PRIN-20/Scope": "979de7542361",
    "PRIN-20/Not": "baf1de29af93",
    "PRIN-20/Why": "bff7e6bbc01a",
    "PRIN-21": "32ccbd6a5eb2",
    "PRIN-21/Scope": "1f8f6ab34ce7",
    "PRIN-21/Not": "125f3ca10850",
    "PRIN-11": "c5cde54990aa",
    "PRIN-11/Scope": "c922e0deda56",
    "PRIN-11/Not": "2594377f39f9",
    "PRIN-12": "a85678e86e27",
    "PRIN-12/Scope": "d540f126cc46",
    "PRIN-12/Not": "7299ce1c2a83",
    "PRIN-12/Why": "3d9eefb0eedc",
    "PRIN-22": "c97d92a2e7c8",
    "PRIN-22/Scope": "7b910dbe3375",
    "PRIN-22/Not": "b3071e8295aa",
    "PRIN-23": "ea5c41159796",
    "PRIN-23/Scope": "1d5c6357246e",
    "PRIN-23/Not": "554539bcf885",
    "PRIN-24": "7a1febbf6fa3",
    "PRIN-24/Scope": "7657bfebbf9e",
    "PRIN-24/Not": "d6f5203caad7",
    "PRIN-24/Why": "208c97e784ba",
    "PRIN-27": "1a8a0d8a4cba",
    "PRIN-27/Scope": "dcffd3ad5ca3",
    "PRIN-27/Not": "98e59efe4ab4",
    "PRIN-27/Why": "deed6f625b66",
    "PRIN-13": "6569e1900548",
    "PRIN-13/Scope": "060f62f49158",
    "PRIN-13/Not": "d9d0793208a5",
    "PRIN-14": "c536c3f8950d",
    "PRIN-14/Scope": "c01e6a628875",
    "PRIN-14/Not": "bd1309442ee7",
    "PRIN-14/Why": "715624d693db",
    "PRIN-15": "e254ea273a4c",
    "PRIN-15/Scope": "07c0d94b926e",
    "PRIN-15/Not": "4c479fff2ad3",
    "PRIN-15/Why": "47f18f7da28a",
    "PRIN-25": "7a21460cdba8",
    "PRIN-25/Scope": "27460e4ffe23",
    "PRIN-25/Not": "43bc89eaa4dd",
    "PRIN-26": "fc6223419653",
    "PRIN-26/Scope": "04b4b671a188",
    "PRIN-26/Not": "84e3d5e044b0",
    "AUT-01": "14b005f66b8b",
    "AUT-01/Scope": "b22976e3321c",
    "AUT-01/Not": "226a57523dab",
    "AUT-02": "44977e3eeae7",
    "AUT-02/Scope": "c7146118bec2",
    "AUT-02/Not": "4403c84d9846",
    "AUT-02/Why": "b8d56ebbed45",
    "AUT-03": "c1223e20028a",
    "AUT-03/Scope": "a30565a80499",
    "AUT-03/Not": "b296b85135da",
    "AUT-04": "65c7f1af0a9f",
    "AUT-04/Scope": "73dac8513b73",
    "AUT-04/Not": "2e1bf55f085e",
    "AUT-04/Why": "d7a4b3df469d",
    "AUT-06": "0217a9bcdfc5",
    "AUT-06/Scope": "35610da659e4",
    "AUT-06/Not": "2daf2eec06f4",
    "AUT-08": "999e6edad2ed",
    "AUT-08/Scope": "9e8fd41a809e",
    "AUT-08/Not": "ef2dd6e24c84",
    "GOAL-01": "de303104f378",
    "GOAL-01/Scope": "b510a1f2effc",
    "GOAL-01/Not": "95387af77c69",
    "GOAL-02": "b2c8f6e7959b",
    "GOAL-02/Scope": "ff2bd8b3612d",
    "GOAL-02/Not": "08e605a3ad37",
    "GOAL-02/Why": "3596b46c7220",
    "GOAL-03": "d4fd70b2d077",
    "GOAL-03/Scope": "a4ef4ad95022",
    "GOAL-03/Not": "1adec0f4474f",
    "GOAL-04": "45f43ada815c",
    "GOAL-04/Scope": "e77d20d0df91",
    "GOAL-04/Not": "46b1c2370edd",
    "GOAL-05": "72ee6efcc087",
    "GOAL-05/Scope": "d89a938c2539",
    "GOAL-05/Not": "64d56eebda05",
    "GOAL-06": "f49cd715dfc6",
    "GOAL-06/Scope": "5bbfa6c7d6fd",
    "GOAL-06/Not": "70b832b16e34",
    "DOC-06": "9d307ca31273",
    "DOC-06/Scope": "2c1082c6b559",
    "DOC-06/Not": "2fe0d39bb977",
    "DOC-06/Why": "fc980e1c1e31",
    "DOC-07": "8926a0d1c4ba",
    "DOC-07/Scope": "0c1b40d2a350",
    "DOC-07/Not": "a65c8186cda7",
    "DOC-08": "665c938d6dce",
    "DOC-08/Scope": "7e38e11a000e",
    "DOC-08/Not": "5e3bba338cf9",
    "DOC-17": "ece275d2641d",
    "DOC-17/Scope": "9306e2d5632c",
    "DOC-17/Not": "bfafc2622828",
    "DOC-18": "5c62443fefd6",
    "DOC-18/Scope": "6d7583bae7ec",
    "DOC-18/Not": "fc35a93d094d",
    "DOC-15": "c7b926b15355",
    "DOC-15/Scope": "e6c631059a1a",
    "DOC-15/Not": "35620635d692",
    "DOC-01": "ca30fa043c39",
    "DOC-01/Scope": "bc99f26ff756",
    "DOC-01/Not": "23bbc6a3c068",
    "DOC-05": "101690c38b90",
    "DOC-05/Scope": "6490392c7868",
    "DOC-05/Not": "c9b6864ac087",
    "DOC-04": "86943994a51b",
    "DOC-04/Scope": "448c0ebee43a",
    "DOC-04/Not": "4b229e9aad80",
    "DOC-03": "da9ec9d2ae5d",
    "DOC-03/Scope": "526d37be048e",
    "DOC-03/Not": "1f517ad918ef",
    "AUT-05": "7c68af809c59",
    "AUT-05/Scope": "dc4c18617dac",
    "AUT-05/Not": "660c533715d0",
    "AUT-07": "69f6176beba9",
    "AUT-07/Scope": "3ab7934dce4a",
    "AUT-07/Not": "668e50a6d400",
    "DOC-02": "42d640df5b19",
    "DOC-02/Scope": "9cc6e8771009",
    "DOC-02/Not": "f0f2887235da",
    "DOC-09": "5dbf836b5a67",
    "DOC-09/Scope": "063fe9dd75e2",
    "DOC-09/Not": "c39c7e9d0c57",
    "DOC-09/Why": "83d851f0f05b",
    "DOC-10": "8d0e7e6b1d52",
    "DOC-10/Scope": "45d4786e73b8",
    "DOC-10/Not": "db4bf1aec443",
    "DOC-11": "0acda43d2ee2",
    "DOC-11/Scope": "64efec2df7f8",
    "DOC-11/Not": "e8770ae6978e",
    "DOC-12": "53c134f7c613",
    "DOC-12/Scope": "538df6d3bc7d",
    "DOC-12/Not": "01278acc164e",
    "DOC-13": "61f6411601a7",
    "DOC-13/Scope": "c60fa7169fe7",
    "DOC-13/Not": "405c3882e4e7",
    "DOC-14": "42b445fbd730",
    "DOC-14/Scope": "9761f5530568",
    "DOC-14/Not": "1b300fb0e7d9",
    "DOC-16": "03ed204b99ea",
    "DOC-16/Scope": "59d1d49d7672",
    "DOC-16/Not": "5ae36f754460",
    "DOC-19": "648b5eecd55c",
    "DOC-19/Scope": "929bdf00830e",
    "DOC-19/Not": "6d2a8a560c32",
    "DOC-20": "2958c48ce057",
    "DOC-20/Scope": "c8dc26237619",
    "DOC-20/Not": "b9ca347dcc36",
    "DOC-21": "46e89c2170d2",
    "DOC-21/Scope": "42d059ee201d",
    "DOC-21/Not": "2412004613c3",
    "DOC-22": "6096438683f7",
    "DOC-22/Scope": "8879c1bfc0fe",
    "DOC-22/Not": "8ea310badb34",
    "ARCH-00": "a7d381d719d5",
    "ARCH-00/Scope": "5d220756d526",
    "ARCH-00/Not": "56d27034cc1a",
    "ARCH-01": "5a1d8153c670",
    "ARCH-01/Scope": "e5cc70057ae6",
    "ARCH-01/Not": "8cd234d8dc4b",
    "ARCH-02": "609e915821c0",
    "ARCH-02/Scope": "440f8157c7b8",
    "ARCH-02/Not": "8bd0be978a51",
    "ARCH-03": "8aaf439fad5f",
    "ARCH-03/Scope": "47b2fc6fe4b7",
    "ARCH-03/Not": "c1c2787e378c",
    "ARCH-04": "afad60a6ffce",
    "ARCH-04/Scope": "b755ea5fbf0c",
    "ARCH-04/Not": "0f10783a0315",
    "ARCH-05": "0f1a6994d8e5",
    "ARCH-05/Scope": "6df3f7aca506",
    "ARCH-05/Not": "609c98bb256b",
    "ARCH-06": "5c79b37da633",
    "ARCH-06/Scope": "a6a35fef831c",
    "ARCH-06/Not": "562525568857",
    "ARCH-06/Why": "b0173dc91f91",
    "ARCH-07": "94b6fb73d5a9",
    "ARCH-07/Scope": "874c1727f1b9",
    "ARCH-07/Not": "21f7d904691e",
    "ARCH-08": "a065f73e4380",
    "ARCH-08/Scope": "77cd96cfae35",
    "ARCH-08/Not": "3792d9268df9",
    "ARCH-09": "6a7ef8dba8e1",
    "ARCH-09/Scope": "e3093bbf339c",
    "ARCH-09/Not": "3c12f2ecd973",
    "ARCH-10": "8e4f02c7c04a",
    "ARCH-10/Scope": "d0183ae0cb9a",
    "ARCH-10/Not": "73f629495fb1",
    "ARCH-11": "ac736f83297a",
    "ARCH-11/Scope": "34c2a36cf7cf",
    "ARCH-11/Not": "31eca8914d42",
    "ARCH-12": "8597ff5b9f60",
    "ARCH-12/Scope": "cd28c43b7cac",
    "ARCH-12/Not": "bad072faf3ad",
    "ARCH-12/Why": "8adbd7ff8fdf",
    "ARCH-13": "95bab0c903ba",
    "ARCH-13/Scope": "df9edd9675ec",
    "ARCH-13/Not": "1e2a87383a2c",
    "ARCH-13/Why": "5302427617a8",
    "REG-01": "be8a6e7a439e",
    "REG-01/Scope": "f491888bad98",
    "REG-01/Not": "1522bca26fc6",
    "REG-02": "9bed455fc5a0",
    "REG-02/Scope": "28b774eb7530",
    "REG-02/Not": "3e89ad727dfd",
    "REG-02/Why": "ede23a505fb5",
    "REG-03": "6669ae7a3fe0",
    "REG-03/Scope": "a5b20f4a89a7",
    "REG-03/Not": "342f14e61bb9",
    "REG-04": "18aaba060a5c",
    "REG-04/Scope": "2ea62e2ec752",
    "REG-04/Not": "6baf18240dbe",
    "REG-05": "1892fe161740",
    "REG-05/Scope": "e3cdfc03e324",
    "REG-05/Not": "8cd3c0dfa582",
    "REG-06": "699cc2e82c93",
    "REG-06/Scope": "cb41c82e3188",
    "REG-06/Not": "a60f490951bc",
    "REG-07": "a363a7b4fbfd",
    "REG-07/Scope": "5649405a5666",
    "REG-07/Not": "a8dd3b8efd69",
    "REG-08": "d394d6853425",
    "REG-08/Scope": "e02d036acba4",
    "REG-08/Not": "2da4b6abf8f8",
    "REG-09": "b19f1fddbaa5",
    "REG-09/Scope": "8066b63f7c31",
    "REG-09/Not": "e2672103997c",
    "REG-10": "a8100b3143a7",
    "REG-10/Scope": "bf70f3b9f3ac",
    "REG-10/Not": "9c84d0884692",
    "REG-11": "c82554e6be9b",
    "REG-11/Scope": "3931cc238409",
    "REG-11/Not": "e88c5b53f2db",
    "REG-12": "be39dc3d6c60",
    "REG-12/Scope": "27ce7f709757",
    "REG-12/Not": "805cd0a0183b",
    "REG-13": "474909cb53fa",
    "REG-13/Scope": "736ed2c01044",
    "REG-13/Not": "073a8fde21dd",
    "REG-14": "4680912922f9",
    "REG-14/Scope": "6c56047bd1e8",
    "REG-14/Not": "333c1b5f9a0a",
    "REG-14/Why": "ae74e19cfce5",
    "REG-15": "abe310f95256",
    "REG-15/Scope": "1466b6733c19",
    "REG-15/Not": "6f8e6bb4b2f0",
    "REG-16": "c9186f15817b",
    "REG-16/Scope": "86837173817f",
    "REG-16/Not": "75e0356fa9bd",
    "REG-16/Why": "1977b0ae9f83",
    "REG-17": "03c020ded7d2",
    "REG-17/Scope": "a525f1fb5798",
    "REG-17/Not": "188404fee4a0",
    "REG-18": "9ed58155f1b2",
    "REG-18/Scope": "0b78b6d961b9",
    "REG-18/Not": "2d7b039b6ed0",
    "REG-19": "88f9f8e438f0",
    "REG-19/Scope": "ea9e82c96751",
    "REG-19/Not": "756073c96e53",
    "REG-19/Why": "f029579c1d4b",
    "REG-20": "2fe673ba5758",
    "REG-20/Scope": "7433c4e3c401",
    "REG-20/Not": "042758d95be0",
    "REG-21": "b9ad9435557f",
    "REG-21/Scope": "f8998d324eeb",
    "REG-21/Not": "9e893ed3b589",
    "REG-22": "58b44402169a",
    "REG-22/Scope": "7bcea45ea1bd",
    "REG-22/Not": "23564aac8c8a",
    "REG-23": "3ff22e062b76",
    "REG-23/Scope": "1af276a2eb97",
    "REG-23/Not": "959f1bd4fa35",
    "REG-23/Why": "c0bc64b3f162",
    "REG-24": "bc286608f64e",
    "REG-24/Scope": "7e698f1f3c03",
    "REG-24/Not": "f477315a73fe",
    "REG-25": "545d4bd8a50b",
    "REG-25/Scope": "752146eba9ab",
    "REG-25/Not": "cf0bdfec9093",
    "REG-25/Why": "b2cbeec9b159",
    "REG-26": "53b1bf3aa385",
    "REG-26/Scope": "79b8012e9741",
    "REG-26/Not": "0a604538abe1",
    "REG-27": "2333300873df",
    "REG-27/Scope": "802aac0b1cc7",
    "REG-27/Not": "49194e5fdb6c",
    "REG-28": "95d42018fde6",
    "REG-28/Scope": "6cda71a0bac5",
    "REG-28/Not": "0c06bd89d9f7",
    "REG-29": "648c2e74bbe3",
    "REG-29/Scope": "269d7b66ed1a",
    "REG-29/Not": "6f1a99ead82f",
    "REG-30": "68540b955d5e",
    "REG-30/Scope": "75b457fa786c",
    "REG-30/Not": "a2f98f571260",
    "IND-01": "6e18d1684f41",
    "IND-01/Scope": "6a06baeeb954",
    "IND-01/Not": "778408a7bc91",
    "IND-01/Why": "022bc6deab93",
    "IND-02": "3d23c0a15d09",
    "IND-02/Scope": "1143b6f4a36c",
    "IND-02/Not": "f3d70c72cf0f",
    "IND-03": "a8c75f43da72",
    "IND-03/Scope": "d3763272554d",
    "IND-03/Not": "8edbabe0f03a",
    "IND-04": "688a13d52eb9",
    "IND-04/Scope": "3b9a2c2b2d0d",
    "IND-04/Not": "02549a06ff5a",
    "IND-04/Why": "1be99321f3af",
    "IND-05": "f33b25078d54",
    "IND-05/Scope": "1ee9a7af3575",
    "IND-05/Not": "55afd92af766",
    "IND-06": "acd850ac63c4",
    "IND-06/Scope": "8d9b6c1ca841",
    "IND-06/Not": "2d3fead522b7",
    "COLD-01": "3daa307abb9f",
    "COLD-01/Scope": "1341348acf86",
    "COLD-01/Not": "c503dcb7e790",
    "COLD-01/Why": "6ec1734eeb92",
    "COLD-02": "d72eea948e9e",
    "COLD-02/Scope": "25df25206fc1",
    "COLD-02/Not": "9bc538c768ed",
    "COLD-03": "16ae13370852",
    "COLD-03/Scope": "4b8c7e41d6a5",
    "COLD-03/Not": "8799fd24789a",
    "COLD-04": "1c10be648c19",
    "COLD-04/Scope": "bacedf18d8be",
    "COLD-04/Not": "82c3d1576714",
    "COLD-05": "9a142843a2aa",
    "COLD-05/Scope": "d80a4c5ab994",
    "COLD-05/Not": "e21f3019d8ca",
    "COLD-06": "bd13c0e7cc5e",
    "COLD-06/Scope": "07c105086856",
    "COLD-06/Not": "d00a08965e96",
    "COLD-07": "2d4129138cf0",
    "COLD-07/Scope": "befbb73cab8c",
    "COLD-07/Not": "82c1cc939a4f",
    "COLD-08": "17c9a2038d8e",
    "COLD-08/Scope": "f9a88880f2bf",
    "COLD-08/Not": "cfc86bcaad29",
    "COLD-08/Why": "88dc8dcc1f1c",
    "COLD-09": "662df469eb85",
    "COLD-09/Scope": "669f90f7a28c",
    "COLD-09/Not": "27599e8a7fa9",
    "COLD-10": "3ffe90662324",
    "COLD-10/Scope": "cd8609f04e15",
    "COLD-10/Not": "6854a0552209",
    "COLD-11": "3bb7d02e74b2",
    "COLD-11/Scope": "5c54f1334e9b",
    "COLD-11/Not": "279635554214",
    "LT1-01": "ec752edea4c0",
    "LT1-01/Scope": "fb9db174cacc",
    "LT1-01/Not": "b9e76894d32f",
    "LT1-01/Why": "0fa6ac491686",
    "LT1-02": "21429b02283a",
    "LT1-02/Scope": "3ee8eecd9664",
    "LT1-02/Not": "91982543c74b",
    "LT1-02/Why": "dc0aeb112337",
    "LT1-03": "90be4e761815",
    "LT1-03/Scope": "04f3f3aede00",
    "LT1-03/Not": "aec7ef8b0bff",
    "LT1-04": "7d9abe42ccf3",
    "LT1-04/Scope": "a7106765bf85",
    "LT1-04/Not": "bd1a327c0e72",
    "LT1-04/Why": "2723f99e611f",
    "LT1-05": "0f351ff9869a",
    "LT1-05/Scope": "9758e879f429",
    "LT1-05/Not": "b502557d92c7",
    "FTO-01": "876d8ccfaba4",
    "FTO-01/Scope": "96a0d2db5c3f",
    "FTO-01/Not": "21c6e447a2f1",
    "FTO-02": "c36b4627ebf8",
    "FTO-02/Scope": "52e92b6e5fdd",
    "FTO-02/Not": "9429a49615cc",
    "FTO-03": "626d3ba5b63b",
    "FTO-03/Scope": "c5067c659d0f",
    "FTO-03/Not": "210af7094c5d",
    "FTO-04": "5ad96d979aca",
    "FTO-04/Scope": "ad3da349d2ed",
    "FTO-04/Not": "561a25e107b5",
    "FTO-05": "d021db51a463",
    "FTO-05/Scope": "d2588d21a5dd",
    "FTO-05/Not": "aebe215c2274",
    "FTO-06": "4e5a96163418",
    "FTO-06/Scope": "3a9f67c96292",
    "FTO-06/Not": "78a3d316c2b6",
    "FTO-07": "b1ae655aa6a7",
    "FTO-07/Scope": "897cfe61d509",
    "FTO-07/Not": "12477ed1d24d",
    "DEC-01": "0dbb579d725f",
    "DEC-01/Scope": "94472783afa0",
    "DEC-01/Not": "cfdfeca10678",
    "DEC-01/Why": "491ea98ec31d",
    "DEC-02": "11ff1fd5e52d",
    "DEC-02/Scope": "38005f202450",
    "DEC-02/Not": "4ab6f87f0f98",
    "GATE-01": "cf96d4802d8a",
    "GATE-01/Scope": "841595026314",
    "GATE-01/Not": "c7a0fd65a3ef",
    "GATE-01/Why": "95489ef2dbe7",
    "GATE-02": "c0d5ee9c73ab",
    "GATE-02/Scope": "f028282b1a9a",
    "GATE-02/Not": "860ae5316c96",
    "GATE-02/Why": "698d51be4369",
    "GATE-03": "dd851028af0e",
    "GATE-03/Scope": "d30db35db2c7",
    "GATE-03/Not": "6b3988bb8e3c",
    "GATE-03/Why": "0fc5817022f2",
    "GATE-04": "717c94d0f4f4",
    "GATE-04/Scope": "ba55066506a0",
    "GATE-04/Not": "5f3ad43c8bef",
    "GATE-04/Why": "a88a70edfcf5",
    "GATE-05": "8b34a9aa2475",
    "GATE-05/Scope": "9a66e8d65126",
    "GATE-05/Not": "8346fee91043",
    "GATE-06": "ebbb2a214339",
    "GATE-06/Scope": "5e4455767c6c",
    "GATE-06/Not": "670560076b05",
    "GATE-07": "3190daf4868f",
    "GATE-07/Scope": "24674bd09f6d",
    "GATE-07/Not": "c9881f2d41fd",
    "GATE-08": "75760bc56c65",
    "GATE-08/Scope": "0dec8f8cf82c",
    "GATE-08/Not": "819bc4fb232c",
    "FIG-01": "54b8790d3ef7",
    "FIG-01/Scope": "7602cab5bc22",
    "FIG-01/Not": "7c0a6e96cc21",
    "FIG-02": "9db86bc6bcd6",
    "FIG-02/Scope": "f997f524c272",
    "FIG-02/Not": "0fe6eb993790",
    "FIG-02/Why": "f04f67777fb6",
    "FIG-03": "cd8556b6ab66",
    "FIG-03/Scope": "04169881839a",
    "FIG-03/Not": "0313a975943e",
    "FIG-03/Why": "70d05a492d22",
    "FIG-04": "dd8ee4dea7d7",
    "FIG-04/Scope": "f22a6a5c8637",
    "FIG-04/Not": "715df7436d73",
    "FIG-05": "f559004f254f",
    "FIG-05/Scope": "2d8c3ef5f98f",
    "FIG-05/Not": "a9fe7df147e8",
    "FIG-05/Why": "7f82d0a9dc8a",
    "FIG-06": "6460af4708fa",
    "FIG-06/Scope": "8e59d3fb32fa",
    "FIG-06/Not": "0cc7eb8f5a1d",
    "FIG-07": "d0dec773f6ba",
    "FIG-07/Scope": "5d55799c5922",
    "FIG-07/Not": "a621487d752f",
    "FIG-08": "bbc8dcf968aa",
    "FIG-08/Scope": "5d621796a548",
    "FIG-08/Not": "a455b0b23896",
    "FIG-09": "c6c9b0a5f27b",
    "FIG-09/Scope": "d63c63d1407a",
    "FIG-09/Not": "71635c41ec1b",
    "FIG-10": "e8472e373310",
    "FIG-10/Scope": "002ec3d47dba",
    "FIG-10/Not": "4a189de10700",
    "FIG-11": "0463ec082878",
    "FIG-11/Scope": "8ce10885a996",
    "FIG-11/Not": "54b0cc8f95a7",
    "HRV-07": "7f414512ca69",
    "HRV-07/Scope": "b8feac613953",
    "HRV-07/Not": "de800d5edf8d",
    "HRV-07/Why": "239aa7aaab70",
    "HRV-01": "a625a83601e9",
    "HRV-01/Scope": "243f189a9f04",
    "HRV-01/Not": "f46cdc9d6fca",
    "HRV-02": "20d441b9a0aa",
    "HRV-02/Scope": "d6dab8d3537c",
    "HRV-02/Not": "3ac4b1f6d808",
    "HRV-03": "233a618c4c86",
    "HRV-03/Scope": "98264b8ab629",
    "HRV-03/Not": "6ef2edcd7c7d",
    "HRV-04": "07a190672132",
    "HRV-04/Scope": "89ff1a02764f",
    "HRV-04/Not": "e512b41dee8a",
    "HRV-04/Why": "9b4e7d69e7cb",
    "HRV-05": "26b50ed7192e",
    "HRV-05/Scope": "3d13b8e75ef8",
    "HRV-05/Not": "dbf19636b248",
    "HRV-05/Why": "b42a4bd76ed1",
    "HRV-06": "9957d79c329b",
    "HRV-06/Scope": "72cc54ee5ab8",
    "HRV-06/Not": "afdfccf36a18",
    "HRV-47": "239630b97f30",
    "HRV-47/Scope": "ca8aa342c493",
    "HRV-47/Not": "ac283b1b1450",
    "HRV-08": "1615ce63fbfe",
    "HRV-08/Scope": "4a63ff18af52",
    "HRV-08/Not": "133bd4eaad06",
    "HRV-09": "d04af2c3ba9a",
    "HRV-09/Scope": "569eba6a02c7",
    "HRV-09/Not": "5129423a7c1c",
    "HRV-10": "d637ff0161a9",
    "HRV-10/Scope": "3f0b03a0da51",
    "HRV-10/Not": "2f2881f674b1",
    "HRV-11": "4bdf6890af3e",
    "HRV-11/Scope": "9f446c42ab0c",
    "HRV-11/Not": "75a057c40b11",
    "HRV-11/Why": "3cf41eb2fd4b",
    "HRV-12": "8cf935028db1",
    "HRV-12/Scope": "21416ba764c7",
    "HRV-12/Not": "e6ccb2eddef1",
    "HRV-12/Why": "79ef85be4721",
    "HRV-13": "fc040263734d",
    "HRV-13/Scope": "1bfa4052c28e",
    "HRV-13/Not": "1e91faedfc4b",
    "HRV-14": "395a425a3509",
    "HRV-14/Scope": "33201b9540fd",
    "HRV-14/Not": "39df47c3e654",
    "HRV-15": "76b6f65edfe5",
    "HRV-15/Scope": "e38d8cc6f905",
    "HRV-15/Not": "66515a413aa4",
    "HRV-15/Why": "b046ce9afd46",
    "HRV-16": "78feb26ec92a",
    "HRV-16/Scope": "75d75d1b4d20",
    "HRV-16/Not": "9f3c524331e4",
    "HRV-17": "481b7a5a4f89",
    "HRV-17/Scope": "26538333515b",
    "HRV-17/Not": "18004ee97cb5",
    "HRV-17/Why": "d3808c3e3783",
    "HRV-18": "81264ff3f6d0",
    "HRV-18/Scope": "8e895b601a61",
    "HRV-18/Not": "d8d6479eeb2a",
    "HRV-19": "47c4f894ba02",
    "HRV-19/Scope": "6ec8a9583186",
    "HRV-19/Not": "6bbec61df342",
    "HRV-20": "37174055b80d",
    "HRV-20/Scope": "33a2defa9927",
    "HRV-20/Not": "d218f17bed90",
    "HRV-21": "aa139e59709f",
    "HRV-21/Scope": "36d9f3025f91",
    "HRV-21/Not": "85ca110fca99",
    "HRV-22": "9fe1409c69ac",
    "HRV-22/Scope": "0298748d6c2e",
    "HRV-22/Not": "f785719f483d",
    "HRV-23": "eacc88513e50",
    "HRV-23/Scope": "eef515146df0",
    "HRV-23/Not": "9f62adcf5e2a",
    "HRV-24": "3f1eddde7c88",
    "HRV-24/Scope": "ec48081aa242",
    "HRV-24/Not": "1a43fd5b24c1",
    "HRV-25": "7a7ccb6a7f4a",
    "HRV-25/Scope": "159597698bb3",
    "HRV-25/Not": "9915afb6edc6",
    "HRV-25/Why": "3da33ed3af5b",
    "HRV-26": "84f4b2977e9f",
    "HRV-26/Scope": "65efcfcbc566",
    "HRV-26/Not": "c627af519756",
    "HRV-27": "d8b9809e057a",
    "HRV-27/Scope": "332af08c2f96",
    "HRV-27/Not": "2dda0790beac",
    "HRV-28": "e1766d9f24d7",
    "HRV-28/Scope": "48b63cbecd5a",
    "HRV-28/Not": "464e3a7f028e",
    "HRV-29": "f26cfe401eec",
    "HRV-29/Scope": "58650852e3e0",
    "HRV-29/Not": "add4c8de4e50",
    "HRV-30": "b082fa60338d",
    "HRV-30/Scope": "456e7b3a5613",
    "HRV-30/Not": "4ab8c225ebb2",
    "HRV-30/Why": "f099d87304a2",
    "HRV-31": "f82bad50cf73",
    "HRV-31/Scope": "993f7bc7f0d4",
    "HRV-31/Not": "1d8ab42fc187",
    "HRV-31/Why": "133a6f0017c8",
    "HRV-32": "7732a71b4716",
    "HRV-32/Scope": "47efb08528cb",
    "HRV-32/Not": "ee6b116b56b5",
    "HRV-33": "b2ffa07e56f1",
    "HRV-33/Scope": "5732c4a3c8b3",
    "HRV-33/Not": "81ae6bda4b4f",
    "HRV-34": "e0527a154c24",
    "HRV-34/Scope": "7ad8f02428c0",
    "HRV-34/Not": "1d85bcc986c4",
    "HRV-34/Why": "1f654144da33",
    "HRV-35": "3fd804604fab",
    "HRV-35/Scope": "3c301df1a263",
    "HRV-35/Not": "604d81abe2d3",
    "HRV-36": "9f09b9858281",
    "HRV-36/Scope": "d52fba9bc671",
    "HRV-36/Not": "b1fd3064a6b8",
    "HRV-37": "fec6986aab63",
    "HRV-37/Scope": "61f8b8b467f1",
    "HRV-37/Not": "4b86614dae48",
    "HRV-38": "db2cdad5a137",
    "HRV-38/Scope": "bdcfbec48c04",
    "HRV-38/Not": "e1c045c9fba2",
    "HRV-38/Why": "7c791380eaba",
    "HRV-39": "5064f5f10420",
    "HRV-39/Scope": "ea9877b5a93f",
    "HRV-39/Not": "53ed93d4fd37",
    "HRV-40": "6716a4cd6af3",
    "HRV-40/Scope": "7035b9f2e668",
    "HRV-40/Not": "3b30ea4950f7",
    "HRV-41": "1e89f14f16d2",
    "HRV-41/Scope": "6e38595f378c",
    "HRV-41/Not": "3fb475e37a05",
    "HRV-42": "db1906c8362a",
    "HRV-42/Scope": "26fa62a3a697",
    "HRV-42/Not": "eee87546e809",
    "HRV-43": "6fe7b63a3dc6",
    "HRV-43/Scope": "c706f190a6fe",
    "HRV-43/Not": "6f38e8f32c03",
    "HRV-44": "40259c446cbd",
    "HRV-44/Scope": "7707568447bb",
    "HRV-44/Not": "2b1c8f91caa7",
    "HRV-45": "0048eca30436",
    "HRV-45/Scope": "1aa1f38af6c9",
    "HRV-45/Not": "9b43ccae0cee",
    "HRV-46": "17c95f8e69c0",
    "HRV-46/Scope": "7520b05d1369",
    "HRV-46/Not": "7ca387c59741",
    "HRV-48": "676992b9094c",
    "HRV-48/Scope": "2e415cd24a45",
    "HRV-48/Not": "c409d9677d4b",
    "HRV-49": "19cd427ee28a",
    "HRV-49/Scope": "adfbfae9f814",
    "HRV-49/Not": "0a879f7c5ece",
    "HRV-50": "8ecd5f5df9ea",
    "HRV-50/Scope": "b6517109197d",
    "HRV-50/Not": "3f19a2f7139c",
    "HRV-51": "84ee799fd975",
    "HRV-51/Scope": "0b9f30a89f35",
    "HRV-51/Not": "7d4f2dfeef0d",
    "HRV-52": "86ff4b905f64",
    "HRV-52/Scope": "c14d75c3742f",
    "HRV-52/Not": "f6d7ca383449",
    "HRV-52/Why": "6617f7a0ac54",
    "HRV-53": "0d03ce426146",
    "HRV-53/Scope": "88aa094c4d01",
    "HRV-53/Not": "1d551d5c3908",
    "HRV-54": "848f9d489da6",
    "HRV-54/Scope": "af4d2966ef69",
    "HRV-54/Not": "83a75833c1bf",
    "HRV-55": "e74cfeac42e0",
    "HRV-55/Scope": "128f16079fd5",
    "HRV-55/Not": "149edfccb45d",
    "HRV-56": "5f4a18ee49b9",
    "HRV-56/Scope": "c8c45216a5ae",
    "HRV-56/Not": "31eede38fe9b",
    "HRV-57": "c836edc10b43",
    "HRV-57/Scope": "34f8d49cc160",
    "HRV-57/Not": "c19dfbc7247b",
    "HRV-58": "4b38e934ebc8",
    "HRV-58/Scope": "950539ebad97",
    "HRV-58/Not": "718d168148d2",
    "HRV-59": "cddb6a1cc113",
    "HRV-59/Scope": "131bad36bcfb",
    "HRV-59/Not": "b69a8455091e",
    "HRV-60": "63afb18b3c60",
    "HRV-60/Scope": "006060959152",
    "HRV-60/Not": "475a2cb7a0de",
    "HRV-61": "00239eedaff7",
    "HRV-61/Scope": "76e934782b2c",
    "HRV-61/Not": "211bd285d15d",
    "HRV-62": "82732dbd589a",
    "HRV-62/Scope": "3d73f2c89d44",
    "HRV-62/Not": "f96f795412bd",
    "HRV-63": "f5dda6ba642b",
    "HRV-63/Scope": "39e3a2934d45",
    "HRV-63/Not": "009bb3359461",
    "HRV-64": "d6699ecfa38f",
    "HRV-64/Scope": "419a40f78192",
    "HRV-64/Not": "6c592c76ab10",
    "HRV-65": "b3f5536cd4cb",
    "HRV-65/Scope": "cf16c2648716",
    "HRV-65/Not": "99e62ff2e023",
    "HRV-66": "adbfb1777796",
    "HRV-66/Scope": "516675b0c93e",
    "HRV-66/Not": "1c3717399402",
    "HRV-66/Why": "f86ae8a65382",
    "HRV-67": "eed22294b9d5",
    "HRV-67/Scope": "c37f23a0c3f0",
    "HRV-67/Not": "d64936645691",
    "HRV-68": "68c188fe64fe",
    "HRV-68/Scope": "b12471268f1e",
    "HRV-68/Not": "ddb74b41a3b0",
    "HRV-69": "73ae0408f1ae",
    "HRV-69/Scope": "a27c26543e10",
    "HRV-69/Not": "d6364bfbf3a6",
    "HRV-70": "28224ab49645",
    "HRV-70/Scope": "874613f114da",
    "HRV-70/Not": "bb7b6fcf2a90",
    "HRV-71": "a0b8f06b84f2",
    "HRV-71/Scope": "20c51fd682df",
    "HRV-71/Not": "627c2840e705",
    "HRV-72": "bcbb5ad4409d",
    "HRV-72/Scope": "6ae442139a78",
    "HRV-72/Not": "a40e7afbd69d",
    "HRV-73": "b943ed6b7e63",
    "HRV-73/Scope": "e3140f4fbcf9",
    "HRV-73/Not": "9610caeb3596",
    "HRV-74": "dae142a008ea",
    "HRV-74/Scope": "7b592affba73",
    "HRV-74/Not": "65bd17f87bf9",
    "HRV-75": "c5523b79f0fb",
    "HRV-75/Scope": "0c114d971ab6",
    "HRV-75/Not": "80103ab6de75",
    "HRV-76": "24ed651d0e3d",
    "HRV-76/Scope": "3c58dbb172d7",
    "HRV-76/Not": "93e42a004389",
    "HRV-76/Why": "2d66a8730c0f",
    "HRV-77": "746a26e69ad6",
    "HRV-77/Scope": "6ba6e098d612",
    "HRV-77/Not": "69582122149d",
    "HRV-78": "5342a113382c",
    "HRV-78/Scope": "c6c1d49f2511",
    "HRV-78/Not": "f8cb13a3d622",
    "HRV-79": "59f55e60abf6",
    "HRV-79/Scope": "55a949b973db",
    "HRV-79/Not": "43d9704b9fcc",
    "HRV-80": "ff7d473d318e",
    "HRV-80/Scope": "509dc8740daa",
    "HRV-80/Not": "fbf6b3a1265a",
    "HRV-81": "fcf709831c42",
    "HRV-81/Scope": "b358483ee04d",
    "HRV-81/Not": "81d4fb26bdd1",
    "HRV-82": "c28ec4b212da",
    "HRV-82/Scope": "9e58d80adb2a",
    "HRV-82/Not": "ae5fc5ac916a",
    "HRV-83": "ffda90f684d3",
    "HRV-83/Scope": "09a61b79bc1d",
    "HRV-83/Not": "2cde49728668",
    "HRV-84": "7a0f91ec5fb7",
    "HRV-84/Scope": "202d25511444",
    "HRV-84/Not": "393f305612ff",
    "HRV-85": "9081f6194f1e",
    "HRV-85/Scope": "677977127480",
    "HRV-85/Not": "a4214b223c24",
    "HRV-85/Why": "57e51fc978a3",
    "T-01": "9bdd9c159bd4",
    "T-02": "59cb80cac177",
    "T-03": "128dcb9ecadf",
    "T-04": "281e9636216e",
    "T-05": "7d804dcbeed8",
    "T-06": "b929ab8e30d8",
    "T-07": "aaf9dbc71499",
    "T-08": "6c47411381db",
    "T-09": "56e1217ead9d",
    "T-10": "95811317a1e3",
    "T-11": "b2d6e1b95c71",
    "T-12": "98c25ed721dd",
    "T-13": "3f43ba0aa12d",
    "T-14": "1ebfb600f65c",
    "T-15": "d42b7d3d57dd",
    "T-16": "91a4703cec61",
    "T-17": "de3e34f6f99e",
    "T-18": "c70f5d04b9ef",
    "T-19": "69c497243bc4",
    "T-20": "77a34c675844",
    "T-21": "9b34f424fda7",
    "T-22": "7239237d8256",
    "T-23": "1faeae78af2b",
    "T-24": "982c09e126e0",
    "T-25": "7e93b75a643b",
    "T-26": "a4feff5cba8e",
    "T-27": "96193acf57e1",
    "T-28": "12619b4b4664",
    "T-29": "7c1305033cc9",
    "T-30": "5c8a43064104",
    "T-31": "c3305747107d",
    "T-32": "26cc12f15622",
    "T-33": "1ca03b6623e0",
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
        "Pinned: runcoach-api/tests/test_hrv25_population.py::test_hrv_25_population_is_counted_and_does_not_grow",
    )),
    "C07": ("PRIN-14", ("forbidden direction",)),
    "C32": ("HRV-07", ("max(0.5 · SD(ln rMSSD), 0.01)",)),
    "C33": ("PRIN-12", (
        "baseline_days", "min_baseline_readings", "min_window_readings", "gap_reset_days", "band_floor",
        "swc_factor", "recency_tolerance_days",
        "Pinned: runcoach-api/tests/test_hrv_trend_endpoint.py::test_the_recency_skip_is_recomputable_from_the_response_at_the_exact_boundary",
    )),
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
    r"^- \*\*(?P<id>H-\d{2})\*\* \((?P<dates>20\d{2}-\d{2}-\d{2}(?:, 20\d{2}-\d{2}-\d{2})*|undated)\) (?P<what>\S.*) → "
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


def history_dates(text: str) -> dict[str, frozenset[str]]:
    """``{date: H-NNs}``: each ISO date in a well-formed history entry's date list, mapped to every entry
    whose list carries it (T193 item 3, G2). An undated entry carries none, and a line ``history_errors``
    rejects adds nothing."""
    dates: dict[str, set[str]] = {}
    for line in _lines(text):
        if m := _HISTORY_ENTRY.match(line):
            for date in _ISO_DATE.findall(m.group("dates")):
                dates.setdefault(date, set()).add(m.group("id"))
    return {date: frozenset(entries) for date, entries in dates.items()}


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


def proxy_errors(sentence: str, block: str, dates: dict[str, frozenset[str]]) -> list[str]:
    """AC9 and R6: an unchanged row's inventory sentence survives in its mapped block(s). Date
    expressions are removed first, and for each date the block must cite an ``H-NN`` whose history
    entry carries that date (``dates``, the history's ``history_dates``; T193 item 3, G2: any H-NN
    passed before). Then every number (``\\d+(?:\\.\\d+)?``, after ``−`` and ``–`` fold to ``-``), every
    backticked identifier (exactly) and each quantifier (``QUANTIFIERS``, whole words, any case) must
    appear in the block."""
    errors = []
    undated = _ISO_DATE.sub(" ", sentence)
    cited = set(_H_ID.findall(block))
    for date in dict.fromkeys(_ISO_DATE.findall(sentence)):
        entries = dates.get(date, frozenset())
        if not cited & entries:
            errors.append(f"[proxy] the sentence carries the date {date}, and the block cites no H-NN whose "
                          f"history entry carries it ({sorted(entries)}; R6)")
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
    only the rules whose prefix is in that group.

    T192 (R13, G1): with no ``group``, the Glossary joins the critic scope, one row per T-NN key of the
    Glossary section (``glossary_entries``), after the rules, each once. A Glossary sits in no group."""
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
    if group is None:
        need += list(dict.fromkeys(key for key, _lines_of_key in glossary_entries(research_text)[0]))
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


def split_numbering_errors(rows: list[dict[str, str]]) -> list[str]:
    """AC3 and R6 "a split row keeps its ID for its first rule; the others take the next free number in
    the prefix" (T193 item 4, G3), from the table and ``INVENTORY_IDS``. For each prefix, the rule IDs
    the new-ID cells name that are not inventory IDs are exactly the numbers after the prefix's highest
    inventory ID, with none skipped; and a row naming two or more rule IDs (a split) names its own
    inventory ID. Before T193 only the frozen snapshots held either, so a regenerated snapshot cleared a
    skipped number or a split that dropped its ID."""
    errors = []
    top: dict[str, int] = {}
    for i in INVENTORY_IDS:
        top[_prefix(i)] = max(top.get(_prefix(i), 0), int(i.rsplit("-", 1)[1]))
    new = sorted({i for r in rows for i in _new_rule_ids(r)} - INVENTORY_IDS, key=_order_key)
    for prefix in PREFIX_ORDER:
        have = [i for i in new if _prefix(i) == prefix]
        want = [f"{prefix}-{n:02d}" for n in range(top.get(prefix, 0) + 1, top.get(prefix, 0) + 1 + len(have))]
        if [_order_key(i) for i in have] != [_order_key(i) for i in want]:
            errors.append(f"[ids] {prefix}: the new IDs {have} are not {want}, numbered on from the inventory's "
                          f"highest, {prefix}-{top.get(prefix, 0):02d} (AC3: the next free number in the prefix)")
    for row in rows:
        inv, named = row["inventory ID"], _new_rule_ids(row)
        if not _is_blank(inv) and len(named) > 1 and inv not in named:
            errors.append(f"[ids] {inv}: a split row names {named} and not its own ID (AC3: a split row keeps its "
                          "ID for its first rule)")
        elif not _is_blank(inv) and len(named) > 1 and named[0] != inv:
            errors.append(f"[ids] {inv}: a split row names {named} and its own ID is not first (AC3 and R6: a split "
                          "row keeps its ID for its first rule)")
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
    two blocks), is a problem, not a line.

    T192 (R13, G1): a ``T-NN`` label is that term's Glossary line (``glossary_entries``), so a Glossary
    line changed after its verdict reds like a block line. A term keyed on more than one line is a
    problem, and so is a Glossary line with no T-NN key, which no verdict could bind."""
    blocks = {rule_id: block.split("\n") for rule_id, block in rule_blocks(research_text).items()}
    has_glossary = _glossary_span(_lines(research_text)) is not None
    glossary, glossary_problems = glossary_entries(research_text) if has_glossary else ([], [])
    terms: dict[str, list[str]] = {}
    for key, key_lines in glossary:
        terms.setdefault(key, []).extend(key_lines)
    lines, problems = {}, list(glossary_problems)
    for label in required_review_rows(research_text, rows):
        if label in terms:
            if len(terms[label]) == 1:
                lines[label] = terms[label][0]
            else:
                problems.append(f"[reviewed-block] {label}: {len(terms[label])} Glossary lines, not one")
            continue
        rule_id, _, kind = label.partition("/")
        block = blocks.get(rule_id, [])
        hits = [line for line in block if (_RULE_LINE.match(line) if not kind
                                           else (m := _SUB_LINE.match(line)) and m.group("kind") == kind)]
        if len(hits) == 1:
            lines[label] = hits[0]
        else:
            problems.append(f"[reviewed-block] {label}: {len(hits)} block lines, not one")
    return lines, problems


def reviewed_block_errors(research_text: str, rows: list[dict[str, str]], review_text: str) -> list[str]:
    """Each meaning-review verdict is bound to the text it judged (iteration 4, M3, ruled 2026-09-26; made
    mechanical by T192 under R13), both ways: the labels of ``reviewed_block_lines`` are exactly the rows
    with a verdict line in ``review_text``, and each line's ``_cell_digest`` is the digest that row's
    verdict line records (``review_digests``). The judged digest is read from the critic-owned review
    file and never from a literal in this file, so regenerating every literal here clears nothing. No
    message prints a digest: a changed block reds until a fresh critic writes a new verdict for it."""
    lines, errors = reviewed_block_lines(research_text, rows)
    judged = review_digests(review_text)
    required = set(required_review_rows(research_text, rows))
    errors += [f"[reviewed-block] {label} has a verdict in 00-meaning-review.md and is not a required review row"
               for label in sorted(judged.keys() - required)]
    for label, line in lines.items():
        if label not in judged:
            errors.append(f"[reviewed-block] {label} needs a critic verdict: no verdict line in "
                          f"00-meaning-review.md records the text it judged (R7)")
        elif _cell_digest(line) != judged[label]:
            errors.append(f"[reviewed-block] {label} changed after its verdict: a fresh critic must write a new "
                          f"verdict for {label} in 00-meaning-review.md (R7)")
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
                      frozen: dict[str, str], then: str, paste: bool = True) -> list[str]:
    """The iteration-5 binding, both ways, shared by ``PINNED_SHA256``, ``HISTORY_SHA256`` and
    ``REVIEW_LINE_SHA256``: each key occurs once, every frozen key is in ``where`` and every key in
    ``where`` is frozen, and each key's ``_keyed_digest`` is its frozen digest. A mismatch names the key
    and the first of its lines that differs; every message says what must happen first (``then``) and,
    with ``paste``, prints the new digest to paste. Without ``paste`` (T192, the review lines) a message
    prints no digest and no line text, since a verdict line carries its judged digest."""
    keys = [key for key, _lines_of_key in entries]
    errors = [f"[{tag}] {key} occurs {n} times in {where}, not once" for key, n in Counter(keys).items() if n > 1]
    errors += [f"[{tag}] {key} is frozen in {literal} and is not in {where}" for key in sorted(frozen.keys() - set(keys))]
    for key, lines in entries:
        digest = _keyed_digest(lines)
        if key not in frozen:
            errors.append(f"[{tag}] {key} is not in {literal} (a line was added or re-keyed); {then}"
                          + (f", {literal}[{key!r}] = {digest!r}" if paste else ""))
        elif digest != frozen[key]:
            if not paste:
                errors.append(f"[{tag}] {key}: the line changed; {then}")
                continue
            have, want = digest.split("."), frozen[key].split(".")
            first = _first_difference(have, want)
            now = repr(lines[first][:120]) if first < len(lines) else "no such line: a line was removed"
            errors.append(f"[{tag}] {key}: the line changed (now {now}); {then}, {literal}[{key!r}] = {digest!r}")
    return errors


def glossary_entries(research_text: str) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """``([(T-NN, [line])], problems)`` over the Glossary section, in order (iteration 5, M1). A
    non-blank line with no T-NN key is a problem, so no Glossary line is unbound.

    The T-NN lines are normative IS definitions outside every rule block: T-13's 14 days became 21,
    T-03's ``>=`` became ``>`` and T-05's HRV Status became a tier, with the suite green (iteration 5).
    Since T192 (R13, G1) each is a required review row, bound by the digest its verdict line records
    (``reviewed_block_errors``); ``GLOSSARY_SHA256`` is retired."""
    lines = _lines(research_text)
    span = _glossary_span(lines)
    if span is None:
        return [], [f"[reviewed-block] research/00 has no {GLOSSARY_HEADING!r} section"]
    entries, problems = [], []
    for number in range(*span):
        line = lines[number]
        if not line.strip():
            continue
        if m := _GLOSSARY_KEY.match(line):
            entries.append((m.group("id"), [line]))
        else:
            problems.append(f"[reviewed-block] line {number + 1}: a Glossary line with no T-NN key cannot be "
                            f"bound: {line[:120]!r}")
    return entries, problems


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


#: F011 S4: the one path an F009-owned exception may name.
EXCEPTION_F009_PATH = "runcoach-api/src/runcoach_api/metrics/hrv_trend.py"
#: F011 S4: the C33 sites F010 AC2 lists (both contract copies and spec/03), frozen 2026-09-28 from
#: spec/features/F010-publish-recency-tolerance.md AC2. ``hrv_trend.py`` is F009's, not F010's.
EXCEPTION_F010_PATHS = frozenset({
    "contracts/openapi.yaml",
    "runcoach-api/src/runcoach_api/schemas.py",
    "specification/spec/03-derived-metric-formulas.md",
})


def exceptions_shape_errors(exceptions) -> list[str]:
    """F011 S4's shape for ``EXCEPTIONS``: each entry is a ``(path, excerpt, owner)`` triple of
    strings, ``owner`` is F009 (path exactly ``EXCEPTION_F009_PATH``) or F010 (path in
    ``EXCEPTION_F010_PATHS``), and the excerpt is non-empty. One error per fault. Whether an exception
    shelters a hit is the downstream gate's check (T199), not this one."""
    errors = []
    for i, entry in enumerate(exceptions):
        if not (isinstance(entry, tuple) and len(entry) == 3 and all(isinstance(x, str) for x in entry)):
            errors.append(f"[exceptions] #{i}: not a (path, excerpt, owner) triple of strings: {entry!r}")
            continue
        path, excerpt, owner = entry
        if owner == "F009":
            if path != EXCEPTION_F009_PATH:
                errors.append(f"[exceptions] #{i}: owner F009 may name only {EXCEPTION_F009_PATH}, not {path}")
        elif owner == "F010":
            if path not in EXCEPTION_F010_PATHS:
                errors.append(f"[exceptions] #{i}: owner F010 may name only the C33 sites of F010 AC2, "
                              f"not {path}")
        else:
            errors.append(f"[exceptions] #{i}: owner {owner!r} is not F009 or F010")
        if not excerpt.strip():
            errors.append(f"[exceptions] #{i}: empty excerpt")
    return errors


def _proxy_rows(rows: list[dict[str, str]], research_text: str, dates: dict[str, frozenset[str]],
                only_within: set[str] | None = None) -> list[str]:
    """The AC9 proxy on every ``no`` row with an inventory sentence, over the blocks it names, with
    ``dates`` the history's ``history_dates``. With ``only_within``, a row naming a rule outside that set
    is left to the assembled check."""
    errors, blocks = [], rule_blocks(research_text)
    for row in rows:
        new = _new_rule_ids(row)
        if row["meaning changed"] != "no" or _is_blank(row["inventory sentence"]) or not new:
            continue
        if only_within is not None and not set(new) <= only_within:
            continue
        mapped = "\n".join(blocks[i] for i in new if i in blocks)
        errors += [f"{e} ({row['inventory ID']})" for e in proxy_errors(row["inventory sentence"], mapped, dates)]
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
    errors += _proxy_rows(rows, a.research, history_dates(a.history))
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
    # A draft's dated sentence cites the committed history's entries (H-01..H-41, as above).
    errors += _proxy_rows(rows, rules, history_dates(_REAL_HISTORY.read_text(encoding="utf-8")), only_within=here)
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
    "PRIN-12": ("Rule PRIN-12 MUST hold every day and serves `baseline_days`, `min_baseline_readings`, "
                "`min_window_readings`, `gap_reset_days`, `band_floor`, `swc_factor` and `recency_tolerance_days`."),
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
    "PRIN-12": (("Pinned: runcoach-api/tests/test_hrv_trend_endpoint.py::"
                "test_the_recency_skip_is_recomputable_from_the_response_at_the_exact_boundary"),),
    "PRIN-15": (("Pinned: runcoach-api/tests/test_hrv25_population.py::"
                "test_hrv_25_population_is_counted_and_does_not_grow"),),
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
             "C-number outside NO_ONLY or one of ['HRV-11', 'R13', 'T-07', 'T-27'] (R3, M2)"),
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
             "C-number outside NO_ONLY or one of ['HRV-11', 'R13', 'T-07', 'T-27'] (R3, M2)"),
             ("[decision] REG-02: old-meaning key 'T07-acwr-band' records decision 'T-07', which the row's "
             "cell 'T-08' does not cite (M2, M1: a key cannot be borrowed)")],
            id="m2-unfrozen-non-c-token"),
        # Iteration 2, M1: scanner A's DOC-03 case. "1–4" -> "1–3" under R13, with an R13 key: R13
        # authorizes a yes on PRIN-12 alone, and the key is not borrowed (its decision is R13).
        pytest.param(
            _row("DOC-03", "s", "DOC-03", "R13", "yes", "HRV-01-R13-four-tier-hierarchy"),
            # Iteration 3, S1: the key records R13, which the cell cites, but it is HRV-01's key.
            [("[decision] DOC-03: R13 authorizes a yes only on ['HRV-31', 'PRIN-12'], not on DOC-03 (R3, M1: "
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
    "R13": frozenset({"HRV-31", "PRIN-12"}),
    "T-07": frozenset({"GATE-03", "REG-02", "REG-16", "REG-19"}),
    "T-27": frozenset({}),
}


#: A second copy of ``KEY_OWNERS``, which the pin test compares the map against (sprint-007 review
#: iteration 4, S2), as ``_NON_C_AUTHORITIES_PIN`` does for its map: checked only against the table,
#: the map and the table could widen together. Derived 2026-09-26 from the committed table at
#: 790ea0c: 54 keys, all of ``OLD_MEANINGS``, 58 since T223; one shared, by HRV-04 and REG-09.
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
    "HRV-31-R13-broad-withhold-of-any-verdict": frozenset({"HRV-31"}),
    "HRV-42-R13-reset-in-force-persists-through-it": frozenset({"HRV-42"}),
    "PRIN-05-C06-conservative-wins-unscoped": frozenset({"PRIN-05"}),
    "PRIN-08-C24-sidecar-ignored-by-default": frozenset({"PRIN-08"}),
    "PRIN-08-C25-rule-file-short-list": frozenset({"PRIN-08"}),
    "PRIN-10-C19-reduced-confidence": frozenset({"PRIN-10"}),
    "PRIN-12-C33-tolerance-not-published": frozenset({"PRIN-12"}),
    "PRIN-12-R13-withheld-response-stays-reproducible": frozenset({"PRIN-12"}),
    "PRIN-12-R13-reproducible-by-hand-without-exceptions": frozenset({"PRIN-12"}),
    "PRIN-14-C07-weak-evidence-only": frozenset({"PRIN-14"}),
    "PRIN-15-C06-accepted-as-priced": frozenset({"PRIN-15"}),
    "PRIN-16-C08-silence-tolerated-freely": frozenset({"PRIN-16"}),
    "T07-acwr-band": frozenset({"REG-02"}),
    "T07-ctl-rise-band": frozenset({"REG-19"}),
    "T07-tolerance-band": frozenset({"GATE-03"}),
    "T07-tsb-target-form-band": frozenset({"REG-16"}),
    "T-27-ladder-order-for-the-loop": frozenset({"ARB-01"}),
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
    errors = proxy_errors(sentence, block, _PROXY_DATES)
    print(f"[slice compared] {sentence!r} vs {block!r}: {errors}")
    assert any(fragment in e for e in errors), errors


#: A synthetic ``history_dates`` map: 2026-09-18 is carried by two entries, as H-18 and H-20 carry it.
_PROXY_DATES = {"2026-09-09": frozenset({"H-09"}), "2026-09-18": frozenset({"H-18", "H-20"})}


def test_proxy_errors_is_green_when_every_token_survives() -> None:
    sentence = ("Every count uses `min_baseline_readings` = 14, 21 does not reset (2026-09-18), and it is never CV "
                "− only 0.5·SD.")
    block = ("**HRV-09.** Every count MUST use `min_baseline_readings` = 14, so 21 does not reset and it is never CV, "
             "only 0.5·SD.\nScope: every count.\nNot: the 2 CV forms.\nPinned: none\nWhy: user decision H-18.")
    errors = proxy_errors(sentence, block, _PROXY_DATES)
    print(f"[slice compared] {errors}")
    assert errors == []
    assert proxy_errors("Every day is judged – not 7−1.", "**HRV-08.** Every day MUST be judged, 7-1 excepted.",
                        _PROXY_DATES) == []
    assert proxy_errors("Everything is judged.", "**HRV-08.** All is judged.", _PROXY_DATES) == []


def test_proxy_errors_needs_the_h_nn_whose_history_entry_carries_the_date() -> None:
    """T193 item 3 (G2; R6 "a rule that rests on a dated decision points to its H-NN"): a dated sentence's
    block must cite an H-NN whose history entry's date list carries that date, not any H-NN. Before
    T193 a block citing an unrelated H-NN passed. Each message exactly; a date no entry carries names
    no entry; each date of a two-date sentence is checked on its own; the second entry carrying a date
    serves as well as the first."""
    sentence, two = "Left unchanged (2026-09-18).", "Set (2026-09-09) and kept (2026-09-18)."
    block = "**HRV-33.** It MUST stay unchanged.\nScope: every day.\nNot: a gap.\nPinned: none\nWhy: user decision {}."
    red_18 = ("[proxy] the sentence carries the date 2026-09-18, and the block cites no H-NN whose history entry "
              "carries it (['H-18', 'H-20']; R6)")
    _check_cases(proxy_errors, {
        "an-unrelated-h-nn": ((sentence, block.format("H-09"), _PROXY_DATES), [red_18]),
        "no-h-nn": ((sentence, block.format("the user"), _PROXY_DATES), [red_18]),
        "a-date-no-entry-carries": (("Left unchanged (2026-09-19).", block.format("H-18"), _PROXY_DATES), [
            ("[proxy] the sentence carries the date 2026-09-19, and the block cites no H-NN whose history entry "
             "carries it ([]; R6)")]),
        "one-of-two-dates-unmatched": ((two, block.format("H-09"), _PROXY_DATES), [red_18]),
        "the-second-entry-carrying-it": ((sentence, block.format("H-20"), _PROXY_DATES), []),
        "both-dates-matched": ((two, block.format("H-09, H-18"), _PROXY_DATES), []),
    })


def test_history_dates_maps_each_date_to_every_entry_carrying_it() -> None:
    """T193 item 3: ``history_dates`` reads the date list of each well-formed entry, so a multi-date
    entry is under each of its dates, an undated entry is under none, and a ``## Retired IDs`` line or
    a malformed entry (``history_errors``' finding) adds nothing."""
    history = "\n".join([
        "# research/00 history", "",
        "- **H-01** (undated) A note. → DOC-01",
        "- **H-02** (2026-09-16, 2026-09-18) A change. → HRV-01, HRV-02",
        "- **H-03** (2026-09-18) Another change. → HRV-03",
        "- **H-04** 2026-09-19 no parentheses. → HRV-04", "",
        _RETIRED_HEADING, "", "- **PRIN-16** retired → H-03", ""])
    got = history_dates(history)
    print(f"[slice compared] {got}")
    assert got == {"2026-09-16": frozenset({"H-02"}), "2026-09-18": frozenset({"H-02", "H-03"})}


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
    # T192 (G1): with a Glossary, each T-NN is a row, once, after the rules; a group holds none.
    glossed = f"{HEADINGS[0]}\n\n{GLOSSARY_HEADING}\n\n{_glossary(3)}\n{HEADINGS[1]}\n\n{research}"
    with_terms = required_review_rows(glossed, rows)
    doubled = required_review_rows(_one_edit(glossed, "- **T-03 ", "- **T-02 again** IS twice.\n- **T-03 "), rows)
    print(f"[slice compared] {with_terms}; doubled T-02 {doubled[-3:]}")
    assert with_terms == [*need, "T-01", "T-02", "T-03"] and doubled == with_terms
    assert required_review_rows(glossed, rows, group="hrv") == need[4:]


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
    research, history, rows = _real()
    dates = history_dates(history)
    errors = _proxy_rows(rows, research, dates) + inventory_sentence_errors(rows)
    checked = sum(1 for r in rows if r["meaning changed"] == "no" and not _is_blank(r["inventory sentence"]))
    frozen = sum(1 for r in rows if r["inventory ID"] in INVENTORY_SENTENCE_SHA256)
    blocks = rule_blocks(research)
    dated = {r["inventory ID"]: (_ISO_DATE.findall(r["inventory sentence"]),
                                 sorted({h for i in _new_rule_ids(r) for h in _H_ID.findall(blocks.get(i, ""))}))
             for r in rows if r["meaning changed"] == "no" and _ISO_DATE.search(r["inventory sentence"])}
    print(f"[slice compared] AC9 proxy over {checked} no rows, {frozen} sentence cells against the frozen "
          f"hashes; dated no rows (dates, H-NN cited) {dated}, entries per date "
          f"{ {d: sorted(dates.get(d, ())) for ds, _h in dated.values() for d in ds} }: {errors[:10]}")
    assert errors == []
    assert checked > 0 and dated
    assert frozen == len(INVENTORY_SENTENCE_SHA256) == 149


def test_real_path_ac9_a_dated_row_citing_an_unrelated_h_nn_is_red() -> None:
    """T193 item 3 on the committed text: the first ``no`` row whose inventory sentence carries a date
    (chosen by that property, not by ID) passes, and with every H-NN its block cites replaced by an H-NN
    whose entry carries none of the sentence's dates, the AC9 proxy names each date."""
    research, history, rows = _real()
    dates = history_dates(history)
    row = next(r for r in rows if r["meaning changed"] == "no" and _ISO_DATE.search(r["inventory sentence"])
               and _new_rule_ids(r))
    carried = set(_ISO_DATE.findall(row["inventory sentence"]))
    unrelated = next(h for h in history_ids(history) if not any(h in dates.get(d, ()) for d in carried))
    blocks = rule_blocks(research)
    mapped = "\n".join(blocks[i] for i in _new_rule_ids(row) if i in blocks)
    swapped = _H_ID.sub(unrelated, mapped)
    errors = proxy_errors(row["inventory sentence"], swapped, dates)
    print(f"[slice compared] {row['inventory ID']}: dates {sorted(carried)}, cited {sorted(set(_H_ID.findall(mapped)))}"
          f" -> {unrelated}: {errors}")
    assert proxy_errors(row["inventory sentence"], mapped, dates) == []
    assert errors == [f"[proxy] the sentence carries the date {d}, and the block cites no H-NN whose history entry "
                      f"carries it ({sorted(dates.get(d, ()))}; R6)" for d in dict.fromkeys(
                          _ISO_DATE.findall(row["inventory sentence"]))]


def _real_row_swap(rows: list[dict[str, str]], inv: str, **cells: str) -> list[dict[str, str]]:
    assert sum(r["inventory ID"] == inv for r in rows) == 1, inv
    return [dict(r, **cells) if r["inventory ID"] == inv else r for r in rows]


def test_real_path_every_traceability_row_is_the_frozen_row() -> None:
    """Iteration 4, M1 and M2: the committed table is ``TRACEABILITY_ROW_SHA256``, row by row and cell
    by cell, with no row added or removed.

    T193 item 1: the inventory rows are keyed by exactly ``INVENTORY_IDS``, each once, and any other row
    is an addition keyed ``addition <new ID(s)>`` (AC6 allows them); the table's keys are the literal's
    keys. A bare count of 149 table rows turned the sprint-007 critic's FIG-12 addition red with no hint."""
    _research, _history, rows = _real()
    errors = traceability_row_errors(rows)
    keys = [_row_key(r) for r in rows]
    inventory = [_row_key(r) for r in rows if not _is_blank(r["inventory ID"])]
    additions = [_row_key(r) for r in rows if _is_blank(r["inventory ID"])]
    sample = rows[0]
    print(f"[slice compared] {len(rows)} table rows, {len(set(keys))} keys ({len(inventory)} inventory, additions "
          f"{additions}), {len(TRACEABILITY_ROW_SHA256)} frozen; {_row_key(sample)} {row_digest(sample)} vs "
          f"{TRACEABILITY_ROW_SHA256.get(_row_key(sample))}: {errors[:5]}")
    assert errors == []
    assert len(set(keys)) == len(keys) and set(keys) == set(TRACEABILITY_ROW_SHA256)
    assert sorted(inventory) == sorted(INVENTORY_IDS)
    assert all(k.startswith("addition ") for k in additions)


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
    assert lines[-1] == "" and len(lines) - 3 == len(rows) == len(TRACEABILITY_ROW_SHA256)


def test_real_path_ac3_new_ids_number_on_from_the_inventory_and_each_split_keeps_its_id() -> None:
    """T193 item 4 (G3, AC3): over the committed table, each prefix's non-inventory IDs run on from its
    highest inventory ID, and every split row names its own ID. Before T193 only the frozen snapshots
    held this."""
    _research, _history, rows = _real()
    errors = split_numbering_errors(rows)
    splits = sum(1 for r in rows if not _is_blank(r["inventory ID"]) and len(_new_rule_ids(r)) > 1)
    new = sorted({i for r in rows for i in _new_rule_ids(r)} - INVENTORY_IDS, key=_order_key)
    print(f"[slice compared] {splits} split rows, {len(new)} new IDs from {new[0]} to {new[-1]}: {errors}")
    assert errors == []
    assert splits > 0 and new


def test_split_numbering_errors_names_a_gap_and_a_split_that_drops_its_own_id() -> None:
    """T193 item 4 (G3), on synthetic rows over FIG, whose highest inventory ID is FIG-05, each message
    exactly: a new ID that skips a number; a new ID at or below the inventory's highest that is not an
    inventory ID; a split row that names its new IDs and drops its own; and (review cycle 2, C2) a split
    row that names its own ID second, not first."""
    top = max((i for i in INVENTORY_IDS if _prefix(i) == "FIG"), key=_order_key)
    assert top == "FIG-05" and "FIG-00" not in INVENTORY_IDS
    fig02, fig03 = _row("FIG-02", "s", "FIG-02, FIG-06, FIG-07"), _row("FIG-03", "s", "FIG-03, FIG-08")
    addition = _row(ADDITION, ADDITION, "FIG-09", "C09")
    rows = [_as_row(line) for line in (fig02, fig03, addition)]
    assert split_numbering_errors(rows) == []
    gap = [*rows[:2], _as_row(_row(ADDITION, ADDITION, "FIG-10", "C09"))]
    below = [*rows, _as_row(_row(ADDITION, ADDITION, "FIG-00", "C09"))]
    dropped = [rows[0], _as_row(_row("FIG-03", "s", "FIG-08, FIG-10")), rows[2]]
    _check_cases(split_numbering_errors, {
        "a-gap": ((gap,), [
            ("[ids] FIG: the new IDs ['FIG-06', 'FIG-07', 'FIG-08', 'FIG-10'] are not ['FIG-06', 'FIG-07', "
             "'FIG-08', 'FIG-09'], numbered on from the inventory's highest, FIG-05 (AC3: the next free number "
             "in the prefix)")]),
        "a-new-id-below-the-inventorys-highest": ((below,), [
            ("[ids] FIG: the new IDs ['FIG-00', 'FIG-06', 'FIG-07', 'FIG-08', 'FIG-09'] are not ['FIG-06', "
             "'FIG-07', 'FIG-08', 'FIG-09', 'FIG-10'], numbered on from the inventory's highest, FIG-05 (AC3: "
             "the next free number in the prefix)")]),
        "a-split-that-drops-its-own-id": ((dropped,), [
            ("[ids] FIG-03: a split row names ['FIG-08', 'FIG-10'] and not its own ID (AC3: a split row keeps "
             "its ID for its first rule)")]),
        "c2-own-id-second": (([rows[0], _as_row(_row("FIG-03", "s", "FIG-08, FIG-03")), rows[2]],), [
            ("[ids] FIG-03: a split row names ['FIG-08', 'FIG-03'] and its own ID is not first (AC3 and R6: a "
             "split row keeps its ID for its first rule)")]),
    })


def test_table_line_errors_names_a_line_that_is_not_a_row() -> None:
    """Iteration 6, S1, on synthetic text shaped like the scanner's routes, each message exactly: a
    correction line after the last row (U1c), a blockquoted contradicting row (U1b), the separator deleted
    (U2), and the header edited; and one case per clause of ``table_line_errors``'s row test (Stage 0.5): a
    row with a leading space (``startswith("|")``), ``TRACE_HEADER`` repeated after a row (the header
    clause) and ``TRACE_SEPARATOR`` repeated after a row (the separator clause). Dropping any one clause
    left this test green before those three. ``parse_traceability`` returns the same rows for each route
    that keeps the header and separator, so no row check can see them."""
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
        # Stage 0.5: one route per clause of the row test, so dropping any one clause reds a case.
        "indented": _one_edit(base, rows[1] + "\r\n", " " + rows[1] + "\r\n"),
        "header-again": base + TRACE_HEADER + "\r\n",
        "separator-again": base + TRACE_SEPARATOR + "\r\n",
    }
    for name in ("u1c", "u1b", "indented", "header-again", "separator-again"):
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
        "a-row-with-a-leading-space": ((routes["indented"],), [
            f"[frozen-table] line 4: a table line that is not a row cannot be bound: {(' ' + rows[1])[:120]!r}"]),
        "the-header-repeated-after-a-row": ((routes["header-again"],), [
            f"[frozen-table] line 5: a table line that is not a row cannot be bound: {TRACE_HEADER[:120]!r}"]),
        "the-separator-repeated-after-a-row": ((routes["separator-again"],), [
            f"[frozen-table] line 5: a table line that is not a row cannot be bound: {TRACE_SEPARATOR[:120]!r}"]),
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
    assert set(OLD_MEANING_SHA256) == set(_OM.OLD_MEANINGS) and len(OLD_MEANING_SHA256) == 58


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
    key the table names exists, and ``EXCEPTIONS`` has F011 S4's triple shape."""
    research, _history, rows = _real()
    errors = old_meaning_errors(_OM.OLD_MEANINGS, research)
    named = {k for r in rows for k in _keys(r)}
    print(f"[slice compared] {len(_OM.OLD_MEANINGS)} keys, {len(named)} named by the table: {errors[:10]}")
    assert errors == []
    assert _OM.OLD_MEANINGS and named <= set(_OM.OLD_MEANINGS)
    orphans = sorted(set(_OM.OLD_MEANINGS) - named)
    assert not orphans, f"OLD_MEANINGS keys no traceability row names (S5): {orphans}"
    shape = exceptions_shape_errors(_OM.EXCEPTIONS)
    print(f"[slice compared] {len(_OM.EXCEPTIONS)} EXCEPTIONS triples: {shape}")
    assert shape == []


def test_exceptions_shape_rejects_each_wrong_shape() -> None:
    """F011 S4: each wrong triple gives exactly one error naming its fault, and a well-formed triple
    for each owner gives none."""
    hrv = "runcoach-api/src/runcoach_api/metrics/hrv_trend.py"
    good = [(hrv, "does not echo", "F009"), ("contracts/openapi.yaml", "these six", "F010"),
            ("runcoach-api/src/runcoach_api/schemas.py", "these six", "F010"),
            ("specification/spec/03-derived-metric-formulas.md", "gains no key", "F010")]
    print(f"[slice compared] good: {exceptions_shape_errors(good)}")
    assert exceptions_shape_errors(good) == []
    bad = {
        "owner F011": ((hrv, "does not echo", "F011"), "owner"),
        "F009 on main.py": (("runcoach-api/src/runcoach_api/main.py", "does not echo", "F009"), "F009"),
        "F010 outside AC2": (("specification/spec/01-data-model.md", "these six", "F010"), "F010"),
        "empty excerpt": ((hrv, "", "F009"), "excerpt"),
        "2-tuple": ((hrv, "F009"), "triple"),
    }
    for label, (triple, word) in bad.items():
        errors = exceptions_shape_errors([triple])
        print(f"[slice compared] {label}: {errors}")
        assert len(errors) == 1 and word in errors[0], label
    assert len(exceptions_shape_errors([t for t, _ in bad.values()] + good)) == len(bad)


# ---------------------------------------------------------------------------
# The meaning review (T180, R7/AC9): one verdict per required row, under its group, none differs
# ---------------------------------------------------------------------------

_REAL_REVIEW = _REPO_ROOT / "specification" / "research" / "00-meaning-review.md"
REVIEW_HEADER = "| row | verdict | digest | reason |"
REVIEW_HEADINGS = {"doc-goal": "## DOC–GOAL", "arch-dec": "## ARCH–DEC", "hrv": "## HRV"}
_VERDICTS = ("same", "differs")
#: A verdict line's digest cell (T192): the ``_cell_digest`` of the block line the verdict judged, bare
#: lowercase hex, so the review file stays spanless (``INSIDE_ARM_ABSTAINING``).
_JUDGED_DIGEST = re.compile(r"[0-9a-f]{12}")
#: A Glossary row's label (T192, G1): its verdict line sits under the review's own ``## Glossary`` table.
_GLOSSARY_LABEL = re.compile(r"T-\d{2}")
REVIEW_GLOSSARY_HEADING = GLOSSARY_HEADING


def _review_heading(row: str) -> str | None:
    """The review heading a verdict line for ``row`` sits under: its group's, or the Glossary's."""
    if _GLOSSARY_LABEL.fullmatch(row):
        return REVIEW_GLOSSARY_HEADING
    return REVIEW_HEADINGS.get(_GROUP_OF.get(_prefix(row.split("/", 1)[0]), ""))


def review_digests(review_text: str) -> dict[str, str]:
    """``{row: judged digest}`` from each verdict line of a review file (T192): the third cell of every
    four-cell table line that is not the header or a separator. A repeated row keeps its last line;
    ``review_errors`` reports the repeat, the cell count and a malformed digest."""
    judged = {}
    for line in _lines(review_text):
        if not line.startswith("|") or line.strip() == REVIEW_HEADER:
            continue
        cells = _split_cells(line)
        if len(cells) == 4 and not _is_separator(cells):
            judged[cells[0]] = cells[2]
    return judged


def review_errors(review_text: str, required: list[str]) -> list[str]:
    """R7's coverage over a review file: every ``required`` row has exactly one verdict line, no
    verdict line names a row outside ``required``, every verdict is ``same`` or ``differs`` and none
    is ``differs``, every verdict line carries a judged digest (T192), every line sits under its
    group's heading, and each group's table opens with ``REVIEW_HEADER``. A Glossary row (T192, G1)
    sits under ``## Glossary``, whose table is required once a Glossary row is."""
    errors: list[str] = []
    group_of_heading = {h: g for g, h in REVIEW_HEADINGS.items()}
    if any(_GLOSSARY_LABEL.fullmatch(row) for row in required):
        group_of_heading[REVIEW_GLOSSARY_HEADING] = "glossary"
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
        if len(cells) != 4:
            errors.append(f"[review] line {number}: {len(cells)} cells, not 4: {line[:100]!r}")
            continue
        row, verdict, digest, reason = cells
        counts[row] += 1
        if verdict not in _VERDICTS:
            errors.append(f"[review] {row}: verdict {verdict!r} is neither same nor differs")
        if not _JUDGED_DIGEST.fullmatch(digest):
            errors.append(f"[review] {row}: digest {digest!r} is not 12 lowercase hex digits")
        if verdict == "differs":
            errors.append(f"[review] {row} differs: {reason[:160]}")
        if not reason:
            errors.append(f"[review] {row}: no reason")
        want = _review_heading(row)
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
                       frozen_prose: tuple[str, ...] | None = None,
                       frozen_rounds: dict[str, str] | None = None) -> list[str]:
    """Each verdict line, label, verdict and reason together, is the frozen line (iteration 5, S3), both
    ways, and the file's prose is ``REVIEW_PROSE_SHA256``. ``review_errors`` caught a flipped verdict,
    not a reason: PRIN-01's rewritten to "Not reviewed." stayed green. A new round updates these
    literals deliberately. Since T192 a verdict line holds its judged digest, so an edit to that cell
    alone is also named here.

    T192 step C1 closes the paste route: no message about a verdict line prints a digest, and a row whose
    verdict line differs from ``frozen`` must be re-judged by an unfrozen round. Review cycle 2: a round
    is unfrozen when its name is not in ``frozen_rounds`` (``FROZEN_ROUNDS``, S1), so an edited frozen
    round re-opens nothing and reds on its own (``frozen_round_errors``); a changed or added verdict line
    counts as re-judged only when its reason begins ``Round N:`` for an unfrozen round N whose paragraph
    names the row (S2), so a round that mentions a row in passing does not clear a pasted digest cell;
    and a frozen row whose verdict line is gone reds unless an unfrozen round says "; removed <row>" or
    ". Removed <row>" as its own clause (M1; iteration 2: a passing mention of the row no longer counts;
    T227: only the clause form counts, ``round_removals``).
    ``review_line_literal`` regenerates ``REVIEW_LINE_SHA256`` with the same rule, so regenerating the
    literal clears only a row a critic's new round re-judged, or names as removed."""
    frozen = REVIEW_LINE_SHA256 if frozen is None else frozen
    frozen_prose = REVIEW_PROSE_SHA256 if frozen_prose is None else frozen_prose
    frozen_rounds = FROZEN_ROUNDS if frozen_rounds is None else frozen_rounds
    entries, prose = review_entries(review_text)
    errors = keyed_line_errors(
        "frozen-review", "REVIEW_LINE_SHA256", "00-meaning-review.md", entries, frozen,
        "a verdict line changes only in a critic round", paste=False)
    _regenerated, unnamed = review_line_literal(review_text, frozen, frozen_rounds)
    names = ", ".join(name for name, _ in unfrozen_rounds(review_text, frozen_rounds)) or "none"
    present = {row for row, _lines_of_row in entries}
    errors += [(f"[frozen-review] {row}: its verdict line changed and its reason does not begin 'Round N:' for a "
                f"round N under ## Rounds that is not in FROZEN_ROUNDS ({names}) and names {row}: only a critic "
                f"round changes a verdict line (R7)") if row in present else
               (f"[frozen-review] {row}: its verdict line was removed and no round under ## Rounds that is not in "
                f"FROZEN_ROUNDS ({names}) says '; removed {row}' or '. Removed {row}' as its own clause: only a "
                "critic round removes a verdict line (R7)")
               for row in unnamed]
    errors += frozen_round_errors(review_text, frozen_rounds)
    have = tuple(_cell_digest(line) for line in prose)
    if have != frozen_prose:
        i = _first_difference(have, frozen_prose)
        now = repr(prose[i][:120]) if i < len(prose) else "no such line: a line was removed"
        errors.append(f"[frozen-review] prose line {i + 1} of {len(prose)} (the headings, the opening paragraph and "
                      f"## Rounds) is not the frozen line (now {now}); a round is recorded only with the review it "
                      f"records: once it is, REVIEW_PROSE_SHA256 = {have!r}")
    return errors


_ROUND_LINE = re.compile(r"^Round (?P<name>\d+[a-z]?): ")
#: A review row named in a round's prose: a rule ID with an optional ``/Scope``, ``/Not`` or ``/Why``, or a
#: T-NN; ``<A> to <B>`` with one prefix names every plain ID from A to B (Round 6's "T-01 to T-33"). No
#: label is followed by a digit (review cycle 2, iteration 2), so "T-110" names no T-11.
_ROUND_LABEL = re.compile(rf"\b(?:(?:{_P})-\d{{2,3}}(?!\d)(?:/(?:Scope|Not|Why)\b)?|T-\d{{2}}(?!\d))")
_ROUND_RANGE = re.compile(rf"\b(?P<prefix>{_P}|T)-(?P<a>\d{{2,3}}) to (?P=prefix)-(?P<b>\d{{2,3}})\b")
#: A row a round removes (review cycle 2, iteration 2; T227, IDEA-103 item 4): the removal form as its own
#: clause, ``; removed <label>`` or ``. Removed <label>``, one label per clause. The word anywhere else
#: ("nothing was removed PRIN-12/Why stays", ", removed", "removed A and B" for B) removes nothing.
_ROUND_REMOVED = re.compile(rf"(?:; removed|\. Removed) (?P<label>{_ROUND_LABEL.pattern})")


def review_rounds(review_text: str) -> list[tuple[str, str]]:
    """``[(name, paragraph)]`` of every ``Round N:`` paragraph under ``## Rounds``, in order."""
    found, under = [], False
    for line in _lines(review_text):
        if line.startswith("#"):
            under = line.strip() == "## Rounds"
        elif under and (m := _ROUND_LINE.match(line)):
            found.append((m.group("name"), line))
    return found


def unfrozen_rounds(review_text: str, frozen_rounds=None) -> list[tuple[str, str]]:
    """``[(name, paragraph)]`` of every ``Round N:`` paragraph under ``## Rounds`` whose name is not in
    ``frozen_rounds`` (``FROZEN_ROUNDS`` by default; any collection of names), in order (T194): the rounds
    written since the last commit that froze the review. Review cycle 2, S1: a round is frozen by its
    name, so a frozen round edited stays frozen and names no row (at T194 it was keyed by its prose digest,
    and one character changed in round 8 re-opened the 82 rows it mentions)."""
    frozen = FROZEN_ROUNDS if frozen_rounds is None else frozen_rounds
    return [(name, line) for name, line in review_rounds(review_text) if name not in frozen]


def frozen_round_errors(review_text: str, frozen_rounds: dict[str, str] | None = None) -> list[str]:
    """Review cycle 2, S1 and M1: each round in ``frozen_rounds`` (``FROZEN_ROUNDS`` by default) is under
    ``## Rounds`` once, with its frozen digest, and no round name occurs twice. No message prints a digest,
    and ``round_literal`` keeps the committed entries, so regenerating clears neither an edit nor a removal.
    Iteration 2: each round is named a number above the round before it, in file order, with no leading
    zero, so a new round is never "08" or "8b" beside a frozen 8, and it runs past every frozen round. The
    names are read from the review, not from a literal, so no regeneration clears one. "3b" and "4b",
    frozen before this rule, are the only lettered names (``_LETTERED_ROUNDS``)."""
    frozen = FROZEN_ROUNDS if frozen_rounds is None else frozen_rounds
    rounds = review_rounds(review_text)
    counts = Counter(name for name, _ in rounds)
    errors = [f"[frozen-review] Round {name} occurs {n} times under ## Rounds, not once"
              for name, n in counts.items() if n > 1]
    before: tuple[int, str] = (0, "")
    for name in counts:
        key = (_round_number(name), name.lstrip("0123456789"))
        if not (re.fullmatch(r"[1-9]\d*", name) or name in _LETTERED_ROUNDS) or key <= before:
            errors.append(f"[frozen-review] Round {name} is not named a number above every round before it "
                          f"({'none' if before == (0, '') else ''.join(map(str, before))}): a new round takes "
                          "the next number, and 3b and 4b are the only lettered rounds (R7)")
        before = max(before, key)
    changed = dict.fromkeys(name for name, line in rounds if name in frozen and _cell_digest(line) != frozen[name])
    errors += [f"[frozen-review] Round {name} is frozen in FROZEN_ROUNDS and its paragraph changed: a frozen round "
               "is never edited and names no row, so the edit re-opens no verdict line; a new finding goes in a new "
               "round (R7)" for name in changed]
    errors += [f"[frozen-review] Round {name} is frozen in FROZEN_ROUNDS and is not under ## Rounds: a frozen round "
               "is never removed (R7)" for name in frozen if name not in counts]
    return errors


#: The review file's title, its first prose line (review cycle 2, ``review_shape_errors``).
REVIEW_TITLE = "# Research/00 meaning review"


def review_shape_errors(review_text: str) -> list[str]:
    """The review's prose lines run, in order, ``REVIEW_TITLE``, one opening paragraph, the three group
    headings and the Glossary's, ``## Rounds``, its ``Round N:`` paragraphs and one Final line (review cycle
    2, from M1's shrink-by-deletion probe). ``REVIEW_PROSE_SHA256`` is regenerated from the prose as it
    stands, so the title or the opening paragraph deleted, then that literal regenerated, stayed green; this
    shape is not a literal, so no regeneration clears a removed line."""
    def kind(line: str) -> str:
        if line.startswith("#"):
            return line.strip() if line.strip() != REVIEW_TITLE else "the title"
        return "Round N:" if _ROUND_LINE.match(line) else "Final:" if line.startswith("Final: ") else "a paragraph"
    every = [kind(line) for line in review_entries(review_text)[1]]
    kinds = [k for i, k in enumerate(every) if not (k == "Round N:" and i and every[i - 1] == "Round N:")]
    want = ["the title", "a paragraph", *REVIEW_HEADINGS.values(), REVIEW_GLOSSARY_HEADING, "## Rounds", "Round N:",
            "Final:"]
    if kinds == want:
        return []
    return [f"[frozen-review] the review's prose runs {kinds}, not {want}: none of these lines is ever removed"]


def round_literal(review_text: str, frozen_rounds: dict[str, str] | None = None) -> dict[str, str]:
    """``FROZEN_ROUNDS`` regenerated (review cycle 2, S1): every committed entry kept as it is, and each round
    name not yet frozen added with its paragraph's ``_cell_digest``, in file order."""
    frozen = FROZEN_ROUNDS if frozen_rounds is None else frozen_rounds
    literal = dict(frozen)
    for name, line in review_rounds(review_text):
        literal.setdefault(name, _cell_digest(line))
    return literal


#: The lettered round names frozen before review cycle 2, iteration 2, ruled every later name a plain number.
_LETTERED_ROUNDS = frozenset({"3b", "4b"})


def _round_number(name: str) -> int:
    """A round name's leading digits as a number ("3b" is 3); 0 for a name with none."""
    m = re.match(r"\d+", name)
    return int(m.group()) if m else 0


def round_removals(paragraph: str) -> set[str]:
    """Every review row one round paragraph removes (review cycle 2, iteration 2; T227): each label written
    in the removal form as its own clause, ``; removed <label>`` or ``. Removed <label>`` (``_ROUND_REMOVED``).
    A label named any other way removes nothing: before T227 any "removed <label>" did, so "nothing was
    removed PRIN-12/Why stays" removed the row (IDEA-103 item 4)."""
    return {m.group("label") for m in _ROUND_REMOVED.finditer(paragraph)}


def round_labels(paragraph: str) -> set[str]:
    """Every review row one round paragraph names (T192 step C1): each label token, and each ``A to B`` range."""
    named = set(_ROUND_LABEL.findall(paragraph))
    for m in _ROUND_RANGE.finditer(paragraph):
        width = len(m.group("a"))
        named |= {f"{m.group('prefix')}-{n:0{width}d}" for n in range(int(m.group("a")), int(m.group("b")) + 1)}
    return named


def unfrozen_round_labels(review_text: str, frozen_rounds=None) -> set[str]:
    """Every review row an unfrozen round names (T194: every unfrozen round, not only the last)."""
    return set().union(*(round_labels(p) for _name, p in unfrozen_rounds(review_text, frozen_rounds)))


def _rejudged(row: str, lines: list[str], labels: dict[str, set[str]]) -> bool:
    """Review cycle 2, S2: a verdict line is re-judged when its reason (the fourth cell) begins ``Round N:``
    for an unfrozen round N (a key of ``labels``) whose paragraph names ``row``."""
    cells = _split_cells(lines[0])
    m = _ROUND_LINE.match(cells[3]) if len(cells) == 4 else None
    return bool(m) and row in labels.get(m.group("name"), set())


def review_line_literal(review_text: str, frozen: dict[str, str],
                        frozen_rounds=None) -> tuple[dict[str, str], list[str]]:
    """``(REVIEW_LINE_SHA256 regenerated, rows left unnamed)`` (T192 step C1): each verdict line's
    ``_keyed_digest`` when it equals ``frozen`` or the line is re-judged by an unfrozen round (``_rejudged``:
    its reason begins ``Round N:`` and round N names the row; review cycle 2, S2), otherwise the frozen
    digest kept (none for a new row), with the row listed. A frozen row with no verdict line is dropped only
    when an unfrozen round says "; removed <row>" or ". Removed <row>" as its own clause (``round_removals``;
    iteration 2: a passing mention did it before, and until T227 any "removed <row>" did); otherwise its
    frozen digest is kept at its frozen position and the row listed (M1; T227). Regenerating the literal
    therefore clears only a row a critic's new round re-judged or removed; a pasted digest cell, and a
    deleted verdict line, stay red."""
    labels: dict[str, set[str]] = {}
    named: set[str] = set()
    for name, paragraph in unfrozen_rounds(review_text, frozen_rounds):
        labels.setdefault(name, set()).update(round_labels(paragraph))
        named |= round_removals(paragraph)
    literal, unnamed = {}, []
    entries = review_entries(review_text)[0]
    for row, lines in entries:
        digest = _keyed_digest(lines)
        if frozen.get(row) == digest or _rejudged(row, lines, labels):
            literal[row] = digest
        else:
            unnamed.append(row)
            if row in frozen:
                literal[row] = frozen[row]
    order = [row for row, _lines_of_row in entries]
    for i, row in enumerate(frozen):
        if row not in order and row not in named:
            # T227 (IDEA-103 item 7): the kept row stays at its frozen position, after the nearest frozen row
            # before it that the literal holds; appended after every present row, it read as "keys reordered".
            earlier = [k for k in list(frozen)[:i] if k in literal]
            order.insert(order.index(earlier[-1]) + 1 if earlier else 0, row)
            literal[row] = frozen[row]
            unnamed.append(row)
    return {row: literal[row] for row in order if row in literal}, unnamed


#: The judged digest a synthetic verdict line carries when a test does not bind it to a block line.
_SYNTHETIC_DIGEST = "0123456789ab"


def _synthetic_review(required: list[str], drop: str | None = None, digests: dict[str, str] | None = None) -> str:
    parts = []
    for group, heading in REVIEW_HEADINGS.items():
        parts += [heading, "", REVIEW_HEADER, "| --- | --- | --- | --- |"]
        parts += [f"| {row} | same | {(digests or {}).get(row, _SYNTHETIC_DIGEST)} | A synthetic reason. |"
                  for row in required if row != drop and _GROUP_OF[_prefix(row.split("/", 1)[0])] == group]
        parts.append("")
    return "\n".join(parts)


#: The synthetic review the round tests start from: four rows and round 1.
_ROUND_1_ROWS = ["PRIN-01", "PRIN-01/Scope", "ARCH-01/Not", "HRV-07"]
_ROUND_1_REVIEW = _synthetic_review(_ROUND_1_ROWS) + "## Rounds\n\nRound 1: a synthetic critic reviewed 4 rows.\n"
#: What an error message must never offer to paste: a digest, or an ``= '`` assignment.
_PASTEABLE = re.compile(r"[0-9a-f]{12}|= '")


def _frozen_of(text: str) -> tuple[dict[str, str], tuple[str, ...], dict[str, str]]:
    """The three review literals as a commit of ``text`` freezes them: lines, prose and rounds."""
    entries, prose = review_entries(text)
    return ({k: _keyed_digest(v) for k, v in entries}, tuple(_cell_digest(p) for p in prose),
            round_literal(text, {}))


def _changed_red(label: str) -> str:
    return f"[frozen-review] {label}: the line changed; a verdict line changes only in a critic round"


def _unnamed_red(label: str, round_names: str = "none") -> str:
    return (f"[frozen-review] {label}: its verdict line changed and its reason does not begin 'Round N:' for a "
            f"round N under ## Rounds that is not in FROZEN_ROUNDS ({round_names}) and names {label}: only a "
            f"critic round changes a verdict line (R7)")


def _gone_red(label: str) -> str:
    return f"[frozen-review] {label} is frozen in REVIEW_LINE_SHA256 and is not in 00-meaning-review.md"


def _removed_red(label: str) -> str:
    return (f"[frozen-review] {label}: its verdict line was removed and no round under ## Rounds that is not in "
            f"FROZEN_ROUNDS (none) says '; removed {label}' or '. Removed {label}' as its own clause: only a "
            "critic round removes a verdict line (R7)")


def _round_changed_red(name: str) -> str:
    return (f"[frozen-review] Round {name} is frozen in FROZEN_ROUNDS and its paragraph changed: a frozen round is "
            "never edited and names no row, so the edit re-opens no verdict line; a new finding goes in a new "
            "round (R7)")


def test_review_errors_turns_red_on_a_missing_verdict_row() -> None:
    """The synthetic red case: the same file with one required row's verdict line removed."""
    required = ["DOC-01", "DOC-01/Scope", "ARCH-01/Not", "HRV-07", "HRV-07/Why"]
    full = _synthetic_review(required)
    missing = _synthetic_review(required, drop="ARCH-01/Not")
    print(f"[slice compared] full {review_errors(full, required)}; missing {review_errors(missing, required)}")
    assert review_errors(full, required) == []
    assert review_errors(missing, required) == ["[review] ARCH-01/Not has no verdict"]
    # T192: a verdict line without its judged digest, or with a malformed one, is named.
    row = f"| HRV-07 | same | {_SYNTHETIC_DIGEST} | A synthetic reason. |"
    no_digest = _one_edit(full, row, "| HRV-07 | same | A synthetic reason. |")
    upper = _one_edit(full, row, "| HRV-07 | same | 0123456789AB | A synthetic reason. |")
    print(f"[slice compared] no digest {review_errors(no_digest, required)}; upper {review_errors(upper, required)}")
    assert review_errors(no_digest, required) == [
        "[review] line 18: 3 cells, not 4: '| HRV-07 | same | A synthetic reason. |'", "[review] HRV-07 has no verdict"]
    assert review_errors(upper, required) == ["[review] HRV-07: digest '0123456789AB' is not 12 lowercase hex digits"]
    # T192 step C1 (G1): a Glossary row sits under the review's ## Glossary table, required once a T-NN is.
    t01 = f"| T-01 | same | {_SYNTHETIC_DIGEST} | A synthetic reason. |\n"
    glossed = f"{full}{REVIEW_GLOSSARY_HEADING}\n\n{REVIEW_HEADER}\n| --- | --- | --- | --- |\n{t01}"
    misplaced = full + t01
    print(f"[slice compared] glossed {review_errors(glossed, [*required, 'T-01'])}; misplaced "
          f"{review_errors(misplaced, [*required, 'T-01'])}")
    assert review_errors(glossed, [*required, "T-01"]) == []
    assert review_errors(misplaced, [*required, "T-01"]) == [
        "[review] T-01 sits under '## HRV', not '## Glossary'",
        f"[review] ## Glossary has no {REVIEW_HEADER} table"]


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
    """Iteration 4, M3 (ruled 2026-09-26; T192): the committed review's verdict lines carry a judged
    digest for exactly ``required_review_rows``, each label maps to one block line, in that order, and
    each line is the text its verdict line records it judged. T192 step C1 (G1): the rows are the rule
    rows and one per Glossary term, T-01 to T-33, each mapped to its Glossary line."""
    research, _history, rows = _real()
    required = required_review_rows(research, rows)
    lines, problems = reviewed_block_lines(research, rows)
    judged = review_digests(_REAL_REVIEW.read_text(encoding="utf-8"))
    errors = reviewed_block_errors(research, rows, _REAL_REVIEW.read_text(encoding="utf-8"))
    terms = [label for label in required if _GLOSSARY_LABEL.fullmatch(label)]
    rule_rows = [label for label in required if not _GLOSSARY_LABEL.fullmatch(label)]
    # T193 item 1: the printed samples are chosen by property (a no row's rule, a yes row's rule, the first
    # term), not by ID, so a retired HRV-24 or DOC-09 cannot raise a KeyError before the assertions.
    samples = [*(next((i for r in rows if r["meaning changed"] == m for i in _new_rule_ids(r) if i in lines), None)
                 for m in ("no", "yes")), *terms[:1]]
    shown = ", ".join(f"{s} {_cell_digest(lines[s])} vs {judged.get(s)}" for s in samples if s in lines)
    print(f"[slice compared] {len(required)} required rows ({len(rule_rows)} rule rows, {len(terms)} Glossary "
          f"rows), {len(lines)} mapped, {len(judged)} judged digests in {_REAL_REVIEW.name}, problems {problems}; "
          f"{shown}: {errors[:5]}")
    assert problems == [] and errors == []
    assert list(lines) == required and sorted(judged) == sorted(required)
    assert terms == list(GLOSSARY_TERMS) and required == [*rule_rows, *terms]
    assert all(lines[t].startswith(f"- **{t} {GLOSSARY_TERMS[t]}** IS ") for t in terms)


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
    code span) cannot change what this test asserts.

    T192: the judged side is a synthetic review whose verdict lines carry the unmutated lines' digests,
    and no message prints a digest (the old messages printed the one digest to paste)."""
    research, rows, lines = _synthetic_reviewed_world()
    judged = {label: _cell_digest(line) for label, line in lines.items()}
    review = _synthetic_review(list(lines), digests=judged)

    def drop_last_code_span(line: str) -> str:
        span = list(_CODE_SPAN.finditer(line))[-1]
        return line[:span.start()] + line[span.end():]

    ra3_line, rc_line = drop_last_code_span(lines["HRV-24"]), drop_last_code_span(lines["DOC-09"])
    ra3 = _edit_block(research, "HRV-24", lines["HRV-24"], ra3_line)
    rc = _edit_block(research, "DOC-09", lines["DOC-09"], rc_line)
    scope = lines["HRV-24/Scope"]
    doubled = _edit_block(research, "HRV-24", scope, scope + "\nScope: every day.")
    assert {r["meaning changed"] for r in rows if r["inventory ID"] in ("HRV-24", "DOC-09")} == {"no", "yes"}
    assert reviewed_block_errors(research, rows, review) == []
    narrowed = _one_edit(review, f"| HRV-24/Scope | same | {judged['HRV-24/Scope']} | A synthetic reason. |\n",
                         "") + "| HRV-99 | same | 000000000000 | A synthetic reason. |\n"
    _check_cases(reviewed_block_errors, {
        "ra3-hrv-24": ((ra3, rows, review), [
            ("[reviewed-block] HRV-24 changed after its verdict: a fresh critic must write a new verdict for "
             "HRV-24 in 00-meaning-review.md (R7)")]),
        "rc-doc-09": ((rc, rows, review), [
            ("[reviewed-block] DOC-09 changed after its verdict: a fresh critic must write a new verdict for "
             "DOC-09 in 00-meaning-review.md (R7)")]),
        "key-set": ((research, rows, narrowed), [
            "[reviewed-block] HRV-99 has a verdict in 00-meaning-review.md and is not a required review row",
            ("[reviewed-block] HRV-24/Scope needs a critic verdict: no verdict line in 00-meaning-review.md "
             "records the text it judged (R7)")]),
        "doubled": ((doubled, rows, review), ["[reviewed-block] HRV-24/Scope: 2 block lines, not one"]),
    })


def _synthetic_reviewed_world() -> tuple[str, list[dict[str, str]], dict[str, str]]:
    """``(research, rows, {label: block line})``: two synthetic blocks shaped like the committed ones,
    DOC-09 a ``yes`` row whose rule line ends in the history path and HRV-24 a ``no`` row whose rule
    line says the verdict MUST be unavailable and ends in the ``band`` span (iteration 6, S2)."""
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
    lines, problems = reviewed_block_lines(research, rows)
    assert problems == [] and list(lines) == ["DOC-09", "DOC-09/Scope", "DOC-09/Not", "HRV-24", "HRV-24/Scope",
                                              "HRV-24/Not"]
    return research, rows, lines


def _edit_block(research: str, rule_id: str, old: str, new: str) -> str:
    """``research`` with ``old`` replaced by ``new`` inside ``rule_id``'s block, each occurring once."""
    block = rule_blocks(research)[rule_id]
    return _one_edit(research, block, _one_edit(block, old, new))


def test_a_changed_rule_stays_red_until_a_critic_writes_a_new_verdict() -> None:
    """T192's first failing test, the sprint-007 review critic's experiment on synthetic text (R13,
    "redesign, then ratify"): HRV-24, a ``no`` row, changes MUST to MAY. At 67e1cce the one digest the
    message printed, pasted into ``REVIEWED_BLOCK_SHA256``, turned the suite green while the review
    still read "HRV-24 | same". Now:

    1. the message is exact and offers nothing to paste: no digest, no ``= '`` assignment;
    2. every literal this file holds over the review file, regenerated from the files as they stand (what
       ``frozen_literals()`` prints), is satisfied, and the check is still red: nothing in this file is
       what ``reviewed_block_errors`` compares against;
    3. an edit to the verdict line's digest cell alone, verdict and reason unchanged, clears this check but
       is named by the committed ``REVIEW_LINE_SHA256`` side (``review_line_errors``); since step C1 that
       message prints no digest, an unfrozen round must name the row, and ``review_line_literal`` (what
       ``frozen_literals()`` prints) keeps the committed digest, so regenerating the literal stays red;
    4. a critic's new verdict line, carrying the new digest, is what turns it green, and its round, naming
       the row, is what lets the literal be regenerated."""
    research, rows, lines = _synthetic_reviewed_world()
    judged = {label: _cell_digest(line) for label, line in lines.items()}
    review = _synthetic_review(list(lines), digests=judged) + "## Rounds\n\nRound 1: a synthetic critic.\n"
    frozen_lines, frozen_prose, rounds = _frozen_of(review)
    assert rounds == {"1": _cell_digest("Round 1: a synthetic critic.")}
    assert reviewed_block_errors(research, rows, review) == [] == review_line_errors(
        review, frozen_lines, frozen_prose, rounds)
    may_line = _one_edit(lines["HRV-24"], "the HRV verdict MUST be unavailable", "the HRV verdict MAY be unavailable")
    may = _edit_block(research, "HRV-24", lines["HRV-24"], may_line)
    red = ("[reviewed-block] HRV-24 changed after its verdict: a fresh critic must write a new verdict for HRV-24 "
           "in 00-meaning-review.md (R7)")
    errors = reviewed_block_errors(may, rows, review)
    print(f"[slice compared] MUST to MAY: {lines['HRV-24'][:80]!r} {judged['HRV-24']} -> {may_line[:80]!r} "
          f"{_cell_digest(may_line)}; errors {errors}")
    assert errors == [red]
    assert _cell_digest(may_line) not in red and not _PASTEABLE.search(red)
    # 2. Regenerate every literal from the files as they stand: the review's line and prose digests.
    regenerated = {k: _keyed_digest(v) for k, v in review_entries(review)[0]}
    after = reviewed_block_errors(may, rows, review)
    print(f"[slice compared] regenerated {len(regenerated)} review-line digests, review_line_errors "
          f"{review_line_errors(review, regenerated, frozen_prose, rounds)}; reviewed_block_errors {after}")
    assert review_line_errors(review, regenerated, frozen_prose, rounds) == [] and after == [red]
    # 3. A non-critic pastes the new digest into the verdict line's digest cell and keeps "same".
    old_row = f"| HRV-24 | same | {judged['HRV-24']} | A synthetic reason. |"
    pasted_row = f"| HRV-24 | same | {_cell_digest(may_line)} | A synthetic reason. |"
    pasted = _one_edit(review, old_row, pasted_row)
    seen = review_line_errors(pasted, frozen_lines, frozen_prose, rounds)
    print(f"[slice compared] digest cell pasted: reviewed_block_errors {reviewed_block_errors(may, rows, pasted)}; "
          f"review_line_errors {seen}")
    assert reviewed_block_errors(may, rows, pasted) == []
    pasted_red = [_changed_red("HRV-24"), _unnamed_red("HRV-24")]
    assert seen == pasted_red and not any(_PASTEABLE.search(e) for e in seen)
    # 3b. Regenerating REVIEW_LINE_SHA256 as frozen_literals() does keeps the committed HRV-24 digest.
    regenerated, unnamed = review_line_literal(pasted, frozen_lines, rounds)
    print(f"[slice compared] regenerated after the paste: HRV-24 {regenerated['HRV-24']} (committed "
          f"{frozen_lines['HRV-24']}, pasted line {_cell_digest(pasted_row)}), unnamed {unnamed}; "
          f"{review_line_errors(pasted, regenerated, frozen_prose, rounds)}")
    assert regenerated == frozen_lines and unnamed == ["HRV-24"]
    assert review_line_errors(pasted, regenerated, frozen_prose, rounds) == pasted_red
    # 4. The critic's new verdict line for HRV-24, with the digest of the text it judged and a reason that
    # begins with its round's name (review cycle 2, S2), and that round, naming the row.
    renewed_row = f"| HRV-24 | same | {_cell_digest(may_line)} | Round 2: a new synthetic verdict. |"
    renewed = _one_edit(review, old_row, renewed_row)
    print(f"[slice compared] new verdict: {reviewed_block_errors(may, rows, renewed)}")
    assert reviewed_block_errors(may, rows, renewed) == []
    rounded = renewed + "\nRound 2: a fresh critic re-reviewed HRV-24 and returned 0 differs verdicts.\n"
    regenerated, unnamed = review_line_literal(rounded, frozen_lines, rounds)
    new_prose = tuple(_cell_digest(p) for p in review_entries(rounded)[1])
    new_rounds = round_literal(rounded, rounds)
    print(f"[slice compared] with round 2: unnamed {unnamed}, HRV-24 {regenerated['HRV-24']}, rounds {new_rounds}; "
          f"{review_line_errors(rounded, regenerated, new_prose, rounds)}")
    assert unnamed == [] and regenerated == {**frozen_lines, "HRV-24": _cell_digest(renewed_row)}
    assert new_rounds == {**rounds, "2": _cell_digest("Round 2: a fresh critic re-reviewed HRV-24 and returned 0 "
                                                      "differs verdicts.")}
    assert review_line_errors(rounded, regenerated, new_prose, rounds) == [] == reviewed_block_errors(may, rows, rounded)
    assert review_line_errors(rounded, regenerated, new_prose, new_rounds) == []


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


def test_real_path_every_glossary_line_is_keyed_and_the_terms_are_glossary_terms() -> None:
    """Iteration 5, M1: every committed Glossary line has a T-NN key, once, and the keys are
    ``GLOSSARY_TERMS``'s, in order. T192 step C1 retired ``GLOSSARY_SHA256``: each line is bound by its
    verdict line's digest (``test_real_path_every_reviewed_block_line_is_the_text_its_verdict_judged``)."""
    research, _history, _rows = _real()
    entries, problems = glossary_entries(research)
    print(f"[slice compared] {len(entries)} Glossary lines, problems {problems}; keys {[k for k, _ in entries]}")
    assert problems == []
    assert [k for k, _ in entries] == list(GLOSSARY_TERMS)
    assert all(len(lines) == 1 for _k, lines in entries)


def test_a_glossary_line_changed_after_its_verdict_stays_red_until_a_critic_writes_a_new_one() -> None:
    """T192 step C1 (R13, G1), on synthetic text shaped like the scanner's iteration-5 routes, each message
    exactly and none printing a digest: T-13's 14 days made 21 (N1), T-05's HRV Status made a tier (N3),
    then a term replaced by another, a term repeated and a line with no key. The judged side is a synthetic
    review whose Glossary verdict lines carry the unmutated lines' digests; at e4ab89d these edits reached
    only ``GLOSSARY_SHA256``, whose message printed the digest that cleared them."""
    t13 = "- **T-13 sustains** IS the highest-fidelity tier with at least 14 distinct days in a window."
    t05 = "- **T-05 tier** IS a value of `hrv_source_tier`; HRV Status is a sidecar metric and not a tier."
    base = f"{HEADINGS[0]}\n\n{GLOSSARY_HEADING}\n\n{_glossary(**{'T-13': t13, 'T-05': t05})}\n{HEADINGS[1]}\n"
    lines, problems = reviewed_block_lines(base, [])
    assert problems == [] and list(lines) == list(GLOSSARY_TERMS) and lines["T-13"] == t13
    review = (f"{REVIEW_GLOSSARY_HEADING}\n\n{REVIEW_HEADER}\n| --- | --- | --- | --- |\n"
              + "".join(f"| {t} | same | {_cell_digest(line)} | A synthetic reason. |\n" for t, line in lines.items()))
    assert reviewed_block_errors(base, [], review) == []
    t13_21 = t13.replace("at least 14", "at least 21")
    t05_tier = t05.replace("and not a tier", "and a tier")
    t33 = "- **T-33 term33** IS the synthetic definition 33."
    t34 = "- **T-34 term34** IS a new term."

    def changed(term: str) -> str:
        return (f"[reviewed-block] {term} changed after its verdict: a fresh critic must write a new verdict for "
                f"{term} in 00-meaning-review.md (R7)")
    cases = {
        "n1-t13": ((_one_edit(base, t13, t13_21), [], review), [changed("T-13")]),
        "n3-t05": ((_one_edit(base, t05, t05_tier), [], review), [changed("T-05")]),
        "re-keyed": ((_one_edit(base, t33, t34), [], review), [
            "[reviewed-block] T-33 has a verdict in 00-meaning-review.md and is not a required review row",
            ("[reviewed-block] T-34 needs a critic verdict: no verdict line in 00-meaning-review.md records the text "
             "it judged (R7)")]),
        "repeated-and-unkeyed": ((_one_edit(base, t33, t33 + "\n- **T-01 again** IS twice.\nSome prose."), [], review), [
            "[reviewed-block] line 39: a Glossary line with no T-NN key cannot be bound: 'Some prose.'",
            "[reviewed-block] T-01: 2 Glossary lines, not one"]),
    }
    _check_cases(reviewed_block_errors, cases)
    assert not any(_PASTEABLE.search(e) for (_args, expected) in cases.values() for e in expected)
    # A critic's new verdict line for T-13, carrying the digest of the text it judged, turns it green.
    renewed = _one_edit(review, f"| T-13 | same | {_cell_digest(t13)} |", f"| T-13 | same | {_cell_digest(t13_21)} |")
    assert reviewed_block_errors(_one_edit(base, t13, t13_21), [], renewed) == []


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
    """Iteration 5, S3: each committed verdict line, label, verdict, judged digest and reason together,
    is ``REVIEW_LINE_SHA256``, both ways, over exactly the rows ``required_review_rows`` gives (T192:
    these were ``REVIEWED_BLOCK_SHA256``'s keys); and the file's prose, ``## Rounds`` included, is
    ``REVIEW_PROSE_SHA256``. T192 step C1: every row whose verdict line differs from the literal is named
    by an unfrozen round (inside ``review_line_errors``), and ``review_line_literal`` regenerates the literal
    exactly.

    Review cycle 2 (S1, S2): the rounds under ``## Rounds`` are ``FROZEN_ROUNDS``'s, by name and digest, in
    order, and equal to ``_FROZEN_ROUNDS_PIN``; no round is unfrozen; and the reasons that begin ``Round N:``
    are counted per round, so the S2 rule's reach over the committed file is printed."""
    research, _history, rows = _real()
    review = _REAL_REVIEW.read_text(encoding="utf-8")
    entries, prose = review_entries(review)
    errors = review_line_errors(review)
    required = required_review_rows(research, rows)
    regenerated, unnamed = review_line_literal(review, REVIEW_LINE_SHA256)
    rounds = review_rounds(review)
    reasons = Counter(m.group("name") for _row, (line,) in entries
                      if len(cells := _split_cells(line)) == 4 and (m := _ROUND_LINE.match(cells[3])))
    print(f"[slice compared] {len(entries)} verdict lines, {len(REVIEW_LINE_SHA256)} frozen, {len(required)} "
          f"required, {len(prose)} prose lines, unfrozen rounds {[n for n, _ in unfrozen_rounds(review)]}, unnamed "
          f"{unnamed}; PRIN-01 "
          f"{_keyed_digest(dict(entries)['PRIN-01'])} vs {REVIEW_LINE_SHA256['PRIN-01']}, T-13 "
          f"{_keyed_digest(dict(entries)['T-13'])} vs {REVIEW_LINE_SHA256.get('T-13')}: {errors[:5]}; rounds "
          f"{[n for n, _ in rounds]} vs FROZEN_ROUNDS {list(FROZEN_ROUNDS)}; reasons beginning Round N: {dict(reasons)}")
    assert errors == []
    assert [k for k, _ in entries] == list(REVIEW_LINE_SHA256) and regenerated == REVIEW_LINE_SHA256
    assert set(REVIEW_LINE_SHA256) == set(required) and len(REVIEW_LINE_SHA256) == len(required)
    assert "## Rounds" in prose and len(prose) == len(REVIEW_PROSE_SHA256)
    assert {n: _cell_digest(p) for n, p in rounds} == FROZEN_ROUNDS == _FROZEN_ROUNDS_PIN
    assert [n for n, _ in rounds] == list(FROZEN_ROUNDS) and unfrozen_rounds(review) == []
    assert round_literal(review) == FROZEN_ROUNDS and frozen_round_errors(review) == []
    assert set(reasons) <= set(FROZEN_ROUNDS)
    assert review_shape_errors(review) == []


def test_review_shape_errors_names_a_removed_title_paragraph_or_final_line() -> None:
    """Review cycle 2 (M1's shrink-by-deletion probe), on a synthetic review with the committed shape, each
    message exactly: the title removed, the opening paragraph removed, and the Final line removed. Each of
    the first two, with ``REVIEW_PROSE_SHA256`` regenerated, was green on the real files."""
    headings = [*REVIEW_HEADINGS.values(), REVIEW_GLOSSARY_HEADING]
    base = "\n\n".join([REVIEW_TITLE, "An opening paragraph.", *headings, "## Rounds", "Round 1: a critic.",
                        "Round 2: a critic.", "Final: 4 rows."]) + "\n"
    want = ["the title", "a paragraph", *headings, "## Rounds", "Round N:", "Final:"]

    def red(kinds: list[str]) -> list[str]:
        return [f"[frozen-review] the review's prose runs {kinds}, not {want}: none of these lines is ever removed"]
    assert review_shape_errors(base) == []
    _check_cases(review_shape_errors, {
        "title-removed": ((_one_edit(base, REVIEW_TITLE + "\n\n", ""),), red(want[1:])),
        "paragraph-removed": ((_one_edit(base, "An opening paragraph.\n\n", ""),), red([want[0], *want[2:]])),
        "final-removed": ((_one_edit(base, "\n\nFinal: 4 rows.", ""),), red(want[:-1])),
    })


def test_review_line_errors_names_a_rewritten_reason_and_a_changed_round() -> None:
    """Iteration 5, S3, on a synthetic review, each message exactly: PRIN-01's reason rewritten to "Not
    reviewed." (N12), its verdict flipped (N11), a row dropped, and a ``## Rounds`` paragraph edited. The
    frozen side is derived (S5). T192: its judged digest alone rewritten, verdict and reason kept, is named
    too, so a digest cell pasted without a new verdict is visible.

    T192 step C1 (the residual paste route): no verdict-line message prints a digest, and each changed or
    added row no unfrozen round names is named again as such; a row an unfrozen round names reds only
    until the literal is regenerated. T194: an unfrozen round is one not yet in ``REVIEW_PROSE_SHA256``.

    Review cycle 2: an unfrozen round is one whose name is not in ``FROZEN_ROUNDS`` (S1), so the edited
    round is named as a frozen round changed; a round that names PRIN-01 clears its changed line only when
    the line's reason begins with that round's ``Round N:`` (S2); and a dropped row stays red after the
    literal is regenerated unless an unfrozen round names it (M1, "row dropped, literal regenerated")."""
    base = _ROUND_1_REVIEW
    frozen, frozen_prose, frozen_r = _frozen_of(base)
    assert len(frozen) == 4 and len(frozen_prose) == 5 and review_line_errors(base, frozen, frozen_prose, frozen_r) == []
    d = _SYNTHETIC_DIGEST
    row = f"| PRIN-01 | same | {d} | A synthetic reason. |"
    n12, n11 = f"| PRIN-01 | same | {d} | Not reviewed. |", f"| PRIN-01 | differs | {d} | A synthetic reason. |"
    pasted = "| PRIN-01 | same | 0123456789ac | A synthetic reason. |"
    rejudged = "| PRIN-01 | same | 0123456789ac | Round 2: a new synthetic verdict. |"
    rounds = "Round 1: a synthetic critic reviewed 5 rows."
    edited = _one_edit(base, "Round 1: a synthetic critic reviewed 4 rows.", rounds)
    round_2 = "Round 2: a fresh critic re-reviewed PRIN-01 and returned 0 differs verdicts."
    named = _one_edit(base, row, pasted) + f"\n{round_2}\n"
    renamed = _one_edit(base, row, rejudged) + f"\n{round_2}\n"
    added = _one_edit(base, row, f"{row}\n| PRIN-01/Why | same | {d} | A synthetic reason. |")
    dropped = _one_edit(base, f"| HRV-07 | same | {d} | A synthetic reason. |\n", "")
    changed, frozen_and_gone, removed = _changed_red("PRIN-01"), _gone_red("HRV-07"), _removed_red("HRV-07")
    unnamed = _unnamed_red("PRIN-01")

    def prose_6(line: str) -> str:
        return ("[frozen-review] prose line 6 of 6 (the headings, the opening paragraph and ## Rounds) is not the "
                f"frozen line (now {line!r}); a round is recorded only with the review it records: once it is, "
                f"REVIEW_PROSE_SHA256 = {(*frozen_prose, _cell_digest(line))!r}")
    cases = {
        "n12-reason": ((_one_edit(base, row, n12), frozen, frozen_prose, frozen_r), [changed, unnamed]),
        "n11-verdict": ((_one_edit(base, row, n11), frozen, frozen_prose, frozen_r), [changed, unnamed]),
        "digest-cell": ((_one_edit(base, row, pasted), frozen, frozen_prose, frozen_r), [changed, unnamed]),
        "added-row": ((added, frozen, frozen_prose, frozen_r), [
            ("[frozen-review] PRIN-01/Why is not in REVIEW_LINE_SHA256 (a line was added or re-keyed); a verdict line "
             "changes only in a critic round"), _unnamed_red("PRIN-01/Why")]),
        "named-by-round-2": ((named, frozen, frozen_prose, frozen_r),
                             [changed, _unnamed_red("PRIN-01", "2"), prose_6(round_2)]),
        "round-2-rejudged": ((renamed, frozen, frozen_prose, frozen_r), [changed, prose_6(round_2)]),
        "row-dropped": ((dropped, frozen, frozen_prose, frozen_r), [frozen_and_gone, removed]),
        "row-dropped-literal-regenerated": ((dropped, review_line_literal(dropped, frozen, frozen_r)[0], frozen_prose,
                                             frozen_r), [frozen_and_gone, removed]),
        "round-edited": ((edited, frozen, frozen_prose, frozen_r), [
            _round_changed_red("1"),
            ("[frozen-review] prose line 5 of 5 (the headings, the opening paragraph and ## Rounds) is not the "
             "frozen line (now 'Round 1: a synthetic critic reviewed 5 rows.'); a round is recorded only with the "
             f"review it records: once it is, REVIEW_PROSE_SHA256 = {(*frozen_prose[:4], _cell_digest(rounds))!r}")]),
    }
    _check_cases(review_line_errors, cases)
    assert not any(_JUDGED_DIGEST.search(e) for name, (_a, want) in cases.items() for e in want
                   if not e.startswith("[frozen-review] prose line"))
    # Regenerating as frozen_literals() does: the pasted digest cell keeps its committed digest, even with a
    # round 2 that names PRIN-01 (S2); the row round 2 re-judged takes its new one; the dropped row keeps its
    # frozen digest (M1) until a round names it.
    drop_named = dropped + "\nRound 2: a fresh critic read HRV-07's rule; removed HRV-07.\n"
    for name, text, want in (("digest-cell", _one_edit(base, row, pasted), (frozen, ["PRIN-01"])),
                             ("named-by-round-2", named, (frozen, ["PRIN-01"])),
                             ("round-2-rejudged", renamed, ({**frozen, "PRIN-01": _cell_digest(rejudged)}, [])),
                             ("row-dropped", dropped, (frozen, ["HRV-07"])),
                             ("row-dropped-and-named", drop_named,
                              ({k: v for k, v in frozen.items() if k != "HRV-07"}, []))):
        print(f"[slice compared] regenerated {name}: {review_line_literal(text, frozen, frozen_r)}")
        assert review_line_literal(text, frozen, frozen_r) == want
    assert _regenerate(drop_named, frozen, frozen_r) == []


def _regenerate(text: str, frozen: dict[str, str], frozen_rounds: dict[str, str]) -> list[str]:
    """``review_line_errors`` after regenerating the three review literals as ``frozen_literals()`` does:
    ``REVIEW_LINE_SHA256`` through ``review_line_literal`` and ``FROZEN_ROUNDS`` through ``round_literal``,
    each against the committed literals, and ``REVIEW_PROSE_SHA256`` from the prose as it stands."""
    lines = review_line_literal(text, frozen, frozen_rounds)[0]
    return review_line_errors(text, lines, tuple(_cell_digest(p) for p in review_entries(text)[1]),
                              round_literal(text, frozen_rounds))


def test_review_line_errors_accepts_every_unfrozen_round_and_no_frozen_one() -> None:
    """T194: two critic rounds between commits (rounds 8 and 9) each freeze the rows they name, so the
    rows a changed verdict line may carry are those named by every ``Round N:`` paragraph not yet frozen,
    not only the last one's. Review cycle 2: a round is frozen by its name (``FROZEN_ROUNDS``, S1), and a
    changed line counts only when its reason begins ``Round N:`` for an unfrozen round N that names it
    (S2). On a synthetic review, each message exactly:

    (a) two unfrozen rounds, each re-judging its own changed row: green once the literals are regenerated
        (a last-round-only rule leaves round 2's row unnamed); with the two reasons swapped, each reason's
        round does not name its row, and both stay red after regenerating (the naming check);
    (b) a row changed, its reason citing an already-frozen round that names it, with a new round naming
        another row: red after regenerating (a rule that accepts every round clears it);
    (c) a label appended to an already-frozen round paragraph to cover a pasted row whose reason cites that
        round: the round is named as a frozen round changed, it re-opens nothing, and it stays red after
        every literal is regenerated (at T194 the edit unfroze the round, and regenerating
        ``REVIEW_PROSE_SHA256`` cleared it)."""
    d = _SYNTHETIC_DIGEST
    row, arch = (f"| PRIN-01 | same | {d} | A synthetic reason. |", f"| ARCH-01/Not | same | {d} | A synthetic reason. |")
    pasted, arch_new = ("| PRIN-01 | same | 0123456789ac | Round 2: a new synthetic verdict. |",
                        "| ARCH-01/Not | same | 0123456789ad | Round 3: a new synthetic verdict. |")

    # (a) Rounds 2 and 3 both written since the last freeze, re-judging PRIN-01 and ARCH-01/Not in turn.
    base = _ROUND_1_REVIEW
    frozen, frozen_prose, rounds = _frozen_of(base)
    tail = "\nRound 2: a fresh critic re-reviewed PRIN-01.\n\nRound 3: a fresh critic re-reviewed ARCH-01/Not.\n"
    two = _one_edit(_one_edit(base, row, pasted), arch, arch_new) + tail
    swapped = _one_edit(_one_edit(base, row, pasted.replace("Round 2:", "Round 3:")), arch,
                        arch_new.replace("Round 3:", "Round 2:")) + tail
    names = [n for n, _ in unfrozen_rounds(two, rounds)]
    lines_a, unnamed_a = review_line_literal(two, frozen, rounds)
    print(f"[slice compared] (a) unfrozen rounds {names}, labels {sorted(unfrozen_round_labels(two, rounds))}, "
          f"unnamed {unnamed_a}; committed {review_line_errors(two, frozen, frozen_prose, rounds)}; "
          f"regenerated {_regenerate(two, frozen, rounds)}; swapped regenerated {_regenerate(swapped, frozen, rounds)}")
    assert names == ["2", "3"] and unfrozen_round_labels(two, rounds) == {"PRIN-01", "ARCH-01/Not"}
    assert unnamed_a == [] and lines_a == {**frozen, "PRIN-01": _cell_digest(pasted), "ARCH-01/Not": _cell_digest(arch_new)}
    assert review_line_errors(two, frozen, frozen_prose, rounds) == [
        _changed_red("PRIN-01"), _changed_red("ARCH-01/Not"),
        ("[frozen-review] prose line 6 of 7 (the headings, the opening paragraph and ## Rounds) is not the "
         "frozen line (now 'Round 2: a fresh critic re-reviewed PRIN-01.'); a round is recorded only with the "
         f"review it records: once it is, REVIEW_PROSE_SHA256 = {_frozen_of(two)[1]!r}")]
    assert _regenerate(two, frozen, rounds) == []
    assert review_line_literal(swapped, frozen, rounds) == (frozen, ["PRIN-01", "ARCH-01/Not"])
    assert _regenerate(swapped, frozen, rounds) == [
        _changed_red("PRIN-01"), _changed_red("ARCH-01/Not"), _unnamed_red("PRIN-01"),
        _unnamed_red("ARCH-01/Not")]
    # (b) Round 1, already frozen, names PRIN-01; a new round 2 names only HRV-07; PRIN-01's cell is pasted
    # and its reason cites round 1.
    base_b = _synthetic_review(_ROUND_1_ROWS) + "## Rounds\n\nRound 1: a synthetic critic reviewed PRIN-01 and 3 more.\n"
    frozen_b, _prose_b, rounds_b = _frozen_of(base_b)
    stale_row = "| PRIN-01 | same | 0123456789ac | Round 1: a re-read verdict. |"
    stale = _one_edit(base_b, row, stale_row) + "\nRound 2: a fresh critic re-reviewed HRV-07.\n"
    print(f"[slice compared] (b) unfrozen rounds {[n for n, _ in unfrozen_rounds(stale, rounds_b)]}, "
          f"regenerated {_regenerate(stale, frozen_b, rounds_b)}")
    assert review_line_literal(stale, frozen_b, rounds_b) == (frozen_b, ["PRIN-01"])
    assert review_line_errors(stale, frozen_b, _prose_b, rounds_b)[:2] == [
        _changed_red("PRIN-01"), _unnamed_red("PRIN-01", "2")]
    assert _regenerate(stale, frozen_b, rounds_b) == [_changed_red("PRIN-01"), _unnamed_red("PRIN-01")]
    # (c) The frozen round 1 paragraph gains the label PRIN-01 to cover a pasted cell whose reason cites it.
    edited_round = "Round 1: a synthetic critic reviewed 4 rows, PRIN-01 among them."
    cover = "| PRIN-01 | same | 0123456789ac | Round 1: a re-read verdict. |"
    appended = _one_edit(_one_edit(base, row, cover), "Round 1: a synthetic critic reviewed 4 rows.", edited_round)
    prose_red = ("[frozen-review] prose line 5 of 5 (the headings, the opening paragraph and ## Rounds) is not the "
                 f"frozen line (now {edited_round!r}); a round is recorded only with the review it records: once it "
                 f"is, REVIEW_PROSE_SHA256 = {_frozen_of(appended)[1]!r}")
    round_red = _round_changed_red("1")
    lines_c = review_line_literal(appended, frozen, rounds)[0]
    print(f"[slice compared] (c) committed {review_line_errors(appended, frozen, frozen_prose, rounds)}; line literal "
          f"regenerated {review_line_errors(appended, lines_c, frozen_prose, rounds)}; every literal regenerated "
          f"{_regenerate(appended, frozen, rounds)}")
    assert unfrozen_rounds(appended, rounds) == [] and lines_c == frozen
    assert review_line_errors(appended, frozen, frozen_prose, rounds) == [
        _changed_red("PRIN-01"), _unnamed_red("PRIN-01"), round_red, prose_red]
    assert review_line_errors(appended, lines_c, frozen_prose, rounds) == [
        _changed_red("PRIN-01"), _unnamed_red("PRIN-01"), round_red, prose_red]
    assert _regenerate(appended, frozen, rounds) == [_changed_red("PRIN-01"), _unnamed_red("PRIN-01"), round_red]
    # A frozen round removed, and a frozen round's name used twice, are named too; neither message has a digest.
    removed = _one_edit(base, "Round 1: a synthetic critic reviewed 4 rows.\n", "")
    doubled = base + "\nRound 1: a second round 1 names PRIN-01.\n"
    _check_cases(frozen_round_errors, {
        "round-removed": ((removed, rounds), [
            "[frozen-review] Round 1 is frozen in FROZEN_ROUNDS and is not under ## Rounds: a frozen round is never "
            "removed (R7)"]),
        "round-doubled": ((doubled, rounds), ["[frozen-review] Round 1 occurs 2 times under ## Rounds, not once", round_red]),
    })
    assert round_literal(removed, rounds) == rounds == round_literal(doubled, rounds)


def test_unfrozen_round_labels_reads_labels_and_ranges_from_every_unfrozen_round() -> None:
    """T192 step C1, T194: the rows a round names are its label tokens, with ``/Scope``, ``/Not`` or
    ``/Why`` kept, and each ``A to B`` range of one prefix; every ``Round N:`` under ``## Rounds`` whose
    name is not frozen counts (review cycle 2, S1: a round is frozen by name, so round 1 edited stays
    frozen), a frozen round, a line before the heading and the Final line do not."""
    round_1 = "Round 1: a critic named PRIN-01."
    review = ("# Review\n\nRound 9: prose before the Rounds heading names HRV-01.\n\n## Rounds\n\n"
              f"{round_1}\n\nRound 2: a critic named ARCH-01/Why.\n\n"
              "Round 3b: a critic re-reviewed HRV-82/Scope, DOC-04, FIG-11/Scope and the 3 Glossary lines T-01 to T-03.\n"
              "\nFinal: 4 rows, naming ARCH-12.\n")
    frozen_rounds = {"1": _cell_digest(round_1)}
    edited = review.replace(round_1, "Round 1: a critic named PRIN-01 and HRV-24.")
    print(f"[slice compared] {[n for n, _ in unfrozen_rounds(review, frozen_rounds)]}: "
          f"{sorted(unfrozen_round_labels(review, frozen_rounds))}; edited round 1: "
          f"{sorted(unfrozen_round_labels(edited, frozen_rounds))}")
    assert [n for n, _ in unfrozen_rounds(review, frozen_rounds)] == ["2", "3b"]
    assert unfrozen_round_labels(review, frozen_rounds) == {
        "ARCH-01/Why", "HRV-82/Scope", "DOC-04", "FIG-11/Scope", "T-01", "T-02", "T-03"}
    assert unfrozen_round_labels(edited, frozen_rounds) == unfrozen_round_labels(review, frozen_rounds)
    assert unfrozen_round_labels(review, ()) == unfrozen_round_labels(review, frozen_rounds) | {"PRIN-01"}
    assert unfrozen_rounds("## Rounds\n\nNo round yet.\n", ()) == [] and unfrozen_round_labels("", ()) == set()


def test_a_new_round_is_named_above_every_frozen_round_in_the_exact_form() -> None:
    """Review cycle 2, iteration 2 (the advisories), on a synthetic review with round 1 frozen, each message
    exactly after every literal is regenerated: PRIN-01's cell pasted with a reason naming a new round that
    names it. ``Round 2:`` clears it. ``round 2:`` (lower case) and ``Round 2ab:`` are not rounds, so the row
    stays red (the ``re.I`` and ``\\w+`` mutants of ``_ROUND_LINE`` cleared both). ``Round 01:`` and
    ``Round 1b:`` are rounds not named a number above round 1, and a new ``Round 3:`` after a frozen round
    5 is not above it, so each reds by name, after regeneration too, since the names are read from the
    review. The committed "3b" and "4b" red nothing."""
    base = _ROUND_1_REVIEW
    frozen, _prose, rounds = _frozen_of(base)
    row = f"| PRIN-01 | same | {_SYNTHETIC_DIGEST} | A synthetic reason. |"

    def rejudged_by(name: str, text: str = base) -> str:
        pasted = f"| PRIN-01 | same | 0123456789ac | {name}: a new synthetic verdict. |"
        return _one_edit(text, row, pasted) + f"\n{name}: a fresh critic re-reviewed PRIN-01.\n"
    base_5 = base + "\nRound 5: a synthetic critic reviewed nothing new.\n"

    changed, unnamed = _changed_red("PRIN-01"), _unnamed_red("PRIN-01")

    def low(name: str, top: str = "1") -> str:
        return (f"[frozen-review] Round {name} is not named a number above every round before it ({top}): a new "
                "round takes the next number, and 3b and 4b are the only lettered rounds (R7)")
    _check_cases(_regenerate, {
        "round-2": ((rejudged_by("Round 2"), frozen, rounds), []),
        "lower-case": ((rejudged_by("round 2"), frozen, rounds), [changed, unnamed]),
        "two-letters": ((rejudged_by("Round 2ab"), frozen, rounds), [changed, unnamed]),
        "zero-padded": ((rejudged_by("Round 01"), frozen, rounds), [low("01")]),
        "lettered": ((rejudged_by("Round 1b"), frozen, rounds), [low("1b")]),
        "below-a-frozen-round": ((rejudged_by("Round 3", base_5), frozen, round_literal(base_5, {})), [low("3", "5")]),
    })
    committed = review_rounds(_REAL_REVIEW.read_text(encoding="utf-8"))
    print(f"[slice compared] committed round names {[n for n, _ in committed]}")
    assert {"3b", "4b"} <= {n for n, _ in committed} <= set(FROZEN_ROUNDS)
    assert frozen_round_errors(_REAL_REVIEW.read_text(encoding="utf-8")) == []


def test_a_label_is_not_a_prefix_and_a_removal_is_written_as_one() -> None:
    """Review cycle 2, iteration 2 (the advisories): a round names no row through a longer number ("T-110"
    is not T-11, "HRV-2401" is not HRV-240), and a frozen row whose verdict line is gone is dropped from the
    regenerated literal only when an unfrozen round says "; removed <row>" or ". Removed <row>" as its own
    clause (T227: the clause form); a passing mention kept it red before and after (the mention cleared it
    at iteration 1)."""
    assert round_labels("Round 2: a critic re-read T-110 and HRV-2401.") == set()
    assert round_labels("Round 2: a critic re-read T-11, HRV-240 and PRIN-12/Why.") == {"T-11", "HRV-240", "PRIN-12/Why"}
    assert round_removals("Round 2: a critic read PRIN-01; removed HRV-07; removed T-110.") == {"HRV-07"}
    frozen, _prose, rounds = _frozen_of(_ROUND_1_REVIEW)
    dropped = _one_edit(_ROUND_1_REVIEW, f"| HRV-07 | same | {_SYNTHETIC_DIGEST} | A synthetic reason. |\n", "")
    mention = dropped + "\nRound 2: a fresh critic re-read HRV-07's neighbours and returned 0 differs verdicts.\n"
    removal = dropped + "\nRound 2: a fresh critic read HRV-07's rule; removed HRV-07.\n"
    gone, unsaid = _gone_red("HRV-07"), _removed_red("HRV-07")
    print(f"[slice compared] mention {review_line_literal(mention, frozen, rounds)[1]}, "
          f"removal {review_line_literal(removal, frozen, rounds)[1]}")
    assert review_line_literal(mention, frozen, rounds) == (frozen, ["HRV-07"])
    assert _regenerate(mention, frozen, rounds) == [gone, unsaid]
    assert _regenerate(removal, frozen, rounds) == []


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
#: no edit regenerates, and ``RESEARCH_STRUCTURE`` carries where each heading sits. T192 retired
#: ``REVIEWED_BLOCK_SHA256``: the digest a verdict judged lives in that verdict's line in
#: 00-meaning-review.md, which no regeneration here writes. T192 step C1 retired ``GLOSSARY_SHA256`` the
#: same way: each Glossary line is a required review row, bound by its verdict line's digest.
FROZEN_LITERALS = (
    "INVENTORY_SENTENCE_SHA256", "GLOSSARY_TERMS", "NON_C_AUTHORITIES", "_NON_C_AUTHORITIES_PIN", "KEY_OWNERS",
    "_KEY_OWNERS_PIN", "TRACEABILITY_ROW_SHA256", "RETIRED_IDS", "OLD_MEANING_SHA256",
    "PINNED_SHA256", "RESEARCH_STRUCTURE", "HISTORY_SHA256", "REVIEW_LINE_SHA256",
    "REVIEW_PROSE_SHA256", "FROZEN_ROUNDS", "_FROZEN_ROUNDS_PIN",
)


#: The literals ``derived_literals`` reads as its frozen side, not from the files: a regeneration keeps
#: what they hold, and ``--against`` (``against_report``) passes a base commit's in their place.
FROZEN_SIDE = ("INVENTORY_SENTENCE_SHA256", "REVIEW_LINE_SHA256", "FROZEN_ROUNDS")


def derived_literals(research: str | None = None, rows: list[dict[str, str]] | None = None,
                     review: str | None = None,
                     frozen: dict[str, object] | None = None) -> tuple[dict[str, object], list[str]]:
    """``({name: value}, notes)``: each of ``FROZEN_LITERALS`` as the committed files now give it, and
    what the derivation found (counts, and every line it could not key). The two maps are rulings drawn
    from the table: ``NON_C_AUTHORITIES`` holds each ``yes`` row citing a non-C token under a key whose
    ``_lead_decision`` is that token (PRIN-15 cites R13 for S9's counts under a C06 key only), and
    ``KEY_OWNERS`` each key's naming rows; each pin is the same value, pasted twice after review.

    T192: no literal carries a required review row's block digest. A required row with no verdict line
    is listed as needing a critic verdict, and one whose text differs from the digest its verdict line
    records as changed after its verdict; each is a problem, never a digest. ``research``, ``rows`` and
    ``review`` replace the committed files, for the test of exactly that. T192 step C1:
    ``REVIEW_LINE_SHA256`` comes from ``review_line_literal``, so a verdict line that changed with no
    round re-judging it keeps its committed digest and is a problem, and so is a frozen row whose verdict
    line is gone (review cycle 2, M1). ``FROZEN_ROUNDS`` and its pin come from ``round_literal``, which
    keeps every committed round; an edited or removed frozen round is a problem (S1), and so is a verdict
    line for a row that is not a required review row (C3).

    Review cycle 2, iteration 2: ``INVENTORY_SENTENCE_SHA256`` is the frozen side's, never re-derived from
    the table (S4): each table cell whose sentence differs is a problem, and no new digest is printed. The
    review's shape errors are problems too. ``frozen`` replaces the ``FROZEN_SIDE`` literals, for
    ``--against``."""
    return _derive(research, rows, review, frozen)[:2]


def _derive(research: str | None, rows: list[dict[str, str]] | None, review: str | None,
            frozen: dict[str, object] | None) -> tuple[dict[str, object], list[str], list[str]]:
    """``derived_literals`` with its problems as a list: ``(values, notes, problems)``."""
    side = {name: globals()[name] for name in FROZEN_SIDE} | (frozen or {})
    frozen_lines, frozen_rounds, sentences = (side["REVIEW_LINE_SHA256"], side["FROZEN_ROUNDS"],
                                              side["INVENTORY_SENTENCE_SHA256"])
    committed_research, history, committed_rows = _real()
    research = committed_research if research is None else research
    rows = committed_rows if rows is None else rows
    review = _REAL_REVIEW.read_text(encoding="utf-8") if review is None else review
    inventory = [r for r in rows if not _is_blank(r["inventory ID"])]
    in_table = {r["inventory ID"]: r["inventory sentence"] for r in inventory}
    moved_sentences = [i for i, digest in sentences.items()
                       if i not in in_table or _sentence_digest(in_table[i]) != digest]
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
    judged = review_digests(review)
    unreviewed = [label for label in blocks if label not in judged]
    changed = [label for label, line in blocks.items() if label in judged and _cell_digest(line) != judged[label]]
    orphans = [label for label in judged if label not in set(required_review_rows(research, rows))]
    review_literal, unnamed = review_line_literal(review, frozen_lines, frozen_rounds)
    removed = [row for row in unnamed if row not in {k for k, _ in review_lines}]
    rounds = round_literal(review, frozen_rounds)
    values: dict[str, object] = {
        "INVENTORY_SENTENCE_SHA256": dict(sentences),
        "GLOSSARY_TERMS": {m.group("id"): m.group("term") for _k, (line,) in glossary
                           if (m := _GLOSSARY_LINE.match(line))},
        "NON_C_AUTHORITIES": authorities,
        "_NON_C_AUTHORITIES_PIN": authorities,
        "KEY_OWNERS": {k: frozenset(v) for k, v in sorted(owners.items())},
        "_KEY_OWNERS_PIN": {k: frozenset(v) for k, v in sorted(owners.items())},
        "TRACEABILITY_ROW_SHA256": {_row_key(r): row_digest(r) for r in rows},
        "RETIRED_IDS": dict(sorted((i, h) for i, h in retired_ids(rows).items() if h)),
        "OLD_MEANING_SHA256": {k: old_meaning_digest(e) for k, e in sorted(_OM.OLD_MEANINGS.items())},
        "PINNED_SHA256": {k: _keyed_digest(lines) for k, lines in pinned},
        "RESEARCH_STRUCTURE": research_structure(research),
        "HISTORY_SHA256": {k: _keyed_digest(lines) for k, lines in history_lines},
        "REVIEW_LINE_SHA256": review_literal,
        "REVIEW_PROSE_SHA256": tuple(_cell_digest(line) for line in prose),
        "FROZEN_ROUNDS": rounds,
        "_FROZEN_ROUNDS_PIN": rounds,
    }
    multi = [k for k, lines in pinned if len(lines) > 1]
    notes = [
        f"{len(inventory)} inventory rows, {len(rows)} table rows; non-C tokens {tokens}; {len(owners)} keys",
        f"retired: table {retired_ids(rows)!r}, listed {_retired_listed(history)!r}",
        f"reviewed_block_lines: {len(blocks)} labels mapped, problems {block_problems}",
        (f"meaning review: {len(judged)} verdict lines carry a judged digest; no digest is emitted for a "
         f"review row; needs a critic verdict {unreviewed}; changed after its verdict {changed}"),
        (f"Glossary: {len(glossary)} keyed lines, {len({k for k, _ in glossary})} unique keys, "
         f"unkeyable {glossary_problems}"),
        (f"Pinned: {len(pinned)} rules, {sum(len(v) for _, v in pinned)} lines, more than one line {multi}, "
         f"unkeyable {pinned_problems}"),
        f"structure: {len(values['RESEARCH_STRUCTURE'])} entries",
        (f"history: {len(history_lines)} keyed lines, {len({k for k, _ in history_lines})} unique keys, "
         f"unkeyable {history_problems}"),
        (f"review: {len(review_lines)} verdict lines, {len({k for k, _ in review_lines})} unique rows, "
         f"{len(prose)} prose lines; rounds not in FROZEN_ROUNDS {[n for n, _ in unfrozen_rounds(review, frozen_rounds)]} "
         f"naming {sorted(unfrozen_round_labels(review, frozen_rounds))}; verdict lines changed with no such round "
         f"re-judging them {[row for row in unnamed if row not in removed]}; verdict lines removed with no such "
         f"round saying '; removed <row>' or '. Removed <row>' as its own clause {removed}; verdicts for rows that "
         f"are not required {orphans}"),
        (f"inventory sentences: {len(sentences)} frozen, never regenerated; table cells that differ "
         f"{moved_sentences}"),
    ]
    problems = glossary_problems + pinned_problems + history_problems + block_problems
    problems += [f"{i}'s inventory sentence is not the 4e47d0e inventory's: INVENTORY_SENTENCE_SHA256 is never "
                 f"regenerated" for i in moved_sentences]
    problems += [f"{i} is an inventory ID that INVENTORY_SENTENCE_SHA256 does not hold" for i in in_table
                 if i not in sentences]
    problems += [f"{label} needs a critic verdict" for label in unreviewed]
    problems += [f"{label} changed after its verdict" for label in changed]
    problems += [f"{label} has a verdict and is not a required review row" for label in orphans]
    problems += [f"{row}'s verdict line changed and no unfrozen round re-judged it" for row in unnamed
                 if row not in removed]
    problems += [f"{row}'s verdict line was removed and no unfrozen round says '; removed {row}' or '. Removed {row}' "
                 "as its own clause" for row in removed]
    problems += [error.removeprefix("[frozen-review] ") for error in frozen_round_errors(review, frozen_rounds)]
    problems += [error.removeprefix("[frozen-review] ") for error in review_shape_errors(review)]
    problems += [f"{k} occurs more than once" for entries in (glossary, history_lines, review_lines)
                 for k, n in Counter(k for k, _ in entries).items() if n > 1]
    return values, notes + [f"problems {problems}"], problems


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


def frozen_literals(**overrides: object) -> str:
    """The source of every literal in ``FROZEN_LITERALS``, as the committed files now give it, headed by
    what the derivation found. For a reviewed edit only: paste the entries the edit changed, and no
    others, so the diff of the literal shows what was approved. Run
    ``uv run --package runcoach-api python runcoach-api/tests/test_research00_traceability.py``. It
    prints this file's own frozen side (``FROZEN_SIDE``) back, so it cannot see a hand edit to one of
    those literals; ``--against <commit>`` (``against_report``) is the reviewer's check that can."""
    values, notes = derived_literals(**overrides)
    out = [f"# {note}" for note in notes]
    for name in FROZEN_LITERALS:
        out += ["", *_literal_source(name, values[name])]
    return "\n".join(out)


#: This file, relative to the repository root, as ``git show <commit>:<path>`` names it.
_THIS_FILE = "runcoach-api/tests/test_research00_traceability.py"


def _literal_value(node: ast.expr) -> object:
    """A frozen literal's value from its source expression: constants, dicts, tuples, lists, sets and
    ``frozenset({...})``, nothing else. A base commit's source is parsed, never run."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset"
            and len(node.args) <= 1 and not node.keywords):
        return frozenset(_literal_value(node.args[0])) if node.args else frozenset()
    if isinstance(node, ast.Dict) and None not in node.keys:
        return {_literal_value(k): _literal_value(v) for k, v in zip(node.keys, node.values, strict=True)}
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        kind = {ast.Tuple: tuple, ast.List: list, ast.Set: set}[type(node)]
        return kind(_literal_value(element) for element in node.elts)
    return ast.literal_eval(node)


def committed_source(commit: str) -> str:
    """This file's source as ``commit`` holds it, read with ``git show``."""
    done = subprocess.run(["git", "show", f"{commit}:{_THIS_FILE}"], cwd=_REPO_ROOT, capture_output=True,
                          check=False)
    if done.returncode:
        raise ValueError(f"git show {commit}:{_THIS_FILE} failed: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout.decode("utf-8")


def committed_literals(commit: str, source: str | None = None) -> dict[str, object]:
    """``{name: value}`` of each ``FROZEN_LITERALS`` literal as ``commit`` holds it in this file, read with
    ``git show`` and ``ast`` (review cycle 2, iteration 2, S1). A literal the commit lacks is absent.
    ``source`` is the commit's source already read."""
    values = {}
    for node in ast.parse(committed_source(commit) if source is None else source).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if (len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id in FROZEN_LITERALS
                and node.value is not None):
            values[targets[0].id] = _literal_value(node.value)
    return values


def _literal_changes(old: object, new: object) -> str:
    """What changed from ``old`` to ``new``: keys for a map, positions (1-based) for a tuple, never a value."""
    if isinstance(old, dict) and isinstance(new, dict):
        common = [k for k in old if k in new]
        parts = [f"{label} {keys}" for label, keys in (
            ("changed", [k for k in common if old[k] != new[k]]), ("added", [k for k in new if k not in old]),
            ("removed", [k for k in old if k not in new])) if keys]
        if common != [k for k in new if k in old]:
            parts.append("keys reordered")
        return "; ".join(parts) or "unchanged"
    if isinstance(old, tuple) and isinstance(new, tuple):
        words = {"replace": "replaced by new", "delete": "deleted, new", "insert": "and new"}
        parts = [f"old {i1 + 1}..{i2} {words[tag]} {j1 + 1}..{j2}" for tag, i1, i2, j1, j2
                 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes() if tag != "equal"]
        return f"{len(old)} -> {len(new)} entries: {'; '.join(parts)}" if parts else "unchanged"
    return "unchanged" if old == new else "changed"


def _top_level_code(source: str) -> dict[str, list[str]]:
    """``{name: [ast.dump, ...]}`` of each top-level node of ``source`` outside ``FROZEN_LITERALS`` and the
    ``if __name__ == "__main__"`` block. A function or class is keyed by its name, an assignment by its
    targets, and any other node by its first source line."""
    code: dict[str, list[str]] = {}
    for node in ast.parse(source).body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"):
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            name = node.name
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            name = ", ".join(ast.unparse(target) for target in targets)
            if name in FROZEN_LITERALS:
                continue
        else:
            name = ast.unparse(node).splitlines()[0][:80]
        code.setdefault(name, []).append(ast.dump(node))
    return code


def code_changes(base_source: str, current_source: str) -> list[str]:
    """F011 AC6 (T196, S13): a ``# code changed``, ``# code added`` or ``# code removed`` line for each
    top-level node of ``current_source`` (``_top_level_code``) whose ``ast.dump`` differs from
    ``base_source``'s; changed and added in the current order, then removed in the base's. A node that
    only moved is named by nothing. ``--against`` runs this file's checker code, so it cannot judge a
    change to it; these lines make the change visible for the reviewer to read."""
    base, current = _top_level_code(base_source), _top_level_code(current_source)
    return ([f"# code {'changed' if name in base else 'added'}: {name}" for name, dumps in current.items()
             if base.get(name) != dumps]
            + [f"# code removed: {name}" for name in base if name not in current])


def against_report(base: dict[str, object], base_name: str, current: dict[str, object] | None = None,
                   research: str | None = None, rows: list[dict[str, str]] | None = None,
                   review: str | None = None, base_source: str | None = None,
                   current_source: str | None = None) -> tuple[list[str], list[str]]:
    """``(report lines, differences)`` of ``--against <commit>`` (review cycle 2, iteration 2, S1): the
    reviewer's check. ``frozen_literals()`` derives against this file's own ``FROZEN_SIDE``, so a hand
    edit to one of them is printed back as committed. Here the derivation runs over the current files
    with ``base``'s ``FROZEN_SIDE`` (the base commit's), as the documented command ran for a round
    written on that base, and each literal is reported by what changed from ``base`` (keys or positions,
    never a value). A difference is each derivation problem; each literal of ``current`` (this file's, by
    default) that is not the derived one; ``INVENTORY_SENTENCE_SHA256`` changed from ``base`` at all; and
    ``REVIEW_PROSE_SHA256`` moved other than by one entry per new round, each before Final's, with
    Final's changed exactly when a round is new. With ``base_source`` (the base commit's source of this
    file), the report also names each checker-code change from it (``code_changes``) against
    ``current_source`` (this file on disk, by default): report lines, never differences."""
    current = {name: globals()[name] for name in FROZEN_LITERALS} if current is None else current
    missing = [name for name in FROZEN_LITERALS if name not in base]
    if missing:
        return ([f"# --against {base_name}: it defines no {missing}"],
                [f"{base_name} defines no {missing}: --against needs a base with every frozen literal"])
    review = _REAL_REVIEW.read_text(encoding="utf-8") if review is None else review
    values, notes, problems = _derive(research, rows, review, {name: base[name] for name in FROZEN_SIDE})
    differences = [f"the derivation against {base_name} finds: {problem}" for problem in problems]
    report = [f"# --against {base_name}: the current files derived with {base_name}'s {', '.join(FROZEN_SIDE)} "
              "as the frozen side", *(f"# {note}" for note in notes)]
    for name in FROZEN_LITERALS:
        mine = "the derived one" if current[name] == values[name] else (
            f"not the derived one: {_literal_changes(values[name], current[name])}")
        report.append(f"# {name}: from {base_name} {_literal_changes(base[name], values[name])}; this file's is {mine}")
        if name == "INVENTORY_SENTENCE_SHA256":
            if current[name] != base[name]:
                differences.append(f"INVENTORY_SENTENCE_SHA256 changed from {base_name} "
                                   f"({_literal_changes(base[name], current[name])}): it never changes")
        elif current[name] != values[name]:
            differences.append(f"{name} is not what the documented command derives against {base_name} "
                               f"({_literal_changes(values[name], current[name])})")
    new = [(name, line) for name, line in review_rounds(review) if name not in base["FROZEN_ROUNDS"]]
    old, have = tuple(base["REVIEW_PROSE_SHA256"]), tuple(values["REVIEW_PROSE_SHA256"])
    want = (*old[:-1], *(_cell_digest(line) for _name, line in new))
    moved = [i + 1 for i in range(max(len(want), len(have) - 1))
             if i >= len(want) or i >= len(have) - 1 or want[i] != have[i]]
    if moved or not have or (have[-1] != old[-1]) != bool(new):
        differences.append(
            f"REVIEW_PROSE_SHA256 against {base_name}: the prose gains one entry per new round "
            f"({[name for name, _ in new] or 'none'}), each before Final's, and Final's changes exactly when a round "
            f"is new; nothing else moves. Moved: prose lines {moved} of {len(have)}; Final's "
            f"{'changed' if have and have[-1] != old[-1] else 'unchanged'}")
    if base_source is not None:
        current_source = Path(__file__).read_text(encoding="utf-8") if current_source is None else current_source
        report += code_changes(base_source, current_source)
    report += [f"# difference: {difference}" for difference in differences] or ["# differences: none"]
    return report, differences


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


def _with_a_new_fig(research: str, rows: list[dict[str, str]]) -> tuple[str, str, str, list[dict[str, str]]]:
    """``(last FIG, new FIG, research, rows)``: a rule block after the last committed FIG rule, numbered next,
    and an addition row naming it (T193 item 1: chosen by property, so a committed FIG-12 changes nothing)."""
    blocks = rule_blocks(research)
    last_fig = max((i for i in blocks if _prefix(i) == "FIG"), key=_order_key)
    new_fig = f"FIG-{_order_key(last_fig)[1] + 1:02d}"
    fig = _block(new_fig, "The spec MUST publish a synthetic figure.")
    added = _one_edit(research, blocks[last_fig], f"{blocks[last_fig]}\n\n{fig}")
    return last_fig, new_fig, added, [*rows, _as_row(_row(ADDITION, ADDITION, new_fig, "C03"))]


def test_frozen_literals_emits_no_digest_for_a_row_without_a_verdict() -> None:
    """T192 (R13): the sprint-007 critic added a rule, FIG-12, and ``frozen_literals()`` printed digests for
    its review rows with no review. Over the committed files plus a FIG-12 block that an addition row names,
    and HRV-24's verdict line dropped from the review, the printed source holds no block digest of any of
    those four review rows, and its notes name each as needing a critic verdict, as a problem.

    T193 item 1: the added rule is the next free FIG number after the committed FIG rules and the dropped
    verdict is a ``no`` row's rule, each chosen by property, so a committed FIG-12 (the critic's own route)
    or a retired HRV-24 leaves this test asserting the same thing.

    Review cycle 2: the dropped verdict line's place holds a verdict for HRV-99, which is not a review row
    (C3), and round 1 gains a label; the notes name the orphan, the removed verdict line (M1) and the
    edited frozen round (S1) as problems."""
    research, _history, rows = _real()
    _last_fig, new_fig, added, added_rows = _with_a_new_fig(research, rows)
    review = _REAL_REVIEW.read_text(encoding="utf-8")
    judged = review_digests(review)
    no_rule = next(i for r in rows if r["meaning changed"] == "no" for i in _new_rule_ids(r) if i in judged)
    verdict = next(line for line in _lines(review) if line.startswith(f"| {no_rule} |"))
    # Review cycle 2: an orphan verdict line (C3) and an edited frozen round (S1) are problems too.
    orphan = "| HRV-99 | same | 000000000000 | A synthetic reason. |"
    round_1 = next(line for line in _lines(review) if line.startswith("Round 1: "))
    dropped = _one_edit(_one_edit(review, verdict + "\n", orphan + "\n"), round_1, round_1 + " HRV-24 again.")
    lines, _problems = reviewed_block_lines(added, added_rows)
    labels = [no_rule, new_fig, f"{new_fig}/Scope", f"{new_fig}/Not"]
    in_order = [label for label in required_review_rows(added, added_rows) if label in labels]
    digests = {label: _cell_digest(lines[label]) for label in labels}
    source = frozen_literals(research=added, rows=added_rows, review=dropped)
    notes = [line for line in source.splitlines() if line.startswith("# ")]
    leaked = {label: d for label, d in digests.items() if d in source}
    verdicts = next(n for n in notes if n.startswith("# meaning review:"))
    print(f"[slice compared] {digests}; leaked {leaked}; {verdicts}; {notes[-1][:300]}")
    assert sorted(in_order) == sorted(labels)
    assert leaked == {}
    assert verdicts.endswith(f"needs a critic verdict {in_order!r}; changed after its verdict []")
    assert all(f"{label} needs a critic verdict" in notes[-1] for label in labels)
    assert "HRV-99 has a verdict and is not a required review row" in notes[-1]
    assert (f"{no_rule}'s verdict line was removed and no unfrozen round says '; removed {no_rule}' or "
            f"'. Removed {no_rule}' as its own clause") in notes[-1]
    assert "Round 1 is frozen in FROZEN_ROUNDS and its paragraph changed" in notes[-1]


def test_an_added_rule_with_its_verdicts_and_round_is_green_with_the_regenerated_literals() -> None:
    """Review cycle 2, C1 (T193's FIG-12 route, end to end in the suite): over the committed files, a new
    FIG rule named by an addition row, a verdict line for each of its review rows carrying the digest of
    the line it judged and a reason beginning with the new round's name, and that round, naming the rows,
    under ``## Rounds`` before Final. Every literal ``derived_literals`` gives for those files, with no
    problem, turns every bound check green; before regeneration only the unregenerated literals red, and
    no row is left unnamed. The literals that move are the table, Pinned, structure and three review
    literals, and ``REVIEW_PROSE_SHA256`` gains exactly one entry, before Final's."""
    research, history, rows = _real()
    last_fig, new_fig, added, added_rows = _with_a_new_fig(research, rows)
    lines, _problems = reviewed_block_lines(added, added_rows)
    labels = [label for label in required_review_rows(added, added_rows) if label.split("/")[0] == new_fig]
    name = str(max(_round_number(n) for n in FROZEN_ROUNDS) + 1)
    review = _REAL_REVIEW.read_text(encoding="utf-8")
    anchor = next(line for line in reversed(_lines(review)) if line.startswith(f"| {last_fig}"))
    verdicts = "".join(f"\n| {label} | same | {_cell_digest(lines[label])} | Round {name}: a synthetic verdict. |"
                       for label in labels)
    paragraph = f"Round {name}: a fresh critic judged the new rule, {', '.join(labels)}, and returned 0 differs verdicts."
    final = next(line for line in _lines(review) if line.startswith("Final: "))
    review = _one_edit(_one_edit(review, anchor, anchor + verdicts), final, f"{paragraph}\n\n{final}")
    values, notes = derived_literals(research=added, rows=added_rows, review=review)
    moved = [n for n in FROZEN_LITERALS if values[n] != globals()[n]]
    literal, unnamed = review_line_literal(review, REVIEW_LINE_SHA256)
    before = review_line_errors(review)
    required = required_review_rows(added, added_rows)
    after = {
        "review": review_errors(review, required),
        "reviewed-block": reviewed_block_errors(added, added_rows, review),
        "review-line": review_line_errors(review, values["REVIEW_LINE_SHA256"], values["REVIEW_PROSE_SHA256"],
                                          values["FROZEN_ROUNDS"]),
        "table": traceability_row_errors(added_rows, values["TRACEABILITY_ROW_SHA256"]),
        "trace": traceability_errors(added_rows, added, history, _OM.OLD_MEANINGS),
        "ids": split_numbering_errors(added_rows),
        "grammar": rule_grammar_errors(added),
        "pinned": pinned_digest_errors(added, values["PINNED_SHA256"]),
        "structure": structure_errors(added, values["RESEARCH_STRUCTURE"]),
    }
    print(f"[slice compared] {new_fig} rows {labels}, round {name}; problems {notes[-1]}; moved {moved}; unnamed "
          f"{unnamed}; before regeneration {[e[:90] for e in before]}; after {after}")
    assert len(labels) == 3 and all(label in literal for label in labels) and unnamed == []
    assert notes[-1] == "problems []"
    assert after == dict.fromkeys(after, [])
    assert moved == ["TRACEABILITY_ROW_SHA256", "PINNED_SHA256", "RESEARCH_STRUCTURE", "REVIEW_LINE_SHA256",
                     "REVIEW_PROSE_SHA256", "FROZEN_ROUNDS", "_FROZEN_ROUNDS_PIN"]
    assert values["REVIEW_PROSE_SHA256"] == (*REVIEW_PROSE_SHA256[:-1], _cell_digest(paragraph), REVIEW_PROSE_SHA256[-1])
    assert values["FROZEN_ROUNDS"] == {**FROZEN_ROUNDS, name: _cell_digest(paragraph)} == values["_FROZEN_ROUNDS_PIN"]
    assert [e for e in before if "its verdict line" in e] == [] and before


def test_the_regeneration_never_prints_a_new_inventory_sentence_digest_and_names_the_shape() -> None:
    """Review cycle 2, iteration 2, S4: ``INVENTORY_SENTENCE_SHA256`` was re-derived from the table, so a
    ``no`` row's sentence edited in the table printed its new digest with no problem, and pasting it cleared
    the gate. It is the frozen side's now: the printed literal is the committed one, the edited cell is a
    problem, and its digest is printed nowhere. The data dir is not read. The review's shape errors are
    problems too (a title removed)."""
    research, _history, rows = _real()
    edited = [dict(r) for r in rows]
    row = next(r for r in edited if r["meaning changed"] == "no" and r["inventory ID"] in INVENTORY_SENTENCE_SHA256)
    row["inventory sentence"] += " Edited."
    source = frozen_literals(rows=edited)
    printed: dict[str, object] = {}
    exec(source, {}, printed)  # noqa: S102 -- the module's own generated source, run to prove it
    notes = [line for line in source.splitlines() if line.startswith("# ")]
    untitled = _one_edit(_REAL_REVIEW.read_text(encoding="utf-8"), REVIEW_TITLE + "\n", "")
    shape = derived_literals(research=research, review=untitled)[1][-1]
    print(f"[slice compared] {row['inventory ID']}: {notes[-2:]}; untitled review {shape[:300]}")
    assert printed["INVENTORY_SENTENCE_SHA256"] == INVENTORY_SENTENCE_SHA256
    assert notes[-1] == (f"# problems [\"{row['inventory ID']}'s inventory sentence is not the 4e47d0e inventory's: "
                         "INVENTORY_SENTENCE_SHA256 is never regenerated\"]")
    assert _sentence_digest(row["inventory sentence"]) not in source
    assert "the review's prose runs [" in shape and "none of these lines is ever removed" in shape


def test_against_names_every_hand_edit_the_regeneration_prints_back() -> None:
    """Review cycle 2, iteration 2, S1: ``frozen_literals()`` prints this file's own ``FROZEN_SIDE`` back,
    so a hand-edited literal came out as committed. ``against_report`` derives with the base commit's
    literals instead; here the base is the committed literals (``--against HEAD~0`` in memory), and each
    of the rule file's hand edits is named: a ``REVIEW_LINE_SHA256`` entry dropped by hand with its Why and
    verdict lines deleted, a rule changed with its cell pasted and its entry set by hand, a frozen round
    edited with ``FROZEN_ROUNDS``, its pin and ``REVIEW_PROSE_SHA256`` set by hand, and the opening
    paragraph and Final edited with ``REVIEW_PROSE_SHA256`` regenerated. A new round with Final rewritten
    and every literal regenerated against the base is named by nothing."""
    research, _history, _rows = _real()
    review = _REAL_REVIEW.read_text(encoding="utf-8")
    base = {name: globals()[name] for name in FROZEN_LITERALS}

    def line_of(text: str, prefix: str) -> str:
        return next(line for line in _lines(text) if line.startswith(prefix))

    def against(research_text: str, review_text: str, **hand: object) -> list[str]:
        return against_report(base, "HEAD~0", {**base, **hand}, research=research_text, review=review_text)[1]
    why_rule = next(r for r in required_review_rows(research, _rows) if r.endswith("/Why"))
    why = next(line for line in rule_blocks(research)[why_rule.split("/")[0]].split("\n") if line.startswith("Why: "))
    l1 = against(_one_edit(research, why + "\n", ""), _one_edit(review, line_of(review, f"| {why_rule} |") + "\n", ""),
                 REVIEW_LINE_SHA256={k: v for k, v in REVIEW_LINE_SHA256.items() if k != why_rule})
    rule = next(line for line in _lines(research) if line.startswith("**HRV-24.** "))
    may = rule.replace(" MUST ", " MAY ", 1)
    cells = _split_cells(line_of(review, "| HRV-24 |"))
    pasted = f"| HRV-24 | {cells[1]} | {_cell_digest(may)} | {cells[3]} |"
    l2 = against(_one_edit(research, rule, may), _one_edit(review, line_of(review, "| HRV-24 |"), pasted),
                 REVIEW_LINE_SHA256={**REVIEW_LINE_SHA256, "HRV-24": _keyed_digest([pasted])})
    round_7 = line_of(review, "Round 7: ")
    edited_7 = _one_edit(review, round_7, round_7 + " An edit.")
    hand_7 = {**FROZEN_ROUNDS, "7": _cell_digest(round_7 + " An edit.")}
    prose_7 = review_entries(edited_7)[1]
    l3 = against(research, edited_7, FROZEN_ROUNDS=hand_7, _FROZEN_ROUNDS_PIN=dict(hand_7),
                 REVIEW_PROSE_SHA256=tuple(_cell_digest(p) for p in prose_7))
    final = line_of(review, "Final: ")
    edited_4 = _one_edit(_one_edit(review, final, final + " Edited."), prose_7[1], prose_7[1] + " Edited.")
    l4 = against(research, edited_4, REVIEW_PROSE_SHA256=tuple(_cell_digest(p) for p in review_entries(edited_4)[1]))
    name = str(max(_round_number(n) for n in FROZEN_ROUNDS) + 1)
    legit = _one_edit(_one_edit(review, line_of(review, "| HRV-24 |"), pasted.replace(cells[3], f"Round {name}: MAY.")),
                      final, f"Round {name}: a fresh critic re-judged HRV-24.\n\n{final} Round {name}.")
    regenerated = derived_literals(research=_one_edit(research, rule, may), review=legit)[0]
    clean = against(_one_edit(research, rule, may), legit, **regenerated)

    def prose(moved: int, final_state: str) -> str:
        return ("REVIEW_PROSE_SHA256 against HEAD~0: the prose gains one entry per new round (none), each before "
                "Final's, and Final's changes exactly when a round is new; nothing else moves. Moved: prose lines "
                f"[{moved}] of {len(prose_7)}; Final's {final_state}")
    frozen_7 = [f"{n} is not what the documented command derives against HEAD~0 (changed ['7'])"
                for n in ("FROZEN_ROUNDS", "_FROZEN_ROUNDS_PIN")]
    print(f"[slice compared] L1 {l1}\nL2 {l2}\nL3 {l3}\nL4 {l4}\nround {name}: {clean}")
    assert l1 == [f"the derivation against HEAD~0 finds: {why_rule}'s verdict line was removed and no unfrozen "
                  f"round says '; removed {why_rule}' or '. Removed {why_rule}' as its own clause",
                  f"REVIEW_LINE_SHA256 is not what the documented command derives against HEAD~0 (removed "
                  f"['{why_rule}'])"]
    assert l2 == ["the derivation against HEAD~0 finds: HRV-24's verdict line changed and no unfrozen round "
                  "re-judged it",
                  "REVIEW_LINE_SHA256 is not what the documented command derives against HEAD~0 (changed ['HRV-24'])"]
    assert l3 == ["the derivation against HEAD~0 finds: Round 7 is frozen in FROZEN_ROUNDS and its paragraph "
                  "changed: a frozen round is never edited and names no row, so the edit re-opens no verdict line; a "
                  "new finding goes in a new round (R7)", *frozen_7,
                  prose(prose_7.index(round_7 + " An edit.") + 1, "unchanged")]
    assert l4 == [prose(2, "changed")]
    assert clean == []


def test_against_reports_a_checker_code_change() -> None:
    """F011 AC6 (T196, S13): ``--against`` compared only ``FROZEN_LITERALS`` and ran this file's checker
    code, so a diff editing ``_rejudged`` ended ``# differences: none`` with nothing named. It now prints a
    ``# code changed``, ``# code added`` or ``# code removed`` line for each top-level node outside
    ``FROZEN_LITERALS`` and the ``__main__`` block whose ``ast.dump`` differs from the base's. These are
    report lines, never differences: they change no exit code. A node that only moved is named by
    nothing, and neither is a frozen literal's value or the ``__main__`` block."""
    base_source = textwrap.dedent("""\
        import os
        A = 1
        INVENTORY_SENTENCE_SHA256 = {"x": "1"}
        def kept():
            return 1
        def changed():
            return 1
        def gone():
            return 2
        class K:
            x = 1
        if __name__ == "__main__":
            print(1)
        """)
    current_source = textwrap.dedent("""\
        import os
        class K:
            x = 1
        A = 1
        INVENTORY_SENTENCE_SHA256 = {"x": "2"}
        def kept():
            return 1
        def changed():
            return 2
        def new():
            return 3
        if __name__ == "__main__":
            print(2)
        """)
    synthetic = code_changes(base_source, current_source)
    current = Path(__file__).read_text(encoding="utf-8")
    head = "def _rejudged(row: str, lines: list[str], labels: dict[str, set[str]]) -> bool:\n"
    base_file = _one_edit(current, head, head + '    """A base whose _rejudged body differs."""\n')
    base = {name: globals()[name] for name in FROZEN_LITERALS}
    report, differences = against_report(base, "HEAD~0", base_source=base_file, current_source=current)
    code = [line for line in report if line.startswith("# code ")]
    print(f"[slice compared] synthetic {synthetic}; real {code}; differences {differences}; last {report[-1]}")
    assert synthetic == ["# code changed: changed", "# code added: new", "# code removed: gone"]
    assert code_changes(current, current) == []
    assert code == ["# code changed: _rejudged"]
    assert differences == [] and report[-1] == "# differences: none"
    assert [line for line in report if line.startswith("# difference:")] == []


def _against_world() -> tuple[str, str, dict[str, object], str]:
    """``(research, review, base, Final line)`` of a ``--against HEAD~0`` world over the committed files
    (T227): the base is this file's own literals, the review is the committed one."""
    research, _history, _rows = _real()
    review = _REAL_REVIEW.read_text(encoding="utf-8")
    base = {name: globals()[name] for name in FROZEN_LITERALS}
    final = next(line for line in _lines(review) if line.startswith("Final: "))
    return research, review, base, final


def test_against_reports_a_final_only_edit_as_one_difference() -> None:
    """F012 AC4 (T227, IDEA-103 item 2a): the Final line edited alone, with ``REVIEW_PROSE_SHA256``
    regenerated (the rule file's route 4 without the opening paragraph, so nothing before Final moves),
    is exactly one difference: the prose branch's, saying no round is new and Final's changed. Every
    earlier test of that branch moved a prose line too, so the branch's Final clause had no killing
    test: the mutant ``if moved or not have:`` (the clause ``(have[-1] != old[-1]) != bool(new)`` dropped
    from the condition in ``against_report``) reports zero differences for this world and turns this
    test red; the committed condition reports one."""
    research, review, base, final = _against_world()
    edited = _one_edit(review, final, final + " Edited.")
    regenerated = derived_literals(research=research, review=edited)[0]
    report, differences = against_report(base, "HEAD~0", {**base, **regenerated}, research=research, review=edited)
    prose = review_entries(edited)[1]
    print(f"[slice compared] Final-only world: {len(prose)} prose lines; differences {differences}; last {report[-1]}")
    assert regenerated["REVIEW_PROSE_SHA256"] == (*REVIEW_PROSE_SHA256[:-1], _cell_digest(final + " Edited."))
    assert len(differences) == 1
    assert differences == [
        "REVIEW_PROSE_SHA256 against HEAD~0: the prose gains one entry per new round (none), each before Final's, "
        "and Final's changes exactly when a round is new; nothing else moves. Moved: prose lines [] of "
        f"{len(prose)}; Final's changed"]
    assert report[-1] == f"# difference: {differences[0]}"


def test_against_reports_a_leading_zero_round_name_as_a_difference() -> None:
    """F012 AC4 (T227, IDEA-103 item 2b): a new round named with a leading zero (``012`` after round 11),
    Final rewritten and every literal regenerated against the base, is a difference: the derivation's
    ``frozen_round_errors`` message that the round is not named a number above every round before it.
    ``_derive`` surfaced it before T227 and nothing asserted it. The same world with the round named
    ``12`` is named by nothing, so the difference is the zero's."""
    research, review, base, final = _against_world()
    last = max(_round_number(n) for n in FROZEN_ROUNDS)

    def world(name: str) -> tuple[list[str], list[str]]:
        edited = _one_edit(review, final, f"Round {name}: a fresh critic read HRV-24 and left it same.\n\n"
                                          f"{final} Round {name}.")
        regenerated = derived_literals(research=research, review=edited)[0]
        return against_report(base, "HEAD~0", {**base, **regenerated}, research=research, review=edited)
    zero_report, zero = world(f"0{last + 1}")
    _plain_report, plain = world(str(last + 1))
    print(f"[slice compared] last frozen round {last}; 0{last + 1} -> {zero}; {last + 1} -> {plain}; "
          f"last {zero_report[-1]}")
    assert zero == [f"the derivation against HEAD~0 finds: Round 0{last + 1} is not named a number above every "
                    f"round before it ({last}): a new round takes the next number, and 3b and 4b are the only "
                    "lettered rounds (R7)"]
    assert zero_report[-1] == f"# difference: {zero[0]}"
    assert plain == []


def test_round_removals_take_only_the_removal_form_as_its_own_clause() -> None:
    """F012 AC4 (T227, IDEA-103 item 4): ``round_removals`` read every label straight after the word
    "removed", so a round saying "nothing was removed PRIN-12/Why stays" removed the row. It now takes
    only the clause form: ``; removed <label>`` or ``. Removed <label>``, the word at a clause boundary
    with lower-case ``removed`` after a semicolon and ``Removed`` after a full stop, one label per
    clause; a comma, the wrong case, the round's opening clause ("Round 99: removed ...") and a second
    label after "and" are not the form. In a world, a Why line and its verdict line deleted with a new
    round naming the row in passing stays a difference, and the message names the form; the clause form
    clears it and the regenerated literal drops the row."""
    passing = "Round 99: nothing was removed PRIN-12/Why stays as it is."
    clause = "Round 99: re-judged PRIN-12; removed PRIN-12/Why."
    sentence = "Round 99: re-judged PRIN-12. Removed PRIN-12/Why. Removed HRV-24/Scope; removed T-11."
    wrong_case = ["Round 99: re-judged PRIN-12; Removed PRIN-12/Why.", "Round 99: re-judged PRIN-12. removed PRIN-12/Why.",
                  "Round 99: re-judged PRIN-12, removed PRIN-12/Why.", "Round 99: removed PRIN-12/Why.",
                  "Round 99: re-judged PRIN-12; removed PRIN-12/Why and HRV-24/Scope."]
    research, review, base, final = _against_world()
    why_rule = next(r for r in required_review_rows(research, _real()[2]) if r.endswith("/Why"))
    why = next(line for line in rule_blocks(research)[why_rule.split("/")[0]].split("\n") if line.startswith("Why: "))
    verdict = next(line for line in _lines(review) if line.startswith(f"| {why_rule} |"))
    cut_research, cut_review = _one_edit(research, why + "\n", ""), _one_edit(review, verdict + "\n", "")
    name = str(max(_round_number(n) for n in FROZEN_ROUNDS) + 1)

    def world(paragraph: str) -> tuple[dict[str, object], list[str]]:
        edited = _one_edit(cut_review, final, f"Round {name}: {paragraph}\n\n{final} Round {name}.")
        regenerated = derived_literals(research=cut_research, review=edited)[0]
        return regenerated, against_report(base, "HEAD~0", {**base, **regenerated}, research=cut_research,
                                           review=edited)[1]
    passing_literal, in_passing = world(f"nothing was removed {why_rule} stays as it is.")
    clause_literal, as_clause = world(f"re-judged {why_rule.split('/')[0]}; removed {why_rule}.")
    print(f"[slice compared] passing {round_removals(passing)}; clause {round_removals(clause)}; sentence "
          f"{round_removals(sentence)}; wrong case {[round_removals(p) for p in wrong_case]}; world {why_rule}: "
          f"in passing {in_passing}; as a clause {as_clause}")
    assert round_removals(passing) == set()
    assert round_removals(clause) == {"PRIN-12/Why"}
    assert round_removals(sentence) == {"PRIN-12/Why", "HRV-24/Scope", "T-11"}
    assert [round_removals(p) for p in wrong_case] == [set(), set(), set(), set(), {"PRIN-12/Why"}]
    assert in_passing == [f"the derivation against HEAD~0 finds: {why_rule}'s verdict line was removed and no "
                          f"unfrozen round says '; removed {why_rule}' or '. Removed {why_rule}' as its own clause"]
    assert why_rule in passing_literal["REVIEW_LINE_SHA256"]
    assert as_clause == [] and why_rule not in clause_literal["REVIEW_LINE_SHA256"]


def test_against_reports_a_removed_row_named_never_keys_reordered() -> None:
    """F012 AC4 (T227, IDEA-103 item 7): ``review_line_literal`` kept a frozen row whose verdict line was
    deleted with no round removing it, but appended it after every present row, so ``against_report``
    printed "from HEAD~0 keys reordered" for ``REVIEW_LINE_SHA256`` though nothing was reordered. The
    kept row now stays at its frozen position: with HRV-24/Scope's verdict line deleted and its entry
    dropped by hand (the rule file's route 1), the report line names the row as ``removed
    ['HRV-24/Scope']``, neither it nor any difference says "keys reordered", and the derived literal's
    keys are the base's in the base's order.

    Wave-1 test-fix: the sweep was over the whole report, and on the merged tree T223's hand-inserted
    ``KEY_OWNERS`` and ``_KEY_OWNERS_PIN`` keys sit out of the derivation's sorted order, so those two
    literals' own report lines say "from HEAD~0 keys reordered" (a report line, never a difference) and
    the sweep read another literal's order as this row's. The property is ``REVIEW_LINE_SHA256``'s."""
    research, review, base, _final = _against_world()
    verdict = next(line for line in _lines(review) if line.startswith("| HRV-24/Scope |"))
    edited = _one_edit(review, verdict + "\n", "")
    dropped = {**base, "REVIEW_LINE_SHA256": {k: v for k, v in REVIEW_LINE_SHA256.items() if k != "HRV-24/Scope"}}
    report, differences = against_report(base, "HEAD~0", dropped, research=research, review=edited)
    line = next(line for line in report if line.startswith("# REVIEW_LINE_SHA256: "))
    derived = _derive(research, None, edited, {name: base[name] for name in FROZEN_SIDE})[0]["REVIEW_LINE_SHA256"]
    reordered = [l for l in report if "keys reordered" in l]
    print(f"[slice compared] {line}; differences {differences}; derived keys as base's "
          f"{list(derived) == list(REVIEW_LINE_SHA256)}; report lines saying keys reordered {reordered}")
    assert line == ("# REVIEW_LINE_SHA256: from HEAD~0 unchanged; this file's is not the derived one: "
                    "removed ['HRV-24/Scope']")
    assert "keys reordered" not in line and not any("keys reordered" in d for d in differences)
    assert not any(l.startswith("# REVIEW_LINE_SHA256") or l.startswith("# difference") for l in reordered)
    assert list(derived) == list(REVIEW_LINE_SHA256) and derived == REVIEW_LINE_SHA256
    assert differences[-1] == ("REVIEW_LINE_SHA256 is not what the documented command derives against HEAD~0 "
                               "(removed ['HRV-24/Scope'])")
    assert any("HRV-24/Scope's verdict line was removed and no unfrozen round says" in d for d in differences)


def test_the_against_command_runs_from_the_documented_command() -> None:
    """Review cycle 2, iteration 2, S1: the documented command with ``--against HEAD`` reads HEAD's literals
    with ``git show`` and ``ast``, and over the committed files it names no difference and exits 0."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
    done = subprocess.run([sys.executable, str(Path(__file__)), "--against", "HEAD"], capture_output=True, env=env,
                          cwd=_REPO_ROOT, check=False, timeout=180)
    out = done.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    code = [line for line in out.splitlines() if line.startswith("# code ")]
    want = code_changes(committed_source("HEAD"), Path(__file__).read_text(encoding="utf-8"))
    print(f"[slice compared] exit {done.returncode}; {out.splitlines()[-3:]}; code {code}; stderr {done.stderr[-300:]!r}")
    assert done.returncode == 0, out[-2000:] + done.stderr.decode("utf-8", errors="replace")[-2000:]
    assert "\n# REVIEW_LINE_SHA256: from HEAD " in out and out.endswith("\n# differences: none\n")
    assert code == want


def test_the_against_cli_reports_code_changes_from_an_older_base() -> None:
    """F011 AC6 (T196, spec review wave 1): ``--against HEAD`` on a clean tree expects no ``# code`` line, so
    it passes even when the ``__main__`` path never hands ``against_report`` the base's source. Run the
    documented command against ``a15610d``, which predates T196's own checker-code changes, so the lines
    it prints must be ``code_changes(a15610d's source, this file)`` and must not be empty. Its exit code
    judges the literals, not the code lines, so a later legitimate difference from ``a15610d`` may exit 1."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
    done = subprocess.run([sys.executable, str(Path(__file__)), "--against", "a15610d"], capture_output=True,
                          env=env, cwd=_REPO_ROOT, check=False, timeout=180)
    out = done.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    code = [line for line in out.splitlines() if line.startswith("# code ")]
    want = code_changes(committed_source("a15610d"), Path(__file__).read_text(encoding="utf-8"))
    print(f"[slice compared] exit {done.returncode}; code {code}; want {want}; stderr {done.stderr[-300:]!r}")
    assert done.returncode in (0, 1), out[-2000:] + done.stderr.decode("utf-8", errors="replace")[-2000:]
    assert out.startswith("# --against a15610d: the current files derived with ")
    assert "# code changed: against_report" in want and "# code added: code_changes" in want
    assert code == want


@pytest.mark.parametrize(
    "io_encoding", [pytest.param(None, id="pythonioencoding-unset"), pytest.param("cp1252", id="pythonioencoding-cp1252")])
def test_the_documented_regeneration_command_runs_with_piped_output(io_encoding: str | None) -> None:
    """T193 item 2: ``frozen_literals()``'s documented command, run with its output piped (``> out``),
    exited 1 on a cp1252 console with ``UnicodeEncodeError`` on '₃', since a piped stdout takes the
    locale's encoding. Run the file as the command does, with ``PYTHONIOENCODING`` unset (the user's
    console) and set to cp1252 (the same failure on any platform), and ``PYTHONUTF8=0``."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
    env["PYTHONUTF8"] = "0"
    if io_encoding is not None:
        env["PYTHONIOENCODING"] = io_encoding
    done = subprocess.run([sys.executable, str(Path(__file__))], capture_output=True, env=env, cwd=_REPO_ROOT,
                          check=False, timeout=120)
    out, err = done.stdout.decode("utf-8", errors="replace"), done.stderr.decode("utf-8", errors="replace")
    print(f"[slice compared] exit {done.returncode}, {len(done.stdout)} bytes out, stderr tail {err[-300:]!r}")
    assert done.returncode == 0, err[-2000:]
    assert "\nTRACEABILITY_ROW_SHA256 = {\n" in out.replace("\r\n", "\n")
    assert "₃" in done.stdout.decode("utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) == 3 and sys.argv[1] == "--against":
        try:
            base_text = committed_source(sys.argv[2])
        except ValueError as error:
            sys.exit(str(error))
        against_lines, against_differences = against_report(committed_literals(sys.argv[2], base_text), sys.argv[2],
                                                            base_source=base_text)
        print("\n".join(against_lines))
        sys.exit(1 if against_differences else 0)
    if len(sys.argv) > 1:
        sys.exit(f"usage: {Path(__file__).name} [--against <commit>]")
    print(frozen_literals())
