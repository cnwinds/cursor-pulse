from pulse.tool_center.key_loan_delivery import (
    DELIVERY_CURSOR_DIRECT,
    DELIVERY_PROXY_ALIAS,
    LENDER_MODE_AUTO,
    LENDER_MODE_MANUAL,
    ROUTING_PINNED,
    ROUTING_POOL,
    assignment_mode_label,
)


def test_assignment_mode_label_cursor_direct():
    assert (
        assignment_mode_label(
            delivery_mode=DELIVERY_CURSOR_DIRECT,
            lender_mode=LENDER_MODE_MANUAL,
            routing_mode=ROUTING_PINNED,
        )
        == "Cursor Key"
    )


def test_assignment_mode_label_manual_pinned():
    assert (
        assignment_mode_label(
            delivery_mode=DELIVERY_PROXY_ALIAS,
            lender_mode=LENDER_MODE_MANUAL,
            routing_mode=ROUTING_PINNED,
        )
        == "指定账号"
    )


def test_assignment_mode_label_auto_pinned():
    assert (
        assignment_mode_label(
            delivery_mode=DELIVERY_PROXY_ALIAS,
            lender_mode=LENDER_MODE_AUTO,
            routing_mode=ROUTING_PINNED,
        )
        == "自动分配"
    )


def test_assignment_mode_label_pool():
    assert (
        assignment_mode_label(
            delivery_mode=DELIVERY_PROXY_ALIAS,
            lender_mode=LENDER_MODE_AUTO,
            routing_mode=ROUTING_POOL,
        )
        == "自动分配"
    )
