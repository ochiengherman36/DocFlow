import datetime
import json
import random
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

import main
from schemas import InvoiceData
from zoho_bill import (
    ADJUSTMENT_DESCRIPTION,
    MAX_DESCRIPTION_LENGTH,
    TAX_DESCRIPTION,
    BillError,
    build_bill,
    missing_currency_message,
    payload_total,
)
from zoho_books import find_currency_id

VENDOR = "9001"
ACCOUNT = "4445"


def invoice(**changes) -> InvoiceData:
    data = {
        "supplier": "Mwangaza Office Supplies Ltd",
        "invoice_number": "INV-2026-0142",
        "date": "2026-10-28",
        "currency": "KES",
        "line_items": [
            {"description": "Printer paper A4", "quantity": 4, "unit_price": 3200.0, "amount": 12800.0},
            {"description": "Toner cartridge - black", "quantity": 2, "unit_price": 8500.0, "amount": 17000.0},
            {"description": "Stapler, heavy duty", "quantity": 3, "unit_price": 1250.0, "amount": 3750.0},
            {"description": "Delivery - Nairobi CBD", "quantity": 1, "unit_price": 800.0, "amount": 800.0},
        ],
        "totals": {"subtotal": 34350.0, "tax": 5496.0, "total": 39846.0},
    }
    data.update(changes)
    return InvoiceData.model_validate(data)


def build(inv=None, **kwargs):
    return build_bill(inv or invoice(), vendor_id=VENDOR, account_id=ACCOUNT, **kwargs)


def money(value) -> Decimal:
    return Decimal(str(value))


class TestTheBill:
    def test_the_header_comes_from_the_invoice(self):
        bill = build()
        assert bill["vendor_id"] == VENDOR
        assert bill["bill_number"] == "INV-2026-0142"
        assert bill["date"] == "2026-10-28"

    def test_each_line_is_booked_to_the_account_with_its_quantity_and_rate(self):
        lines = build()["line_items"]
        assert lines[0] == {"account_id": ACCOUNT, "description": "Printer paper A4", "quantity": 4.0, "rate": 3200.0}
        assert lines[3] == {"account_id": ACCOUNT, "description": "Delivery - Nairobi CBD", "quantity": 1.0, "rate": 800.0}

    def test_the_tax_becomes_its_own_line(self):
        lines = build()["line_items"]
        assert len(lines) == 5
        assert lines[-1] == {"account_id": ACCOUNT, "description": TAX_DESCRIPTION, "quantity": 1.0, "rate": 5496.0}

    def test_the_bill_total_equals_the_invoice_total(self):
        assert payload_total(build()) == Decimal("39846.00")

    def test_an_exact_invoice_needs_no_adjustment(self):
        bill = build()
        assert "adjustment" not in bill
        assert "adjustment_description" not in bill

    def test_no_tax_means_no_tax_line(self):
        inv = invoice(totals={"subtotal": 34350.0, "tax": None, "total": 34350.0})
        bill = build(inv)
        assert len(bill["line_items"]) == 4
        assert payload_total(bill) == Decimal("34350.00")

    def test_zero_tax_means_no_tax_line(self):
        inv = invoice(totals={"subtotal": 34350.0, "tax": 0, "total": 34350.0})
        assert len(build(inv)["line_items"]) == 4

    def test_the_currency_notes_and_nothing_else_are_added_when_given(self):
        assert "currency_id" not in build()
        assert "notes" not in build()
        bill = build(currency_id="777", notes="Sent from DocFlow")
        assert bill["currency_id"] == "777"
        assert bill["notes"] == "Sent from DocFlow"

    def test_the_body_can_be_sent_as_json(self):
        assert json.loads(json.dumps(build(currency_id="1", notes="n")))["bill_number"] == "INV-2026-0142"

    def test_building_does_not_change_the_invoice(self):
        inv = invoice()
        before = inv.model_dump()
        build(inv)
        assert inv.model_dump() == before


