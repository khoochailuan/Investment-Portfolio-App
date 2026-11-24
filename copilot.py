# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""AI Investment Copilot.

Design principle: the language model never does arithmetic and never sees the trade journal.

  1. PLAN     The model reads the question and picks ONE tool from a fixed list (JSON reply).
  2. COMPUTE  Ordinary tested Python runs that tool on the live portfolio and returns facts.
  3. EXPLAIN  The model rewrites those facts as a short answer. Unrecognised multi-digit
              figures cause the app to show its built-in answer instead.

Without a model, or if the call fails, a keyword router selects the tool. A configured provider
receives the question and selected computed facts. A local Ollama server runs on this computer.

Configure with environment variables (see START_HERE.txt):
  FOLIO_LLM_PROVIDER  ollama | openai | anthropic | gemini
  FOLIO_LLM_MODEL     optional model name
  FOLIO_LLM_KEY       API key (not needed for ollama)
  FOLIO_LLM_URL       optional base URL (Ollama host, or any OpenAI-compatible endpoint)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from analytics import day_moves, plan_rebalance
from portfolio import PortfolioError, ZERO, dollars, percent, scenario, unit_price, minor_unit
from valuation import SGD, complete_sum, in_sgd

DISCLAIMER = "Information only, not financial advice."
MAX_QUESTION = 500
SUGGESTIONS = (
    "What is my best-performing stock?",
    "Which holdings are currently losing money?",
    "How much have I made this month?",
    "How many additional shares should I buy to break even?",
    "Summarise my current portfolio performance.",
    "What are my biggest risks?",
)


# --------------------------------------------------------------------------- context


@dataclass
class Context:
    """Everything the tools may read. Built fresh for each question."""

    view: object  # valuation.Overview
    sales: object  # valuation.SalesReport in SGD
    histories: dict
    payments: list
    rates: dict
    risk: object  # analytics.RiskReport
    today: object
    targets: dict
    band: Decimal = Decimal(2)


def sgd(value, signed=False):
    return dollars(value, signed, SGD)


def row_for(ctx, symbol):
    if not symbol:
        return None
    wanted = str(symbol).strip().upper()
    for r in ctx.view.rows:
        s = r.position.symbol.upper()
        if s == wanted or s.split(".")[0] == wanted:
            return r
    return None


def describe(r):
    return {
        "symbol": r.position.symbol,
        "name": r.quote.listing.name if r.quote and r.quote.listing else r.position.symbol,
        "return": percent(r.return_pct),
        "gain_sgd": sgd(r.gain, True),
        "value_sgd": sgd(r.value),
    }


# --------------------------------------------------------------------------- tools
# Each tool returns a dict of display-ready facts. "text" is the built-in answer.


def best_performer(ctx, **_):
    rows = [r for r in ctx.view.rows if r.return_pct is not None]
    if not rows:
        return dict(text="There are no priced holdings yet. Refresh prices on the Overview first.")
    best, worst = max(rows, key=lambda r: r.return_pct), min(rows, key=lambda r: r.return_pct)
    facts = dict(best=describe(best), weakest=describe(worst))
    facts["text"] = (
        f"Your best performer is {best.position.symbol} ({facts['best']['name']}), "
        f"{facts['best']['return']} on cost, an open gain of {facts['best']['gain_sgd']}. "
        f"The weakest is {worst.position.symbol} at {facts['weakest']['return']}."
    )
    return facts


def losing_holdings(ctx, **_):
    losers = sorted(
        (r for r in ctx.view.rows if r.gain is not None and r.gain < 0), key=lambda r: r.gain
    )
    if not losers:
        return dict(losers=[], text="None of your priced holdings is currently showing a loss.")
    items = [describe(r) for r in losers]
    lines = "; ".join(f"{i['symbol']} {i['gain_sgd']} ({i['return']})" for i in items)
    return dict(losers=items, text=f"{len(items)} holding(s) are below cost: {lines}.")


