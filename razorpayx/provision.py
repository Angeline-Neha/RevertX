"""
razorpayx/provision.py
Run ONCE to create a test Contact + Fund Account. Print the fund_account_id
and put it in .env as RZP_DEMO_FUND_ACCOUNT_ID — the payout path reuses it
on every run instead of creating a new contact per payment.
"""
import asyncio
from razorpayx.client import create_contact, create_fund_account_vpa

async def main():
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
    asyncio.run(main())