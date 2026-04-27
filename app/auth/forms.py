"""Login and signup forms."""
from __future__ import annotations

from flask_wtf import FlaskForm
from slugify import slugify
from sqlalchemy import select
from wtforms import BooleanField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Regexp, ValidationError

from app.extensions import db
from app.models import Trade


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=1, max=200)])
    remember_me = BooleanField("Stay signed in")


class SignupForm(FlaskForm):
    business_name = StringField(
        "Business name", validators=[DataRequired(), Length(min=2, max=120)]
    )
    contact_name = StringField(
        "Your name", validators=[DataRequired(), Length(min=2, max=120)]
    )
    trade_type = SelectField(
        "Trade",
        choices=[
            ("plumber", "Plumber / heating engineer"),
            ("electrician", "Electrician"),
            ("other", "Other"),
        ],
        default="plumber",
    )
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    phone = StringField(
        "Phone",
        validators=[
            DataRequired(),
            Regexp(r"^[\d\s\+\-\(\)]{7,20}$", message="Enter a valid phone number"),
        ],
    )
    password = PasswordField(
        "Password", validators=[DataRequired(), Length(min=8, max=200)]
    )
    confirm_password = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match")],
    )

    def validate_email(self, field):
        existing = db.session.execute(
            select(Trade).where(Trade.email == field.data.lower())
        ).scalar_one_or_none()
        if existing:
            raise ValidationError("An account with this email already exists.")

    def unique_slug(self) -> str:
        base = slugify(self.business_name.data)[:60] or "trade"
        candidate = base
        n = 1
        while db.session.execute(
            select(Trade).where(Trade.slug == candidate)
        ).scalar_one_or_none():
            n += 1
            candidate = f"{base}-{n}"
        return candidate
