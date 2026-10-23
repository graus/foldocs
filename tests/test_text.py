from foldocs.text import excerpt, photo_title, slugify, split_date


def test_slugify():
    assert slugify("Één café, twee!") == "een-cafe-twee"
    assert slugify("???") == "item"


def test_photo_title():
    assert photo_title("crosslab_day-1.jpeg") == "Crosslab day 1"
    assert photo_title("IMG_1234.HEIC") is None
    assert photo_title("PXL_20261006_140734.jpg") is None


def test_excerpt_cuts_on_a_word():
    assert excerpt("een twee drie", 9) == "een twee…"


def test_excerpt_prefers_a_whole_sentence():
    assert excerpt("Een twee drie. Vier vijf zes zeven acht.", 24) == "Een twee drie."


def test_a_date_opening_a_file_name_is_split_off():
    assert split_date("2026-08-26 Support de comeback") == ((2026, 8, 26), "Support de comeback")
    assert split_date("Support de comeback") == (None, "Support de comeback")
    assert photo_title("2026-01-16_sneeuw-in-de-stad.jpg") == "Sneeuw in de stad"
