FROM python:3.11-slim
WORKDIR /worker
COPY requirements.txt /worker/requirements.txt
RUN pip install --no-cache-dir -r /worker/requirements.txt || true
COPY . /worker
CMD ["python", "worker.py"]
