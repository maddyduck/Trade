"""Forms for invoicing flows."""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    FieldList,
    FormField,
    HiddenField,
    IntegerField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class LineItemForm(FlaskForm):
    """A single invoice line item.

    `class Meta: csrf = False` — line items are sub-forms; CSRF lives on the parent.
    """
    class Meta:
        csrf = False

    description = StringField(
        "Description",
        validators=[DataRequired(), Length(max=255)],
    )
    quantity = StringField(  # we'll parse "1.5h" or "2" client-side
        "Qty",
        default="1",
        validators=[Length(max=12)],
    )
    unit_price = StringField(  # £ amount, parsed to pence
        "Unit price",
        validators=[DataRequired()],
    )


class CreateInvoiceForm(FlaskForm):
    booking_id = HiddenField(validators=[DataRequired()])
    notes = TextAreaField(
        "Notes (visible to customer)",
        validators=[Optional(), Length(max=2000)],
    )
    is_vat_registered = BooleanField("Add VAT to this invoice")
    vat_number = StringField(
        "VAT number",
        validators=[Optional(), Length(max=40)],
    )
    vat_rate = IntegerField(
        "VAT rate %",
        default=20,
        validators=[Optional(), NumberRange(min=0, max=50)],
    )
    payment_terms_days = IntegerField(
        "Payment terms (days)",
        default=14,
        validators=[NumberRange(min=0, max=180)],
    )


class WriteOffForm(FlaskForm):
    reason = TextAreaField(
        "Reason (internal — not sent to customer)",
        validators=[Optional(), Length(max=500)],
    )


class VoidInvoiceForm(FlaskForm):
    reason = TextAreaField(
        "Reason (internal — not sent to customer)",
        validators=[Optional(), Length(max=500)],
    )
