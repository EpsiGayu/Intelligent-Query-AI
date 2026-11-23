import os
import sqlite3
import requests
import hmac
import hashlib
import re

from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from werkzeug.utils import secure_filename

from dotenv import load_dotenv

from agents import run_agents
from rag import add_document


load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "development-secret"
)

UPLOAD_FOLDER = "uploads"

ALLOWED_EXTENSIONS = {
    "pdf",
    "docx",
    "txt",
    "csv"
}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# DATABASE

def get_db():

    connection = sqlite3.connect(
        "database.db"
    )

    connection.row_factory = sqlite3.Row

    return connection


def initialize_database():

    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            full_name TEXT,
            email TEXT
        )
    """)

    # Safe migration for existing database.db files. Existing user rows
    # and their conversation/memory IDs are preserved.
    user_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()
    }
    if "full_name" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
    if "email" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN email TEXT")

    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")

    db.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            question TEXT,
            answer TEXT,
            sources TEXT,
            created_at TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            user_id INTEGER PRIMARY KEY,
            memory TEXT
        )
    """)

    # WhatsApp users have independent memory, keyed by their phone number.
    db.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_memories (
            phone_number TEXT PRIMARY KEY,
            memory TEXT NOT NULL DEFAULT ''
        )
    """)

    # Prevent Meta webhook retries from processing the same message twice.
    db.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_processed_messages (
            message_id TEXT PRIMARY KEY,
            phone_number TEXT,
            processed_at TEXT
        )
    """)

    db.commit()
    db.close()


initialize_database()


# AUTHENTICATION

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return wrapper


