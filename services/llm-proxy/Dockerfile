FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY services/llm-proxy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/common/ /app/services/common/
COPY services/llm-proxy/app.py /app/app.py
COPY services/llm-proxy/runtime/ /app/runtime/
COPY configs/pairs/ /app/configs/pairs/
COPY tests/fixtures/llm/ /app/replays/
USER 10001:10001
EXPOSE 8000
CMD ["python", "app.py"]
