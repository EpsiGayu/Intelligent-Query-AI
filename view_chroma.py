
import chromadb

client = chromadb.PersistentClient(path="./chroma_db")

collection = client.get_or_create_collection(
    name="knowledge_base"
)

print("Total document chunks:", collection.count())

data = collection.get(
    limit=10,
    include=["documents", "metadatas"]
)

for i, doc in enumerate(data["documents"]):
    print(f"\nChunk {i + 1}:")
    print(doc)
    print("Metadata:", data["metadatas"][i])
