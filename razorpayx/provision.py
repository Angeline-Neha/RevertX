"""
razorpayx/provision.py
Run ONCE to create a test Contact + Fund Account. Print the fund_account_id
and put it in .env as RZP_DEMO_FUND_ACCOUNT_ID — the payout path reuses it
on every run instead of creating a new contact per payment.

Also provisions a SECOND fund account for Phase 4 (Preset 3 flagship) —
reverse_payout() needs somewhere to send a reversal that lands back on the
same RazorpayX account, distinct from the demo payee's fund account.
Run with --self to provision that one instead (or in addition).
"""
import argparse
import asyncio
from razorpayx.client import create_contact, create_fund_account_vpa

async def main(self_account: bool = False):
    if self_account:
        contact = await create_contact(name="Aegis Reversal Account (self)", contact_type="self")
        print("contact_id:", contact["id"])
        # Any syntactically valid VPA works in test mode. Distinct address
        # from the demo payee's so the two fund accounts are never confused
        # in the RazorpayX dashboard.
        fa = await create_fund_account_vpa(contact["id"], address="aegis-reversal@upi")
        print("fund_account_id:", fa["id"])
        print("\nAdd to .env: RZP_SELF_FUND_ACCOUNT_ID=" + fa["id"])
        return

    contact = await create_contact(name="Rahul (Conference Registration)", contact_type="vendor")
    print("contact_id:", contact["id"])
    # Any syntactically valid VPA works in test mode — RazorpayX doesn't use
    # Payments' success@razorpay/failure@razorpay convention for payouts.
    fa = await create_fund_account_vpa(contact["id"], address="rahul@upi")
    print("fund_account_id:", fa["id"])
    print("\nAdd to .env: RZP_DEMO_FUND_ACCOUNT_ID=" + fa["id"])
    print(
        "\nNote: test-mode payouts often stay 'queued'/'processing' and don't "
        "auto-advance to 'processed' — that's a real, accepted payout for our "
        "purposes (money is committed), so treat those statuses as success "
        "rather than polling and waiting for 'processed'."
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self", action="store_true", dest="self_account",
                         help="Provision the reversal fund account (RZP_SELF_FUND_ACCOUNT_ID) instead of the demo payee one.")
    args = parser.parse_args()
    asyncio.run(main(self_account=args.self_account))