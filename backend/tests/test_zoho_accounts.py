import httpx
import pytest
from pydantic import SecretStr

import main
from zoho_books import (
    ACCOUNTS_PER_PAGE,
    MAX_ACCOUNT_PAGES,
    NO_EXPENSE_ACCOUNT,
    Account,
    ZohoBooksError,
    choose_expense_account,
    find_expense_account,
    list_accounts,
)
from zoho_oauth import ZohoError

SETTINGS = main.settings.model_copy(
    update=dict(
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret=SecretStr("the-client-secret"),
        zoho_accounts_url="https://accounts.zoho.com",
        zoho_organization_id="944669917",
        zoho_expense_account_id="",
    )
)
DOMAIN = "https://www.zohoapis.com"


def entry(account_id, name, account_type, **extra):
    return {"account_id": account_id, "account_name": name, "account_type": account_type, "is_active": True, **extra}


CHART = [
    entry("1", "Petty Cash", "cash"),
    entry("2", "Advertising And Marketing", "expense"),
    entry("3", "Office Supplies", "expense"),
    entry("4", "Cost of Goods Sold", "cost_of_goods_sold"),
    entry("5", "Other Expenses", "other_expense"),
    entry("6", "Zebra Costs", "expense"),
]


def books_client(pages, *, seen=None, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        index = int(request.url.params.get("page", "1")) - 1
        index = min(index, len(pages) - 1)
        body = pages[index]
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def reply(accounts, more=False):
    return {"code": 0, "message": "success", "chartofaccounts": accounts, "page_context": {"has_more_page": more}}


class TestListing:
    def test_it_asks_for_the_chart_of_accounts_with_the_organization(self):
        seen = []
        list_accounts(SETTINGS, "tok", DOMAIN, http=books_client([reply(CHART)], seen=seen))

        request = seen[0]
        assert request.method == "GET"
        assert request.url.path == "/books/v3/chartofaccounts"
        assert request.url.params["organization_id"] == "944669917"
        assert request.url.params["page"] == "1"
        assert request.url.params["per_page"] == str(ACCOUNTS_PER_PAGE)
        assert request.headers["authorization"] == "Zoho-oauthtoken tok"

    def test_it_returns_the_accounts_with_their_types(self):
        accounts = list_accounts(SETTINGS, "tok", DOMAIN, http=books_client([reply(CHART)]))
        assert accounts[2] == Account("3", "Office Supplies", "expense")
        assert len(accounts) == 6

    def test_inactive_accounts_are_left_out(self):
        chart = [entry("1", "Old", "expense", is_active=False), entry("2", "New", "expense")]
        accounts = list_accounts(SETTINGS, "tok", DOMAIN, http=books_client([reply(chart)]))
        assert [a.account_id for a in accounts] == ["2"]

    def test_entries_without_an_id_or_that_are_not_objects_are_skipped(self):
        chart = [{"account_name": "No id", "account_type": "expense"}, "junk", entry("9", "Fine", "expense")]
        accounts = list_accounts(SETTINGS, "tok", DOMAIN, http=books_client([reply(chart)]))
        assert [a.account_id for a in accounts] == ["9"]

    def test_a_reply_with_no_list_is_an_empty_list(self):
        assert list_accounts(SETTINGS, "tok", DOMAIN, http=books_client([{"code": 0}])) == []

    def test_more_pages_are_read_until_there_are_no_more(self):
        seen = []
        pages = [reply([entry("1", "A", "expense")], more=True), reply([entry("2", "B", "expense")], more=False)]
        accounts = list_accounts(SETTINGS, "tok", DOMAIN, http=books_client(pages, seen=seen))
        assert [a.account_id for a in accounts] == ["1", "2"]
        assert [r.url.params["page"] for r in seen] == ["1", "2"]

    def test_a_reply_that_never_stops_saying_there_is_more_is_cut_off(self):
        seen = []
        pages = [reply([entry("1", "A", "expense")], more=True)]
        list_accounts(SETTINGS, "tok", DOMAIN, http=books_client(pages, seen=seen))
        assert len(seen) == MAX_ACCOUNT_PAGES

    def test_a_missing_permission_asks_to_connect_again(self):
        error = {"code": 57, "message": "You are not authorized to perform this operation"}
        with pytest.raises(ZohoBooksError) as caught:
            list_accounts(SETTINGS, "tok", DOMAIN, http=books_client([error], status=401))
        assert caught.value.needs_reconnect is True


class TestChoosing:
    def accounts(self, *entries):
        return [Account(*e) for e in entries]

    def test_other_expenses_is_preferred(self):
        chosen = choose_expense_account(list_accounts_of(CHART))
        assert chosen == Account("5", "Other Expenses", "other_expense")

    def test_office_supplies_is_next(self):
        chart = [c for c in CHART if c["account_name"] != "Other Expenses"]
        assert choose_expense_account(list_accounts_of(chart)).name == "Office Supplies"

    def test_the_names_are_matched_without_regard_to_capitals(self):
        accounts = self.accounts(("1", "OFFICE SUPPLIES", "expense"), ("2", "Advertising", "expense"))
        assert choose_expense_account(accounts).account_id == "1"

    def test_without_a_preferred_name_the_first_expense_by_name_wins(self):
        accounts = self.accounts(
            ("1", "Zebra Costs", "expense"),
            ("2", "advertising", "expense"),
            ("3", "Cost of Goods Sold", "cost_of_goods_sold"),
        )
        assert choose_expense_account(accounts).account_id == "2"

    def test_cost_of_goods_sold_is_used_when_there_is_no_expense(self):
        accounts = self.accounts(("1", "Petty Cash", "cash"), ("2", "Cost of Goods Sold", "cost_of_goods_sold"))
        assert choose_expense_account(accounts).account_id == "2"

    def test_other_expense_is_the_last_resort(self):
        accounts = self.accounts(("1", "Petty Cash", "cash"), ("2", "Bank Fees", "other_expense"))
        assert choose_expense_account(accounts).account_id == "2"

    def test_accounts_that_are_not_expenses_are_never_chosen(self):
        assert choose_expense_account(self.accounts(("1", "Petty Cash", "cash"), ("2", "Sales", "income"))) is None
        assert choose_expense_account([]) is None

    def test_the_choice_does_not_depend_on_the_order_zoho_listed_them(self):
        accounts = self.accounts(("1", "B", "expense"), ("2", "A", "expense"))
        assert choose_expense_account(accounts) == choose_expense_account(list(reversed(accounts)))


def list_accounts_of(chart):
    return list_accounts(SETTINGS, "tok", DOMAIN, http=books_client([reply(chart)]))


class TestFinding:
    def test_it_asks_zoho_and_returns_the_choice(self):
        assert find_expense_account(SETTINGS, "tok", DOMAIN, http=books_client([reply(CHART)])).account_id == "5"

    def test_a_chart_with_no_expense_account_says_what_to_do(self):
        chart = [entry("1", "Petty Cash", "cash")]
        with pytest.raises(ZohoError) as caught:
            find_expense_account(SETTINGS, "tok", DOMAIN, http=books_client([reply(chart)]))
        assert str(caught.value) == NO_EXPENSE_ACCOUNT
        assert "ZOHO_EXPENSE_ACCOUNT_ID" in str(caught.value)

    def test_a_set_account_id_is_used_without_asking_zoho(self):
        seen = []
        settings = SETTINGS.model_copy(update=dict(zoho_expense_account_id="4445000000000351"))
        account = find_expense_account(settings, "tok", DOMAIN, http=books_client([reply(CHART)], seen=seen))
        assert account.account_id == "4445000000000351"
        assert seen == []

    def test_zoho_problems_pass_through(self):
        def down(request):
            raise httpx.ConnectError("no route")

        with pytest.raises(ZohoError, match="could not reach"):
            find_expense_account(SETTINGS, "tok", DOMAIN, http=httpx.Client(transport=httpx.MockTransport(down)))