FROM python:3.11-slim
WORKDIR /app
COPY index.html /app/index.html
CMD ["python", "-m", "http.server", "3000", "--bind", "0.0.0.0"]
