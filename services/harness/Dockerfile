FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY services/harness/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/common/ /app/services/common/
COPY services/harness/app.py /app/app.py
COPY services/harness/runtime/ /app/runtime/
COPY configs/pairs/ /app/configs/pairs/
COPY contracts/harness-sandbox.v0.schema.json /app/contracts/harness-sandbox.v0.schema.json
RUN mkdir -p /app/results && chown 10001:10001 /app/results
USER 10001:10001
EXPOSE 8000
CMD ["python", "app.py"]
