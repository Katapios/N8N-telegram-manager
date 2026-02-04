# AI Agent on n8n + Ollama

This project provides a complete, self-hosted AI agent that runs on a low-resource machine.

## Architecture
- **Orchestration**: n8n
- **LLM**: Ollama
- **Memory (RAG)**: ChromaDB
- **Web Search**: Tavily
- **Interface**: Telegram
- **Networking**: Traefik for SSL

## Prerequisites
- Docker & Docker Compose
- A domain name
- An external IP address
- Ports 80 and 443 forwarded from your router to the machine running Docker.

## Step-by-Step Launch

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd N8N-telegram-manager
    ```

2.  **Configure Environment:**
    Copy the example `.env` file and fill in your actual data.
    ```bash
    cp .env.example .env
    ```
    - `N8N_HOST`: Your domain for n8n (e.g., `n8n.example.com`).
    - `N8N_ENCRYPTION_KEY`: A long, random string to secure credentials.
    - `TELEGRAM_TOKEN`: Your Telegram bot token from BotFather.
    - `TAVILY_API_KEY`: Your API key from Tavily.
    - `ACME_EMAIL`: Your email for Let's Encrypt notifications.

3.  **DNS Setup:**
    Create an `A` record in your DNS provider settings pointing your `N8N_HOST` domain to your external IP address.

4.  **Run Docker Compose:**
    ```bash
    docker-compose up -d
    ```
    This will start all services. Ollama will start downloading the LLM model, which may take some time.

5.  **Configure n8n:**
    - Open `https://<your-n8n-domain>` in your browser and set up an owner account.
    - Go to **Workflows** and import `n8n/workflows/ai-agent.json`.
    - Go to **Credentials** and add credentials for:
      - **Telegram**: Use your bot token.
      - **Ollama**:
        - Base URL: `http://ollama:11434`
      - **Chroma**:
        - Base URL: `http://chroma:8000`
      - **Tavily**:
        - API Key: Your Tavily API Key.
    - Open the imported workflow, connect the correct credentials to the nodes, and **Activate** the workflow.

6.  **Talk to your Bot:**
    You can now interact with your AI agent via Telegram.

## Debugging
- Check logs for a specific service:
  ```bash
  docker-compose logs -f n8n
  docker-compose logs -f ollama
  ```
- The n8n UI provides detailed information about each workflow execution under **Executions**.

## Common Issues
- **502 Bad Gateway**: n8n is likely still starting up or has crashed. Check `docker-compose logs -f n8n`.
- **SSL Certificate not working**: Ensure ports 80/443 are correctly forwarded and your DNS `A` record has propagated.
- **Ollama errors in n8n**: The `ollama` service might be down or still pulling the model. Check its logs.