def earned_this_month(ctx, **_):
    key = ctx.today.strftime("%Y-%m")
    realised = ctx.sales.totals[key].profit if key in ctx.sales.totals else ZERO
    received = complete_sum(
        in_sgd(d.net, d.currency, ctx.rates)
        for d in ctx.payments
        if d.status == "Received" and d.day.strftime("%Y-%m") == key
    )
    if realised is None or received is None:
        return dict(text="An exchange rate is missing for this month's sales or dividends. Refresh prices & FX to get the complete SGD total.")
    total = realised + received
    return dict(
        month=ctx.today.strftime("%B %Y"),
        realised_sgd=sgd(realised, True),
        dividends_sgd=sgd(received),
        total_sgd=sgd(total, True),
        unrealised_excluded=True,
        text=(
            f"In {ctx.today:%B %Y} you have realised {sgd(realised, True)} from sales and received "
            f"{sgd(received)} in dividends, {sgd(total, True)} in total. "
            "Unrealised changes on shares you still hold are not counted."
        ),
    )


def breakeven_shares(ctx, symbol=None, target_price=None, **_):
    """Extra shares, bought at today's price, so selling everything at the target breaks even."""
    r = row_for(ctx, symbol)
    if r is None:
        losers = [x for x in ctx.view.rows if x.gain is not None and x.gain < 0]
        if not losers:
            return dict(text="None of your holdings is below cost, so no extra shares are needed.")
        r = min(losers, key=lambda x: x.return_pct)
    if r.quote is None:
        return dict(text=f"{r.position.symbol} has no current price. Refresh prices first.")
    p, price = r.position, r.quote.price
    assumed = target_price is None
    target = Decimal(str(target_price)) if not assumed else (price * Decimal("1.10"))
    target = target.quantize(minor_unit(p.currency))
    try:
        plan = scenario(p, [], str(target), "0", str(price), "0")
    except PortfolioError as exc:
        return dict(text=str(exc))
    unit = lambda v: unit_price(v, p.currency)
    facts = dict(
        symbol=p.symbol,
        current_price=unit(price),
        average_cost=unit(p.average),
        target_price=unit(target),
        target_was_assumed=assumed,
        extra_shares=plan.extra,
        extra_cost=dollars(plan.further_outlay, currency=p.currency) if plan.further_outlay else None,
        fees_assumed="none",
    )
    if plan.extra == 0:
        facts["text"] = (
            f"Selling all {p.symbol} at {unit(target)} would already cover your cost of "
            f"{unit(p.average)} a share, so no extra shares are needed."
        )
    elif plan.extra is None:
        facts["text"] = f"At {unit(target)}, extra purchases at {unit(price)} cannot reach break-even."
    else:
        basis = "an assumed 10% rebound" if assumed else "your target"
        facts["text"] = (
            f"{p.symbol}: you pay {unit(p.average)} on average and it trades at {unit(price)}. "
            f"To break even by selling everything at {unit(target)} ({basis}), buy about "
            f"{plan.extra} more share(s) at today's price, costing roughly {facts['extra_cost']}. "
            "Fees are ignored. Try other targets in the Recovery planner."
        )
    return facts


def portfolio_summary(ctx, **_):
    v = ctx.view
    if not v.rows:
        return dict(text="Your portfolio has no open holdings. Use Realised P/L to review completed sales.")
    if v.value is None:
        return dict(text="Some prices or exchange rates are missing. Refresh prices & FX first.")
    top = max((r for r in v.rows if r.value is not None), key=lambda r: r.value)
    facts = dict(
        holdings=len(v.rows),
        market_value=sgd(v.value),
        unrealised=sgd(v.gain, True),
        unrealised_pct=percent(v.return_pct),
        realised=sgd(v.realised, True),
        largest_holding=f"{top.position.symbol} ({v.weight(top):.1f}%)",
        risk_flags=len(ctx.risk.flags),
    )
    facts["text"] = (
        f"You hold {facts['holdings']} stocks worth {facts['market_value']}. Open P/L is "
        f"{facts['unrealised']} ({facts['unrealised_pct']}) and realised P/L is {facts['realised']}. "
        f"Your largest position is {facts['largest_holding']}, and there are "
        f"{facts['risk_flags']} risk flag(s) to review."
    )
    return facts