class TestOddLines:
    def test_a_line_with_only_an_amount_is_one_unit(self):
        inv = invoice(
            line_items=[{"description": "Consulting", "amount": 500.0}],
            totals={"subtotal": 500.0, "tax": None, "total": 500.0},
        )
        line = build(inv)["line_items"][0]
        assert (line["quantity"], line["rate"]) == (1.0, 500.0)

    def test_a_line_with_only_a_unit_price_uses_it_for_the_amount(self):
        inv = invoice(
            line_items=[{"description": "Fee", "unit_price": 750.0}],
            totals={"subtotal": 750.0, "tax": None, "total": 750.0},
        )
        bill = build(inv)
        assert bill["line_items"][0]["rate"] == 750.0
        assert payload_total(bill) == Decimal("750.00")

    def test_a_quantity_and_price_that_miss_the_amount_by_cents_fall_back_to_the_amount(self):
        inv = invoice(
            line_items=[{"description": "Washers", "quantity": 3, "unit_price": 1.33, "amount": 4.0}],
            totals={"subtotal": 4.0, "tax": None, "total": 4.0},
        )
        line = build(inv)["line_items"][0]
        assert (line["quantity"], line["rate"]) == (1.0, 4.0)
        assert line["description"] == "Washers (qty 3 x 1.33)"

    def test_a_line_that_is_not_whole_cents_is_not_left_for_zoho_to_round(self):
        inv = invoice(
            line_items=[{"description": "Cable", "quantity": 0.5, "unit_price": 33.29, "amount": 16.65}],
            totals={"subtotal": 16.65, "tax": None, "total": 16.65},
        )
        line = build(inv)["line_items"][0]
        assert (line["quantity"], line["rate"]) == (1.0, 16.65)

    def test_a_fractional_quantity_is_kept(self):
        inv = invoice(
            line_items=[{"description": "Sand", "quantity": 2.5, "unit_price": 10.0, "amount": 25.0}],
            totals={"subtotal": 25.0, "tax": None, "total": 25.0},
        )
        line = build(inv)["line_items"][0]
        assert (line["quantity"], line["rate"]) == (2.5, 10.0)

    def test_a_line_without_a_description_is_named_by_its_number(self):
        inv = invoice(
            line_items=[
                {"description": "First", "quantity": 1, "unit_price": 10.0, "amount": 10.0},
                {"quantity": 1, "unit_price": 5.0, "amount": 5.0},
            ],
            totals={"subtotal": 15.0, "tax": None, "total": 15.0},
        )
        assert build(inv)["line_items"][1]["description"] == "Line 2"

    def test_a_very_long_description_is_cut_to_what_zoho_accepts(self):
        inv = invoice(
            line_items=[{"description": "x" * 5000, "quantity": 1, "unit_price": 1.0, "amount": 1.0}],
            totals={"subtotal": 1.0, "tax": None, "total": 1.0},
        )
        assert len(build(inv)["line_items"][0]["description"]) == MAX_DESCRIPTION_LENGTH

    def test_a_line_with_nothing_to_charge_is_refused(self):
        inv = invoice(line_items=[{"description": "Mystery"}], totals={"total": 10.0})
        with pytest.raises(BillError, match="Line 1 has no amount"):
            build(inv)

    def test_a_negative_line_is_refused(self):
        inv = invoice(
            line_items=[{"description": "Refund", "amount": -5.0}, {"description": "Fee", "amount": 15.0}],
            totals={"subtotal": 10.0, "tax": None, "total": 10.0},
        )
        with pytest.raises(BillError, match="Line 1 is negative"):
            build(inv)


class TestRounding:
    def test_a_cent_of_difference_becomes_the_adjustment(self):
        inv = invoice(totals={"subtotal": 34350.0, "tax": 5496.0, "total": 39846.01})
        bill = build(inv)
        assert bill["adjustment"] == 0.01
        assert bill["adjustment_description"] == ADJUSTMENT_DESCRIPTION
        assert payload_total(bill) == Decimal("39846.01")

    def test_a_cent_the_other_way_is_a_negative_adjustment(self):
        inv = invoice(totals={"subtotal": 34350.0, "tax": 5496.0, "total": 39845.99})
        bill = build(inv)
        assert bill["adjustment"] == -0.01
        assert payload_total(bill) == Decimal("39845.99")

    def test_a_difference_the_checks_would_not_have_allowed_is_refused(self):
        inv = invoice(totals={"subtotal": 34350.0, "tax": 5496.0, "total": 39900.0})
        with pytest.raises(BillError, match="add up to 39,846.00, but the invoice total is 39,900.00"):
            build(inv)

    def test_lines_that_already_include_the_tax_are_refused_not_double_counted(self):
        inv = invoice(totals={"subtotal": 34350.0, "tax": 5496.0, "total": 34350.0})
        with pytest.raises(BillError, match="add up to"):
            build(inv)

    def test_a_missing_subtotal_and_tax_cannot_hide_a_wrong_total(self):
        inv = invoice(totals={"subtotal": None, "tax": None, "total": 1000.0})
        with pytest.raises(BillError, match="add up to"):
            build(inv)


