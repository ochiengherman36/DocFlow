import json

import httpx
import pytest
from pydantic import SecretStr

import main
from models import Supplier
from supplier_store import add_supplier
from zoho_books import Contact, ZohoBooksError, create_vendor, find_vendor
from zoho_oauth import ZohoError
from zoho_vendors import ensure_vendor, inactive_vendor_message

SETTINGS = main.settings.model_copy(
    update=dict(
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret=SecretStr("the-client-secret"),
        zoho_accounts_url="https://accounts.zoho.com",
        zoho_organization_id="944669917",
    )
)
DOMAIN = "https://www.zohoapis.com"


def contact(contact_id, name, contact_type="vendor", status="active"):
    return {"contact_id": contact_id, "contact_name": name, "contact_type": contact_type, "status": status}


def zoho(contacts=(), *, created_id="777", seen=None, create_reply=None, create_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"code": 0, "message": "success", "contacts": list(contacts)})
        sent = json.loads(request.content)
        reply = create_reply or {
            "code": 0,
            "message": "The contact has been added.",
            "contact": contact(created_id, sent["contact_name"]),
        }
        return httpx.Response(create_status, json=reply)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestFindVendor:
    def test_it_asks_for_the_name_in_the_right_organization(self):
        seen = []
        find_vendor(SETTINGS, "tok", DOMAIN, "Kijani Fuel", http=zoho(seen=seen))

        request = seen[0]
        assert request.method == "GET"
        assert request.url.path == "/books/v3/contacts"
        assert request.url.params["contact_name"] == "Kijani Fuel"
        assert request.url.params["filter_by"] == "Status.All"
        assert request.url.params["organization_id"] == "944669917"
        assert request.headers["authorization"] == "Zoho-oauthtoken tok"

    def test_an_exact_vendor_is_found(self):
        found = find_vendor(SETTINGS, "tok", DOMAIN, "Kijani Fuel", http=zoho([contact("11", "Kijani Fuel")]))
        assert found == Contact("11", "Kijani Fuel", True)

    def test_capitals_and_extra_spaces_do_not_matter(self):
        found = find_vendor(SETTINGS, "tok", DOMAIN, "kijani  FUEL", http=zoho([contact("11", "Kijani Fuel")]))
        assert found.contact_id == "11"

    def test_a_name_that_only_starts_the_same_is_not_a_match(self):
        listed = [contact("11", "Kijani Fuel Distributors Ltd"), contact("12", "Kijani Fuel Station")]
        assert find_vendor(SETTINGS, "tok", DOMAIN, "Kijani Fuel", http=zoho(listed)) is None

    def test_a_customer_with_the_same_name_is_not_a_vendor(self):
        listed = [contact("11", "Kijani Fuel", contact_type="customer")]
        assert find_vendor(SETTINGS, "tok", DOMAIN, "Kijani Fuel", http=zoho(listed)) is None

    def test_the_right_one_is_picked_from_several(self):
        listed = [contact("10", "Other"), contact("11", "Kijani Fuel", contact_type="customer"), contact("12", "Kijani Fuel")]
        assert find_vendor(SETTINGS, "tok", DOMAIN, "Kijani Fuel", http=zoho(listed)).contact_id == "12"

    def test_an_inactive_vendor_is_found_and_marked_inactive(self):
        found = find_vendor(SETTINGS, "tok", DOMAIN, "Kijani Fuel", http=zoho([contact("11", "Kijani Fuel", status="inactive")]))
        assert found == Contact("11", "Kijani Fuel", False)

    def test_nothing_listed_means_none(self):
        assert find_vendor(SETTINGS, "tok", DOMAIN, "Kijani Fuel", http=zoho([])) is None

    def test_junk_entries_are_skipped(self):
        listed = ["junk", {"contact_name": "Kijani Fuel", "contact_type": "vendor"}, contact("12", "Kijani Fuel")]
        assert find_vendor(SETTINGS, "tok", DOMAIN, "Kijani Fuel", http=zoho(listed)).contact_id == "12"


class TestCreateVendor:
    def test_it_posts_the_name_as_a_vendor(self):
        seen = []
        made = create_vendor(SETTINGS, "tok", DOMAIN, "Kijani Fuel", http=zoho(seen=seen, created_id="42"))

        request = seen[0]
        assert request.method == "POST"
        assert request.url.path == "/books/v3/contacts"
        assert request.url.params["organization_id"] == "944669917"
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == {"contact_name": "Kijani Fuel", "contact_type": "vendor"}
        assert made == Contact("42", "Kijani Fuel", True)

    def test_zohos_refusal_is_shown(self):
        reply = {"code": 3062, "message": "The vendor name already exists."}
        with pytest.raises(ZohoBooksError, match="already exists"):
            create_vendor(SETTINGS, "tok", DOMAIN, "Kijani Fuel", http=zoho(create_reply=reply, create_status=400))

    def test_a_reply_without_the_new_vendor_is_caught(self):
        with pytest.raises(ZohoError, match="did not say which vendor"):
            create_vendor(SETTINGS, "tok", DOMAIN, "Kijani Fuel", http=zoho(create_reply={"code": 0}))


@pytest.fixture
def supplier(database):
    with database() as session:
        made, _ = add_supplier(session, "  Baobab   Stationers  ")
        session.commit()
        return made.id


