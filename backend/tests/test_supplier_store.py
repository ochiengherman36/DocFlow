import pytest
from sqlalchemy.orm import Session

from db import Base, make_engine
from models import Supplier
from supplier_store import add_supplier, list_supplier_names, seed_suppliers, supplier_key


@pytest.fixture
def empty():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_the_shipped_list_is_loaded_into_the_test_database(database):
    with database() as session:
        assert list_supplier_names(session) == [
            "Kijani Fuel Distributors Ltd",
            "Mwangaza Office Supplies",
            "Twiga Auto Spares",
        ]


def test_an_empty_table_gives_an_empty_list(empty):
    assert list_supplier_names(empty) == []


def test_a_supplier_is_added_and_listed(empty):
    supplier, created = add_supplier(empty, "Savannah Hardware Wholesalers")
    empty.commit()
    assert created is True
    assert supplier.id is not None
    assert supplier.name_key == "savannah hardware wholesalers"
    assert list_supplier_names(empty) == ["Savannah Hardware Wholesalers"]


def test_names_are_listed_in_alphabetical_order(empty):
    for name in ["Twiga Auto Spares", "Kijani Fuel", "Mwangaza Office Supplies"]:
        add_supplier(empty, name)
    empty.commit()
    assert list_supplier_names(empty) == ["Kijani Fuel", "Mwangaza Office Supplies", "Twiga Auto Spares"]


def test_surrounding_spaces_are_trimmed(empty):
    supplier, _ = add_supplier(empty, "   Twiga Auto Spares  ")
    assert supplier.name == "Twiga Auto Spares"


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_a_blank_name_is_refused(empty, blank):
    with pytest.raises(ValueError, match="cannot be blank"):
        add_supplier(empty, blank)


@pytest.mark.parametrize(
    "again",
    [
        "Twiga Auto Spares",
        "twiga auto spares",
        "TWIGA AUTO SPARES LTD",
        "Twiga Auto Spares Limited",
        "Twiga Auto Spares.",
    ],
)
def test_adding_the_same_company_again_changes_nothing(empty, again):
    first, _ = add_supplier(empty, "Twiga Auto Spares")
    empty.commit()
    second, created = add_supplier(empty, again)
    empty.commit()
    assert created is False
    assert second.id == first.id
    assert list_supplier_names(empty) == ["Twiga Auto Spares"]


def test_a_different_company_is_a_new_row(empty):
    add_supplier(empty, "Twiga Auto Spares")
    _, created = add_supplier(empty, "Twiga Auto Parts")
    empty.commit()
    assert created is True
    assert len(list_supplier_names(empty)) == 2


def test_the_key_matches_the_one_used_for_matching():
    assert supplier_key("Kijani Fuel Distributors Ltd") == "kijani fuel distributors"
    assert supplier_key("KIJANI FUEL DISTRIBUTORS LIMITED") == "kijani fuel distributors"


def test_seeding_adds_only_what_is_missing(empty):
    add_supplier(empty, "Twiga Auto Spares")
    empty.commit()
    added = seed_suppliers(empty, ["Twiga Auto Spares", "Kijani Fuel", "Mwangaza Office Supplies"])
    empty.commit()
    assert added == 2
    assert len(list_supplier_names(empty)) == 3


def test_seeding_twice_adds_nothing_the_second_time(empty):
    names = ["Kijani Fuel", "Twiga Auto Spares"]
    assert seed_suppliers(empty, names) == 2
    empty.commit()
    assert seed_suppliers(empty, names) == 0
    empty.commit()
    assert empty.query(Supplier).count() == 2


def test_seeding_ignores_duplicates_within_the_list(empty):
    assert seed_suppliers(empty, ["Twiga Auto Spares", "twiga auto spares ltd"]) == 1