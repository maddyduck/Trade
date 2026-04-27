"""Customer-facing forms."""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import HiddenField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Regexp

UK_POSTCODE_RE = r"^[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}$"
NI_POSTCODE_PREFIX = "BT"


def is_ni_postcode(postcode: str) -> bool:
    """Northern Ireland uses BT prefix for all postcodes."""
    if not postcode:
        return False
    return postcode.strip().upper().startswith(NI_POSTCODE_PREFIX)


class BookingForm(FlaskForm):
    service_id = HiddenField(validators=[DataRequired()])
    start_at_iso = HiddenField(validators=[DataRequired()])

    customer_name = StringField(
        "Your name", validators=[DataRequired(), Length(min=2, max=120)]
    )
    customer_phone = StringField(
        "Phone",
        validators=[
            DataRequired(),
            Regexp(r"^[\d\s\+\-\(\)]{7,20}$", message="Enter a valid phone number"),
        ],
    )
    customer_email = StringField(
        "Email", validators=[DataRequired(), Email(), Length(max=255)]
    )
    customer_postcode = StringField(
        "Postcode",
        validators=[
            DataRequired(),
            Regexp(
                UK_POSTCODE_RE,
                message="Enter a valid UK postcode, e.g. BT9 6AB",
            ),
        ],
    )
    customer_address = StringField("Address (flat / house)", validators=[Length(max=255)])
    job_notes = TextAreaField(
        "What's the problem?",
        validators=[DataRequired(), Length(min=5, max=2000)],
    )

    def validate(self, extra_validators=None) -> bool:
        # Run base validation first
        if not super().validate(extra_validators=extra_validators):
            return False
        # Normalise postcode to upper + single-space
        if self.customer_postcode.data:
            pc = self.customer_postcode.data.strip().upper()
            if " " not in pc and len(pc) >= 5:
                pc = pc[:-3] + " " + pc[-3:]
            self.customer_postcode.data = pc
        return True


class FindBookingForm(FlaskForm):
    reference = StringField("Booking reference", validators=[DataRequired(), Length(max=20)])
    contact = StringField(
        "Phone or email used to book", validators=[DataRequired(), Length(max=255)]
    )


class MagicLinkForm(FlaskForm):
    contact = StringField(
        "Phone or email", validators=[DataRequired(), Length(max=255)]
    )
