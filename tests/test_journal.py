import csv

from polybot.journal import TradeJournal
from polybot.models import OrderResult, Side


def make_result(success=True, side=Side.BUY):
    return OrderResult(
        success=success,
        token_id="tok1",
        side=side,
        price=0.55,
        size=100.0,
        order_id="order-1",
    )


def read_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_records_successful_trade_with_header(tmp_path):
    path = tmp_path / "journal.csv"
    journal = TradeJournal(str(path))
    journal.record("copy", "0xtrader", "Some market?", "cond1", "Yes", make_result())

    rows = read_rows(path)
    assert len(rows) == 1
    row = rows[0]
    assert row["strategy"] == "copy"
    assert row["source"] == "0xtrader"
    assert row["side"] == "BUY"
    assert row["price"] == "0.5500"
    assert row["notional_usd"] == "55.00"
    assert row["order_id"] == "order-1"


def test_header_written_only_once(tmp_path):
    path = tmp_path / "journal.csv"
    journal = TradeJournal(str(path))
    journal.record("copy", "0xa", "m1", "c1", "Yes", make_result())
    journal.record("threshold", "threshold-entry", "m2", "c2", "No", make_result(side=Side.SELL))

    rows = read_rows(path)
    assert len(rows) == 2
    assert rows[1]["strategy"] == "threshold"
    assert rows[1]["side"] == "SELL"


def test_failed_orders_are_not_journaled(tmp_path):
    path = tmp_path / "journal.csv"
    journal = TradeJournal(str(path))
    journal.record("copy", "0xa", "m1", "c1", "Yes", make_result(success=False))
    assert not path.exists()
