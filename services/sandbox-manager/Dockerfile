FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY services/sandbox-manager/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/common/ /app/services/common/
COPY services/sandbox-manager/app.py /app/app.py
COPY services/sandbox-manager/runtime/ /app/runtime/
COPY contracts/harness-sandbox.v0.schema.json /app/contracts/harness-sandbox.v0.schema.json
COPY baf/B1-read/B1.1-service-port/fixture/ /app/fixtures/B1_1/
COPY baf/B2-search/B2.1-find-function/fixture/ /app/fixtures/B2_1/
COPY baf/B3-create/B3.1-json-config/fixture/ /app/fixtures/B3_1/
COPY baf/B4-edit/B4.1-install-version/fixture/ /app/fixtures/B4_1/
COPY baf/B5-tools/B5.1-median/fixture/ /app/fixtures/B5_1/
COPY baf/B1-read/B1.2-payment-term/fixture/ /app/fixtures/B1_2/
COPY baf/B1-read/B1.3-missing-penalty/fixture/ /app/fixtures/B1_3/
COPY baf/B2-search/B2.2-env-readers/fixture/ /app/fixtures/B2_2/
COPY baf/B2-search/B2.3-timezone-parser/fixture/ /app/fixtures/B2_3/
COPY baf/B3-create/B3.2-sorted-report/fixture/ /app/fixtures/B3_2/
COPY baf/B3-create/B3.3-yaml-strings/fixture/ /app/fixtures/B3_3/
COPY baf/B4-edit/B4.2-rename-function/fixture/ /app/fixtures/B4_2/
COPY baf/B4-edit/B4.3-cp1251-price/fixture/ /app/fixtures/B4_3/
COPY baf/B5-tools/B5.2-failing-tests/fixture/ /app/fixtures/B5_2/
COPY baf/B5-tools/B5.3-build-exit/fixture/ /app/fixtures/B5_3/
# Docker socket access is root-equivalent; only this trusted service gets it.
USER 0:0
EXPOSE 8000
CMD ["python", "app.py"]
