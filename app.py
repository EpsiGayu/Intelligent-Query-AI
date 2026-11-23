
import os
import sqlite3

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

# Load environment variables before importing other modules
load_dotenv()

# Import project modules
from agents import run_agents
from rag import add_document
from whatsapp import whatsapp_bp


# ==========================================
# FLASK APPLICATION CONFIGURATION
# ==========================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "development-secret"
)

# Register WhatsApp blueprint exactly once
app.register_blueprint(whatsapp_bp)


# ==========================================
# APPLICATION SETTINGS
# ==========================================

UPLOAD_FOLDER = "uploads"

ALLOWED_EXTENSIONS = {
    "pdf",
    "docx",
    "txt",
    "csv"
}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ==========================================
# DATABASE
# ==========================================

def get_db():

    connection = sqlite3.connect("database.db")

    connection.row_factory = sqlite3.Row

    return connection


def initialize_database():

    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )
    """)

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

    db.commit()
    db.close()


initialize_database()


# ==========================================
# AUTHENTICATION
# ==========================================

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return wrapper


# ==========================================
# HOME PAGE
# ==========================================

@app.route("/")
def index():

    if "user_id" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


# ==========================================
# USER REGISTRATION
# ==========================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form.get("username")
        password = request.form.get("password")

        if not username or not password:

            return render_template(
                "register.html",
                error="Please fill in all fields."
            )

        hashed_password = generate_password_hash(
            password
        )

        db = get_db()

        try:

            db.execute(
                """
                INSERT INTO users(username, password)
                VALUES (?, ?)
                """,
                (username, hashed_password)
            )

            db.commit()

        except sqlite3.IntegrityError:

            db.close()

            return render_template(
                "register.html",
                error="Username already exists."
            )

        db.close()

        return redirect(url_for("login"))

    return render_template("register.html")


# ==========================================
# USER LOGIN
# ==========================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username")
        password = request.form.get("password")

        db = get_db()

        user = db.execute(
            """
            SELECT * FROM users
            WHERE username = ?
            """,
            (username,)
        ).fetchone()

        db.close()

        if user and check_password_hash(
            user["password"],
            password
        ):

            session["user_id"] = user["id"]
            session["username"] = user["username"]

            return redirect(url_for("dashboard"))

        return render_template(
            "login.html",
            error="Invalid username or password."
        )

    return render_template("login.html")


# ==========================================
# USER LOGOUT
# ==========================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


# ==========================================
# DASHBOARD
# ==========================================

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


# ==========================================
# DOCUMENT UPLOAD
# ==========================================

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


# ==========================================
# AI CHAT API
# ==========================================

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

    try:

        # Retrieve user's previous memory
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

        # Process query using AI agents
        result = run_agents(
            query,
            history
        )

        answer = result["answer"]
        sources = result["sources"]
        memory = result["memory"]

        # Save conversation
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

        # Update user memory
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

        return jsonify({
            "answer": answer,
            "sources": sources,
            "agents": result["agents"]
        })

    except Exception as error:

        db.rollback()

        print("Chat API Error:", error)

        return jsonify({
            "error": str(error)
        }), 500

    finally:

        db.close()


# ==========================================
# ANALYTICS API
# ==========================================

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


# ==========================================
# APPLICATION START
# ==========================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
