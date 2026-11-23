
import os
import re
import time
from dotenv import load_dotenv
from google import genai
from rag import retrieve_documents
import random

# =====================================================
# GEMINI CONFIGURATION
# =====================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY not found. Please check your .env file."
    )

client = genai.Client(api_key=GEMINI_API_KEY)


# =====================================================
# GEMINI RESPONSE FUNCTION
# Only the response agent normally calls this function.
# =====================================================

def ask_gemini(prompt, retries=1):

    for attempt in range(retries + 1):
        try:
            start_time = time.time()

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={
                    "temperature": 0.2,
                    "max_output_tokens": 512
                }
            )

            answer = response.text

            if answer and answer.strip():
                print(
                    "Gemini response time:",
                    round(time.time() - start_time, 2),
                    "seconds"
                )
                return answer.strip()

            raise ValueError("Gemini returned an empty response.")

        except Exception as error:

            error_message = str(error).lower()

            print(
                f"Gemini attempt {attempt + 1} failed: {error}"
            )

            # Quota errors need quota/billing attention.
            if "429" in error_message or "resource_exhausted" in error_message:
                raise RuntimeError(
                    "Gemini API quota or rate limit exceeded. "
                    "Check your API usage and retry later."
                ) from error

            # Retry only temporary server/network errors.
            temporary_error = any(
                word in error_message
                for word in [
                    "503", "500", "502", "504",
                    "unavailable", "overloaded",
                    "timeout", "temporarily"
                ]
            )

            if temporary_error and attempt < retries:

                wait_time = (2 ** (attempt + 1)) + random.uniform(0, 1)

                print(
                    f"Gemini temporarily unavailable. "
                    f"Retrying in {wait_time:.1f} seconds..."
                )

                time.sleep(wait_time)
                continue

            raise

    raise RuntimeError("Gemini request failed after retries.")

# =====================================================
# PROGRESS CALLBACK
# Optional: app.py can pass a callback to receive updates.
# =====================================================

def update_progress(status, agent, state, callback=None):

    status[agent] = state

    if callback:
        try:
            callback(status.copy(), agent, state)
        except Exception as error:
            print("Progress callback error:", error)


# =====================================================
# AGENT 1: QUERY UNDERSTANDING AGENT
# Local processing - no Gemini API call.
# =====================================================

def query_understanding_agent(query, history=None):

    query = (query or "").strip()

    words = re.findall(r"\b[\w'-]+\b", query.lower())

    pronouns = {
        "it", "this", "that", "they", "them",
        "he", "she", "those", "these"
    }

    needs_clarification = (
        len(words) == 1
        and words[0] in pronouns
        and not history
    )

    return {
        "intent": "document_question",
        "topic": words[0] if words else "general",
        "keywords": words,
        "needs_clarification": needs_clarification,
        "clarification_question": (
            "Could you clarify what you are referring to?"
            if needs_clarification else ""
        )
    }


# =====================================================
# AGENT 2: RETRIEVAL AGENT
# Searches ChromaDB through rag.py.
# =====================================================

def retrieval_agent(query):

    documents = retrieve_documents(query)

    return documents or []


# =====================================================
# AGENT 3: RESPONSE GENERATION AGENT
# The main Gemini API call.
# =====================================================


def response_generation_agent(query, documents, history=None):

    context_parts = []

    for doc in documents[:3]:

        source = doc.get("source", "Unknown document")
        content = doc.get("content", "")

        if content:
            # Limit each retrieved document.
            content = content[:1500]

            context_parts.append(
                f"Source: {source}\n{content}"
            )

    context = "\n\n".join(context_parts)

    # Keep conversation history smaller.
    history_text = str(history or "")[-1000:]

    prompt = f"""
You are an intelligent document query assistant.

Answer the user's question using the provided documents.

Rules:
- Be accurate and concise.
- Do not invent facts.
- Cite sources using [Source: filename].
- If information is unavailable, say so.
- Use previous conversation only when relevant.

DOCUMENT CONTEXT:
{context or "No relevant documents found."}

CONVERSATION HISTORY:
{history_text or "No previous conversation."}

QUESTION:
{query}

Provide a direct answer:
"""

    return ask_gemini(prompt)



# =====================================================
# AGENT 4: CLARIFICATION AGENT
# Local processing - no Gemini API call.
# =====================================================

def clarification_agent(query, analysis):

    if not analysis.get("needs_clarification"):
        return None

    return (
        analysis.get("clarification_question")
        or "Could you please provide a little more detail?"
    )


# =====================================================
# AGENT 5: CONVERSATION MEMORY AGENT
# Local memory update - no Gemini API call.
# =====================================================


def conversation_memory_agent(history, query, answer):

    previous_memory = str(history or "").strip()

    new_memory = f"""
{previous_memory}

User: {query}
Assistant: {answer}
""".strip()

    # Keep only recent conversation.
    return new_memory[-2500:]



# =====================================================
# MAIN MULTI-AGENT PIPELINE
# Compatible with existing app.py:
# run_agents(query, history)
# =====================================================

def run_agents(query, history=None, progress_callback=None):

    query = (query or "").strip()

    if not query:
        raise ValueError("Please enter a question.")

    agent_status = {
        "query_understanding": "waiting",
        "retrieval": "waiting",
        "response_generation": "waiting",
        "clarification": "waiting",
        "conversation_memory": "waiting"
    }

    # -------------------------------------------------
    # AGENT 1: QUERY UNDERSTANDING
    # -------------------------------------------------

    update_progress(
        agent_status,
        "query_understanding",
        "running",
        progress_callback
    )

    analysis = query_understanding_agent(query, history)

    update_progress(
        agent_status,
        "query_understanding",
        "completed",
        progress_callback
    )

    # -------------------------------------------------
    # AGENT 4: CLARIFICATION
    # -------------------------------------------------

    update_progress(
        agent_status,
        "clarification",
        "running",
        progress_callback
    )

    clarification = clarification_agent(query, analysis)

    update_progress(
        agent_status,
        "clarification",
        "completed",
        progress_callback
    )

    if clarification:

        return {
            "answer": clarification,
            "sources": [],
            "agents": agent_status,
            "memory": history or ""
        }

    # -------------------------------------------------
    # AGENT 2: DOCUMENT RETRIEVAL
    # -------------------------------------------------

    update_progress(
        agent_status,
        "retrieval",
        "running",
        progress_callback
    )

    documents = retrieval_agent(query)

    update_progress(
        agent_status,
        "retrieval",
        "completed",
        progress_callback
    )

    # -------------------------------------------------
    # AGENT 3: GEMINI RESPONSE GENERATION
    # -------------------------------------------------

    update_progress(
        agent_status,
        "response_generation",
        "running",
        progress_callback
    )

    answer = response_generation_agent(
        query,
        documents,
        history
    )

    update_progress(
        agent_status,
        "response_generation",
        "completed",
        progress_callback
    )

    # -------------------------------------------------
    # AGENT 5: CONVERSATION MEMORY
    # -------------------------------------------------

    update_progress(
        agent_status,
        "conversation_memory",
        "running",
        progress_callback
    )

    memory = conversation_memory_agent(
        history,
        query,
        answer
    )

    update_progress(
        agent_status,
        "conversation_memory",
        "completed",
        progress_callback
    )

    # -------------------------------------------------
    # COLLECT DOCUMENT SOURCES
    # -------------------------------------------------

    sources = list(dict.fromkeys(
        doc.get("source", "Unknown document")
        for doc in documents
    ))

    return {
        "answer": answer,
        "sources": sources,
        "agents": agent_status,
        "memory": memory
    }
