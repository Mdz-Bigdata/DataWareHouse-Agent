FROM python:3.12-slim

WORKDIR /engine/backend
COPY apps/data-agent-engine/backend/requirements.txt ./requirements.txt
COPY integrations/data_engine/requirements.txt /tmp/integration-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r /tmp/integration-requirements.txt

COPY apps/data-agent-engine/backend /engine/backend
COPY integrations/data_engine /integration/integrations/data_engine
RUN python -m seed.seed --out seed/sample.db --seed 42

ENV PYTHONPATH=/engine/backend:/integration \
    DATA_AGENT_BACKEND=/engine/backend \
    DATA_AGENT_DB=/engine/backend/seed/sample.db \
    DATA_AGENT_LOG_DIR=/engine/backend/logs

RUN mkdir -p /engine/backend/logs && \
    useradd --create-home --shell /usr/sbin/nologin appuser && \
    chown -R appuser:appuser /engine
USER appuser

EXPOSE 8050
CMD ["uvicorn", "integrations.data_engine.app:create_configured_app", "--factory", "--host", "0.0.0.0", "--port", "8050"]
