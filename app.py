"""ArcDrive: a vehicle rental web app built with Flask.

Customers browse cars and bikes, book them for a date range and pay when they
return the vehicle. The first account to register becomes the admin, who manages
the fleet and records payments.
"""
import datetime as dt
import os
import re
import secrets
import smtplib
from email.message import EmailMessage
from functools import wraps

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from assistant import recommend
from database import close_db, commit, init_db, query

TAX_RATE = 0.18
RESET_LINK_MINUTES = 30
PAYMENT_METHODS = ("Cash", "UPI", "Card")

# ---------------------------------------------------------------- app setup

# public/ holds the CSS, JS and photos. Vercel serves it straight from its CDN.
app = Flask(__name__, static_folder="public", static_url_path="")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # trust the proxy's headers
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # Vercel rejects bigger uploads anyway

ON_VERCEL = bool(os.environ.get("VERCEL"))
app.secret_key = os.environ.get("SECRET_KEY") or (None if ON_VERCEL else "dev-only-secret")
if not app.secret_key:
    raise RuntimeError("Set SECRET_KEY in the Vercel environment variables.")
app.config.update(SESSION_COOKIE_SECURE=ON_VERCEL, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_HTTPONLY=True)
app.teardown_appcontext(close_db)
token_signer = URLSafeTimedSerializer(app.secret_key)


@app.before_request
def prepare_request():
    init_db()
    # Every form carries a CSRF token. Logout is exempt: signing someone out is
    # harmless, and it should never fail because of an old session.
    if request.method != "POST" or request.endpoint == "logout":
        return
    sent_token = request.form.get("csrf") or request.headers.get("X-CSRF")
    if sent_token != session.get("csrf"):
        abort(400)


@app.context_processor
def template_helpers():
    """Values every template can use."""
    session.setdefault("csrf", secrets.token_hex(16))
    return {
        "csrf": session["csrf"],
        "uname": session.get("uname"),
        "is_admin": session.get("admin"),
        "inr": format_rupees,
        "imgsrc": image_url,
    }


@app.errorhandler(400)
def bad_request(_error):
    flash("Your session expired. Please try again.")
    return redirect(request.referrer or url_for("index"))


@app.errorhandler(413)
def file_too_large(_error):
    flash("Image is too large. The limit is 4 MB.")
    return redirect(request.referrer or url_for("index"))


# ------------------------------------------------------------------ helpers

def format_rupees(amount):
    return f"₹{amount:,}"


def image_url(name):
    if not name:
        return ""
    return name if name.startswith("http") else "/img/" + name


