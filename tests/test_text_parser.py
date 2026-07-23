from __future__ import annotations

from pulse.extract.text_parser import looks_like_usage_csv


def test_looks_like_usage_csv_positive():
    text = (
        "Date,Model,Cost\n"
        '"2026-06-17T07:10:03.037Z","auto","Included"\n'
    )
    assert looks_like_usage_csv(text)


def test_looks_like_usage_csv_negative_short():
    assert not looks_like_usage_csv("hello world")


def test_looks_like_usage_csv_negative_no_header():
    assert not looks_like_usage_csv("2026-06-17,auto,Included")
