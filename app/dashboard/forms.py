"""Dashboard forms — services, availability, profile editing."""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    IntegerField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional, Regexp

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class ServiceForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=1000)])
    icon = StringField("Icon (emoji)", validators=[Optional(), Length(max=8)])
    duration_minutes = IntegerField(
        "Duration (minutes)", validators=[DataRequired(), NumberRange(min=15, max=480)]
    )
    deposit_pounds = IntegerField(
        "Deposit (£)", validators=[DataRequired(), NumberRange(min=0, max=1000)]
    )
    is_active = BooleanField("Active", default=True)
    is_emergency = BooleanField(
        "Emergency callout — surface prominently on booking page",
        default=False,
    )


class AvailabilityRuleForm(FlaskForm):
    weekday = SelectField(
        "Day",
        coerce=int,
        choices=[
            (0, "Monday"),
            (1, "Tuesday"),
            (2, "Wednesday"),
            (3, "Thursday"),
            (4, "Friday"),
            (5, "Saturday"),
            (6, "Sunday"),
        ],
    )
    start_time = StringField(
        "Start", validators=[DataRequired(), Regexp(HHMM, message="Use HH:MM format")]
    )
    end_time = StringField(
        "End", validators=[DataRequired(), Regexp(HHMM, message="Use HH:MM format")]
    )


class ProfileForm(FlaskForm):
    business_name = StringField(
        "Business name", validators=[DataRequired(), Length(max=120)]
    )
    contact_name = StringField("Your name", validators=[DataRequired(), Length(max=120)])
    bio = TextAreaField("Bio", validators=[Optional(), Length(max=1000)])
    phone = StringField("Phone", validators=[DataRequired(), Length(max=40)])
    service_area = StringField("Service area", validators=[Optional(), Length(max=200)])
    credentials = TextAreaField(
        "Credentials (Gas Safe, NICEIC, etc.)", validators=[Optional(), Length(max=1000)]
    )
    years_active = IntegerField(
        "Years trading", validators=[Optional(), NumberRange(min=0, max=80)]
    )
    cancellation_policy = SelectField(
        "Cancellation policy",
        choices=[
            ("24h_full", "Full refund if cancelled 24h+ before"),
            ("24h_partial", "Full 24h+, 50% under 24h, none under 2h"),
            ("non_refundable", "Non-refundable deposits"),
        ],
    )
    is_published = BooleanField("Publish booking page")
