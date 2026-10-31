import datetime
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from config import Settings
from crypto import get_cipher
from models import AuditAction, AuditEntry, Document, DocumentStatus, Supplier
from schemas import InvoiceData
from zoho_bill import BillError, build_bill, missing_currency_message
from zoho_books import ZohoBooksError, create_bill, find_bill, find_currency_id, find_expense_account
from zoho_oauth import ZohoError
from zoho_store import get_tokens
from zoho_tokens import get_access_token
from zoho_vendors import ensure_vendor


class NotSendable(Exception):
    """The document cannot be sent, whatever Zoho says. The message says why."""


class DocumentNotFound(NotSendable):
    pass


@dataclass(frozen=True)
class SendResult:
    bill_id: str
    organization_id: str
    sent_at: datetime.datetime
    sent_by: str
    already_sent: bool
    recovered: bool


def _already_sent(document: Document) -> SendResult:
    return SendResult(
        bill_id=document.zoho_bill_id,
        organization_id=document.zoho_bill_organization_id or "",
        sent_at=document.zoho_sent_at,
        sent_by=document.zoho_sent_by or "",
        already_sent=True,
        recovered=False,
    )


def send_to_zoho(
    session: Session,
    settings: Settings,
    document_id: uuid.UUID,
    actor: str,
    *,
    http: httpx.Client | None = None,
    now: datetime.datetime | None = None,
) -> SendResult:
    """Make the approved invoice a bill in Zoho Books, once.

    The document row is locked from the first check to the commit, so two sends cannot both create a bill.
    A document that was already sent just returns its bill. Before creating, Zoho Books is asked whether
    the vendor already has a bill with this number, which covers a crash after Zoho made the bill and
    before the id was saved. Any failure saves nothing and the send can be tried again. This commits.
    """
    try:
        access_token = get_access_token(session, settings)
    except ZohoError as exc:
        raise ZohoError("Zoho did not renew the connection. Try again in a moment.") from exc
    tokens = get_tokens(session, get_cipher(settings))
    api_domain = tokens.api_domain if tokens else None

    document = session.get(Document, document_id)
    if document is None:
        raise DocumentNotFound("This document was not found.")

    try:
        session.refresh(document, with_for_update=True)
        if document.zoho_bill_id:
            result = _already_sent(document)
            session.rollback()
            return result
        if document.status != DocumentStatus.APPROVED.value:
            raise NotSendable("Only approved invoices can be sent to Zoho Books.")
        supplier = session.get(Supplier, document.supplier_id) if document.supplier_id else None
        if supplier is None:
            raise NotSendable("This invoice has no supplier on the list, so it cannot be sent.")

        invoice = InvoiceData.model_validate(document.current_data)
        vendor = ensure_vendor(session, settings, access_token, api_domain, supplier, http=http)
        account = find_expense_account(settings, access_token, api_domain, http=http)

        currency_id = None
        if invoice.currency:
            currency_id = find_currency_id(settings, access_token, api_domain, invoice.currency, http=http)
            if currency_id is None:
                raise BillError(missing_currency_message(invoice.currency))

        payload = build_bill(
            invoice,
            vendor_id=vendor.vendor_id,
            account_id=account.account_id,
            currency_id=currency_id,
            notes=f"Created by DocFlow from {document.filename}. Approved by {document.decided_by or 'unknown'}.",
            tolerance=settings.validation_tolerance,
        )

        existing = find_bill(settings, access_token, api_domain, vendor.vendor_id, payload["bill_number"], http=http)
        try:
            bill = existing or create_bill(settings, access_token, api_domain, payload, http=http)
        except ZohoBooksError as exc:
            if vendor.cached and not exc.needs_reconnect:
                supplier.zoho_vendor_id = None
                supplier.zoho_vendor_organization_id = None
                session.commit()
            raise

        sent_at = now or datetime.datetime.now(datetime.UTC)
        document.zoho_bill_id = bill.bill_id
        document.zoho_bill_organization_id = settings.zoho_organization_id
        document.zoho_sent_at = sent_at
        document.zoho_sent_by = actor
        session.add(
            AuditEntry(
                document_id=document.id,
                extraction_id=document.extraction.id if document.extraction else None,
                action=AuditAction.SENT_TO_ZOHO.value,
                actor=actor,
                details={
                    "bill_id": bill.bill_id,
                    "bill_number": payload["bill_number"],
                    "organization_id": settings.zoho_organization_id,
                    "vendor_id": vendor.vendor_id,
                    "vendor_created": vendor.created,
                    "recovered": existing is not None,
                },
            )
        )
        session.commit()
    except BaseException:
        session.rollback()
        raise

    return SendResult(
        bill_id=bill.bill_id,
        organization_id=settings.zoho_organization_id,
        sent_at=sent_at,
        sent_by=actor,
        already_sent=False,
        recovered=existing is not None,
    )