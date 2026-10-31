import datetime
import json
import uuid
from decimal import Decimal

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

import main
from crypto import TokenCipher
from models import AuditAction, AuditEntry, Document, Supplier
from supplier_store import add_supplier
from zoho_bill import BillError, payload_total
from zoho_books import (
    Bill,
    ZohoBooksError,
    bill_url,
    create_bill,
    find_bill,
)
from zoho_oauth import ZohoError
from zoho_send import DocumentNotFound, NotSendable, send_to_zoho
from zoho_store import save_tokens
from zoho_tokens import ZohoNotConnected

KEY = Fernet.generate_key().decode()
CIPHER = TokenCipher(KEY)

SETTINGS = main.settings.model_copy(
    update=dict(
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret=SecretStr("the-client-secret"),
        zoho_redirect_uri="http://localhost:8000/integrations/zoho/callback",
        zoho_accounts_url="https://accounts.zoho.com",
        zoho_organization_id="944669917",
        zoho_expense_account_id="",
        token_encryption_key=SecretStr(KEY),
    )
)
DOMAIN = "https://www.zohoapis.com"

INVOICE = {
    "supplier": "Baobab Stationers",
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


class FakeBooks:
    """A small stand-in for Zoho Books that remembers what was made, so repeats can be seen."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.vendors: list[dict] = []
        self.bills: list[dict] = []
        self.accounts = [{"account_id": "4445", "account_name": "Other Expenses", "account_type": "other_expense", "is_active": True}]
        self.currencies = [{"currency_id": "22", "currency_code": "KES"}]
        self.create_errors: list[tuple[int, dict]] = []
        self.lose_reply = 0
        self.unauthorised = False

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == f"/books/v3/{path}"]

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unauthorised:
            return httpx.Response(401, json={"code": 57, "message": "You are not authorized to perform this operation"})
        path = request.url.path.removeprefix("/books/v3/")
        params = request.url.params

        if (request.method, path) == ("GET", "contacts"):
            wanted = params.get("contact_name", "").casefold()
            found = [v for v in self.vendors if v["contact_name"].casefold() == wanted]
            return self.ok(contacts=found)
        if (request.method, path) == ("POST", "contacts"):
            sent = json.loads(request.content)
            vendor = {"contact_id": str(100 + len(self.vendors)), "contact_name": sent["contact_name"], "contact_type": "vendor", "status": "active"}
            self.vendors.append(vendor)
            return self.ok(contact=vendor)
        if (request.method, path) == ("GET", "chartofaccounts"):
            return self.ok(chartofaccounts=self.accounts, page_context={"has_more_page": False})
        if (request.method, path) == ("GET", "settings/currencies"):
            return self.ok(currencies=self.currencies)
        if (request.method, path) == ("GET", "bills"):
            found = [
                b for b in self.bills
                if b["vendor_id"] == params.get("vendor_id") and b["bill_number"] == params.get("bill_number")
            ]
            return self.ok(bills=found)
        if (request.method, path) == ("POST", "bills"):
            if self.create_errors:
                status, body = self.create_errors.pop(0)
                return httpx.Response(status, json=body)
            sent = json.loads(request.content)
            bill = {
                "bill_id": str(5000 + len(self.bills)),
                "bill_number": sent["bill_number"],
                "vendor_id": sent["vendor_id"],
                "status": "open",
                "payload": sent,
            }
            self.bills.append(bill)
            if self.lose_reply:
                self.lose_reply -= 1
                raise httpx.ReadTimeout("the reply never came")
            return self.ok(bill=bill)
        return httpx.Response(404, json={"code": 1, "message": f"no stub for {request.method} {path}"})

    @staticmethod
    def ok(**body) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "message": "success", **body})


@pytest.fixture
def zoho():
    return FakeBooks()


def connect(database, **overrides):
    values = dict(
        access_token="1000.access-abc",
        refresh_token="1000.refresh-def",
        expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
        scope="ZohoBooks.bills.CREATE",
        api_domain=DOMAIN,
        connected_by="Herman",
    )
    with database() as session:
        save_tokens(session, CIPHER, **{**values, **overrides})
        session.commit()


@pytest.fixture(autouse=True)
def connected(database):
    connect(database)


def make_document(database, *, status="approved", with_supplier=True, number="INV-2026-0142", data=None, n=1) -> uuid.UUID:
    invoice = {**(data or INVOICE), "invoice_number": number}
    with database() as session:
        supplier = add_supplier(session, "Baobab Stationers")[0] if with_supplier else None
        document = Document(
            filename="invoice.pdf",
            file_hash=f"{n:064x}",
            file_size=1,
            status=status,
            current_data=invoice,
            supplier_key="baobab",
            invoice_number_key=number.lower(),
            supplier_id=supplier.id if supplier else None,
            decided_by="Herman",
        )
        session.add(document)
        session.commit()
        return document.id


def send(database, document_id, zoho, actor="Herman", **kwargs):
    with database() as session:
        return send_to_zoho(session, SETTINGS, document_id, actor, http=zoho.client(), **kwargs)


def saved(database, document_id) -> Document:
    with database() as session:
        return session.get(Document, document_id)


def audit_actions(database, document_id) -> list[str]:
    with database() as session:
        rows = session.scalars(select(AuditEntry).where(AuditEntry.document_id == document_id).order_by(AuditEntry.id))
        return [row.action for row in rows]


class TestSending:
    def test_an_approved_invoice_becomes_one_bill(self, database, zoho):
        document_id = make_document(database)

        result = send(database, document_id, zoho)

        assert len(zoho.bills) == 1
        assert result.bill_id == zoho.bills[0]["bill_id"]
        assert (result.already_sent, result.recovered) == (False, False)
        assert result.organization_id == "944669917"
        assert result.sent_by == "Herman"

    def test_the_bill_has_the_vendor_lines_tax_and_currency(self, database, zoho):
        send(database, make_document(database), zoho)

        bill = zoho.bills[0]["payload"]
        assert bill["vendor_id"] == zoho.vendors[0]["contact_id"]
        assert bill["bill_number"] == "INV-2026-0142"
        assert bill["date"] == "2026-10-28"
        assert bill["currency_id"] == "22"
        assert bill["line_items"][0]["account_id"] == "4445"
        assert len(bill["line_items"]) == 5
        assert payload_total(bill) == Decimal("39846.00")
        assert "invoice.pdf" in bill["notes"] and "Herman" in bill["notes"]

    def test_the_vendor_is_made_once_and_named_after_the_supplier(self, database, zoho):
        send(database, make_document(database), zoho)
        assert [v["contact_name"] for v in zoho.vendors] == ["Baobab Stationers"]

    def test_the_bill_is_recorded_on_the_document(self, database, zoho):
        document_id = make_document(database)
        before = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(seconds=5)

        result = send(database, document_id, zoho, actor="Ochieng")

        document = saved(database, document_id)
        assert document.zoho_bill_id == result.bill_id
        assert document.zoho_bill_organization_id == "944669917"
        assert document.zoho_sent_by == "Ochieng"
        assert document.zoho_sent_at.replace(tzinfo=None) > before
        assert document.status == "approved"

    def test_an_entry_is_added_to_the_audit_trail(self, database, zoho):
        document_id = make_document(database)
        send(database, document_id, zoho, actor="Ochieng")

        assert audit_actions(database, document_id) == [AuditAction.SENT_TO_ZOHO.value]
        with database() as session:
            entry = session.scalars(select(AuditEntry).where(AuditEntry.document_id == document_id)).one()
        assert entry.actor == "Ochieng"
        assert entry.details == {
            "bill_id": zoho.bills[0]["bill_id"],
            "bill_number": "INV-2026-0142",
            "organization_id": "944669917",
            "vendor_id": zoho.vendors[0]["contact_id"],
            "vendor_created": True,
            "recovered": False,
        }

    def test_the_token_never_reaches_the_audit_trail_or_the_database_rows(self, database, zoho):
        document_id = make_document(database)
        send(database, document_id, zoho)
        with database() as session:
            entry = session.scalars(select(AuditEntry).where(AuditEntry.document_id == document_id)).one()
        assert "access-abc" not in json.dumps(entry.details)


class TestNoDoublePush:
    def test_sending_twice_creates_one_bill(self, database, zoho):
        document_id = make_document(database)
        first = send(database, document_id, zoho)
        calls_after_first = len(zoho.requests)

        second = send(database, document_id, zoho)

        assert len(zoho.bills) == 1
        assert second.already_sent is True
        assert second.bill_id == first.bill_id
        assert (second.organization_id, second.sent_by) == ("944669917", "Herman")
        assert len(zoho.requests) == calls_after_first
        assert audit_actions(database, document_id) == [AuditAction.SENT_TO_ZOHO.value]

    def test_a_send_that_lost_the_race_returns_the_other_bill_without_asking_zoho(self, database, zoho):
        document_id = make_document(database)
        with database() as first:
            first.get(Document, document_id)
            with database() as other:
                row = other.get(Document, document_id)
                row.zoho_bill_id = "777"
                row.zoho_bill_organization_id = "944669917"
                row.zoho_sent_at = datetime.datetime.now(datetime.UTC)
                row.zoho_sent_by = "Someone"
                other.commit()

            result = send_to_zoho(first, SETTINGS, document_id, "Herman", http=zoho.client())

        assert (result.already_sent, result.bill_id, result.sent_by) == (True, "777", "Someone")
        assert zoho.requests == []

    def test_a_crash_between_create_and_save_does_not_make_a_second_bill(self, database, zoho):
        document_id = make_document(database)

        with database() as session:
            real_commit = session.commit

            def lost_connection():
                session.commit = real_commit
                raise OperationalError("commit", {}, Exception("connection lost"))

            session.commit = lost_connection
            with pytest.raises(OperationalError):
                send_to_zoho(session, SETTINGS, document_id, "Herman", http=zoho.client())

        assert len(zoho.bills) == 1
        assert saved(database, document_id).zoho_bill_id is None

        result = send(database, document_id, zoho)

        assert len(zoho.bills) == 1
        assert (result.recovered, result.already_sent) == (True, False)
        assert result.bill_id == zoho.bills[0]["bill_id"]
        assert saved(database, document_id).zoho_bill_id == zoho.bills[0]["bill_id"]
        assert len(zoho.calls("POST", "bills")) == 1
        assert len(zoho.vendors) == 1

    def test_a_reply_that_never_arrived_does_not_make_a_second_bill(self, database, zoho):
        document_id = make_document(database)
        zoho.lose_reply = 1

        with pytest.raises(ZohoError, match="could not reach"):
            send(database, document_id, zoho)
        assert len(zoho.bills) == 1
        assert saved(database, document_id).zoho_bill_id is None

        result = send(database, document_id, zoho)

        assert len(zoho.bills) == 1
        assert result.recovered is True
        assert saved(database, document_id).zoho_bill_id == zoho.bills[0]["bill_id"]

    def test_a_recovered_bill_is_recorded_as_recovered(self, database, zoho):
        document_id = make_document(database)
        zoho.lose_reply = 1
        with pytest.raises(ZohoError):
            send(database, document_id, zoho)
        send(database, document_id, zoho)

        with database() as session:
            entry = session.scalars(select(AuditEntry).where(AuditEntry.document_id == document_id)).one()
        assert entry.details["recovered"] is True

    def test_a_bill_someone_already_made_in_zoho_is_adopted_not_duplicated(self, database, zoho):
        document_id = make_document(database)
        zoho.vendors.append({"contact_id": "300", "contact_name": "Baobab Stationers", "contact_type": "vendor", "status": "active"})
        zoho.bills.append({"bill_id": "9", "bill_number": "INV-2026-0142", "vendor_id": "300", "status": "open"})

        result = send(database, document_id, zoho)

        assert (result.bill_id, result.recovered) == ("9", True)
        assert len(zoho.bills) == 1
        assert zoho.calls("POST", "bills") == []

    def test_the_same_number_from_another_vendor_is_not_adopted(self, database, zoho):
        document_id = make_document(database)
        zoho.vendors.append({"contact_id": "300", "contact_name": "Baobab Stationers", "contact_type": "vendor", "status": "active"})
        zoho.bills.append({"bill_id": "9", "bill_number": "INV-2026-0142", "vendor_id": "999", "status": "open"})

        result = send(database, document_id, zoho)

        assert result.bill_id != "9"
        assert len(zoho.bills) == 2

    def test_a_void_bill_with_the_same_number_does_not_count(self, database, zoho):
        document_id = make_document(database)
        zoho.vendors.append({"contact_id": "300", "contact_name": "Baobab Stationers", "contact_type": "vendor", "status": "active"})
        zoho.bills.append({"bill_id": "9", "bill_number": "INV-2026-0142", "vendor_id": "300", "status": "void"})

        result = send(database, document_id, zoho)

        assert result.bill_id != "9"
        assert result.recovered is False


class TestFailures:
    def test_a_refusal_from_zoho_sends_nothing_and_saves_nothing(self, database, zoho):
        document_id = make_document(database)
        zoho.create_errors.append((400, {"code": 1001, "message": "Please ensure that the bill number is unique."}))

        with pytest.raises(ZohoBooksError, match="bill number is unique"):
            send(database, document_id, zoho)

        assert zoho.bills == []
        document = saved(database, document_id)
        assert (document.zoho_bill_id, document.zoho_sent_at, document.zoho_sent_by) == (None, None, None)
        assert audit_actions(database, document_id) == []

    def test_after_a_failure_the_send_can_be_tried_again(self, database, zoho):
        document_id = make_document(database)
        zoho.create_errors.append((500, {"code": 1, "message": "Something went wrong."}))
        with pytest.raises(ZohoBooksError):
            send(database, document_id, zoho)

        result = send(database, document_id, zoho)

        assert len(zoho.bills) == 1
        assert result.already_sent is False
        assert audit_actions(database, document_id) == [AuditAction.SENT_TO_ZOHO.value]

    def test_a_vendor_made_before_a_failure_is_not_made_again_on_retry(self, database, zoho):
        document_id = make_document(database)
        zoho.create_errors.append((500, {"code": 1, "message": "Something went wrong."}))
        with pytest.raises(ZohoBooksError):
            send(database, document_id, zoho)
        send(database, document_id, zoho)
        assert len(zoho.vendors) == 1

    def test_an_expired_connection_asks_to_connect_again_and_saves_nothing(self, database, zoho):
        document_id = make_document(database)
        zoho.unauthorised = True

        with pytest.raises(ZohoBooksError) as caught:
            send(database, document_id, zoho)

        assert caught.value.needs_reconnect is True
        assert saved(database, document_id).zoho_bill_id is None

    def test_a_disconnected_zoho_stops_before_any_request(self, database, zoho):
        with database() as session:
            from zoho_store import delete_tokens

            delete_tokens(session)
            session.commit()
        document_id = make_document(database)

        with pytest.raises(ZohoNotConnected, match="not connected"):
            send(database, document_id, zoho)

        assert zoho.requests == []
        assert saved(database, document_id).zoho_bill_id is None

    def test_an_invoice_that_does_not_add_up_is_refused_before_anything_is_created(self, database, zoho):
        bad = {**INVOICE, "totals": {"subtotal": 34350.0, "tax": 5496.0, "total": 40000.0}}
        document_id = make_document(database, data=bad)

        with pytest.raises(BillError, match="add up to"):
            send(database, document_id, zoho)

        assert zoho.calls("POST", "bills") == []
        assert saved(database, document_id).zoho_bill_id is None

    def test_a_currency_zoho_does_not_have_is_refused_with_the_fix(self, database, zoho):
        document_id = make_document(database, data={**INVOICE, "currency": "UGX"})

        with pytest.raises(BillError, match="UGX"):
            send(database, document_id, zoho)

        assert zoho.calls("POST", "bills") == []

    def test_no_expense_account_is_refused_with_the_fix(self, database, zoho):
        zoho.accounts = [{"account_id": "1", "account_name": "Petty Cash", "account_type": "cash", "is_active": True}]
        with pytest.raises(ZohoError, match="ZOHO_EXPENSE_ACCOUNT_ID"):
            send(database, make_document(database), zoho)
        assert zoho.bills == []

    def test_a_vendor_that_zoho_forgot_is_looked_up_again_on_the_next_try(self, database, zoho):
        first_id = make_document(database, number="A-1", n=1)
        send(database, first_id, zoho)
        zoho.vendors.clear()
        second_id = make_document(database, number="A-2", n=2)
        zoho.create_errors.append((400, {"code": 1, "message": "The vendor does not exist."}))

        with pytest.raises(ZohoBooksError, match="vendor does not exist"):
            send(database, second_id, zoho)

        with database() as session:
            supplier = session.scalars(select(Supplier).where(Supplier.name == "Baobab Stationers")).one()
            assert supplier.zoho_vendor_id is None

        send(database, second_id, zoho)
        assert [v["contact_name"] for v in zoho.vendors] == ["Baobab Stationers"]
        assert len(zoho.bills) == 2


class TestWhatCannotBeSent:
    @pytest.mark.parametrize("status", ["pending", "rejected"])
    def test_only_approved_invoices_are_sent(self, database, zoho, status):
        document_id = make_document(database, status=status)
        with pytest.raises(NotSendable, match="Only approved"):
            send(database, document_id, zoho)
        assert zoho.requests == []

    def test_an_invoice_without_a_listed_supplier_is_not_sent(self, database, zoho):
        document_id = make_document(database, with_supplier=False)
        with pytest.raises(NotSendable, match="no supplier"):
            send(database, document_id, zoho)
        assert zoho.requests == []

    def test_an_unknown_document_is_not_sent(self, database, zoho):
        with pytest.raises(NotSendable, match="not found"):
            send(database, uuid.uuid4(), zoho)


class TestSeveralDocuments:
    def test_two_invoices_from_one_supplier_share_the_vendor(self, database, zoho):
        send(database, make_document(database, number="A-1", n=1), zoho)
        send(database, make_document(database, number="A-2", n=2), zoho)

        assert len(zoho.vendors) == 1
        assert len(zoho.bills) == 2
        assert zoho.bills[0]["vendor_id"] == zoho.bills[1]["vendor_id"]
        assert len(zoho.calls("POST", "contacts")) == 1
        assert len(zoho.calls("GET", "contacts")) == 1


class TestBillCalls:
    def test_the_address_of_a_bill_in_the_web_app(self):
        assert bill_url(SETTINGS, "944669917", "55") == "https://books.zoho.com/app/944669917#/bills/55"
        eu = SETTINGS.model_copy(update=dict(zoho_accounts_url="https://accounts.zoho.eu"))
        assert bill_url(eu, "1", "2") == "https://books.zoho.eu/app/1#/bills/2"

    def test_a_bill_is_looked_up_by_vendor_and_number(self, zoho):
        zoho.bills.append({"bill_id": "9", "bill_number": "INV-1", "vendor_id": "300", "status": "open"})
        found = find_bill(SETTINGS, "tok", DOMAIN, "300", "inv-1", http=httpx.Client(transport=httpx.MockTransport(
            lambda r: FakeBooks.ok(bills=zoho.bills)
        )))
        assert found == Bill("9", "INV-1", "open")

    def test_the_lookup_asks_with_the_vendor_and_number_in_the_address(self, zoho):
        find_bill(SETTINGS, "tok", DOMAIN, "300", "INV-1", http=zoho.client())
        request = zoho.calls("GET", "bills")[0]
        assert request.url.params["vendor_id"] == "300"
        assert request.url.params["bill_number"] == "INV-1"
        assert request.url.params["organization_id"] == "944669917"

    def test_a_reply_that_does_not_say_which_bill_was_made_is_caught(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda r: FakeBooks.ok()))
        with pytest.raises(ZohoError, match="did not say which bill"):
            create_bill(SETTINGS, "tok", DOMAIN, {"bill_number": "X"}, http=client)

    def test_a_made_bill_is_returned(self, zoho):
        made = create_bill(SETTINGS, "tok", DOMAIN, {"vendor_id": "1", "bill_number": "X"}, http=zoho.client())
        assert made == Bill("5000", "X", "open")
HEADERS = {"X-Access-Key": main.settings.access_key.get_secret_value()}
client = TestClient(main.app, headers=HEADERS)


@pytest.fixture
def api(monkeypatch, zoho):
    monkeypatch.setattr(main, "settings", SETTINGS)
    real_send = send_to_zoho
    monkeypatch.setattr(
        main,
        "send_to_zoho",
        lambda session, settings, document_id, actor: real_send(session, settings, document_id, actor, http=zoho.client()),
    )
    return zoho


def post(document_id, **headers):
    return client.post(f"/documents/{document_id}/send-to-zoho", headers=headers)


class TestEndpoint:
    def test_an_approved_invoice_is_sent_and_the_link_comes_back(self, database, api):
        document_id = make_document(database)

        r = post(document_id, **{"X-Reviewer": "Ochieng"})

        assert r.status_code == 200
        body = r.json()
        assert body["document_id"] == str(document_id)
        assert body["zoho_bill_id"] == api.bills[0]["bill_id"]
        assert body["zoho_bill_url"] == f"https://books.zoho.com/app/944669917#/bills/{api.bills[0]['bill_id']}"
        assert (body["already_sent"], body["recovered"], body["zoho_sent_by"]) == (False, False, "Ochieng")
        assert body["zoho_sent_at"]

    def test_sending_again_returns_the_same_link_instead_of_an_error(self, database, api):
        document_id = make_document(database)
        first = post(document_id).json()

        r = post(document_id)

        assert r.status_code == 200
        assert r.json()["already_sent"] is True
        assert r.json()["zoho_bill_url"] == first["zoho_bill_url"]
        assert len(api.bills) == 1

    def test_the_response_has_no_token(self, database, api):
        r = post(make_document(database))
        assert "access-abc" not in r.text
        assert "refresh-def" not in r.text

    @pytest.mark.parametrize("status", ["pending", "rejected"])
    def test_a_document_that_is_not_approved_is_a_409(self, database, api, status):
        r = post(make_document(database, status=status))
        assert r.status_code == 409
        assert "Only approved" in r.json()["detail"]
        assert api.requests == []

    def test_an_invoice_without_a_listed_supplier_is_a_409(self, database, api):
        r = post(make_document(database, with_supplier=False))
        assert r.status_code == 409
        assert "no supplier" in r.json()["detail"]

    def test_an_unknown_document_is_a_404(self, database, api):
        r = post(uuid.uuid4())
        assert r.status_code == 404
        assert "not found" in r.json()["detail"]

    def test_not_connected_is_a_clear_409(self, database, api):
        with database() as session:
            from zoho_store import delete_tokens

            delete_tokens(session)
            session.commit()

        r = post(make_document(database))

        assert r.status_code == 409
        assert r.json()["detail"] == "Zoho is not connected."
        assert api.requests == []

    def test_an_expired_connection_is_a_409_that_says_to_connect_again(self, database, api):
        api.unauthorised = True
        r = post(make_document(database))
        assert r.status_code == 409
        assert "connect Zoho again" in r.json()["detail"]

    def test_a_refused_connection_that_cannot_be_renewed_is_a_409(self, database, api, monkeypatch):
        import zoho_tokens
        from zoho_oauth import ZohoRefreshRejected

        connect(database, expires_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2))

        def dead(settings, refresh_token, **kwargs):
            raise ZohoRefreshRejected("invalid_grant", code="invalid_grant")

        monkeypatch.setattr(zoho_tokens, "refresh_access_token", dead)

        r = post(make_document(database))

        assert r.status_code == 409
        assert "Connect Zoho again" in r.json()["detail"]

    def test_a_renewal_that_failed_for_now_is_a_502_in_plain_words(self, database, api, monkeypatch):
        import zoho_tokens

        connect(database, expires_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2))

        def down(settings, refresh_token, **kwargs):
            raise ZohoError("invalid_client")

        monkeypatch.setattr(zoho_tokens, "refresh_access_token", down)

        r = post(make_document(database))

        assert r.status_code == 502
        assert r.json()["detail"] == "Zoho did not renew the connection. Try again in a moment."

    def test_zohos_refusal_is_a_502_with_its_words_and_the_send_can_be_retried(self, database, api):
        document_id = make_document(database)
        api.create_errors.append((400, {"code": 1001, "message": "Please ensure that the bill number is unique."}))

        r = post(document_id)

        assert r.status_code == 502
        assert "bill number is unique" in r.json()["detail"]
        assert api.bills == []
        assert post(document_id).status_code == 200
        assert len(api.bills) == 1

    def test_a_lost_reply_is_a_502_and_the_retry_finds_the_bill(self, database, api):
        document_id = make_document(database)
        api.lose_reply = 1

        assert post(document_id).status_code == 502
        r = post(document_id)

        assert r.status_code == 200
        assert r.json()["recovered"] is True
        assert len(api.bills) == 1

    def test_an_invoice_that_does_not_add_up_is_a_422(self, database, api):
        bad = {**INVOICE, "totals": {"subtotal": 34350.0, "tax": 5496.0, "total": 40000.0}}
        r = post(make_document(database, data=bad))
        assert r.status_code == 422
        assert "add up to" in r.json()["detail"]
        assert api.calls("POST", "bills") == []

    def test_zoho_not_set_up_is_a_503(self, database, api, monkeypatch):
        monkeypatch.setattr(main, "settings", SETTINGS.model_copy(update=dict(zoho_client_id="")))
        r = post(make_document(database))
        assert r.status_code == 503
        assert "not set up" in r.json()["detail"]

    def test_a_missing_organization_id_is_a_503(self, database, api, monkeypatch):
        monkeypatch.setattr(main, "settings", SETTINGS.model_copy(update=dict(zoho_organization_id="")))
        assert post(make_document(database)).status_code == 503

    def test_a_database_failure_is_a_503(self, database, api, monkeypatch):
        def broken(*args, **kwargs):
            raise OperationalError("select", {}, Exception("connection lost"))

        monkeypatch.setattr(main, "send_to_zoho", broken)
        assert post(make_document(database)).status_code == 503

    def test_it_needs_the_access_key(self, database, api):
        r = TestClient(main.app).post(f"/documents/{make_document(database)}/send-to-zoho")
        assert r.status_code == 401

    def test_a_bad_id_is_a_422(self, api):
        assert client.post("/documents/not-an-id/send-to-zoho").status_code == 422

    def test_a_missing_document_error_is_a_kind_of_not_sendable(self):
        assert issubclass(DocumentNotFound, NotSendable)


class TestTheLinkInOtherResponses:
    def test_the_document_list_shows_the_bill_for_a_sent_invoice_only(self, database, api):
        sent_id = make_document(database, number="A-1", n=1)
        make_document(database, number="A-2", n=2)
        post(sent_id)

        documents = {d["invoice_number"]: d for d in client.get("/documents").json()["documents"]}

        assert documents["A-1"]["zoho_bill_id"] == api.bills[0]["bill_id"]
        assert documents["A-1"]["zoho_bill_url"].startswith("https://books.zoho.com/app/944669917#/bills/")
        assert documents["A-1"]["zoho_sent_by"] == "reviewer"
        assert documents["A-2"]["zoho_bill_id"] is None
        assert documents["A-2"]["zoho_bill_url"] is None

    def test_the_history_shows_the_bill_and_the_sending(self, database, api):
        document_id = make_document(database)
        post(document_id, **{"X-Reviewer": "Ochieng"})

        body = client.get(f"/documents/{document_id}/audit").json()

        assert body["zoho_bill_url"].endswith(f"/bills/{api.bills[0]['bill_id']}")
        assert body["zoho_sent_by"] == "Ochieng"
        entry = body["entries"][0]
        assert (entry["action"], entry["actor"]) == ("sent_to_zoho", "Ochieng")
        assert entry["details"]["bill_id"] == api.bills[0]["bill_id"]

    def test_the_history_of_an_unsent_invoice_has_no_bill(self, database, api):
        body = client.get(f"/documents/{make_document(database)}/audit").json()
        assert body["zoho_bill_id"] is None and body["zoho_bill_url"] is None

    def test_uploading_a_sent_invoice_again_shows_its_bill(self, database, api):
        from documents import file_hash

        pdf = b"%PDF-1.4 a sent invoice"
        document_id = make_document(database)
        with database() as session:
            session.get(Document, document_id).file_hash = file_hash(pdf)
            session.commit()
        post(document_id)

        r = client.post("/extract", files={"file": ("again.pdf", pdf, "application/pdf")})

        assert r.status_code == 200
        assert r.json()["document_id"] == str(document_id)
        assert r.json()["zoho_bill_id"] == api.bills[0]["bill_id"]
        assert r.json()["zoho_bill_url"].endswith(f"/bills/{api.bills[0]['bill_id']}")

    def test_the_bill_link_uses_the_organization_it_was_sent_to(self, database, api, monkeypatch):
        document_id = make_document(database)
        post(document_id)
        other = SETTINGS.model_copy(update=dict(zoho_organization_id="100200300"))
        monkeypatch.setattr(main, "settings", other)

        body = client.get(f"/documents/{document_id}/audit").json()

        assert "/app/944669917#/bills/" in body["zoho_bill_url"]