def risk_summary(ctx, **_):
    r = ctx.risk
    if not r.flags:
        return dict(flags=[], text="No concentration, currency or volatility flags at current thresholds.")
    items = [f"{f.title}" for f in r.flags[:5]]
    return dict(
        flags=items,
        top_holding=f"{r.top1:.1f}%" if r.top1 is not None else None,
        text="Main risks: " + "; ".join(items) + ".",
    )


def top_movers(ctx, **_):
    moves = day_moves(ctx.view, ctx.histories, ctx.rates)[:3]
    if not moves:
        return dict(text="No daily price history is cached yet. Open Performance to download it.")
    items = [dict(symbol=m.symbol, move=percent(Decimal(str(round(m.pct, 2)))), sgd=sgd(m.change, True))
             for m in moves]
    return dict(movers=items, text="Latest-close movers: " + "; ".join(
        f"{i['symbol']} {i['move']} ({i['sgd']})" for i in items) + ".")


def rebalance_summary(ctx, **_):
    if not ctx.targets:
        return dict(text="No target weights are saved yet. Set them on the Rebalance tab.")
    try:
        plan = plan_rebalance(ctx.view, ctx.targets, ZERO, band=ctx.band)
    except PortfolioError as exc:
        return dict(text=str(exc))
    off = [l for l in plan.lines if l.shares != 0]
    if not off:
        return dict(trades=[], text="Every holding is within your tolerance band of its target.")
    items = [f"{'buy' if l.shares > 0 else 'sell'} {abs(l.shares)} {l.symbol}" for l in off]
    return dict(trades=items, text="To return to target: " + ", ".join(items) + ".")


@dataclass(frozen=True)
class Tool:
    name: str
    run: object
    summary: str
    args: str = ""


TOOLS = {
    t.name: t
    for t in (
        Tool("best_performer", best_performer, "Best and weakest holding by return on cost."),
        Tool("losing_holdings", losing_holdings, "Holdings currently below cost."),
        Tool("earned_this_month", earned_this_month, "Realised profit and dividends this month."),
        Tool("breakeven_shares", breakeven_shares,
             "Extra shares to buy at today's price so a sale at a target price breaks even.",
             'optional "symbol" (string) and "target_price" (number)'),
        Tool("portfolio_summary", portfolio_summary, "Overall value, P/L and concentration."),
        Tool("risk_summary", risk_summary, "Concentration, currency and volatility warnings."),
        Tool("top_movers", top_movers, "Biggest moves at the latest close."),
        Tool("rebalance_summary", rebalance_summary, "Trades needed to return to target weights."),
    )
}


# --------------------------------------------------------------------------- built-in router

RULES = (  # first match wins, so more specific phrases come first
    ("breakeven_shares", ("break even", "break-even", "breakeven", "additional share", "average down", "recover")),
    ("rebalance_summary", ("rebalanc", "target weight", "drift")),
    ("risk_summary", ("risk", "concentrat", "diversif", "volatil", "exposure")),
    ("top_movers", ("mover", "today", "yesterday", "latest close")),
    ("earned_this_month", ("this month", "made", "earned", "income this")),
    ("losing_holdings", ("losing", "loss", "losers", "underwater", "below cost", "down")),
    ("best_performer", ("best", "top perform", "winner", "strongest", "worst", "weakest")),
    ("portfolio_summary", ("summar", "overview", "how am i doing", "how is my", "performance", "status")),
)


