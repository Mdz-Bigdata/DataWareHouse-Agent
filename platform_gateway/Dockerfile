FROM python:3.12-slim

WORKDIR /app
COPY platform_gateway/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY platform_gateway ./platform_gateway

EXPOSE 8080
CMD ["uvicorn", "platform_gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]

