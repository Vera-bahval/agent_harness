FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY services/gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/common/ /app/services/common/
COPY services/gateway/ /app/services/gateway/
COPY contracts/client-server.v0.schema.json /app/contracts/client-server.v0.schema.json
COPY configs/pairs/ /app/configs/pairs/
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "services.gateway.app"]
