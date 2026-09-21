import re
from decimal import Decimal

from django.core.exceptions import ValidationError


def validate_bangladeshi_phone(value):
    """
    Validates that the value is a valid Bangladeshi mobile number:
    exactly 11 digits starting with "01".
    """
    if not isinstance(value, str) or not re.fullmatch(r'01\d{9}', value):
        raise ValidationError(
            'Must be 11 digits starting with 01.',
            code='invalid_phone',
        )


def validate_transaction_id(value):
    """
    Validates that the transaction ID is a string of at most 30 characters.
    """
    if not isinstance(value, str) or len(value) > 30 or len(value) == 0:
        raise ValidationError(
            'Transaction ID must be between 1 and 30 characters.',
            code='invalid_transaction_id',
        )


def validate_amount(value):
    """
    Validates that the amount is a decimal between 1 and 99999 (inclusive).
    """
    try:
        amount = Decimal(str(value))
    except Exception:
        raise ValidationError(
            'Amount must be a valid decimal number.',
            code='invalid_amount',
        )

    if amount < Decimal('1') or amount > Decimal('99999'):
        raise ValidationError(
            'Amount must be between 1 and 99999.',
            code='invalid_amount',
        )
