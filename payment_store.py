# payment_store.py
import secrets

def generate_unique_hash():
    return secrets.token_urlsafe(16)

valid_payment_hashes = {}
payment_links = {}