def positive_int(value):
    """Return value as a positive int, or None if it isn't one."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_date(value):
    try:
        return dt.date.fromisoformat(value or "")
    except ValueError:
        return None


def booking_status(booking):
    """cancelled, upcoming, active or completed, worked out from the dates."""
    today = dt.date.today().isoformat()
    if booking["status"] == "cancelled":
        return "cancelled"
    if booking["start_date"] > today:
        return "upcoming"
    if booking["end_date"] >= today:
        return "active"
    return "completed"


def rental_days(booking):
    start = dt.date.fromisoformat(booking["start_date"])
    end = dt.date.fromisoformat(booking["end_date"])
    return (end - start).days


def vehicle_is_booked(vehicle_id, start, end):
    """True if another live booking overlaps the dates (the return day itself is free)."""
    return query(
        "select 1 from bookings where vehicle_id=? and status!='cancelled' "
        "and start_date<? and end_date>?",
        (vehicle_id, end, start)).fetchone() is not None


def cancel_if_upcoming(booking):
    if booking_status(booking) != "upcoming":
        flash("Only upcoming bookings can be cancelled.")
        return
    query("update bookings set status='cancelled' where id=?", (booking["id"],))
    commit()
    flash(f"Booking {booking['ref']} cancelled.")


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "uid" not in session:
            flash("Log in to continue.")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            abort(403)
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------- the fleet

def vehicles_of_kind(kind, fuel="All", sort="low"):
    sql, params = "select * from vehicles where kind=?", [kind]
    if fuel != "All":
        sql += " and fuel=?"
        params.append(fuel)
    sql += " order by price" + (" desc" if sort == "high" else "")
    return query(sql, params).fetchall()


def show_fleet(kind):
    fuel = request.args.get("fuel", "All")
    sort = request.args.get("sort", "low")
    fuels = [row["fuel"] for row in
             query("select distinct fuel from vehicles where kind=?", (kind,)).fetchall()]
    return render_template("fleet.html", vehicles=vehicles_of_kind(kind, fuel, sort), kind=kind,
                           fuel=fuel, sort=sort, fuels=fuels, today=dt.date.today().isoformat())


@app.route("/")
def index():
    featured = vehicles_of_kind("Car")[:2] + vehicles_of_kind("Bike")[:2]
    return render_template("index.html", vehicles=featured, today=dt.date.today().isoformat())


@app.route("/cars")
def cars():
    return show_fleet("Car")


@app.route("/bikes")
def bikes():
    return show_fleet("Bike")


@app.route("/ai", methods=["POST"])
def ai():
    text = (request.get_json(silent=True) or {}).get("q", "")[:300]
    picks = recommend(text, query("select * from vehicles").fetchall())
    if picks:
        message = "Here are my top picks:"
    else:
        message = "I could not find a match. Try a different budget, fuel or seat count."
    return {"message": message, "results": picks}


# ----------------------------------------------------------- accounts

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        if len(name) < 2 or "@" not in email:
            flash("Enter your name and a valid email.")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.")
        elif query("select 1 from users where email=?", (email,)).fetchone():
            flash("An account with this email already exists. Log in instead.")
        else:
            is_first_user = query("select count(*) as n from users").fetchone()["n"] == 0
            cursor = query("insert into users (name, email, pw, is_admin) values (?,?,?,?) returning id",
                           (name, email, generate_password_hash(password), int(is_first_user)))
            user_id = cursor.fetchone()["id"]
            commit()
            session.update(uid=user_id, uname=name, admin=is_first_user)  # the first account is the admin
            return redirect(url_for("index"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        user = query("select * from users where email=?", (email,)).fetchone()
        if user and check_password_hash(user["pw"], request.form["password"]):
            session.update(uid=user["id"], uname=user["name"], admin=bool(user["is_admin"]))
            next_page = request.args.get("next", "/")
            # Only follow local paths, so the login page can't redirect to another site.
            if not next_page.startswith("/") or next_page.startswith("//"):
                next_page = "/"
            return redirect(next_page)
        flash("Email or password is incorrect.")
    return render_template("auth.html", mode="login")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


def send_reset_email(email, link):
    """Email the reset link. Without SMTP settings the link is printed to the log instead."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        print(f"\n[ArcDrive] Password reset link for {email}: {link}\n")
        return False
    message = EmailMessage()
    message["Subject"] = "Reset your ArcDrive password"
    message["From"] = os.environ.get("SMTP_FROM", "no-reply@arcdrive.example")
    message["To"] = email
    message.set_content(f"Use this link within {RESET_LINK_MINUTES} minutes to reset your password:\n{link}")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as server:
        server.starttls()
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        server.send_message(message)
    return True


@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        if query("select 1 from users where email=?", (email,)).fetchone():
            token = token_signer.dumps(email, salt="password-reset")
            link = url_for("reset", token=token, _external=True)
            email_sent = send_reset_email(email, link)
            if not email_sent and app.debug:
                flash(f"Dev mode, email not configured. Reset link: {link}")
        # Same message whether or not the account exists, so emails can't be probed.
        flash("If that email has an account, a reset link is on its way.")
        return redirect(url_for("forgot"))
    return render_template("auth.html", mode="forgot")


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    try:
        email = token_signer.loads(token, salt="password-reset", max_age=RESET_LINK_MINUTES * 60)
    except (BadSignature, SignatureExpired):
        flash("This reset link is invalid or has expired. Request a new one.")
        return redirect(url_for("forgot"))
    if request.method == "POST":
        password = request.form["password"]
        if len(password) < 8:
            flash("Password must be at least 8 characters.")
        elif password != request.form["confirm"]:
            flash("Passwords do not match.")
        else:
            query("update users set pw=? where email=?", (generate_password_hash(password), email))
            commit()
            flash("Password updated. Log in with your new password.")
            return redirect(url_for("login"))
    return render_template("auth.html", mode="reset")


