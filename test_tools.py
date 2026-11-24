# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Risk, rebalancing, digest and copilot behaviour, using the bundled demo portfolio."""

import fixtures_setup  # noqa: F401  (demo seed for tests)
import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import copilot
from analytics import (
    annual_volatility, build_digest, daily_returns, digest_text, drawdowns, plan_rebalance,
    risk_report,
)
from dividends import Dividend
from performance import Series
from portfolio import Ledger, PortfolioError
from storage import Store
from valuation import Overview, SalesReport

TODAY = date(2026, 11, 21)


class Demo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        store = Store(Path(cls.tmp.name), "demo")
        cls.book = store.snapshot().ledger
        cls.rates, cls.hist, cls.pay = store.fx_rates(), store.histories(), store.payments()
        cls.sales = SalesReport(cls.book, cls.rates)
        sectors = {k: v.sector for k, v in cls.hist.items() if v.sector}
        cls.view = Overview(cls.book, store.quotes(), cls.rates, cls.sales, sectors)
        cls.risk = risk_report(cls.view, cls.hist, TODAY)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ctx(self, targets=None):
        return copilot.Context(self.view, self.sales, self.hist, list(self.pay.rows.values()),
                               self.rates, self.risk, TODAY, targets or {})


class MathTests(unittest.TestCase):
    def test_drawdown_and_volatility(self):
        self.assertEqual(drawdowns([100, 120, 90, 110]), (-25.0, (110 / 120 - 1) * 100))
        self.assertIsNone(annual_volatility([0.01] * 5))
        self.assertAlmostEqual(annual_volatility([0.01, -0.01] * 15), 15.87, 1)
        self.assertEqual(len(daily_returns([(0, 10.0), (1, 11.0), (2, 9.9)])), 2)


class RiskTests(Demo):
    def test_concentration_flag(self):
        self.assertTrue(any("MSFT" in f.title and f.level == "High" for f in self.risk.flags))
        self.assertGreater(self.risk.top3, self.risk.top1)
        self.assertEqual(self.risk.without_history, [])

    def test_incomplete_sgd_valuation_leaves_portfolio_risk_unpriced(self):
        quotes = {row.position.symbol: row.quote for row in self.view.rows if row.quote}
        view = Overview(self.book, quotes, {})
        self.assertIsNone(view.value)
        start = TODAY - timedelta(days=30)
        closes = {start + timedelta(days=i): Decimal("1.50") + Decimal(i) / 100
                  for i in range(30)}
        history = Series("ME8U.SI", "SGD", start, TODAY, "test prices", closes)
        summary = risk_report(view, {"ME8U.SI": history}, TODAY)
        sgd_holding = next(h for h in summary.holdings if h.symbol == "ME8U.SI")
        self.assertIsNotNone(sgd_holding.volatility)
        self.assertIsNone(summary.portfolio_volatility)
        self.assertIsNone(summary.portfolio_drawdown)

    def test_sold_in_full_marks_only_closing_sells(self):
        closed = self.book.closed_sales
        by_id = {t.id: t for t in self.book.trades}
        self.assertTrue(closed)
        self.assertTrue(all(by_id[i].side == "SELL" for i in closed))
        self.assertNotIn("MSFT", {by_id[i].symbol for i in closed})


class RebalanceTests(Demo):
    def test_full_rebalance_sells_overweight_and_never_oversells(self):
        plan = plan_rebalance(self.view, {"MSFT": Decimal(20)}, Decimal(0))
        line = {l.symbol: l for l in plan.lines}["MSFT"]
        self.assertLess(line.shares, 0)
        self.assertLessEqual(abs(line.shares), self.book.positions["MSFT"].qty)
        self.assertLess(line.after, line.weight)

    def test_buy_only_spends_at_most_new_cash(self):
        plan = plan_rebalance(self.view, {"V": Decimal(20), "AAPL": Decimal(12)}, Decimal(2000), "Buy only")
        self.assertTrue(all(l.shares >= 0 for l in plan.lines))
        self.assertGreaterEqual(plan.cash_left, 0)

    def test_band_suppresses_small_drifts(self):
        current = {r.position.symbol: self.view.weight(r) for r in self.view.rows}
        near = {s: w.quantize(Decimal("0.1")) for s, w in current.items()}
        self.assertTrue(all(l.shares == 0 for l in plan_rebalance(self.view, near).lines))

    def test_full_rebalance_is_self_funding(self):
        equal = {r.position.symbol: Decimal("16.7") for r in self.view.rows}
        plan = plan_rebalance(self.view, equal, Decimal(0))
        self.assertGreaterEqual(plan.cash_left, 0)
        plan = plan_rebalance(self.view, equal, Decimal(500))
        self.assertGreaterEqual(plan.cash_left, 0)

    def test_rounded_equal_weights_are_accepted(self):
        equal = {r.position.symbol: Decimal("16.7") for r in self.view.rows}
        plan = plan_rebalance(self.view, equal)
        self.assertEqual(plan.reserve, 0)

    def test_targets_over_100_are_rejected(self):
        with self.assertRaises(PortfolioError):
            plan_rebalance(self.view, {"MSFT": Decimal(90), "V": Decimal(90)})


