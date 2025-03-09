# server.py
import os
from flask import Flask, request, render_template
from db import init_db, add_request

app = Flask(__name__)

# 1) Initialize DB on startup (optional, ensures the tables exist)
init_db()

@app.route("/")
def index():
    """
    Renders the 'test.html' file from the 'templates/' folder.
    """
    return render_template("test.html")

@app.route("/submit_order", methods=["POST"])
def submit_order():
    """
    Receives JSON data from the voice wizard (test.html) and stores it in bot.db
    via add_request(...) from db.py.
    """
    data = request.json  # Should be {type, material, quantity, city, info}
    if not data:
        return "No JSON data received", 400

    # Hardcode user_id=1 or adapt if you have a real user mapping
    user_id = 1
    req_type = data.get("type", "не указан")
    material = data.get("material", "не указан")
    quantity = data.get("quantity", "не указано")
    city = data.get("city", "не указан")
    info = data.get("info", "не указана")

    # Insert into requests table
    add_request(
        user_id=user_id,
        req_type=req_type,
        material=material,
        quantity=quantity,
        city=city,
        info=info
    )

    return "OK"

if __name__ == "__main__":
    # 2) Run the Flask server on port 5000
    app.run(debug=True, port=5000)