# ----------------------------------------------------------------- bookings

@app.route("/book/<int:vid>", methods=["POST"])
@login_required
def book(vid):
    vehicle = query("select * from vehicles where id=?", (vid,)).fetchone() or abort(404)
    back = request.referrer or url_for("index")

    start = parse_date(request.form.get("start"))
    end = parse_date(request.form.get("end"))
    if not start or not end:
        flash("Choose valid dates.")
        return redirect(back)
    if start < dt.date.today() or end <= start:
        flash("Pick a future pickup date and a return date after it.")
        return redirect(back)
    if vehicle_is_booked(vid, start.isoformat(), end.isoformat()):
        flash(f"{vehicle['name']} is already booked for those dates. Try other dates.")
        return redirect(back)

    days = (end - start).days
    total = round(vehicle["price"] * days * (1 + TAX_RATE))
    reference = "AD-" + secrets.token_hex(3).upper()
    query("insert into bookings (user_id, vehicle_id, start_date, end_date, total, ref) values (?,?,?,?,?,?)",
          (session["uid"], vid, start.isoformat(), end.isoformat(), total, reference))
    commit()
    flash(f"Booking confirmed. Reference {reference}. Pay {format_rupees(total)} when you return the vehicle.")
    return redirect(url_for("profile"))


@app.route("/bookings")
def bookings():
    return redirect(url_for("profile"))


@app.route("/profile")
@login_required
def profile():
    user = query("select * from users where id=?", (session["uid"],)).fetchone()
    rows = query(
        "select b.*, v.name, v.kind, v.seats, v.fuel, v.gear, v.price, v.img "
        "from bookings b join vehicles v on v.id = b.vehicle_id "
        "where b.user_id=? order by b.id desc", (user["id"],)).fetchall()
    live = [r for r in rows if r["status"] != "cancelled"]
    return render_template(
        "profile.html", u=user, rows=rows, last=rows[0] if rows else None,
        state=booking_status, days=rental_days, count=len(live),
        spent=sum(r["total"] for r in live if r["paid_at"]),
        due=sum(r["total"] for r in live if not r["paid_at"]),
        upcoming=sum(1 for r in rows if booking_status(r) == "upcoming"))


@app.route("/cancel/<int:bid>", methods=["POST"])
@login_required
def cancel(bid):
    booking = query("select * from bookings where id=? and user_id=?",
                    (bid, session["uid"])).fetchone() or abort(404)
    cancel_if_upcoming(booking)
    return redirect(url_for("profile"))


# -------------------------------------------------------------------- admin

def detect_image_type(first_bytes):
    """Identify JPG, PNG or WebP from the file's first bytes, not from its name."""
    if first_bytes[:3] == b"\xff\xd8\xff":
        return "jpg"
    if first_bytes[:4] == b"\x89PNG":
        return "png"
    if first_bytes[:4] == b"RIFF" and first_bytes[8:12] == b"WEBP":
        return "webp"
    return None


def store_vehicle_image(upload, vehicle_name):
    """Save an uploaded photo. Returns what to keep in vehicles.img, or None on failure."""
    if not upload or not upload.filename:
        return None
    extension = detect_image_type(upload.read(12))
    upload.seek(0)
    if not extension:
        return None
    if os.environ.get("CLOUDINARY_URL"):  # persistent storage, needed on Vercel
        import cloudinary.uploader
        result = cloudinary.uploader.upload(upload, folder="arcdrive", resource_type="image")
        return result["secure_url"]
    if ON_VERCEL:
        return None  # Vercel's filesystem is read-only
    slug = re.sub(r"[^a-z0-9]+", "-", vehicle_name.lower()).strip("-")
    filename = f"{slug}-{secrets.token_hex(3)}.{extension}"
    upload.save(os.path.join(app.root_path, "public", "img", filename))
    return filename


@app.route("/admin")
@admin_required
def admin():
    uploads_enabled = bool(os.environ.get("CLOUDINARY_URL")) or not ON_VERCEL
    vehicles = query("select * from vehicles order by kind, name").fetchall()
    return render_template("admin.html", vehicles=vehicles, uploads_ok=uploads_enabled)


