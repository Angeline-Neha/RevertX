"""
refund_math.py
Pure deterministic refund calculation.  The LLM (policy_extractor) only
extracts penalty_percentage and refundable — it never computes a ₹ figure.
This function is the only place arithmetic happens.
"""


def compute_refund(
    original_amount: float,
    penalty_percentage: float | None,
    refundable: bool,
) -> float | None:
    """
    Return the net refund amount, or None if the policy is non-refundable.

    Args:
        original_amount:    The amount originally charged (e.g. 50000.0).
        penalty_percentage: Cancellation fee as a percentage (e.g. 10.0 for 10%).
                            None means no penalty.
        refundable:         Whether the merchant's policy allows any refund at all.

    Returns:
        Net refund amount (float) rounded to 2 decimal places,
        or None if non-refundable.
    """
    if not refundable:
        return None
    penalty_pct = penalty_percentage or 0.0
    penalty = original_amount * (penalty_pct / 100.0)
    return round(original_amount - penalty, 2)
