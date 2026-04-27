"""Auth routes — trade signup, login, logout."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from app.auth.forms import LoginForm, SignupForm
from app.extensions import db
from app.models import Trade

bp = Blueprint("auth", __name__, template_folder="../templates/auth")


def _safe_next(next_url: str | None) -> str:
    # Only allow same-site relative URLs.
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return url_for("dashboard.today")


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.today"))

    form = SignupForm()
    if form.validate_on_submit():
        trade = Trade(
            email=form.email.data.strip().lower(),
            slug=form.unique_slug(),
            business_name=form.business_name.data.strip(),
            contact_name=form.contact_name.data.strip(),
            trade_type=form.trade_type.data,
            phone=form.phone.data.strip(),
        )
        trade.set_password(form.password.data)
        db.session.add(trade)
        db.session.commit()
        login_user(trade)
        flash("Welcome to Sorted. Let's get you set up.", "success")
        return redirect(url_for("dashboard.today"))
    return render_template("auth/signup.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.today"))

    form = LoginForm()
    if form.validate_on_submit():
        trade = db.session.execute(
            select(Trade).where(Trade.email == form.email.data.strip().lower())
        ).scalar_one_or_none()
        if trade and trade.check_password(form.password.data):
            login_user(trade, remember=form.remember_me.data)
            flash(f"Welcome back, {trade.contact_name.split()[0]}.", "success")
            return redirect(_safe_next(request.args.get("next")))
        flash("Email or password not recognised.", "error")
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Signed out.", "info")
    return redirect(url_for("public.home"))


# ---------- Password reset ----------


@bp.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    """Request a password reset email.

    Always returns the same flash message regardless of whether the
    email exists, to avoid leaking which emails have accounts.
    """
    from app.auth.password_reset_forms import ForgotPasswordForm
    from app.services import email as email_svc
    from app.services.password_reset import make_reset_token

    if current_user.is_authenticated:
        return redirect(url_for("dashboard.today"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        trade = db.session.execute(
            select(Trade).where(Trade.email == email)
        ).scalar_one_or_none()

        if trade:
            token = make_reset_token(trade.id)
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            email_svc.send_email(
                to=trade.email,
                subject="Reset your password",
                template="emails/password_reset",
                trade=trade,
                reset_url=reset_url,
            )

        # Same response either way — don't leak which emails exist.
        flash(
            "If that email is on file, you'll receive a reset link shortly. "
            "Check your spam folder if you don't see it within a few minutes.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html", form=form)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    """Verify a reset token and let the trade pick a new password."""
    from app.auth.password_reset_forms import ResetPasswordForm
    from app.services.password_reset import read_reset_token

    if current_user.is_authenticated:
        return redirect(url_for("dashboard.today"))

    trade_id = read_reset_token(token)
    if trade_id is None:
        flash(
            "That reset link has expired or is invalid. Request a new one.",
            "error",
        )
        return redirect(url_for("auth.forgot_password"))

    trade = db.session.get(Trade, trade_id)
    if trade is None:
        flash("Account not found.", "error")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        trade.set_password(form.password.data)
        db.session.commit()
        flash("Password updated. You can sign in now.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form, trade=trade)