@app.route("/")
def index():

    if "user_id" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not all([full_name, email, password, confirm_password]):
            return render_template(
                "register.html",
                error="Please fill in all fields.",
                full_name=full_name,
                email=email
            )

        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            return render_template(
                "register.html",
                error="Please enter a valid email address.",
                full_name=full_name,
                email=email
            )

        if password != confirm_password:
            return render_template(
                "register.html",
                error="Passwords do not match.",
                full_name=full_name,
                email=email
            )

        hashed_password = generate_password_hash(password)
        db = get_db()

        try:
            # username is retained for compatibility with existing parts
            # of the project; the email is now the sign-in identifier.
            db.execute(
                """
                INSERT INTO users(username, password, full_name, email)
                VALUES (?, ?, ?, ?)
                """,
                (email, hashed_password, full_name, email)
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.close()
            return render_template(
                "register.html",
                error="An account with this email address already exists.",
                full_name=full_name,
                email=email
            )

        db.close()
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()
        db.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["full_name"] or user["username"] or email
            session["email"] = user["email"] or email
            return redirect(url_for("dashboard"))

        return render_template(
            "login.html",
            error="Invalid email address or password.",
            email=email
        )

    return render_template("login.html")

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


# DASHBOARD

@app.route("/dashboard")
@login_required
def dashboard():

    db = get_db()

    conversations = db.execute(
        """
        SELECT * FROM conversations
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (session["user_id"],)
    ).fetchall()

    total = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM conversations
        WHERE user_id = ?
        """,
        (session["user_id"],)
    ).fetchone()["count"]

    db.close()

    return render_template(
        "dashboard.html",
        conversations=conversations,
        total=total,
        username=session["username"]
    )


# DOCUMENT UPLOAD

def allowed_file(filename):

    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


@app.route("/upload", methods=["POST"])
@login_required
def upload_document():

    if "file" not in request.files:

        return jsonify({
            "error": "No file provided"
        }), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({
            "error": "Please select a file"
        }), 400

    if not allowed_file(file.filename):

        return jsonify({
            "error": "Unsupported file format"
        }), 400

    filename = secure_filename(file.filename)

    unique_filename = (
        str(session["user_id"])
        + "_"
        + filename
    )

    file_path = os.path.join(
        UPLOAD_FOLDER,
        unique_filename
    )

    file.save(file_path)

    try:

        chunks = add_document(file_path)

        return jsonify({
            "message": "Document uploaded successfully",
            "filename": filename,
            "chunks": chunks
        })

    except Exception as error:

        return jsonify({
            "error": str(error)
        }), 500


# CHAT API

@app.route("/chat", methods=["POST"])
@login_required
def chat():

    data = request.get_json() or {}

    query = data.get("message", "").strip()

    if not query:

        return jsonify({
            "error": "Message cannot be empty"
        }), 400

    user_id = session["user_id"]

    db = get_db()

    memory_record = db.execute(
        """
        SELECT memory FROM memories
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    history = (
        memory_record["memory"]
        if memory_record
        else ""
    )

    try:

        result = run_agents(
            query,
            history
        )

        answer = result["answer"]
        sources = result["sources"]
        memory = result["memory"]

        db.execute(
            """
            INSERT INTO conversations
            (user_id, question, answer, sources, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                query,
                answer,
                ", ".join(sources),
                datetime.now().isoformat()
            )
        )

        db.execute(
            """
            INSERT INTO memories(user_id, memory)
            VALUES (?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET memory = excluded.memory
            """,
            (user_id, memory)
        )

        db.commit()
        db.close()

        return jsonify({
            "answer": answer,
            "sources": sources,
            "agents": result["agents"]
        })

    except Exception as error:

        db.close()

        return jsonify({
            "error": str(error)
        }), 500


# ANALYTICS API

@app.route("/analytics")
@login_required
def analytics():

    db = get_db()

    total = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM conversations
        WHERE user_id = ?
        """,
        (session["user_id"],)
    ).fetchone()["count"]

    db.close()

    return jsonify({
        "total_queries": total,
        "active_agents": 5
    })


# WHATSAPP CLOUD API INTEGRATION

def verify_whatsapp_signature():
    """
    Validate that the webhook POST was sent by Meta.
    Set WHATSAPP_APP_SECRET in .env to enable validation.
    """
    app_secret = os.getenv("WHATSAPP_APP_SECRET", "").strip()

    # During initial local setup, signature validation can be disabled.
    # Configure the Meta App Secret before production deployment.
    if not app_secret:
        return True

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature.startswith("sha256="):
        return False

    expected = hmac.new(
        app_secret.encode("utf-8"),
        request.get_data(cache=True),
        hashlib.sha256
    ).hexdigest()

    supplied = signature.split("=", 1)[1]
    return hmac.compare_digest(expected, supplied)


def send_whatsapp_message(recipient, message):
    """Send a text reply through the WhatsApp Cloud API."""
    access_token = os.getenv("WHATSAPP_TOKEN", "").strip()
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    api_version = os.getenv("WHATSAPP_API_VERSION", "v23.0").strip()

    if not access_token or not phone_number_id:
        raise RuntimeError(
            "Set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID in your .env file."
        )

    url = (
        f"https://graph.facebook.com/{api_version}/"
        f"{phone_number_id}/messages"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # WhatsApp text messages have a 4096-character body limit.
    # Send in smaller chunks to avoid exceeding that limit.
    message = str(message or "").strip() or "Sorry, I could not generate an answer."
    chunks = [
        message[index:index + 4000]
        for index in range(0, len(message), 4000)
    ]

    results = []
    for chunk in chunks:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": chunk
            }
        }

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=20
        )
        response.raise_for_status()
        results.append(response.json())

    return results


@app.route("/webhook", methods=["GET"])
def verify_whatsapp():
    """Meta calls this endpoint when verifying the webhook URL."""
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()

    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if (
        mode == "subscribe"
        and verify_token
        and token == verify_token
        and challenge
    ):
        return challenge, 200

    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def whatsapp_webhook():
    """Receive WhatsApp messages, run the existing agents, and reply."""
    if not verify_whatsapp_signature():
        return jsonify({"status": "invalid signature"}), 403

    data = request.get_json(silent=True) or {}

    # Meta also sends webhook events for delivery/read status updates.
    # Those events do not contain messages, so simply acknowledge them.
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {}) or {}

                for message in value.get("messages", []):
                    sender = message.get("from")
                    message_id = message.get("id")
                    message_type = message.get("type")

                    if not sender or not message_id:
                        continue

                    if message_type != "text":
                        send_whatsapp_message(
                            sender,
                            "At the moment, I can answer text messages. "
                            "Please send your question as text."
                        )
                        continue

                    user_query = (
                        message.get("text", {}).get("body", "") or ""
                    ).strip()

                    if not user_query:
                        continue

                    db = get_db()
                    try:
                        # Ignore duplicate webhook deliveries from Meta.
                        cursor = db.execute(
                            """
                            INSERT OR IGNORE INTO whatsapp_processed_messages
                            (message_id, phone_number, processed_at)
                            VALUES (?, ?, ?)
                            """,
                            (
                                message_id,
                                sender,
                                datetime.now().isoformat()
                            )
                        )
                        db.commit()

                        if cursor.rowcount == 0:
                            continue

                        memory_record = db.execute(
                            """
                            SELECT memory
                            FROM whatsapp_memories
                            WHERE phone_number = ?
                            """,
                            (sender,)
                        ).fetchone()

                        history = (
                            memory_record["memory"]
                            if memory_record
                            else ""
                        )

                        # Reuse the same five-agent Gemini + RAG pipeline
                        # that powers the website chat endpoint.
                        result = run_agents(user_query, history)

                        answer = result.get(
                            "answer",
                            "Sorry, I could not generate an answer."
                        )
                        sources = result.get("sources", [])
                        memory = result.get("memory", history)

                        if sources:
                            answer += "\n\nSources: " + ", ".join(sources)

                        db.execute(
                            """
                            INSERT INTO whatsapp_memories(phone_number, memory)
                            VALUES (?, ?)
                            ON CONFLICT(phone_number)
                            DO UPDATE SET memory = excluded.memory
                            """,
                            (sender, memory)
                        )
                        db.commit()

                    finally:
                        db.close()

                    send_whatsapp_message(sender, answer)

                    print(
                        "WhatsApp question processed successfully for:",
                        sender
                    )

    except requests.RequestException as error:
        # Log the provider response without exposing access tokens.
        print("WhatsApp API request failed:", error)
    except Exception as error:
        print("WhatsApp message processing failed:", error)

    # Acknowledge the webhook. Meta should not repeatedly redeliver
    # a message while the application is reporting an internal error.
    return jsonify({"status": "received"}), 200


if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