@app.route("/admin/image/<int:vid>", methods=["POST"])
@admin_required
def admin_image(vid):
    vehicle = query("select * from vehicles where id=?", (vid,)).fetchone() or abort(404)
    image = store_vehicle_image(request.files.get("image"), vehicle["name"])
    if image:
        query("update vehicles set img=? where id=?", (image, vid))
        commit()
        flash(f"Photo updated for {vehicle['name']}.")
    else:
        flash("Upload a JPG, PNG or WebP image.")
    return redirect(url_for("admin"))


@app.route("/admin/add", methods=["POST"])
@admin_required
def admin_add():
    form = request.form
    name = form.get("name", "").strip()
    fuel = form.get("fuel", "").strip()
    kind, gear = form.get("kind"), form.get("gear")
    seats, price = positive_int(form.get("seats")), positive_int(form.get("price"))
    if not (name and fuel and kind in ("Car", "Bike") and gear in ("Manual", "Automatic") and seats and price):
        flash("Fill in every field with valid values.")
        return redirect(url_for("admin"))
    image = store_vehicle_image(request.files.get("image"), name) or ""
    query("insert into vehicles (name, kind, seats, fuel, gear, price, img) values (?,?,?,?,?,?,?)",
          (name, kind, seats, fuel, gear, price, image))
    commit()
    flash(f"{name} added." + ("" if image else " No valid photo was uploaded, so an icon is shown."))
    return redirect(url_for("admin"))


@app.route("/admin/price/<int:vid>", methods=["POST"])
@admin_required
def admin_price(vid):
    query("select 1 from vehicles where id=?", (vid,)).fetchone() or abort(404)
    price = positive_int(request.form.get("price"))
    if not price:
        flash("Enter a valid price.")
    else:
        query("update vehicles set price=? where id=?", (price, vid))
        commit()
        flash("Price updated.")
    return redirect(url_for("admin"))


@app.route("/admin/delete/<int:vid>", methods=["POST"])
@admin_required
def admin_delete(vid):
    vehicle = query("select * from vehicles where id=?", (vid,)).fetchone() or abort(404)
    if query("select 1 from bookings where vehicle_id=?", (vid,)).fetchone():
        flash(f"{vehicle['name']} has bookings, so it can't be deleted.")  # keeps booking history intact
    else:
        query("delete from vehicles where id=?", (vid,))
        commit()
        flash(f"{vehicle['name']} deleted.")
    return redirect(url_for("admin"))


@app.route("/admin/dashboard")
@admin_required
def dashboard():
    rows = query(
        "select b.*, u.name as customer, u.email, v.name as vehicle "
        "from bookings b join users u on u.id = b.user_id join vehicles v on v.id = b.vehicle_id "
        "order by b.id desc").fetchall()
    live = [r for r in rows if r["status"] != "cancelled"]
    user_count = query("select count(*) as n from users").fetchone()["n"]
    return render_template(
        "dashboard.html", rows=rows, state=booking_status, users=user_count, bookings=len(live),
        collected=sum(r["total"] for r in live if r["paid_at"]),
        due=sum(r["total"] for r in live if not r["paid_at"]))


@app.route("/admin/cancel/<int:bid>", methods=["POST"])
@admin_required
def admin_cancel(bid):
    booking = query("select * from bookings where id=?", (bid,)).fetchone() or abort(404)
    cancel_if_upcoming(booking)
    return redirect(url_for("dashboard"))


@app.route("/admin/paid/<int:bid>", methods=["POST"])
@admin_required
def admin_paid(bid):
    """Record that the customer paid when they returned the vehicle."""
    booking = query("select * from bookings where id=?", (bid,)).fetchone() or abort(404)
    method = request.form.get("method")
    if method not in PAYMENT_METHODS:
        flash("Choose how the customer paid.")
    elif booking["paid_at"]:
        flash("This booking is already marked as paid.")
    elif booking_status(booking) not in ("active", "completed"):
        flash("Payment can be recorded once the rental has started.")
    else:
        query("update bookings set paid_at=?, pay_method=? where id=?",
              (dt.date.today().isoformat(), method, bid))
        commit()
        flash(f"Payment of {format_rupees(booking['total'])} recorded for {booking['ref']} ({method}).")
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=True)
