from service.paging import page, page_count


def test_nominal_page_at_the_collection_end():
    assert list(page([1, 2, 3], 1, 3)) == [2, 3]


def test_page_count_is_half_open():
    assert page_count(4, 2) == 2
