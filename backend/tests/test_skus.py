import pytest

from app.rag.skus import find_skus, from_filename, key


def test_key_ignores_case_and_dashes():
    assert key("rk-2") == key("RK2") == key("Rk 2") == "RK2"


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("RK-2 install instructions.pdf", ["RK-2"]),
        ("RK-2_wiring_diagram.pdf", ["RK-2"]),
        ("FS161-2X12 shroud.pdf", ["FS161-2X12"]),
        ("relay kit guide 2026.pdf", []),
        ("General warranty.docx", []),
        ("CC161 and MC161 specs.pdf", ["CC161", "MC161"]),
    ],
)
def test_from_filename(filename, expected):
    assert from_filename(filename) == expected


def test_words_end_a_part_number():
    assert from_filename("xr500-manual.txt") == ["XR500"]
    assert from_filename("fs161-install-guide.pdf") == ["FS161"]
    assert from_filename("wiring-RK-2.pdf") == ["RK-2"]
    assert from_filename("FS161-2X12.pdf") == ["FS161-2X12"]


def test_find_skus_in_questions():
    assert find_skus("How do I wire the rk-2 relay kit?") == ["RK-2"]
    assert find_skus("Does a 12in fan fit the FS161-2X12 on a 1970 Chevelle?") == ["FS161-2X12"]
    assert find_skus("What fits a 2005 Silverado 2500?") == []
