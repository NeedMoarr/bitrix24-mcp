FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "mcp>=1.2.0" "httpx>=0.27"
COPY server.py .
ENV MCP_TRANSPORT=http PORT=8080
CMD ["python", "server.py"]
