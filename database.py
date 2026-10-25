"""Database access for ArcDrive.

PostgreSQL is used when DATABASE_URL is set (production on Vercel). Otherwise the
app falls back to a local SQLite file, so it runs with no setup while developing.
"""
import os
import sqlite3

from flask import g

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
USING_POSTGRES = bool(DATABASE_URL)
SQLITE_PATH = os.environ.get("ARCDRIVE_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "arcdrive.db")

if USING_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row

PRIMARY_KEY = "serial primary key" if USING_POSTGRES else "integer primary key"

# Dates are stored as ISO text (YYYY-MM-DD), so comparing them as strings works.
SCHEMA = [
    f"""create table if not exists users (
        id {PRIMARY_KEY}, name text, email text unique, pw text, is_admin int default 0)""",
    f"""create table if not exists vehicles (
        id {PRIMARY_KEY}, name text, kind text, seats int, fuel text, gear text,
        price int, img text)""",
    f"""create table if not exists bookings (
        id {PRIMARY_KEY}, user_id int, vehicle_id int, start_date text, end_date text,
        total int, ref text, status text default 'confirmed', paid_at text, pay_method text)""",
]

# Photos are either a file name inside public/img/ or a full image URL.
SEED_VEHICLES = [
    {"name": "Honda City", "kind": "Car", "seats": 5, "fuel": "Petrol", "gear": "Automatic", "price": 2800, "img": "honda-city.jpg"},
    {"name": "Toyota Innova Crysta", "kind": "Car", "seats": 7, "fuel": "Diesel", "gear": "Manual", "price": 4200, "img": "innova.jpg"},
    {"name": "BMW 5 Series", "kind": "Car", "seats": 5, "fuel": "Petrol", "gear": "Automatic", "price": 11500, "img": "bmw-5.jpg"},
    {"name": "Tata Nexon EV", "kind": "Car", "seats": 5, "fuel": "Electric", "gear": "Automatic", "price": 3500, "img": "nexon-ev.jpg"},
    {"name": "Royal Enfield Classic 350", "kind": "Bike", "seats": 2, "fuel": "Petrol", "gear": "Manual", "price": 1200, "img": "classic-350.jpg"},
    {"name": "KTM Duke 390", "kind": "Bike", "seats": 2, "fuel": "Petrol", "gear": "Manual", "price": 1800, "img": "duke-390.jpg"},
    {"name": "Royal Enfield Himalayan", "kind": "Bike", "seats": 2, "fuel": "Petrol", "gear": "Manual", "price": 2200, "img": "himalayan.jpg"},
    {"name": "Vespa Urban Club", "kind": "Bike", "seats": 2, "fuel": "Petrol", "gear": "Automatic", "price": 900, "img": "vespa.jpg"},
]
VEHICLE_COLUMNS = ("name", "kind", "seats", "fuel", "gear", "price", "img")

_schema_ready = False


def get_db():
    """Open one connection per request and reuse it."""
    if "db" not in g:
        if USING_POSTGRES:
            g.db = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        else:
            g.db = sqlite3.connect(SQLITE_PATH)
            g.db.row_factory = sqlite3.Row
    return g.db


def query(sql, params=()):
    """Run a statement. Queries are written with ? placeholders for both databases."""
    if USING_POSTGRES:
        sql = sql.replace("?", "%s")
    return get_db().execute(sql, params)


def commit():
    get_db().commit()


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _add_missing_columns():
    """Upgrade databases created before these columns existed."""
    new_columns = [
        ("users", "is_admin int default 0"),
        ("bookings", "paid_at text"),
        ("bookings", "pay_method text"),
    ]
    for table, column in new_columns:
        maybe = "if not exists " if USING_POSTGRES else ""
        try:
            query(f"alter table {table} add column {maybe}{column}")
            commit()
        except Exception:
            get_db().rollback()  # the column is already there


def init_db():
    """Create the tables and the starting fleet. Safe to call on every request."""
    global _schema_ready
    if _schema_ready:
        return
    for statement in SCHEMA:
        query(statement)
    commit()
    _add_missing_columns()
    if query("select count(*) as n from vehicles").fetchone()["n"] == 0:
        for vehicle in SEED_VEHICLES:
            query("insert into vehicles (name, kind, seats, fuel, gear, price, img) values (?,?,?,?,?,?,?)",
                  tuple(vehicle[column] for column in VEHICLE_COLUMNS))
    commit()
    _schema_ready = True
