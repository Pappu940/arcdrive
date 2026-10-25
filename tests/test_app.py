"""Tests for ArcDrive. Run them with:  pytest

They use a throwaway SQLite file, so they never touch real data.
"""
import datetime as dt
import os
import re
import tempfile

import pytest

os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)
os.environ["ARCDRIVE_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import database  # noqa: E402
from app import app, token_signer  # noqa: E402
from assistant import recommend  # noqa: E402


@pytest.fixture
def client():
    """A fresh app and an empty database for every test."""
    if os.path.exists(database.SQLITE_PATH):
        os.remove(database.SQLITE_PATH)
    database._schema_ready = False
    app.config["TESTING"] = True
    return app.test_client()


def csrf_token(client, path):
    page = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf" value="(\w+)"', page).group(1)


def sign_up(client, name, email):
    token = csrf_token(client, "/register")
    client.post("/register", data={"csrf": token, "name": name, "email": email, "password": "password1"})
    return token


def day(offset):
    return (dt.date.today() + dt.timedelta(days=offset)).isoformat()


def book(client, token, vehicle_id, start_offset, end_offset):
    return client.post(f"/book/{vehicle_id}", follow_redirects=True,
                       data={"csrf": token, "start": day(start_offset), "end": day(end_offset)})


# ---------------------------------------------------------------- pages

def test_pages_load(client):
    for path in ("/", "/cars", "/bikes", "/login", "/register", "/forgot"):
        assert client.get(path).status_code == 200


def test_cars_and_bikes_are_on_separate_pages(client):
    assert b"Honda City" in client.get("/cars").data
    assert b"Honda City" not in client.get("/bikes").data
    assert b"KTM Duke 390" in client.get("/bikes").data


# ------------------------------------------------------------- accounts

def test_first_user_is_admin_and_second_is_not(client):
    sign_up(client, "Admin User", "admin@example.com")
    assert client.get("/admin").status_code == 200
    other = app.test_client()
    sign_up(other, "Normal User", "user@example.com")
    assert other.get("/admin").status_code == 403


def test_wrong_password_is_rejected(client):
    sign_up(client, "Asha", "asha@example.com")
    client.post("/logout")
    token = csrf_token(client, "/login")
    page = client.post("/login", follow_redirects=True,
                       data={"csrf": token, "email": "asha@example.com", "password": "wrong-password"})
    assert b"incorrect" in page.data


def test_password_reset_with_signed_link(client):
    sign_up(client, "Asha", "asha@example.com")
    client.post("/logout")
    link = "/reset/" + token_signer.dumps("asha@example.com", salt="password-reset")
    token = csrf_token(client, "/login")
    client.post(link, data={"csrf": token, "password": "brand-new-pass", "confirm": "brand-new-pass"})
    login = client.post("/login", data={"csrf": token, "email": "asha@example.com", "password": "brand-new-pass"})
    assert login.status_code == 302


def test_bad_reset_link_is_refused(client):
    page = client.get("/reset/not-a-real-token", follow_redirects=True)
    assert b"invalid or has expired" in page.data


# ------------------------------------------------------------- bookings

def test_booking_is_pay_on_return(client):
    sign_up(client, "Asha", "asha@example.com")
    token = csrf_token(client, "/")
    page = book(client, token, 1, 5, 8)
    assert b"when you return the vehicle" in page.data
    assert b"To pay on return" in page.data


def test_overlapping_booking_is_rejected(client):
    sign_up(client, "Asha", "asha@example.com")
    token = csrf_token(client, "/")
    book(client, token, 1, 5, 8)
    assert b"already booked" in book(client, token, 1, 6, 9).data


def test_back_to_back_booking_is_allowed(client):
    sign_up(client, "Asha", "asha@example.com")
    token = csrf_token(client, "/")
    book(client, token, 1, 5, 8)
    assert b"already booked" not in book(client, token, 1, 8, 10).data


def test_past_dates_are_rejected(client):
    sign_up(client, "Asha", "asha@example.com")
    token = csrf_token(client, "/")
    assert b"future pickup date" in book(client, token, 1, -3, 2).data


def test_upcoming_booking_can_be_cancelled(client):
    sign_up(client, "Asha", "asha@example.com")
    token = csrf_token(client, "/")
    book(client, token, 1, 5, 8)
    page = client.post("/cancel/1", data={"csrf": token}, follow_redirects=True)
    assert b"cancelled" in page.data


def test_one_user_cannot_cancel_anothers_booking(client):
    sign_up(client, "Asha", "asha@example.com")
    token = csrf_token(client, "/")
    book(client, token, 1, 5, 8)
    other = app.test_client()
    other_token = sign_up(other, "Ravi", "ravi@example.com")
    assert other.post("/cancel/1", data={"csrf": other_token}).status_code == 404


# ---------------------------------------------------------------- admin

def test_admin_records_payment_after_rental_starts(client):
    admin_token = sign_up(client, "Admin User", "admin@example.com")
    customer = app.test_client()
    customer_token = sign_up(customer, "Ravi", "ravi@example.com")
    book(customer, customer_token, 1, 0, 3)  # starts today, so it counts as active

    page = client.post("/admin/paid/1", follow_redirects=True, data={"csrf": admin_token, "method": "UPI"})
    assert b"recorded" in page.data
    assert b"Paid via UPI" in client.get("/admin/dashboard").data


def test_payment_cannot_be_recorded_for_upcoming_booking(client):
    admin_token = sign_up(client, "Admin User", "admin@example.com")
    customer = app.test_client()
    customer_token = sign_up(customer, "Ravi", "ravi@example.com")
    book(customer, customer_token, 1, 10, 12)
    page = client.post("/admin/paid/1", follow_redirects=True, data={"csrf": admin_token, "method": "Cash"})
    assert b"once the rental has started" in page.data


def test_vehicle_with_bookings_cannot_be_deleted(client):
    admin_token = sign_up(client, "Admin User", "admin@example.com")
    book(client, admin_token, 1, 5, 8)
    page = client.post("/admin/delete/1", data={"csrf": admin_token}, follow_redirects=True)
    assert b"has bookings" in page.data


def test_fake_image_upload_is_rejected(client):
    import io
    token = sign_up(client, "Admin User", "admin@example.com")
    page = client.post("/admin/image/1", content_type="multipart/form-data", follow_redirects=True,
                       data={"csrf": token, "image": (io.BytesIO(b"not really an image"), "photo.png")})
    assert b"Upload a JPG, PNG or WebP" in page.data


def test_form_without_csrf_token_is_refused(client):
    sign_up(client, "Asha", "asha@example.com")
    page = client.post("/book/1", data={"start": day(5), "end": day(8)}, follow_redirects=True)
    assert b"session expired" in page.data


# ------------------------------------------------------------ assistant

FLEET = [dict(id=i, **v) for i, v in enumerate(database.SEED_VEHICLES, start=1)]


def test_assistant_finds_family_diesel_car():
    picks = recommend("family of 6, diesel, under 5000", FLEET)
    assert picks[0]["name"] == "Toyota Innova Crysta"


def test_assistant_suggests_himalayan_for_mountains():
    picks = recommend("mountain adventure ride", FLEET)
    assert picks[0]["name"] == "Royal Enfield Himalayan"


def test_assistant_returns_nothing_when_budget_is_too_low():
    assert recommend("luxury car under 1000", FLEET) == []
