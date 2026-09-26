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

#: The inventory sentence of each of the 149 IDs, frozen as ``sha256(normalize(sentence))`` (sprint-007
#: review iteration 1, S3). The AC9 proxy reads a row's "inventory sentence" cell, so a cell set to
#: ``—`` or edited alongside its rule took the row out of the proxy and the suite stayed green.
#: Computed 2026-09-25 from the inventory's "Current rule" cell (``inventory_sentences()`` over
#: ``spec/references/research00-rewrite-inventory.md``, the inventory at ``4e47d0e``); the committed
#: table's 149 cells matched it verbatim. Asserted by ``inventory_sentence_errors``.
INVENTORY_SENTENCE_SHA256 = {
    "DOC-01": "9f89fe636c53bf7882fd0ff8747ec6991bff3be196f8d3be16bbb0a1fa4389f8",
    "DOC-02": "f82a9ae35f231f698a027dcd9e38e101dd057df1051c5ab66bc45ac68881a59d",
    "DOC-03": "a6ffb669cab1f20642559fd1e5e719bdcf17a58f856f400e84b49e6a9c0c4831",
    "DOC-04": "9314bfbe6752e6b62b6c440d3a91788653fc2b3c239cfd5bd1ab644571a2b068",
    "DOC-05": "0a50082f92e7afbdf27be5fe0a3db5df4dbfbd0b248ef41b3a22dd08c0ee824d",
    "DOC-06": "4109a1fce2ffc74d34ffb54745c44f4e32a2f8e528225fde52bf6a3ed0323f8e",
    "DOC-07": "ab56a14394636820d151d0db9648b15ee336b1a46dc628ba03bfeb342476dc4c",
    "DOC-08": "d9ec83c4752d4291395b3422eb36d03b76e66505d3e0190aab66fbd5f7425f6e",
    "DOC-09": "a4eaf51cace0f5a48ad0e3094526dbe2590f6b66a1d995c19a82f68f77bfcee2",
    "DOC-10": "07584f971a006d12880a530cfc1bf02185073aa6bb9999a0d37f662ab233d6c5",
    "DOC-11": "0ba03505b52e1534354593b3033bbe3a321329a9fd90fa542378ac1f50981438",
    "DOC-12": "0c24e83d0aa697cf5e4093ea53ac8c3894999e69cb60bce927fbef0353c2720f",
    "DOC-13": "2c40da36d9de5183f0e9a83137ea3f2ebb6ed006bfcb7bf238e5bc5dbdf7efb7",
    "DOC-14": "0c1a266d620b15ba0fedf66e9da47148917e63722b3f06cc797a75be7f70e937",
    "DOC-15": "855e1b05c51a5dbd11d4e574d21d65461c8297166023d2f9d6972e7521ad23b0",
    "PRIN-01": "7c3f6c4531ba53480f8f94af31f5aee52f05dd02034e740c214c938597a810ea",
    "PRIN-02": "362fd4a41912927f80231cb64343275da10738c874b139e8ef5d97f240b5ebaa",
    "PRIN-03": "12b8eb803282b27676b2130a1a1866873f9e051bb6f037e9bac29c13a8a9df20",
    "PRIN-04": "27faf0a14ee7ba2243273823b4d9cb2b799fdb78656450b08f885940d85ca9fa",
    "PRIN-05": "f76db6ae4946cd28f8d2bfadcab5fa5d2042ecb3f9df54dfc923e19a78ee311f",
    "PRIN-06": "646dcaa08fc0b1ccb19c06bb8e36edaaaa73d3f149b5d5a6cc29869d47b30f8b",
    "PRIN-07": "10126d7064f1b29c519ffd539a6b244f275556cd3620d6a002c3a8f8ff8ae153",
    "PRIN-08": "fc77ea6c2ac1578c52faeb6b1339b71a02b252feed2889ae51fa45d7d0dc7234",
    "PRIN-09": "13a02323a1de3bf90a9c66b9896f8c5679dc70e817b392dfcd9011cd41f8e8da",
    "PRIN-10": "f582cb827a203f1c41eb44c9f32fffee9794d30fabaf16ac716b9ac61e2dc501",
    "PRIN-11": "9899149c9c43c64dd92437d8f308e904a1ceef476caed31991db9b6a51a7831e",
    "PRIN-12": "34451a7fa93e9a650c3c7749c2cfdc5389493a572ee79208358a82cef2b57e54",
    "PRIN-13": "d195a9e45f3bd453cb38dcb5a6e54b05c70b9c9659deada9821bcd3e8cdd7197",
    "PRIN-14": "0558019548563ad4fb4d4d91f2f773a77bba41fd514c445aadf5b945a125994d",
    "PRIN-15": "90be2081d639d45bb7d26175379861921b45583940b7395fdcc96ef79b880033",
    "PRIN-16": "a238a153e27b3a4b1400265af1dac0088f1e335261593e942082785a6d2baaf7",
    "ARB-01": "1a67b00c3738f571f11d94031ef40fba5c54937f0af69b735d56e05660e84eae",
    "ARB-02": "9e086e3540812c5b7c7efb90471440e46b7ad13b75bb254a3702286c516793b4",
    "ARB-03": "42b76925883fb556945fa6305a1b96326032feaaabc36369d11245f014c0a707",
    "ARB-04": "d6bb975a2dcdf2e9f9c56a96fd56e3ffd19ff1dbec463d7c683241adc2aa7992",
    "ARB-05": "e30d3501155c4f229f3d60de827ca8c8945430c4110a1750f24fc9d3296a7a8c",
    "ARB-06": "d5663b8ab26e05d1877e9ee157da681106a8530017f7f24eddcc645586eafb6c",
    "AUT-01": "a2568ec3f935d6bfcdfaac1647f702e7097d680416e5e5ff13cb28d547130b77",
    "AUT-02": "29d3965653f9ca167a6de42c97581b88da3e3d58ffd4ddc950868104d02223ce",
    "AUT-03": "2ea69d38e84d3960393007726706a0bd025cb5720fd2c5668f45afdcadf33005",
    "AUT-04": "5200703bd134cca442b535b6db5cba56326aa3a1b58b74779a3fccb0638b60bd",
    "AUT-05": "b037e27552607b6b33548f00190c44ff4e1ba66ba92fe386d4129010b68a42c2",
    "AUT-06": "235ff9d4d9df1a836df896d49ca030bc5f598e5bc73e853bca02b33d5d32c698",
    "GOAL-01": "a2d7040901f7bd07cdc689e08650aed9f3b41cebf743fc177c4286417a3ff032",
    "GOAL-02": "f44bb2c9a7bbeb7730219e2de65f6d4e4e86860b5a416e776e4ced3988158863",
    "GOAL-03": "2acb4772ef6e23fa31165febd88ad9ed1a7764a1b91e1aa2ddc91dfe1eac7e40",
    "GOAL-04": "d99847eef546af4deeb53c8988bdb610569125202f1f6d1ac47f229310bf31c7",
    "GOAL-05": "a32f409090f9cc68ec0656992a21bf2bc68ecae303b4c7ee5a185f68eb37252c",
    "GOAL-06": "8111b20b48cdbd76062204803af177ad76e44d6111851f122604bce44ae8f0e0",
    "ARCH-00": "db010c82c749eee7a76152d52de8bbbfde078b16416b23a03db3cdc2055aa971",
    "ARCH-01": "4d7e79b7084627ddba51591af313659931d1388ed6e6bd938d6b44824f26c06c",
    "ARCH-02": "04f31895e08f8cc55862e10af3d1d7a3881da40b9ede3489d8f875388139f9ce",
    "ARCH-03": "25f1e4273cc100b4f05c590266f857f5e614f03ebbee00ba15bfc82a4f7cd7a3",
    "ARCH-04": "3406e5682f587c3ea567b87bfca46e2f19601632b71a7c2e3200015d1650af9f",
    "ARCH-05": "dc0120f7b5518c8f13c3328694c80f3d01429d87a9a598b466e4cfe7713ddcbd",
    "ARCH-06": "1d6fb8c491a54d29eb25dc1928d26db2e0240f5cf8e32060d08cacc74042528c",
    "ARCH-07": "c5030b902ddf9c4ee00e15abe065037dcf4d4bbcf443536984d9c903d49d7c5c",
    "ARCH-08": "5e990e2ff592f8610b8e7bb9df1255182b011d0c4ba24c746a37765cc0b310be",
    "REG-01": "ccb435757f009f58efa1100b59ccacbe2e365d287c4c4ff03f43a702f4cac387",
    "REG-02": "ad6a00f41c0a249b786c002aed2a11e85d5c9230888db2b76361664ab7dc98bd",
    "REG-03": "421542bb4a221a29c2125b4ebe2283efe94da8cfff70bcc688649cc3544cbbd1",
    "REG-04": "1bf99e6baa4e8cf5a13c6dcfd77ac414bb7801f7f2eac7209ee676aa2b563d20",
    "REG-05": "7d95419d2ccf378f11cb04263659070bf98ed344be7c6509c621ee644e56c786",
    "REG-06": "4035125ab9ee37c0fdacb41260ba16875fbf9a03d593279c54f24fb3240c2cee",
    "REG-07": "7237c564449737ceee536e8d83e0e0bb61e8ba52e99a97ad700b52afb15679f6",
    "REG-08": "92bc502e4f17f0608e162cb5cedaa692c723e6b4a550e88f71fb47cded15da05",
    "REG-09": "dccef305fe5998c7abf4a4b4581be895e42649e9cab8f2f5d8e3a3aae6905afe",
    "REG-10": "d32c486a9585d73ebd6502d23b192ba29182e66c87e8b6357806b7b02dd6fe0f",
    "REG-11": "143b8ac76391db6c73c84f173bc41869ba4d9b41e73ee5cccbfcf02c775d0a9a",
    "REG-12": "750a439952ab96f08c6aa1864e520630101607dbdc9946b8f34aee725b511be5",
    "REG-13": "823cd2e5d9f3b5a69a0ad0d14a52dcbf3e7789fad651979f10eba4a2604e074e",
    "REG-14": "9263f98bbfb3e9048bb12ebb51a376a1923cdccc49519d5b2de2a7f43e8c7157",
    "REG-15": "d82797d9121054d8aa406b510f2aebb774401a936bcaf2c01cacea1f44b50939",
    "REG-16": "c4e89cfcbb027ebfc9b8eea3b1a772594af8e3bb7ef4e79807b695e4300e3c3c",
    "REG-17": "36bf688fcbd1291a9e1baa94d5534a2148809629b44689628d8f25b75a698517",
    "REG-18": "9610a4fb6f1d6f5a3ff175a6d53cd5731ff718c48afaeaf22e186ca519e4f02d",
    "REG-19": "4838d100b05c1ffacbbf1e9dc50cb9ae9e6ab5bab7d4193c93f080c43b677100",
    "IND-01": "f2aa9ed30f35944f3466ed62e03dd98e3cdfdb01bd5b8c2ea205a01aa9ad2b90",
    "IND-02": "20c4bf861fc8c9d61b10491f0dc922b90b722d2e43f895e5a1e0b15a9c1f8759",
    "COLD-01": "009ebe47d57687877a1b1da87928837476a66a58ad783d47e545cce4fdaa2516",
    "COLD-02": "6b493f266bbf8482cff2ed1b4ade60c5bf035d185be9b37ac56ba305cf82d8fc",
    "COLD-03": "3f0eb5694e0d5e535607791ad6f3d36cc6396874a50380510432a3eea2935062",
    "COLD-04": "c4d6c50b0d09e980c40629bc509e251c6a13d5700d89e5a7a6385a79a07c4ed1",
    "COLD-05": "bb9adf3c24eb9729a03215c8d04761c9fd669cd320af5d06b115f04de244204b",
    "COLD-06": "6eb7da4366d6cab8e71787d882d75837b883662f0f4db6e962641acd8660a7f5",
    "COLD-07": "1cb34d55c6070dcd1bb1b26443429cf705b959deeed5225a573c00e74d53475a",
    "HRV-01": "486be1292957fb58aa5b8e40487f874e32c2d63144cbbf54d8c2bf01d1e4b00c",
    "HRV-02": "e52b6e5547888b7288d9b9ed493a423a8cd8b8901f36118f62e351d403576c7f",
    "HRV-03": "4e8df7572e2707c4a16d6d68ad9f368378ba03e98fbedd3b9b33e0399d6245cc",
    "HRV-04": "6208dc9dcf3eb26699c70db120f8b91c93e6989444d88a071ffe2abd10245426",
    "HRV-05": "9e32fc15d0eff8847f4c9555acf3e0efa58dd912329df24de9f060a950f8651d",
    "HRV-06": "89baa7eacc952289c3a0fd8e1e0b97ba0628070dd918cee743d24cd14cc1ecc7",
    "HRV-07": "802f09b695a24d7fb94f6a429341cf308748e9ebde1f18e6adadf5bac0dabb28",
    "HRV-08": "168d5d868a94ab630f77e3ad784603667589fc929324ca9ab12f036667411ef8",
    "HRV-09": "84d65922659a1ad181b2fc3b7211f8727e477c00d8fed81096dbc563c9506950",
    "HRV-10": "19a721b519e6cdb7cadbc01b9fb1070daa05c57a105c612637bea451f240ed40",
    "HRV-11": "c5f1b06b72ffc5d681810e2d174a16430f9ec09d12e84c805c2a8a2d5b557110",
    "HRV-12": "232aec4c8dd81194cf083d243d1006e368eb600597d43bc1e518421990b8c379",
    "HRV-13": "420ade1969ee129933da7a48f458beaa9ac3089cb6822098348109a04cd2cc11",
    "HRV-14": "d08cbf5c9f9688652f2d0b97a44bfadedad890f4f33ba2d87038a9068f153b2d",
    "HRV-15": "76c68fe4f5656eeb7ba596da897b852c94b100f23b0702ed826ce31ac09e67cc",
    "HRV-16": "e2b1bd21387a269413e34d4326544335eb48a75d58dffd04ce1f5b99059fc001",
    "HRV-17": "836242ccfbf4d195f1fb489f7f8d555a1bda6477bf5d34c39b2b9f1ba149e4e1",
    "HRV-18": "1a77e83fac678e8926191e119d2ab3982d5c6a29e92cb75b2e39bca5a26647c1",
    "HRV-19": "e9422a5b88ed37178ba14a7f39e900cf333f6b8b5916c65909f4a4170503f2cb",
    "HRV-20": "04a7096b207e8efa7a2ab0e1c6a978758197fc39731cadb1111c0fa4925da065",
    "HRV-21": "6279b46c7af3222008859ddbe028d168f8ec1263d39f0228f59986d9e11511f1",
    "HRV-22": "6425c3dc986c69588b8f6ce8fb2242d695f5d54bab758e56e0e7888bbdb0338c",
    "HRV-23": "4008ffca252350a90fe1c0737c6fbe6e41a6b6a54ee46042ddea2e2111be3c18",
    "HRV-24": "e3e6c95a00ce4aa9ba4d47df103cd018df37c9f37157b9698859967604bfe280",
    "HRV-25": "7599d760f2575bfe1e6894732ddb6f51b8fb082c171c89fddf6be8f368295dcb",
    "HRV-26": "b664195613dde72ae6b7b755dcd3caedf5a25800405f066010a28d6c7f8df2d2",
    "HRV-27": "f979cc796f97003fed8d3f333ce42e6ac0c51d215bf07074722f3624f3eab348",
    "HRV-28": "ab9840be76b2180f1e7ae79db0db0ce271b63024cc33af38bd61a1d2de3cad38",
    "HRV-29": "8f8caab205c2d90e25eb2c1ec94c3e6a6f98c16fc2dcf6516f2c8ef95b5c5f33",
    "HRV-30": "30b330540e71737203ad484a762073558af186b5736cb3f3853c25e05164c157",
    "HRV-31": "587225f6d9f80507099bf098e26f393bea09853255ff6d050a5390c6118dc88b",
    "HRV-32": "5b3be1f258656e5316650bf291366b7c1cfcf0ffec94e592900ee6b6093f9815",
    "HRV-33": "0ad2a7cc479f2c4beb78a9ff5729c2173fd3a92c93cb1910d740b33f9d2440bd",
    "HRV-34": "c07c87cf0250a1c54e10e4812112344de42df12005a00606f2f4bb5c24a42783",
    "HRV-35": "5d19e18bd666290515975636ed84c3503efd03047881bd198fa31768b30f3215",
    "HRV-36": "2154aaebbd01ba447ab3dbab979886c3a595f0c399e38681d4a61701821b4f0c",
    "HRV-37": "736e1cfffc85fcd15d220ae5d589992cb6849fa10129248cf2a6654c722db8b6",
    "HRV-38": "3d7b0fc1f7255ae28cd8c7b2293ff1ed1bb917dcadf2ed79bf48ab1b09beed22",
    "HRV-39": "6a25f7f9ec23cbbd61ac332ebecc30c8781605547e2c4fdef4248a6f3be22748",
    "HRV-40": "4a08571527a20376f753ada1696fad672c57e70cb3601fcafc05f846f07f6f22",
    "HRV-41": "40f6b95aa248f60866cd277f29e4b5349e881b07110c02b7c008eeb549268072",
    "HRV-42": "1f949bf4604936fea7eb17a7b35ad1bee6087efd636bd03e391a41fe964f4a72",
    "HRV-43": "ef2024da40bbec8ed6f204aba828549107356857052954321a436c2fa8974009",
    "HRV-44": "97106368fd9f2e823e3c26f65c4f4df06f9c549e9e884eb0f727e155cafdbc25",
    "HRV-45": "acd117273dcbdd5497d86b241566e3f5ec52c68afe8c0141419d11396b101927",
    "HRV-46": "6d8fcfc7e17b7f011c5ee7c9ed76f6f98025b6efe68626f4ea9539e5d4401f0e",
    "GATE-01": "2a965a10cbf650c57d83aa8e4a806bea7d7bec14f7581ac40f3d4a92978971be",
    "GATE-02": "a01cf69806d1c64486825200f9cda3043bd0ffff09d071aa187f8d29fe74ce5c",
    "GATE-03": "8d247bd888191f4a8619b234948728fd76997254b04922f40662076f1e797609",
    "FIG-01": "8cdbac6957741ca816a5d8985d4e69d206908923ecbe7e4614fe438ec4ffdabb",
    "FIG-02": "b524a188a7f7646d347bdc346646289f93946395550944ea4b3c83542c3545b3",
    "FIG-03": "cd45e40b32d250ec912b1a76d4503c4298a37e3491c82050a11bdc5a239f6357",
    "FIG-04": "1ce77b01c6f920d5e9fed44fc312bddb8e23a97f3feb4ab244b902308fbeddb1",
    "FIG-05": "b17de39f087ce861fdbda9f625bda5c18f35cdcc6d9cb91fcc063de5166e6e3b",
    "LT1-01": "349ba6651fe98e2ed69cdaa782e1e720687a2ab5f80c8fbb25d6f472fe072365",
    "LT1-02": "571af3e4d17a10a795a629a91d60a47b6b3aa368c6037570c09d53427de62443",
    "FTO-01": "632ff24a5be6d51ff6a08c26188e7b2cd88c919a0088b6f32cc566b7b1adad63",
    "FTO-02": "44b5621ae01cfee35376b0d846edaf37b48690fac9b6752d394d3da8f360bdf2",
    "FTO-03": "964f90eec1491ab9a0ec1ace8358e568bc4b7634a2b85d06ba9bf5610172844e",
    "FTO-04": "a812615e7d34b95ffa14324044569ee399a674d6ea934c312b370990b7c2fda0",
    "FTO-05": "f10b95cd6a2ab9bc78e51766a8b551cfdb585d7dbcda30825f1f66971b15bf7a",
    "FTO-06": "a8e0e2922e2fe687a7cd96f3e746871e0d1c0a559e3f6b2f1aa7299e00040f34",
    "DEC-01": "926e5c5de04fa555cc83ce2b30a888e7387ab8f9f743f4ffee5eadca1bf97289",
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

#: The decisions other than a C-number that may authorize a ``yes`` row (sprint-007 review iteration
#: 1, M2). Read 2026-09-25 from every ``yes`` row's decision cell in the committed table at 9cb592c:
#: ``HRV-11`` (the critique-round call, H-40) and ``T-07`` (R9's band renames, H-41) are the only
#: tokens there that are not C-numbers. ``R13`` is the user's 2026-09-25 rulings on this review
#: (M3, S9-S11), whose rows the next pass adds. Frozen: a ``yes`` row citing nothing in this set and
#: no C-number outside NO_ONLY is a meaning change no decision authorized. Do not widen it to fit.
NON_C_AUTHORITIES = frozenset({"HRV-11", "T-07", "R13"})

#: R3: the no-only decisions whose old meaning is still stated downstream, so every row citing one
#: names an old-meaning key (F008 AC6; S5).
KEYED_NO_ONLY = ("C19", "C25")

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
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body)]


