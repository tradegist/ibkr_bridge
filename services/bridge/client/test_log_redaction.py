"""Unit tests for client/log_redaction.py — account IDs never reach the logs."""

import io
import logging
import unittest

from ib_async import Execution, Fill, Order, Position, Stock, Trade
from ib_async.objects import CommissionReport, PortfolioItem

from client.log_redaction import ACCOUNT_MASK, AccountRedactingFormatter, redact_account_ids

# Placeholder IDs in each IBKR format: individual, paper, advisor, paper advisor.
LIVE_ID = "U1234567"
PAPER_ID = "DU7654321"
ADVISOR_ID = "F2345678"
PAPER_ADVISOR_ID = "DF8765432"
ALL_IDS = (LIVE_ID, PAPER_ID, ADVISOR_ID, PAPER_ADVISOR_ID)


class TestRedactAccountIds(unittest.TestCase):
    def assert_no_account_ids(self, text: str) -> None:
        for account_id in ALL_IDS:
            self.assertNotIn(account_id, text)

    def test_position_repr(self) -> None:
        pos = Position(LIVE_ID, Stock("AAPL", "SMART", "USD"), 10.0, 150.0)
        out = redact_account_ids(f"position: {pos}")
        self.assert_no_account_ids(out)
        self.assertIn(f"account='{ACCOUNT_MASK}'", out)
        self.assertIn("symbol='AAPL'", out)

    def test_portfolio_item_repr(self) -> None:
        item = PortfolioItem(
            Stock("TSLA", "SMART", "USD"), 5.0, 250.0, 1250.0, 240.0, 50.0, 0.0, PAPER_ID,
        )
        out = redact_account_ids(f"updatePortfolio: {item}")
        self.assert_no_account_ids(out)
        self.assertIn("marketValue=1250.0", out)

    def test_trade_repr_with_order_and_execution_accounts(self) -> None:
        order = Order(
            orderId=7, action="BUY", totalQuantity=1, account=LIVE_ID,
            clearingAccount=ADVISOR_ID,
        )
        execution = Execution(execId="0001.01", acctNumber=PAPER_ADVISOR_ID, permId=42)
        fill = Fill(Stock("AAPL"), execution, CommissionReport(), execution.time)
        trade = Trade(contract=Stock("AAPL"), order=order, fills=[fill])
        out = redact_account_ids(f"Canceled order: {trade}")
        self.assert_no_account_ids(out)
        self.assertIn("permId=42", out)
        self.assertIn("execId='0001.01'", out)

    def test_bare_ids_in_free_text(self) -> None:
        out = redact_account_ids(
            f"Error 201, reqId 5: rejected for {LIVE_ID}, {PAPER_ID}, {ADVISOR_ID} "
            f"and {PAPER_ADVISOR_ID}",
        )
        self.assert_no_account_ids(out)
        self.assertEqual(out.count(ACCOUNT_MASK), 4)

    def test_double_quoted_field(self) -> None:
        self.assertEqual(
            redact_account_ids(f'account="{LIVE_ID}"'), f'account="{ACCOUNT_MASK}"',
        )

    def test_leaves_other_values_alone(self) -> None:
        # Ticker "U" (Unity), a conId and an empty account are not account IDs.
        text = "symbol='U', conId=76792991, account='', orderId=12345678"
        self.assertEqual(redact_account_ids(text), text)


class TestAccountRedactingFormatter(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = io.StringIO()
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(AccountRedactingFormatter("%(levelname)s %(message)s"))
        # Own handler, no propagation: keeps the output off the root logger.
        self.logger = logging.getLogger("test_log_redaction")
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.addCleanup(self.logger.removeHandler, handler)

    def test_masks_percent_style_args(self) -> None:
        self.logger.info("position: account=%r", LIVE_ID)
        self.assertEqual(self.stream.getvalue(), f"INFO position: account='{ACCOUNT_MASK}'\n")

    def test_masks_exception_traceback(self) -> None:
        try:
            raise RuntimeError(f"order rejected for {PAPER_ID}")
        except RuntimeError:
            self.logger.exception("placeOrder failed")
        out = self.stream.getvalue()
        self.assertIn("RuntimeError: order rejected for ***", out)
        self.assertNotIn(PAPER_ID, out)
