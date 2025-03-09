# server.py
import os
from flask import Flask, request, render_template, jsonify
from db import init_db, add_request
import ollama

app = Flask(__name__)

# Initialize the database on startup
init_db()

def remove_think_tags(text: str) -> str:
    """
    Removes <think>...</think> sections from the response,
    so the user doesn't see the model's internal reasoning.
    """
    import re
    # Regex to remove any <think>...</think> block (including multiline).
    # We use a non-greedy match (.*?) inside <think> and </think>.
    pattern = r"<think>.*?</think>"
    cleaned = re.sub(pattern, "", text, flags=re.DOTALL)
    return cleaned.strip()

@app.route("/")
def index():
    """
    Renders the 'test.html' from templates folder.
    """
    return render_template("test.html")

@app.route("/ask_deepseek", methods=["POST"])
def ask_deepseek():
    """
    Expects JSON { "user_input": "some text" }
    Returns JSON { "ai_reply": "DeepSeek's final text" }
    We'll remove <think> tags from the response.
    """
    data = request.json
    if not data or "user_input" not in data:
        return jsonify({"error": "No user_input provided"}), 400

    user_text = data["user_input"]
    try:
        # Call the local DeepSeek R1 model via Ollama
        result = ollama.generate(model="deepseek-r1:7b", prompt=user_text)
        raw_response = result["response"]
        cleaned_response = remove_think_tags(raw_response)
        return jsonify({"ai_reply": cleaned_response})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/submit_order", methods=["POST"])
def submit_order():
    """
    Receives JSON data from test.html wizard, inserts into bot.db.
    Data => {type, material, quantity, city, info}
    """
    data = request.json
    if not data:
        return "No JSON data received", 400

    user_id = 1  # or adapt if you have real user mapping
    req_type = data.get("type", "не указан")
    material = data.get("material", "не указан")
    quantity = data.get("quantity", "не указано")
    city = data.get("city", "не указан")
    info = data.get("info", "не указана")

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
    app.run(debug=True, port=5000)
