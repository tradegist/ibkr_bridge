FROM python:3.11-slim

WORKDIR /app

COPY services/bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/bridge/main.py ./
COPY services/bridge/bridge_models.py ./
COPY services/bridge/client/ client/
COPY services/bridge/bridge_routes/ bridge_routes/

CMD ["python", "-u", "main.py"]