def find_symbol(ctx, question):
    for word in re.findall(r"[A-Za-z0-9.]{1,12}", question):
        if row_for(ctx, word):
            return row_for(ctx, word).position.symbol
    return None


def find_target(question):
    m = re.search(r"(?:target|at|to|reach)\s*(?:of\s*)?(?:S?US?\$|\$)?\s*(\d+(?:\.\d+)?)", question, re.I)
    return float(m.group(1)) if m else None


def route(ctx, question):
    """Keyword fallback: returns (tool name, args) or (None, {})."""
    q = question.lower()
    for name, words in RULES:
        if any(w in q for w in words):
            args = {}
            if name == "breakeven_shares":
                args = {"symbol": find_symbol(ctx, question), "target_price": find_target(question)}
            return name, args
    return None, {}


# --------------------------------------------------------------------------- language model


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    key: str = ""
    url: str = ""

    @classmethod
    def from_env(cls):
        env = os.environ
        provider = env.get("FOLIO_LLM_PROVIDER", "").strip().lower()
        if provider not in DEFAULT_MODELS:
            return None
        key = env.get("FOLIO_LLM_KEY", "").strip()
        if provider != "ollama" and not key:
            return None
        return cls(provider, env.get("FOLIO_LLM_MODEL", "").strip() or DEFAULT_MODELS[provider],
                   key, env.get("FOLIO_LLM_URL", "").strip().rstrip("/"))

    @property
    def label(self):
        return f"{self.provider} · {self.model}"


DEFAULT_MODELS = {
    "ollama": "llama3.1",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5-5",
    "gemini": "gemini-2.0-flash",
}