def run(database, supplier_id, http, *, settings=SETTINGS):
    with database() as session:
        row = session.get(Supplier, supplier_id)
        vendor = ensure_vendor(session, settings, "tok", DOMAIN, row, http=http)
        session.commit()
        return vendor


def stored(database, supplier_id):
    with database() as session:
        row = session.get(Supplier, supplier_id)
        return row.zoho_vendor_id, row.zoho_vendor_organization_id


class TestEnsureVendor:
    def test_an_existing_vendor_is_found_and_remembered(self, database, supplier):
        seen = []
        vendor = run(database, supplier, zoho([contact("11", "Baobab Stationers")], seen=seen))

        assert (vendor.vendor_id, vendor.created, vendor.cached) == ("11", False, False)
        assert [r.method for r in seen] == ["GET"]
        assert stored(database, supplier) == ("11", "944669917")

    def test_a_missing_vendor_is_created_and_remembered(self, database, supplier):
        seen = []
        vendor = run(database, supplier, zoho([], created_id="42", seen=seen))

        assert (vendor.vendor_id, vendor.created, vendor.cached) == ("42", True, False)
        assert [r.method for r in seen] == ["GET", "POST"]
        assert stored(database, supplier) == ("42", "944669917")

    def test_the_name_is_sent_tidied(self, database, supplier):
        seen = []
        run(database, supplier, zoho([], seen=seen))
        assert seen[0].url.params["contact_name"] == "Baobab Stationers"
        assert json.loads(seen[1].content)["contact_name"] == "Baobab Stationers"

    def test_a_remembered_vendor_is_used_without_asking_zoho(self, database, supplier):
        run(database, supplier, zoho([contact("11", "Baobab Stationers")]))
        seen = []

        vendor = run(database, supplier, zoho([], seen=seen))

        assert (vendor.vendor_id, vendor.created, vendor.cached) == ("11", False, True)
        assert seen == []

    def test_a_vendor_remembered_for_another_organization_is_looked_up_again(self, database, supplier):
        run(database, supplier, zoho([contact("11", "Baobab Stationers")]))
        other = SETTINGS.model_copy(update=dict(zoho_organization_id="100200300"))
        seen = []

        vendor = run(database, supplier, zoho([contact("55", "Baobab Stationers")], seen=seen), settings=other)

        assert (vendor.vendor_id, vendor.cached) == ("55", False)
        assert seen[0].url.params["organization_id"] == "100200300"
        assert stored(database, supplier) == ("55", "100200300")

    def test_an_inactive_vendor_is_not_used_and_nothing_is_remembered(self, database, supplier):
        listed = [contact("11", "Baobab Stationers", status="inactive")]
        seen = []
        with pytest.raises(ZohoError) as caught:
            run(database, supplier, zoho(listed, seen=seen))

        assert str(caught.value) == inactive_vendor_message("Baobab Stationers")
        assert "inactive" in str(caught.value)
        assert [r.method for r in seen] == ["GET"]
        assert stored(database, supplier) == (None, None)

    def test_a_failed_create_remembers_nothing_so_it_can_be_retried(self, database, supplier):
        reply = {"code": 1, "message": "Something is wrong."}
        with pytest.raises(ZohoBooksError):
            run(database, supplier, zoho([], create_reply=reply, create_status=400))
        assert stored(database, supplier) == (None, None)

        vendor = run(database, supplier, zoho([], created_id="43"))
        assert vendor.vendor_id == "43"

    def test_a_vendor_made_before_a_crash_is_found_instead_of_made_twice(self, database, supplier):
        run(database, supplier, zoho([], created_id="42"))
        with database() as session:
            row = session.get(Supplier, supplier)
            row.zoho_vendor_id = None
            row.zoho_vendor_organization_id = None
            session.commit()
        seen = []

        vendor = run(database, supplier, zoho([contact("42", "Baobab Stationers")], seen=seen))

        assert (vendor.vendor_id, vendor.created) == ("42", False)
        assert [r.method for r in seen] == ["GET"]

    def test_two_suppliers_keep_their_own_vendors(self, database, supplier):
        with database() as session:
            other, _ = add_supplier(session, "Twiga Parts")
            session.commit()
            other_id = other.id
        run(database, supplier, zoho([], created_id="1"))
        run(database, other_id, zoho([], created_id="2"))
        assert stored(database, supplier)[0] == "1"
        assert stored(database, other_id)[0] == "2"

    def test_without_an_organization_id_nothing_is_asked_or_remembered(self, database, supplier):
        none = SETTINGS.model_copy(update=dict(zoho_organization_id=""))
        seen = []
        with pytest.raises(ZohoError, match="ZOHO_ORGANIZATION_ID"):
            run(database, supplier, zoho([], seen=seen), settings=none)
        assert seen == []
        assert stored(database, supplier) == (None, None)

    def test_a_missing_permission_asks_to_connect_again(self, database, supplier):
        def refused(request):
            return httpx.Response(401, json={"code": 57, "message": "You are not authorized to perform this operation"})

        with pytest.raises(ZohoBooksError) as caught:
            run(database, supplier, httpx.Client(transport=httpx.MockTransport(refused)))
        assert caught.value.needs_reconnect is True