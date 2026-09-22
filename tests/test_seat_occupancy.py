"""同时在线座位：按人计、满员不挤走、指定账号不拒绝。"""

from pulse.proxy.occupancy import OccupancyBook, seat_holder_id


def _accounts():
    return {"c1": "a1", "c2": "a2", "loan-c": "a1"}


def _choose(book, **kwargs):
    params = dict(
        ranked=[("c1", "a1"), ("c2", "a2")],
        account_by_credential=_accounts(),
        current_credential_id=None,
        release_current=False,
        pinned=False,
        pinned_credential_id=None,
        max_concurrent=3,
        ttl_seconds=180,
        now=0,
    )
    params.update(kwargs)
    return book.choose(**params)


def test_holder_id_collapses_sessions_of_one_member():
    assert seat_holder_id(member_id="m1", loan_id="l1", proxy_key_id="pk") == "member:m1"
    assert seat_holder_id(loan_id="l1") == "loan:l1"
    assert seat_holder_id(proxy_key_id="pk") == "pk:pk"


def test_fourth_holder_is_steered_off_a_full_account():
    book = OccupancyBook()
    for i in range(3):
        choice = _choose(book, holder_id=f"member:{i}")
        assert choice.assigned_credential_id == "c1"
    fourth = _choose(book, holder_id="member:3")
    assert fourth.assigned_credential_id == "c2"
    assert "c1" in fourth.blocked_credential_ids


def test_same_holder_does_not_take_a_second_seat():
    book = OccupancyBook()
    _choose(book, holder_id="member:1")
    _choose(book, holder_id="member:1", current_credential_id="c1")
    for i in range(2):
        assert _choose(book, holder_id=f"member:x{i}").assigned_credential_id == "c1"
    assert _choose(book, holder_id="member:extra").assigned_credential_id == "c2"


def test_heartbeat_keeps_current_instead_of_global_top():
    book = OccupancyBook()
    choice = _choose(
        book,
        holder_id="member:1",
        ranked=[("c2", "a2"), ("c1", "a1")],
        current_credential_id="c1",
    )
    assert choice.assigned_credential_id == "c1"


def test_already_seated_holder_is_not_evicted_when_account_fills():
    book = OccupancyBook()
    _choose(book, holder_id="member:1", current_credential_id="c1")
    _choose(book, holder_id="member:2")
    _choose(book, holder_id="member:3")
    stayed = _choose(book, holder_id="member:1", current_credential_id="c1", now=10)
    assert stayed.assigned_credential_id == "c1"
    assert _choose(book, holder_id="member:4", now=10).assigned_credential_id == "c2"


def test_release_does_not_return_the_credential_just_left():
    book = OccupancyBook()
    _choose(book, holder_id="member:1", current_credential_id="c1")
    moved = _choose(
        book,
        holder_id="member:1",
        current_credential_id="c1",
        release_current=True,
        now=5,
    )
    assert moved.assigned_credential_id == "c2"


def test_release_with_nowhere_to_go_drops_the_seat():
    book = OccupancyBook()
    _choose(book, holder_id="member:1", ranked=[("c1", "a1")])
    empty = _choose(
        book,
        holder_id="member:1",
        ranked=[("c1", "a1")],
        current_credential_id="c1",
        release_current=True,
        now=5,
    )
    assert empty.assigned_credential_id is None
    assert _choose(book, holder_id="member:2", ranked=[("c1", "a1")], now=6).assigned_credential_id == "c1"


def test_expired_seat_frees_the_account():
    book = OccupancyBook()
    for i in range(3):
        _choose(book, holder_id=f"member:{i}", now=0)
    freed = _choose(book, holder_id="member:new", now=180)
    assert freed.assigned_credential_id == "c1"


def test_pinned_keeps_credential_when_account_is_full():
    book = OccupancyBook()
    for i in range(3):
        _choose(book, holder_id=f"member:{i}")
    pinned = _choose(
        book,
        holder_id="member:owner-loan",
        pinned=True,
        pinned_credential_id="loan-c",
        current_credential_id="loan-c",
    )
    assert pinned.assigned_credential_id == "loan-c"
    steered = _choose(book, holder_id="member:4")
    assert steered.assigned_credential_id == "c2"
    assert "c1" in steered.blocked_credential_ids


def test_zero_cap_is_unlimited():
    book = OccupancyBook()
    for i in range(5):
        choice = _choose(book, holder_id=f"member:{i}", max_concurrent=0)
        assert choice.assigned_credential_id == "c1"
        assert choice.blocked_credential_ids == []
