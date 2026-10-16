FROM node:22-alpine AS web
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY examples/ /app/examples/
COPY --from=web /app/frontend/dist /app/frontend/dist
EXPOSE 8000
CMD ["uvicorn", "backtester.api:app", "--host", "0.0.0.0", "--port", "8000"]
