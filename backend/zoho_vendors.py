from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from config import Settings
from models import Supplier
from zoho_books import create_vendor, find_vendor
from zoho_oauth import ZohoError


@dataclass(frozen=True)
class Vendor:
    vendor_id: str
    name: str
    created: bool
    cached: bool


def inactive_vendor_message(name: str) -> str:
    return f'The vendor "{name}" exists in Zoho Books but is inactive. Mark it active there, then try again.'


def ensure_vendor(
    session: Session,
    settings: Settings,
    access_token: str,
    api_domain: str | None,
    supplier: Supplier,
    *,
    http: httpx.Client | None = None,
) -> Vendor:
    """The Zoho Books vendor for this supplier: remembered, found by exact name, or created.

    The vendor id is remembered on the supplier together with the organization it belongs to, so a
    different organization is looked up afresh. The supplier row is locked meanwhile. The caller commits.
    """
    session.refresh(supplier, with_for_update=True)
    name = " ".join(supplier.name.split())
    organization_id = settings.zoho_organization_id

    if organization_id and supplier.zoho_vendor_id and supplier.zoho_vendor_organization_id == organization_id:
        return Vendor(supplier.zoho_vendor_id, name, created=False, cached=True)

    found = find_vendor(settings, access_token, api_domain, name, http=http)
    if found is not None:
        if not found.active:
            raise ZohoError(inactive_vendor_message(found.name))
        contact, created = found, False
    else:
        contact, created = create_vendor(settings, access_token, api_domain, name, http=http), True

    supplier.zoho_vendor_id = contact.contact_id
    supplier.zoho_vendor_organization_id = organization_id
    session.flush()
    return Vendor(contact.contact_id, contact.name, created=created, cached=False)