# test-api.py
import logging
import requests
import json

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BEARER_TOKEN = "jD970oBHeT55QjeyDI9lJEIVlRbI8wFfU9LezwEB2"  # Replace with your actual Bearer token
BASE_URL = "http://localhost:8080"       # Adjust if needed

# Our test payment hash (preloaded in index.py)
TEST_HASH = "TEST123"

def test_verify_payment_link():
    logging.info("=== Automatically testing /team/bot-payment-test with TEST_HASH ===")
    url = f"{BASE_URL}/team/bot-payment-test?id={TEST_HASH}"
    headers = {"Authorization": f"Bearer {BEARER_TOKEN}"}
    logging.info("Sending GET request to %s", url)
    try:
        resp = requests.get(url, headers=headers)
        logging.info("Status Code: %s", resp.status_code)
        logging.info("Response Headers: %s", resp.headers)
        try:
            data = resp.json()
            logging.info("Response JSON: %s", data)
        except json.JSONDecodeError:
            logging.info("Response Text: %s", resp.text)
    except Exception as e:
        logging.error("Exception in test_verify_payment_link: %s", e)

def test_payment_notification():
    logging.info("=== Automatically testing /team/payment-notification with TEST_HASH ===")
    url = f"{BASE_URL}/team/payment-notification"
    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"id": TEST_HASH}
    logging.info("Sending POST request to %s", url)
    logging.info("Payload: %s", payload)
    try:
        resp = requests.post(url, headers=headers, json=payload)
        logging.info("Status Code: %s", resp.status_code)
        logging.info("Response Headers: %s", resp.headers)
        try:
            data = resp.json()
            logging.info("Response JSON: %s", data)
        except json.JSONDecodeError:
            logging.info("Response Text: %s", resp.text)
    except Exception as e:
        logging.error("Exception in test_payment_notification: %s", e)

if __name__ == "__main__":
    logging.info("Starting automatic payment API tests using TEST_HASH=%s", TEST_HASH)
    test_verify_payment_link()
    test_payment_notification()
