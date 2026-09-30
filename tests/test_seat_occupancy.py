"""同时在线座位：按人计、满员不挤走、指定账号不拒绝。"""

from pulse.proxy.occupancy import OccupancyBook, SeatChoice, seat_holder_id
from pulse.proxy.seat_assignment import include_seat_advice


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


def test_count_by_account_respects_ttl():
    book = OccupancyBook()
    _choose(book, holder_id="member:1", now=0)
    _choose(book, holder_id="member:2", now=0)
    assert book.count_by_account(ttl_seconds=180, now=0) == {"a1": 2}
    assert book.count_by_account(ttl_seconds=180, now=200) == {}


def _begin(book, now, boot="", holder="pk:k1"):
    book.begin_call(
        holder_id=holder, account_id="a1", credential_id="c1", boot_id=boot, hold_seconds=1200, now=now
    )


def _end(book, now, boot="", holder="pk:k1", recreate=False):
    book.end_call(
        holder_id=holder,
        account_id="a1",
        credential_id="c1",
        hold_seconds=1200,
        boot_id=boot,
        recreate=recreate,
        now=now,
    )


def test_call_in_flight_outlives_ttl_then_holds_after_end():
    book = OccupancyBook()
    _choose(book, holder_id="pk:k1", now=0)
    _begin(book, 0)
    _begin(book, 10)
    _choose(book, holder_id="pk:k1", current_credential_id="c1", now=20)
    assert book.count_by_account(ttl_seconds=180, now=1000) == {"a1": 1}

    _end(book, 1000)
    assert book.count_by_account(ttl_seconds=180, now=3000) == {"a1": 1}

    _end(book, 3000)
    assert book.count_by_account(ttl_seconds=180, now=4100) == {"a1": 1}
    assert book.count_by_account(ttl_seconds=180, now=4300) == {}


def test_end_call_does_not_recreate_released_seat():
    book = OccupancyBook()
    _begin(book, 0)
    _choose(book, holder_id="pk:k1", current_credential_id="c1", release_current=True, now=5)
    _end(book, 10)
    assert "a1" not in book.count_by_account(ttl_seconds=180, now=20)


def test_end_call_recreates_seat_lost_to_web_restart():
    book = OccupancyBook()
    _end(book, 10, recreate=True)
    assert book.count_by_account(ttl_seconds=180, now=1000) == {"a1": 1}
    assert book.count_by_account(ttl_seconds=180, now=1300) == {}


def test_live_proxy_keeps_long_call_seated():
    book = OccupancyBook()
    _begin(book, 0, boot="b1")
    for t in range(60, 3000, 60):
        book.note_boot("b1", now=t)
    assert book.count_by_account(ttl_seconds=180, now=3000) == {"a1": 1}


def test_dead_proxy_ends_its_calls_at_last_seen_then_holds():
    book = OccupancyBook()
    _begin(book, 0, boot="old")
    book.note_boot("old", now=100)
    book.note_boot("new", now=150)
    assert book.count_by_account(ttl_seconds=180, now=400) == {"a1": 1}
    assert book.count_by_account(ttl_seconds=180, now=100 + 1200 - 1) == {"a1": 1}
    assert book.count_by_account(ttl_seconds=180, now=100 + 1200 + 1) == {}


def test_dead_proxy_does_not_drop_calls_of_another_live_proxy():
    book = OccupancyBook()
    _begin(book, 0, boot="dead")
    _begin(book, 0, boot="live")
    for t in range(60, 3000, 60):
        book.note_boot("live", now=t)
    assert book.count_by_account(ttl_seconds=180, now=3000) == {"a1": 1}


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


def test_same_holder_on_two_accounts_counts_on_each():
    book = OccupancyBook()
    _choose(book, holder_id="member:1", current_credential_id="c1")
    _choose(book, holder_id="member:1", current_credential_id="c2", now=1)
    assert _choose(book, holder_id="member:2", now=2).assigned_credential_id == "c1"
    assert _choose(book, holder_id="member:3", now=2).assigned_credential_id == "c1"
    assert _choose(book, holder_id="member:4", now=2).assigned_credential_id == "c2"
    assert _choose(book, holder_id="member:5", now=2).assigned_credential_id == "c2"
    assert _choose(book, holder_id="member:6", now=2).assigned_credential_id is None


def test_release_one_account_keeps_the_other_seat():
    book = OccupancyBook()
    _choose(book, holder_id="member:1", current_credential_id="c1")
    _choose(book, holder_id="member:1", current_credential_id="c2", now=1)
    _choose(book, holder_id="member:2", now=2)
    _choose(book, holder_id="member:3", now=2)
    stayed = _choose(
        book,
        holder_id="member:1",
        current_credential_id="c2",
        release_current=True,
        now=3,
    )
    assert stayed.assigned_credential_id == "c1"
    assert _choose(book, holder_id="member:4", now=3).assigned_credential_id == "c2"


def test_empty_pool_is_not_a_concurrency_rejection():
    choice = SeatChoice(None, [])
    assert include_seat_advice(choice, pinned=False, release_current=False) is False
    assert include_seat_advice(choice, pinned=False, release_current=True) is True
    assert include_seat_advice(SeatChoice(None, ["c1"]), pinned=False, release_current=False) is True
    assert include_seat_advice(SeatChoice("c1", []), pinned=False, release_current=False) is True


def test_zero_cap_is_unlimited():
    book = OccupancyBook()
    for i in range(5):
        choice = _choose(book, holder_id=f"member:{i}", max_concurrent=0)
        assert choice.assigned_credential_id == "c1"
        assert choice.blocked_credential_ids == []


def test_release_keeps_seat_held_by_other_slot():
    book = OccupancyBook()
    _choose(book, holder_id="member:1", max_concurrent=1)
    left = _choose(
        book,
        holder_id="member:1",
        ranked=[("c1", "a1")],
        current_credential_id="c1",
        release_current=True,
        held_credential_ids=["c1"],
        max_concurrent=1,
    )
    assert left.assigned_credential_id is None
    other = _choose(book, holder_id="member:2", ranked=[("c1", "a1")], max_concurrent=1)
    assert other.assigned_credential_id is None
    assert "c1" in other.blocked_credential_ids


def test_held_slot_seat_is_refreshed():
    book = OccupancyBook()
    _choose(book, holder_id="member:1", current_credential_id="c2", now=0)
    _choose(book, holder_id="member:1", current_credential_id="c1", held_credential_ids=["c2"], now=150)
    assert book.count_by_account(ttl_seconds=180, now=200) == {"a1": 1, "a2": 1}


def test_loan_alias_allowlist_follows_pool_order():
    from pulse.proxy.seat_assignment import _in_pool_order

    allowlist = [("c1", "a1"), ("c2", "a2"), ("c3", "a3")]
    ordered = _in_pool_order(allowlist, [("c3", "a3"), ("c9", "a9"), ("c1", "a1")])
    assert ordered == [("c3", "a3"), ("c1", "a1"), ("c2", "a2")]