def parse_traceability(text: str) -> list[dict[str, str]]:
    """The table's rows, each keyed by ``TRACE_COLUMNS``. Takes the committed table (with its exact
    header and separator) or a header-less draft fragment. Raises ``ValueError`` on a row with the
    wrong number of columns or a header that is not ``TRACE_HEADER``."""
    rows = []
    for number, line in enumerate(_lines(text), 1):
        if not line.strip().startswith("|"):
            continue
        cells = _split_cells(line)
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
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

    def close(end: int) -> None:
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
            close(number)
            state = None
            if line not in HEADINGS and line != GLOSSARY_HEADING:
                errors.append(f"[grammar] line {number}: a heading not in HEADINGS: {line[:100]!r}")
            continue
        if not line.strip():
            close(number)
            continue
        m = _RULE_LINE.match(line)
        if m:
            close(number)
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
    close(len(lines))
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
    ``FORBIDDEN_DIRECTION_CLAUSES`` clause after ``normalize()``."""
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
    for clause in FORBIDDEN_DIRECTION_CLAUSES:
        if not any(normalize(clause) in normalize(d) for d in t24):
            errors.append(f"[glossary] T-24 does not carry {clause!r} (after normalize)")
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
    at least one C-number outside NO_ONLY or a token of ``NON_C_AUTHORITIES`` -- a no-only C-number
    never justifies a meaning change on its own, but may share an authorized cell (R3 "A shared cell",
    e.g. PRIN-08's "C24, C25"), and a cell of ``—`` authorizes nothing (M2); every ``yes`` row names an
    old-meaning key (F008 AC6); every row citing C19 or C25 names a key, a ``no`` row one whose decision
    cites that C-number, and any row citing C25 C25's own key (R3; S5); every key a row names records
    a decision that shares a token with the row's decision cell, so a key cannot be borrowed from
    another row's change (M2); an addition row cites a decision. The key checks read ``meanings``
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
            authorized = any(c in YES_ROW_OWNER and c not in NO_ONLY for c in cs) or bool(
                cell_tokens & NON_C_AUTHORITIES)
            if no_only and not authorized:
                errors.append(f"[decision] {label}: {', '.join(no_only)} never authorizes a yes on its own; "
                              "cite the C-number that changes the meaning, or mark the row no (R3)")
            elif not authorized:
                errors.append(f"[decision] {label}: a yes row cites no decision that authorizes a meaning "
                              f"change ({row['decision']!r}); cite a C-number outside NO_ONLY or one of "
                              f"{sorted(NON_C_AUTHORITIES)} (R3, M2)")
            if _is_blank(row["old-meaning key"]):
                errors.append(f"[decision] {label}: a yes row must name an old-meaning key (F008 AC6)")
        keys = _keys(row)
        key_tokens = {k: set(_DECISION_TOKEN.findall(meanings[k].decision)) for k in keys if k in meanings}
        for c in KEYED_NO_ONLY:
            if c not in cs:
                continue
            if not keys:
                errors.append(f"[decision] {label}: cites {c}, whose old meaning is still stated downstream, "
                              "and names no old-meaning key (R3, F008 AC6)")
            elif ((meaning == "no" or c == "C25") and len(key_tokens) == len(keys)
                  and not any(c in t for t in key_tokens.values())):
                errors.append(f"[decision] {label}: cites {c} and names no old-meaning key whose decision is "
                              f"{c}: {keys} (R3, F008 AC6)")
        for k, tokens in key_tokens.items():
            if not tokens & cell_tokens:
                errors.append(f"[decision] {label}: old-meaning key {k!r} records decision "
                              f"{meanings[k].decision!r}, which shares no decision with the row's cell "
                              f"{row['decision']!r} (M2: a key cannot be borrowed)")
        if _is_blank(row["inventory ID"]) and _is_blank(row["decision"]):
            errors.append(f"[decision] {label}: an addition row must cite a decision")
    if complete:
        cited = {c for r in rows for c in _C_ID.findall(r["decision"])}
        yes = {c for r in rows if r["meaning changed"] == "yes" for c in _C_ID.findall(r["decision"])}
        errors += [f"[decision] {c} appears in no decision cell (R3)" for c in DECISIONS if c not in cited]
        errors += [f"[decision] {c} backs no yes row (R3; YES_ROW_OWNER gives it to {YES_ROW_OWNER[c]})"
                   for c in DECISIONS if c not in NO_ONLY and c in cited and c not in yes]
    return errors


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


def inventory_sentence_errors(rows: list[dict[str, str]]) -> list[str]:
    """AC9's proxy input, frozen (sprint-007 review iteration 1, S3): every non-addition row's
    "inventory sentence" cell is non-blank and hashes, after ``normalize()``, to
    ``INVENTORY_SENTENCE_SHA256[ID]``. Without it, a cell set to ``—`` left the proxy, and a cell
    edited alongside its rule passed it."""
    errors = []
    for row in rows:
        inv = row["inventory ID"]
        if _is_blank(inv) or inv not in INVENTORY_SENTENCE_SHA256:
            continue
        cell = row["inventory sentence"]
        if _is_blank(cell):
            errors.append(f"[inventory] {inv}: the inventory sentence cell is blank ({cell!r})")
        elif hashlib.sha256(normalize(cell).encode("utf-8")).hexdigest() != INVENTORY_SENTENCE_SHA256[inv]:
            errors.append(f"[inventory] {inv}: the inventory sentence cell is not the frozen inventory "
                          f"sentence: {cell[:120]!r}")
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
        cache: dict[str, str] = {}
        for key, entry in meanings.items():
            path = entry.source.split(":", 1)[0]
            if path not in cache:
                cache[path] = normalize(show(path))
            if normalize(entry.example) not in cache[path]:
                errors.append(f"[old-meaning] {key}: example is not verbatim in {path} at 4e47d0e")
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
#: F008 AC6: every ``yes`` row names an old-meaning key. Each group's ``yes`` rows share one key; the
#: arch-dec rows share C05's, and the other two quote ``_old_show``'s text so the verbatim check holds.
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
            # AC6 and R3: a yes row, and a row citing C19 or C25, names the group's key (S5).
            has_key = meaning == "yes" or any(c in KEYED_NO_ONLY for c in cs)
            keyed += cs if has_key else []
            key = _WORLD_OLD[group]["key"] if has_key else ADDITION
            rows.append(_row(inv, _world_sentence(inv), new, ", ".join(cs) or ADDITION, meaning, key))
        # M2: the shared key records every decision of the rows that name it, so none borrows it.
        old = dict(_WORLD_OLD[group], decision=", ".join(dict.fromkeys([_WORLD_OLD[group]["decision"], *keyed])))
        meanings.append(json.dumps(old, ensure_ascii=False))
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
             "which shares no decision with the row's cell '—' (M2: a key cannot be borrowed)")],
            id="m2-yes-without-decision-and-borrowed-key"),
        # M2: an authorized yes row still may not borrow another decision's key.
        pytest.param(
            _row("DOC-03", "s", "DOC-03", "C38", "yes", "C04-hole-at-least"),
            [("[decision] DOC-03: old-meaning key 'C04-hole-at-least' records decision 'C04', which shares no "
             "decision with the row's cell 'C38' (M2: a key cannot be borrowed)")],
            id="m2-borrowed-key-on-an-authorized-row"),
        # M2: a non-C token outside NON_C_AUTHORITIES authorizes nothing.
        pytest.param(
            _row("REG-02", "s", "REG-02", "T-08", "yes", "T07-acwr-band"),
            [("[decision] REG-02: a yes row cites no decision that authorizes a meaning change ('T-08'); cite a "
             "C-number outside NO_ONLY or one of ['HRV-11', 'R13', 'T-07'] (R3, M2)"),
             ("[decision] REG-02: old-meaning key 'T07-acwr-band' records decision 'T-07; downstream reach is "
             ".claude/rules/ only (R9, F011 S12)', which shares no decision with the row's cell 'T-08' "
             "(M2: a key cannot be borrowed)")],
            id="m2-unfrozen-non-c-token"),
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
    """The other side of M2: each frozen non-C token authorizes a ``yes`` row by itself."""
    for token in sorted(NON_C_AUTHORITIES):
        errors = [e for e in decision_column_errors([_as_row(_row("REG-02", "s", "REG-02", token, "yes", "k"))],
                                                    False, meanings={}) if "authorizes" in e]
        print(f"[slice compared] {token} -> {errors}")
        assert errors == [], (token, errors)


def test_glossary_term_errors_names_a_renamed_term_a_cut_clause_and_a_twice_defined_term() -> None:
    """S4 on synthetic glossaries built from the frozen terms: green on the full glossary, and each
    mutation scanner B ran on the real file reds with its own message."""
    t24 = ("- **T-24 forbidden direction** IS asserting `hrv_normal` on evidence the system reports as "
           "insufficient, or while any dataset reported in the same response reads below its own HRV SWC band, "
           "whether or not that dataset is judgeable.")
    lines = {tid: f"- **{tid} {term}** IS a definition." for tid, term in GLOSSARY_TERMS.items()}
    lines["T-24"] = t24

    def text(**override: str) -> str:
        return "\n".join([GLOSSARY_HEADING, "", *{**lines, **override}.values(), "", HEADINGS[1]]) + "\n"

    cut = t24.replace(", or while any dataset reported in the same response reads below its own HRV SWC band", "")
    cases = {
        "full": (text(), []),
        "renamed": (text(**{"T-24": t24.replace("forbidden direction", "unsafe direction", 1)}),
                    ["[glossary] T-24 names the term 'unsafe direction', not 'forbidden direction'"]),
        "cut-disjunct": (text(**{"T-24": cut}),
                         [("[glossary] T-24 does not carry 'or while any dataset reported in the same response "
                          "reads below its own HRV SWC band' (after normalize)")]),
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
        pytest.param("doc-goal", _sub("doc-goal.trace.txt", "| C24, C25 | yes | doc-goal-old |", "| C24, C25 | yes | — |"), "old-meaning key", id="yes-row-without-key"),
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
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
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
