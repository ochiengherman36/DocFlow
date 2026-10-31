import sys
from collections import Counter

from config import get_settings
from crypto import get_cipher
from db import get_sessionmaker
from zoho_books import choose_expense_account, find_expense_account, list_accounts
from zoho_oauth import ZohoError
from zoho_store import get_tokens
from zoho_tokens import ZohoNotConnected, get_access_token


def main() -> int:
    settings = get_settings()
    if not settings.zoho_configured:
        print("Zoho is not set up. Fill in the ZOHO_* values and TOKEN_ENCRYPTION_KEY in .env.")
        return 1
    if not settings.zoho_organization_id:
        print("ZOHO_ORGANIZATION_ID is not set in .env.")
        return 1

    with get_sessionmaker()() as session:
        try:
            token = get_access_token(session, settings)
        except ZohoNotConnected as exc:
            print(f"DISCONNECTED: {exc}")
            return 1
        except ZohoError as exc:
            print(f"FAILED: {exc}")
            return 1
        tokens = get_tokens(session, get_cipher(settings))
        api_domain = tokens.api_domain if tokens else None

    try:
        accounts = list_accounts(settings, token, api_domain)
        account = find_expense_account(settings, token, api_domain)
    except ZohoError as exc:
        print(f"FAILED: {exc}")
        return 1

    print(f"{len(accounts)} active accounts: {dict(Counter(a.account_type for a in accounts))}")
    if settings.zoho_expense_account_id:
        print(f"ZOHO_EXPENSE_ACCOUNT_ID is set, so bills would use account {account.account_id}.")
        chosen = choose_expense_account(accounts)
        if chosen:
            print(f"Without it, DocFlow would choose: {chosen.name} ({chosen.account_type}), id {chosen.account_id}")
    else:
        print(f"Bills would use: {account.name} ({account.account_type}), id {account.account_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())