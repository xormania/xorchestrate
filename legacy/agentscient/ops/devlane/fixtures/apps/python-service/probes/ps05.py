from service.paging import page

raise SystemExit(0 if list(page([1, 2, 3], 0, 2)) == [1, 2] else 1)