class TestRefusals:
    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"invoice_number": None}, "no invoice number"),
            ({"invoice_number": "N" * 101}, "longer than 100"),
            ({"date": None}, "no date"),
            ({"line_items": []}, "no line items"),
            ({"totals": {"subtotal": 1.0, "tax": None, "total": None}}, "no total"),
            ({"totals": {"subtotal": 0.0, "tax": None, "total": 0.0}}, "more than zero"),
            ({"totals": {"subtotal": -5.0, "tax": None, "total": -5.0}}, "more than zero"),
        ],
    )
    def test_an_invoice_that_cannot_be_a_bill_says_why(self, changes, message):
        with pytest.raises(BillError, match=message):
            build(invoice(**changes))

    def test_a_negative_tax_is_refused(self):
        inv = invoice(totals={"subtotal": 34350.0, "tax": -10.0, "total": 34340.0})
        with pytest.raises(BillError, match="tax is negative"):
            build(inv)

    def test_a_missing_vendor_or_account_is_refused(self):
        with pytest.raises(BillError, match="vendor"):
            build_bill(invoice(), vendor_id="", account_id=ACCOUNT)
        with pytest.raises(BillError, match="expense account"):
            build_bill(invoice(), vendor_id=VENDOR, account_id="")

    def test_a_number_that_is_not_a_number_counts_as_missing(self):
        inv = InvoiceData.model_construct(
            invoice_number="A1",
            date=datetime.date(2026, 10, 28),
            line_items=invoice().line_items,
            totals=invoice(totals={"total": float("nan")}).totals,
        )
        with pytest.raises(BillError, match="no total"):
            build(inv)


def random_invoice(rng: random.Random) -> InvoiceData:
    lines = []
    for n in range(rng.randint(1, 8)):
        quantity = rng.choice([1, 2, 3, 5, 10, 0.5, 2.5, 12])
        price = round(rng.uniform(0.5, 9999.0), 2)
        amount = round(quantity * price, 2)
        if rng.random() < 0.2:
            amount = round(amount + rng.choice([-0.01, 0.01]), 2)
        lines.append({"description": f"Item {n}", "quantity": quantity, "unit_price": price, "amount": amount})
    subtotal = round(sum(line["amount"] for line in lines), 2)
    tax = round(subtotal * 0.16, 2) if rng.random() < 0.8 else None
    total = round(subtotal + (tax or 0) + rng.choice([-0.01, 0.0, 0.0, 0.01]), 2)
    return InvoiceData.model_validate(
        {
            "supplier": "Anyone Ltd",
            "invoice_number": f"R-{rng.randint(1, 10**6)}",
            "date": "2026-10-01",
            "currency": "KES",
            "line_items": lines,
            "totals": {"subtotal": subtotal, "tax": tax, "total": total},
        }
    )


class TestTheTotalAlwaysMatches:
    def test_across_many_made_up_invoices_the_bill_total_is_the_invoice_total(self):
        rng = random.Random(2026)
        for _ in range(500):
            inv = random_invoice(rng)
            bill = build(inv)
            assert payload_total(bill) == money(inv.totals.total).quantize(Decimal("0.01")), inv.model_dump()

    def test_the_adjustment_is_never_more_than_a_few_cents(self):
        rng = random.Random(7)
        for _ in range(500):
            bill = build(random_invoice(rng))
            assert abs(money(bill.get("adjustment", 0))) <= Decimal("0.10")

    def test_every_line_is_a_whole_number_of_cents(self):
        rng = random.Random(11)
        for _ in range(200):
            for line in build(random_invoice(rng))["line_items"]:
                cost = money(line["quantity"]) * money(line["rate"])
                assert cost == cost.quantize(Decimal("0.01"))


SETTINGS = main.settings.model_copy(
    update=dict(
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret=SecretStr("the-client-secret"),
        zoho_accounts_url="https://accounts.zoho.com",
        zoho_organization_id="944669917",
    )
)


def currencies_client(items, *, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json={"code": 0, "message": "success", "currencies": items})

    return httpx.Client(transport=httpx.MockTransport(handler))


CURRENCIES = [
    {"currency_id": "11", "currency_code": "USD", "is_base_currency": False},
    {"currency_id": "22", "currency_code": "KES", "is_base_currency": True},
]


class TestCurrency:
    def test_the_organizations_currencies_are_asked_for(self):
        seen = []
        find_currency_id(SETTINGS, "tok", "https://www.zohoapis.com", "KES", http=currencies_client(CURRENCIES, seen=seen))
        assert seen[0].method == "GET"
        assert seen[0].url.path == "/books/v3/settings/currencies"
        assert seen[0].url.params["organization_id"] == "944669917"

    def test_a_code_is_turned_into_its_id(self):
        client = currencies_client(CURRENCIES)
        assert find_currency_id(SETTINGS, "tok", None, "KES", http=client) == "22"
        assert find_currency_id(SETTINGS, "tok", None, "usd", http=client) == "11"

    def test_an_unknown_currency_is_none(self):
        assert find_currency_id(SETTINGS, "tok", None, "UGX", http=currencies_client(CURRENCIES)) is None

    def test_junk_entries_are_skipped(self):
        items = ["junk", {"currency_code": "KES"}, {"currency_id": "5", "currency_code": "KES"}]
        assert find_currency_id(SETTINGS, "tok", None, "KES", http=currencies_client(items)) == "5"

    def test_the_message_for_a_missing_currency_says_what_to_do(self):
        assert "UGX" in missing_currency_message("UGX")
        assert "Currencies" in missing_currency_message("UGX")