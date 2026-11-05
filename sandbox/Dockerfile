FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
COPY sandbox/tools.py /opt/agent_tools.py
COPY sandbox/idle.py /opt/idle.py
COPY sandbox/seed.py /opt/seed.py
COPY sandbox/collect.py /opt/collect.py
USER 10001:10001
WORKDIR /work
CMD ["python", "-I", "/opt/idle.py"]