class DigestTests(Demo):
    def test_digest_contains_sections_and_arrows(self):
        d = build_digest(self.view, self.sales, self.hist, list(self.pay.rows.values()),
                         dict(events=[]), ["Headline"], self.risk, self.rates, TODAY)
        text = digest_text(d)
        self.assertIn("Portfolio value", text)
        self.assertIn("Biggest movers", text)
        self.assertIn("↑", text)
        self.assertNotIn("+", text.split("Information")[0].replace("+0", ""))


class CopilotTests(Demo):
    def test_builtin_router_answers_the_brief_questions(self):
        for question in copilot.SUGGESTIONS:
            reply = copilot.answer(self.ctx(), question)
            self.assertTrue(reply.tool, question)
            self.assertEqual(reply.source, "built-in rules")
        self.assertIn("DIS", copilot.answer(self.ctx(), "Which holdings are losing money?").text)
        self.assertIn("AAPL", copilot.answer(self.ctx(), "What is my best-performing stock?").text)

    def test_breakeven_uses_named_symbol_and_target(self):
        reply = copilot.answer(self.ctx(), "How many DIS shares to break even at 100?")
        self.assertEqual(reply.facts["symbol"], "DIS")
        self.assertEqual(reply.facts["target_price"], "US$100.00")
        self.assertGreater(reply.facts["extra_shares"], 0)

    def test_unknown_question_gets_help(self):
        self.assertIsNone(copilot.answer(self.ctx(), "What is the weather?").tool)

    def test_portfolio_summary_handles_no_open_holdings(self):
        ctx = replace(self.ctx(), view=Overview(Ledger([]), {}, {}))
        reply = copilot.answer(ctx, "Summarise my current portfolio performance.")
        self.assertIn("no open holdings", reply.text)

    def test_monthly_earnings_needs_rates_for_received_dividends(self):
        payment = Dividend(1, "AAPL", TODAY, "USD", Decimal("5"), Decimal("0"), "Received", None, "")
        ctx = replace(self.ctx(), rates={}, payments=[payment])
        reply = copilot.answer(ctx, "How much have I made this month?")
        self.assertIn("An exchange rate is missing", reply.text)
        self.assertNotIn("total_sgd", reply.facts)

    def test_model_flow_uses_tool_then_wording(self):
        calls = []

        def fake(cfg, system, user):
            calls.append(system[:12])
            if "route" in system:
                return 'Sure! {"tool": "best_performer", "args": {}}'
            return "AAPL leads at ↑ 14.62% on cost."

        cfg = copilot.LLMConfig("ollama", "m")
        reply = copilot.answer(self.ctx(), "who is winning?", cfg, send=fake)
        self.assertEqual(reply.source, "language model")
        self.assertEqual(reply.tool, "best_performer")
        self.assertEqual(len(calls), 2)

    def test_invented_number_is_rejected(self):
        def fake(cfg, system, user):
            if "route" in system:
                return '{"tool": "best_performer", "args": {}}'
            return "AAPL is up 99.99% for you."

        reply = copilot.answer(self.ctx(), "best stock", copilot.LLMConfig("ollama", "m"), send=fake)
        self.assertIn("rejected", reply.source)
        self.assertNotIn("99.99", reply.text)

    def test_model_failure_falls_back_to_rules(self):
        def broken(cfg, system, user):
            raise copilot.LLMError("down")

        reply = copilot.answer(self.ctx(), "best stock?", copilot.LLMConfig("ollama", "m"), send=broken)
        self.assertIn("unavailable", reply.source)
        self.assertEqual(reply.tool, "best_performer")

    def test_unknown_tool_from_model_is_not_run(self):
        def fake(cfg, system, user):
            return '{"tool": "delete_everything", "args": {}}'

        reply = copilot.answer(self.ctx(), "best stock?", copilot.LLMConfig("ollama", "m"), send=fake)
        self.assertIn("unavailable", reply.source)

    def test_config_needs_key_except_ollama(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"FOLIO_LLM_PROVIDER": "openai"}, clear=True):
            self.assertIsNone(copilot.LLMConfig.from_env())
        with patch.dict(os.environ, {"FOLIO_LLM_PROVIDER": "ollama"}, clear=True):
            self.assertEqual(copilot.LLMConfig.from_env().model, "llama3.1")


if __name__ == "__main__":
    unittest.main()
