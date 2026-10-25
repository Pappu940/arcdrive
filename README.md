# ArcDrive

A vehicle rental web app for cars and bikes, built with Flask. Customers browse the fleet,
book a vehicle for a date range and pay when they return it. An admin manages the fleet and
records payments.

**Live demo:** _add your Vercel link here_

<!-- Add 2-3 screenshots here: home page, profile page, admin dashboard -->

## Features

- Sign up, log in, log out, and password reset through a signed link that expires after 30 minutes
- Separate pages for cars and bikes, with fuel filters and price sorting
- Bookings that check availability, so the same vehicle can't be booked twice for overlapping dates
- Pay on return: bookings carry no upfront charge, and the admin records the payment (cash, UPI or card)
- Customer profile with the last booking in detail, full history, payment status and cancellation
- Admin area: bookings dashboard, upload vehicle photos, change prices, add and remove vehicles
- ArcAI, a small keyword-based assistant that suggests vehicles from a sentence such as
  "family of 6, diesel, under 5000". It uses regular expressions and scoring, not machine learning.

## Tech stack

Python, Flask, Jinja templates, plain CSS and a little JavaScript, PostgreSQL (SQLite for local
development), Cloudinary for image storage, deployed on Vercel.

## Project structure

    app.py          routes and request handling
    database.py     connection, schema and starting data (PostgreSQL or SQLite)
    assistant.py    the ArcAI recommendation logic
    templates/      Jinja pages
    public/         CSS, JavaScript and photos
    tests/          pytest suite

## Run it locally

    python -m venv .venv
    source .venv/bin/activate        # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    python app.py                    # http://127.0.0.1:5000

The first account you register becomes the admin. Open `/admin` to upload photos and `/admin/dashboard` to manage bookings.

## Run the tests

    pip install -r requirements-dev.txt
    pytest

## Design decisions

- **One query helper for two databases.** Queries use `?` placeholders and `database.query()`
  adapts them for PostgreSQL. Production runs on Postgres, local development needs no setup.
- **Security basics.** Passwords are hashed, every form carries a CSRF token, reset links are
  signed and expire, uploads are checked by file signature, and the login redirect only follows local paths.
- **Dates as ISO text.** `YYYY-MM-DD` strings compare correctly, which keeps the overlap check to one query.
- **Vehicles with bookings can't be deleted**, so booking history never loses its vehicle.

## Possible next steps

Online payments, email confirmations, reviews and ratings, and a smarter ArcAI.
