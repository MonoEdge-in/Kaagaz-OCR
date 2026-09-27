"""The router: a fixed query only when every part is certain (kaagaz/answering/intent.py)."""

from datetime import date

from kaagaz.answering.dates import date_range, mentions_date
from kaagaz.answering.hinglish import normalise
from kaagaz.answering.intent import FixedPlan, plan_fixed

TODAY = date(2026, 9, 27)
AUGUST = (date(2026, 8, 1), date(2026, 9, 1))
REGISTER = {"party_name", "profit", "date"}


def _plan(question: str, fields: set[str]) -> FixedPlan | None:
    normalised = normalise(question)
    return plan_fixed(normalised, date_range(normalised, TODAY), mentions_date(normalised), fields)


def test_the_hinglish_example_is_a_top_group_query() -> None:
    plan = _plan("Kiska profit sabse jyada tha last month?", REGISTER)
    assert plan == FixedPlan("highest", "profit", ("party_name",), ("date",), *AUGUST)


def test_lowest_by_party() -> None:
    plan = _plan("sabse kam munafa kiska tha pichle mahine", REGISTER)
    assert plan == FixedPlan("lowest", "profit", ("party_name",), ("date",), *AUGUST)


def test_highest_without_a_party_is_the_single_highest_value() -> None:
    plan = _plan("What was the highest profit?", REGISTER)
    assert plan == FixedPlan("highest", "profit", (), (), None, None)


def test_total_amount_in_a_month() -> None:
    fields = {"total_amount", "invoice_date", "party_name"}
    plan = _plan("Total amount in August 2026?", fields)
    assert plan == FixedPlan("total", "total_amount", (), ("invoice_date",), *AUGUST)


def test_kul_kitna_means_the_total_amount() -> None:
    plan = _plan("kul kitna tha?", {"amount"})
    assert plan == FixedPlan("total", "amount", (), (), None, None)


def test_count_invoices_needs_a_date_field() -> None:
    fields = {"invoice_date", "date", "total_amount"}
    plan = _plan("Pichle mahine kitne bill aaye?", fields)
    assert plan is None  # "aaye" is a topic word the router does not know
    plan = _plan("Pichle mahine kitne bill the?", fields)
    assert plan == FixedPlan("count", None, (), ("invoice_date", "date"), *AUGUST)
    assert _plan("How many invoices last month?", {"total_amount"}) is None


def test_every_party_and_date_candidate_is_passed_on() -> None:
    fields = {"profit", "party_name", "name", "vendor", "date", "invoice_date"}
    plan = _plan("Kiska profit sabse jyada tha last month?", fields)
    assert plan is not None
    assert plan.group_fields == ("party_name", "name", "vendor")
    assert plan.date_fields == ("invoice_date", "date")


def test_two_metric_fields_for_one_word_is_a_guess() -> None:
    assert _plan("Kiska profit sabse jyada tha last month?", REGISTER | {"net_profit"}) is None


def test_a_missing_field_takes_the_search_route() -> None:
    assert _plan("Kiska profit sabse jyada tha last month?", {"profit", "date"}) is None
    assert _plan("Kiska profit sabse jyada tha last month?", {"party_name", "date"}) is None
    assert _plan("Kiska profit sabse jyada tha last month?", {"party_name", "profit"}) is None
    assert _plan("Kiska kharcha sabse jyada tha last month?", REGISTER) is None


def test_an_unknown_topic_word_is_never_ignored() -> None:
    assert _plan("Kiska profit sabse jyada tha Pune branch me last month?", REGISTER) is None
    assert _plan("Total amount of INV-1042?", {"total_amount"}) is None


def test_a_date_we_cannot_read_is_never_ignored() -> None:
    assert _plan("Kiska profit sabse jyada tha last week?", REGISTER) is None
    assert _plan("Kiska profit sabse jyada tha last month and this month?", REGISTER) is None


def test_no_aggregate_or_two_aggregates_take_the_search_route() -> None:
    assert _plan("profit of Sharma Traders", REGISTER) is None
    assert _plan("highest and lowest profit", REGISTER) is None
    assert _plan("What is the GSTIN of Sharma Traders?", REGISTER | {"gstin"}) is None


def test_two_metrics_take_the_search_route() -> None:
    assert _plan("highest profit and sales", REGISTER | {"sales"}) is None


def test_whose_total_is_not_a_single_total() -> None:
    assert _plan("whose total profit", REGISTER) is None
