"""ArcAI, the small vehicle assistant on the home page.

There is no machine learning here. It reads a free-text request, picks out the
seats, budget, fuel and kind of trip with regular expressions, and ranks the
fleet with simple scores.
"""
import re

BIKE_WORDS = r"\b(bike|bikes|motorcycle|scooter|riding|ride)\b"
CAR_WORDS = r"\b(car|cars|suv|sedan|family|luxury|driver)\b"
SEATS = r"(\d+)\s*(people|persons|passengers|seats|friends|members|of us)"
BUDGET = r"(?:under|below|budget|within|less than|max)\D{0,8}(\d[\d,]*)"
FUELS = ("electric", "diesel", "petrol")
PREMIUM_PRICE = 8000  # cars above this daily price count as "premium"


def parse_request(text):
    """Turn a sentence like 'family of 6, diesel, under 5000' into search filters."""
    text = text.lower()
    kind = None
    if re.search(BIKE_WORDS, text):
        kind = "Bike"
    elif re.search(CAR_WORDS, text):
        kind = "Car"

    seats_match = re.search(SEATS, text)
    if seats_match:
        seats = int(seats_match.group(1))
    else:
        seats = 4 if "family" in text else 1

    budget_match = re.search(BUDGET, text)
    budget = int(budget_match.group(1).replace(",", "")) if budget_match else None

    fuel = next((f for f in FUELS if f in text), None)
    if fuel is None and re.search(r"\bev\b", text):
        fuel = "electric"

    return {
        "kind": kind,
        "seats": seats,
        "budget": budget,
        "fuel": fuel,
        "luxury": bool(re.search(r"luxury|premium|wedding|business", text)),
        "adventure": bool(re.search(r"trek|mountain|adventure|off-?road|ladakh|himalaya|long ride", text)),
        "cheap": bool(re.search(r"cheap|cheapest|affordable|low cost", text)),
    }


def score_vehicle(vehicle, want):
    """Return (score, reasons) for a vehicle, or None if it doesn't fit the request."""
    if want["kind"] and vehicle["kind"] != want["kind"]:
        return None
    if vehicle["seats"] < want["seats"]:
        return None
    if want["budget"] and vehicle["price"] > want["budget"]:
        return None

    score = 0
    reasons = [f"{vehicle['seats']} seats"]
    if want["fuel"]:
        if vehicle["fuel"].lower() != want["fuel"]:
            return None
        score += 3
        reasons.append(vehicle["fuel"])
    if want["luxury"] and vehicle["price"] > PREMIUM_PRICE:
        score += 3
        reasons.append("premium ride")
    if want["adventure"] and "Himalayan" in vehicle["name"]:
        score += 4
        reasons.append("built for rough roads")
    if want["cheap"]:
        score += 2 - vehicle["price"] / 6000
    if want["budget"]:
        reasons.append(f"within ₹{want['budget']:,}")
    return score, reasons


def recommend(text, vehicles, limit=3):
    """Return the best matching vehicles for a free-text request."""
    want = parse_request(text)
    matches = []
    for vehicle in vehicles:
        result = score_vehicle(vehicle, want)
        if result:
            matches.append((result[0], vehicle, result[1]))
    matches.sort(key=lambda m: (-m[0], m[1]["price"]))  # best score first, then cheapest
    return [
        {"id": v["id"], "name": v["name"], "kind": v["kind"], "price": v["price"], "why": ", ".join(why)}
        for _score, v, why in matches[:limit]
    ]