def call_model(cfg, system, user, timeout=45):
    """One request/response with the configured provider using only the standard library."""
    if cfg.provider == "ollama":
        url = (cfg.url or "http://localhost:11434") + "/api/chat"
        body, headers = dict(model=cfg.model, stream=False, options=dict(temperature=0),
                             messages=[dict(role="system", content=system),
                                       dict(role="user", content=user)]), {}
        pick = lambda d: d["message"]["content"]
    elif cfg.provider == "openai":
        url = (cfg.url or "https://api.openai.com/v1") + "/chat/completions"
        body = dict(model=cfg.model, temperature=0,
                    messages=[dict(role="system", content=system), dict(role="user", content=user)])
        headers = {"Authorization": "Bearer " + cfg.key}
        pick = lambda d: d["choices"][0]["message"]["content"]
    elif cfg.provider == "anthropic":
        url = (cfg.url or "https://api.anthropic.com") + "/v1/messages"
        body = dict(model=cfg.model, max_tokens=600, system=system,
                    messages=[dict(role="user", content=user)])
        headers = {"x-api-key": cfg.key, "anthropic-version": "2023-06-01"}
        pick = lambda d: d["content"][0]["text"]
    else:  # gemini
        base = cfg.url or "https://generativelanguage.googleapis.com"
        url = f"{base}/v1beta/models/{cfg.model}:generateContent"
        body = dict(systemInstruction=dict(parts=[dict(text=system)]),
                    contents=[dict(role="user", parts=[dict(text=user)])])
        headers = {"x-goog-api-key": cfg.key}
        pick = lambda d: d["candidates"][0]["content"]["parts"][0]["text"]
    req = Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    try:
        with urlopen(req, timeout=timeout) as response:
            return str(pick(json.load(response))).strip()
    except HTTPError as exc:
        raise LLMError(f"The model service returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError):
        raise LLMError("The model service could not be reached or replied unexpectedly.") from None


PLAN_PROMPT = """You route questions for a personal stock portfolio app.
Choose exactly one tool for the user's question. Tools:
{tools}
Reply with ONLY a JSON object: {{"tool": "<name>", "args": {{}}}}.
Use {{"tool": "none", "args": {{}}}} if the question is not about this portfolio.
Never answer the question yourself."""

EXPLAIN_PROMPT = """You are the explanation step of a portfolio assistant for retired investors.
Answer the question in at most 90 words of plain English using ONLY the FACTS provided.
Rules: copy every figure exactly as written in FACTS; do not calculate, round, convert or
estimate anything; do not recommend buying or selling; if FACTS cannot answer, say so.
End with: Information only, not financial advice."""


def plan_with_model(cfg, question, ctx, send):
    tools = "\n".join(f'- {t.name}: {t.summary}' + (f" Args: {t.args}." if t.args else "")
                      for t in TOOLS.values())
    raw = send(cfg, PLAN_PROMPT.format(tools=tools), question)
    found = re.search(r"\{.*\}", raw, re.S)
    try:
        choice = json.loads(found.group(0)) if found else {}
        name, args = choice.get("tool"), choice.get("args") or {}
    except (ValueError, AttributeError):
        raise LLMError("The model did not return a usable tool choice.") from None
    if name == "none":
        return None, {}
    if name not in TOOLS or not isinstance(args, dict):
        raise LLMError("The model chose an unknown tool.")
    clean = {}
    if name == "breakeven_shares":
        if row_for(ctx, args.get("symbol")):
            clean["symbol"] = row_for(ctx, args["symbol"]).position.symbol
        try:
            if args.get("target_price") is not None and float(args["target_price"]) > 0:
                clean["target_price"] = float(args["target_price"])
        except (TypeError, ValueError):
            pass
    return name, clean


NUMBER = re.compile(r"\d[\d,]*\.?\d*")


def numbers_in(text):
    return {n.replace(",", "").rstrip(".") for n in NUMBER.findall(text)}


def grounded(answer, facts):
    """True when every multi-digit figure in the model's wording appears in the facts."""
    allowed = numbers_in(json.dumps(facts, default=str))
    return all(len(n) < 2 or n in allowed for n in numbers_in(answer))


# --------------------------------------------------------------------------- entry point


@dataclass
class Answer:
    text: str
    tool: str | None
    facts: dict
    source: str  # "language model", "built-in rules" or "built-in rules (model unavailable)"
    note: str = ""


UNKNOWN = (
    "I can answer questions about your best and weakest stocks, holdings below cost, what you made "
    "this month, break-even share counts, risk, rebalancing and a summary. Try one of the suggestions."
)


def answer(ctx, question, cfg=None, send=call_model):
    """Plan, compute and explain. Never raises for model problems."""
    question = " ".join(str(question).split())[:MAX_QUESTION]
    if not question:
        return Answer("Type a question about your portfolio.", None, {}, "built-in rules")
    source, note = "built-in rules", ""
    name, args = None, {}
    if cfg:
        try:
            name, args = plan_with_model(cfg, question, ctx, send)
            source = "language model"
            if name is None:
                return Answer(UNKNOWN, None, {}, source)
        except LLMError as exc:
            source, note = "built-in rules (model unavailable)", str(exc)
    if source != "language model":
        name, args = route(ctx, question)
    if name is None:
        return Answer(UNKNOWN, None, {}, source, note)
    facts = TOOLS[name].run(ctx, **args)
    text = facts["text"]
    if source == "language model":
        shown = {k: v for k, v in facts.items() if k != "text"} or {"answer": text}
        try:
            wording = send(cfg, EXPLAIN_PROMPT, f"QUESTION: {question}\nFACTS: {json.dumps(shown, default=str)}")
            if grounded(wording, {**shown, "text": text}):
                text = wording
            else:
                source, note = "built-in rules (model wording rejected)", "The model quoted a figure that is not in the computed facts, so the plain answer is shown."
        except LLMError as exc:
            source, note = "built-in rules (model unavailable)", str(exc)
    if DISCLAIMER.lower() not in text.lower():
        text += f" {DISCLAIMER}"
    return Answer(text, name, {k: v for k, v in facts.items() if k != "text"}, source, note)
