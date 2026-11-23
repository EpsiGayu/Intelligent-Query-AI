import os
import uuid
import pandas as pd
import chromadb

from pypdf import PdfReader
from docx import Document
from sentence_transformers import SentenceTransformer

CHROMA_DIR = "chroma_db"

client = chromadb.PersistentClient(path=CHROMA_DIR)

collection = client.get_or_create_collection(
    name="knowledge_base"
)

embedding_model = SentenceTransformer(
    "./models/all-MiniLM-L6-v2"
)


def read_document(file_path):

    extension = os.path.splitext(file_path)[1].lower()

    if extension == ".pdf":

        reader = PdfReader(file_path)

        text = "\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )

        return text

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


def add_document(file_path):

    text = read_document(file_path)

    chunks = split_text(text)

    embeddings = embedding_model.encode(
        chunks
    ).tolist()

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


def retrieve_documents(query, top_k=3):

    query_embedding = embedding_model.encode(
        [query],
         show_progress_bar=False,
    convert_to_numpy=True
    ).tolist()

    results = collection.query(
        query_embeddings=query_embedding,
        n_results=top_k
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