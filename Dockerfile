FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock ./
RUN pip install --no-cache-dir uv==0.10.0 \
    && uv pip install --system -r requirements.lock
COPY src ./src
ENV PYTHONPATH=/app/src
CMD ["python", "-m", "uvicorn", "market_analysis.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
