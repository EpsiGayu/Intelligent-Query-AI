
import os
import uuid
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

# New collection because Gemini embeddings have
# a different dimension from the old local model.
collection = chroma_client.get_or_create_collection(
    name="knowledge_base_gemini"
)


# -------------------------------------------------
# GEMINI EMBEDDING FUNCTION
# -------------------------------------------------

def generate_embeddings(texts, task_type):

    all_embeddings = []

    batch_size = 32

    for start in range(0, len(texts), batch_size):

        batch = texts[start:start + batch_size]

        response = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config={
                "task_type": task_type,
                "output_dimensionality": 768
            }
        )

        if not response.embeddings:
            raise RuntimeError("Gemini returned no embeddings.")

        all_embeddings.extend(
            embedding.values
            for embedding in response.embeddings
        )

    return all_embeddings


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

        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()

    elif extension == ".csv":

        dataframe = pd.read_csv(file_path)

        return dataframe.to_string(index=False)

    else:
        raise ValueError("Unsupported file format")


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

    chunks = split_text(text)

    if not chunks:
        return 0

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

    return len(chunks)


# -------------------------------------------------
# RETRIEVE DOCUMENTS
# -------------------------------------------------

def retrieve_documents(query, top_k=3):

    query_embeddings = generate_embeddings(
        [query],
        "RETRIEVAL_QUERY"
    )

    results = collection.query(
        query_embeddings=query_embeddings,
        n_results=min(top_k, collection.count())
    )

    documents = results.get("documents", [[]])[0]
    metadata = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    retrieved = []

    for document, meta, distance in zip(
        documents, metadata, distances
    ):

        retrieved.append({
            "content": document,
            "source": meta.get("source", "Unknown"),
            "distance": distance
        })

    return retrieved
