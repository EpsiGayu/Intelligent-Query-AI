const agentMap = {
    query_understanding: "agent-query",
    retrieval: "agent-retrieval",
    response_generation: "agent-response",
    clarification: "agent-clarification",
    conversation_memory: "agent-memory"
};


function updateAgentStatus(statuses) {

    Object.entries(statuses).forEach(([agent, status]) => {

        const element = document.getElementById(
            agentMap[agent]
        );

        if (!element) return;

        element.classList.remove(
            "running",
            "completed"
        );

        const statusElement = element.querySelector(
            ".agent-status"
        );

        if (status === "running") {

            element.classList.add("running");

        } else if (status === "completed") {

            element.classList.add("completed");

        }

        statusElement.textContent = status;
    });
}


function resetAgentStatus() {

    Object.values(agentMap).forEach(id => {

        const element = document.getElementById(id);

        if (!element) return;

        element.classList.remove(
            "running",
            "completed"
        );

        element.querySelector(
            ".agent-status"
        ).textContent = "Idle";

    });
}


function addMessage(text, sender) {

    const container = document.getElementById(
        "chatMessages"
    );

    const message = document.createElement("div");

    message.className = "message " + sender;

    message.textContent = text;

    container.appendChild(message);

    container.scrollTop = container.scrollHeight;
}


async function sendMessage() {

    const input = document.getElementById(
        "userMessage"
    );

    const message = input.value.trim();

    if (!message) return;

    addMessage(message, "user");

    input.value = "";

    resetAgentStatus();

    try {

        const response = await fetch("/chat", {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                message: message
            })

        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Request failed");
        }

        updateAgentStatus(data.agents);

        addMessage(data.answer, "bot");

        if (data.sources && data.sources.length > 0) {

            addMessage(
                "Sources: " + data.sources.join(", "),
                "bot"
            );

        }

        const analytics = await fetch("/analytics");

        const analyticsData = await analytics.json();

        document.getElementById(
            "totalQueries"
        ).textContent = analyticsData.total_queries;

    } catch (error) {

        addMessage(
            "Error: " + error.message,
            "bot"
        );

    }
}


// Send message with Enter key

document.addEventListener("DOMContentLoaded", () => {

    const input = document.getElementById("userMessage");

    if (input) {

        input.addEventListener("keydown", event => {

            if (event.key === "Enter") {
                sendMessage();
            }

        });

    }

});


// DOCUMENT UPLOAD

document.addEventListener("DOMContentLoaded", () => {

    const form = document.getElementById("uploadForm");

    if (!form) return;

    form.addEventListener("submit", async event => {

        event.preventDefault();

        const fileInput = document.getElementById(
            "documentFile"
        );

        const status = document.getElementById(
            "uploadStatus"
        );

        if (!fileInput.files.length) return;

        const formData = new FormData();

        formData.append(
            "file",
            fileInput.files[0]
        );

        status.textContent = "Uploading document...";

        try {

            const response = await fetch("/upload", {
                method: "POST",
                body: formData
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || "Upload failed");
            }

            status.textContent =
                data.message + " (" + data.chunks + " chunks)";

            fileInput.value = "";

        } catch (error) {

            status.textContent = error.message;

        }

    });

});


// SPEECH RECOGNITION

function startVoiceRecognition() {

    const SpeechRecognition =
        window.SpeechRecognition ||
        window.webkitSpeechRecognition;

    const status = document.getElementById(
        "voiceStatus"
    );

    if (!SpeechRecognition) {

        status.textContent =
            "Speech recognition is not supported in this browser.";

        return;
    }

    const recognition = new SpeechRecognition();

    recognition.lang = "en-IN";

    recognition.interimResults = false;

    recognition.maxAlternatives = 1;

    status.textContent = "Listening...";

    recognition.start();

    recognition.onresult = event => {

        const transcript =
            event.results[0][0].transcript;

        document.getElementById(
            "userMessage"
        ).value = transcript;

        status.textContent = "Voice captured.";

        sendMessage();
    };

    recognition.onerror = event => {

        status.textContent =
            "Speech recognition error: " + event.error;

    };

    recognition.onend = () => {

        if (status.textContent === "Listening...") {
            status.textContent = "Voice input ended.";
        }

    };

}


// TEXT TO SPEECH

function speakText(text) {

    if (!("speechSynthesis" in window)) {
        return;
    }

    const speech = new SpeechSynthesisUtterance(text);

    speech.lang = "en-IN";

    window.speechSynthesis.speak(speech);
}
