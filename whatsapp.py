
import os
import hmac
import hashlib
import requests

from flask import Blueprint, request, jsonify
from dotenv import load_dotenv

from agents import run_agents

load_dotenv()

whatsapp_bp = Blueprint("whatsapp", __name__)

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v23.0")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET")



def verify_signature():
    """Verify that the webhook request came from Meta."""

    if not APP_SECRET:
        print("Signature error: APP_SECRET is missing")
        return False

    signature = request.headers.get("X-Hub-Signature-256", "")

    if not signature:
        print("Signature error: X-Hub-Signature-256 header is missing")
        return False

    if not signature.startswith("sha256="):
        print("Signature error: Invalid signature format")
        return False

    expected_signature = hmac.new(
        APP_SECRET.encode("utf-8"),
        request.get_data(),
        hashlib.sha256
    ).hexdigest()

    received_signature = signature.split("=", 1)[1]

    if not hmac.compare_digest(received_signature, expected_signature):
        print("Signature error: Signature does not match App Secret")
        return False

    print("WhatsApp signature verified successfully!")
    return True



def send_whatsapp_message(to, message):

    url = (
        f"https://graph.facebook.com/"
        f"{API_VERSION}/{PHONE_NUMBER_ID}/messages"
    )

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message[:4000]
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=20
    )

    if not response.ok:
        print("WhatsApp send error:", response.status_code, response.text)

    response.raise_for_status()

    return response.json()


@whatsapp_bp.route("/webhook", methods=["GET"])
def verify_webhook():

    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("WhatsApp webhook verified!")
        return challenge, 200

    return "Verification failed", 403


@whatsapp_bp.route("/webhook", methods=["POST"])
def receive_whatsapp_message():

    if not verify_signature():
        return "Invalid signature", 401

    data = request.get_json(silent=True) or {}

    try:
        entries = data.get("entry", [])

        for entry in entries:
            for change in entry.get("changes", []):

                value = change.get("value", {})

                for message in value.get("messages", []):

                    sender = message.get("from")
                    

                    message_type = message.get("type")

                    # First version supports text messages.
                    if message_type != "text":
                        if sender:
                            send_whatsapp_message(
                                sender,
                                "Currently, I support text questions. "
                                "Document uploads will be enabled next."
                            )
                        continue

                    user_message = message.get(
                        "text", {}
                    ).get("body", "").strip()

                    if not sender or not user_message:
                        continue

                    print("WhatsApp message:", user_message)

                    try:
                        # Reuse your existing AI agents.
                        result = run_agents(
                            user_message,
                            history=[]
                        )

                        answer = result.get(
                            "answer",
                            "Sorry, I could not generate an answer."
                        )

                        send_whatsapp_message(sender, answer)

                    except Exception as error:
                        print("AI processing error:", error)

                        send_whatsapp_message(
                            sender,
                            "Sorry, something went wrong while "
                            "processing your question. Please try again."
                        )

        return jsonify({"status": "received"}), 200

    except Exception as error:
        print("Webhook error:", error)
        return jsonify({"status": "error"}), 500
