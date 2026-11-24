
import os
import uuid
import time
import threading
from collections import OrderedDict

import pandas as pd
import chromadb

from dotenv import load_dotenv
from pypdf import PdfReader
from docx import Document
from google import genai


# -------------------------------------------------
# CONFIGURATION
# -------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is missing.")

client = genai.Client(api_key=GEMINI_API_KEY)

EMBEDDING_MODEL = "gemini-embedding-001"

CHROMA_DIR = os.path.join(BASE_DIR, "chroma_db")

chroma_client = chromadb.PersistentClient(
    path=CHROMA_DIR
)

collection = chroma_client.get_or_create_collection(
    name="knowledge_base_gemini"
)


# -------------------------------------------------
# RATE LIMITING AND CACHE CONFIGURATION
# -------------------------------------------------

BATCH_SIZE = 10

REQUEST_DELAY = 1.2

MAX_RETRIES = 4

embedding_lock = threading.Lock()

last_request_time = 0

# Store frequently used query embeddings
query_cache = OrderedDict()

MAX_CACHE_SIZE = 200

cache_lock = threading.Lock()


# -------------------------------------------------
# SAFE GEMINI EMBEDDING REQUEST
# -------------------------------------------------


def safe_embed_content(**kwargs):

    global last_request_time

    with embedding_lock:

        elapsed = time.time() - last_request_time

        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)

        last_request_time = time.time()

        try:

            response = client.models.embed_content(
                **kwargs
            )

            return response

        except Exception as e:

            error = str(e)

            if (
                "429" in error
                or "RESOURCE_EXHAUSTED" in error
            ):

                print(
                    "Gemini embedding quota exceeded. "
                    "Please retry after the quota resets."
                )

                raise RuntimeError(
                    "Gemini embedding quota exceeded. "
                    "Please wait before uploading or asking another question."
                ) from e

            raise



# -------------------------------------------------
# GEMINI EMBEDDING FUNCTION
# -------------------------------------------------

def generate_embeddings(texts, task_type):

    all_embeddings = []

    if not texts:
        return []

    for start in range(0, len(texts), BATCH_SIZE):

        batch = texts[start:start + BATCH_SIZE]

        response = safe_embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config={
                "task_type": task_type,
                "output_dimensionality": 768
            }
        )

        if not response.embeddings:
            raise RuntimeError(
                "Gemini returned no embeddings."
            )

        if len(response.embeddings) != len(batch):
            raise RuntimeError(
                "Gemini returned an unexpected number of embeddings."
            )

        all_embeddings.extend(
            embedding.values
            for embedding in response.embeddings
        )

    return all_embeddings


# -------------------------------------------------
# QUERY EMBEDDING CACHE
# -------------------------------------------------

def get_query_embedding(query):

    cache_key = query.strip().lower()

    if not cache_key:
        return None

    with cache_lock:

        if cache_key in query_cache:

            query_cache.move_to_end(cache_key)

            print("Using cached query embedding.")

            return query_cache[cache_key]

    embeddings = generate_embeddings(
        [query],
        "RETRIEVAL_QUERY"
    )

    if not embeddings:
        return None

    embedding = embeddings[0]

    with cache_lock:

        query_cache[cache_key] = embedding

        query_cache.move_to_end(cache_key)

        if len(query_cache) > MAX_CACHE_SIZE:
            query_cache.popitem(last=False)

    return embedding


# -------------------------------------------------
# DOCUMENT READER
# -------------------------------------------------

def read_document(file_path):

    extension = os.path.splitext(file_path)[1].lower()

    if extension == ".pdf":

        reader = PdfReader(file_path)

        return "\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )

    elif extension == ".docx":

        document = Document(file_path)

        return "\n".join(
            paragraph.text
            for paragraph in document.paragraphs
        )

    elif extension == ".txt":

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as file:

            return file.read()

    elif extension == ".csv":

        dataframe = pd.read_csv(file_path)

        return dataframe.to_string(index=False)

    else:

        raise ValueError(
            "Unsupported file format"
        )


# -------------------------------------------------
# TEXT CHUNKING
# -------------------------------------------------

def split_text(text, chunk_size=700, overlap=100):

    chunks = []

    start = 0

    while start < len(text):

        end = start + chunk_size

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        start += chunk_size - overlap

    return chunks


# -------------------------------------------------
# ADD DOCUMENT
# -------------------------------------------------

def add_document(file_path):

    text = read_document(file_path)

    if not text or not text.strip():
        return 0

    chunks = split_text(text)

    if not chunks:
        return 0

    print(
        f"Generating embeddings for {len(chunks)} chunks..."
    )

    embeddings = generate_embeddings(
        chunks,
        "RETRIEVAL_DOCUMENT"
    )

    ids = [
        str(uuid.uuid4())
        for _ in chunks
    ]

    metadata = [
        {
            "source": os.path.basename(file_path),
            "chunk": index
        }
        for index in range(len(chunks))
    ]

    collection.add(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadata
    )

    print(
        f"Successfully stored {len(chunks)} chunks."
    )

    return len(chunks)


# -------------------------------------------------
# RETRIEVE DOCUMENTS
# -------------------------------------------------

def retrieve_documents(query, top_k=3):

    if not query or not query.strip():
        return []

    # Check database before generating embeddings
    total_documents = collection.count()

    if total_documents == 0:
        return []

    query_embedding = get_query_embedding(query)

    if query_embedding is None:
        return []

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, total_documents)
    )

    documents = results.get("documents", [[]])[0]

    metadata = results.get("metadatas", [[]])[0]

    distances = results.get("distances", [[]])[0]

    retrieved = []

    for document, meta, distance in zip(
        documents,
        metadata,
        distances
    ):

        retrieved.append({

            "content": document,

            "source": (
                meta.get("source", "Unknown")
                if meta else "Unknown"
            ),

            "distance": distance

        })

    return retrieved
