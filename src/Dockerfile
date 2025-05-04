FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8080

CMD uvicorn --app-dir web fast_api_runner:api_app --port 8000 & python3 web/main.py agents/data_agent & wait
