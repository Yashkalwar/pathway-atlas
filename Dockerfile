# Stage 1: build frontend
FROM node:22-slim AS ui
WORKDIR /repo/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
RUN npm run build

# Stage 2: python app
FROM python:3.12-slim
WORKDIR /repo
ENV USE_LLM=0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MALLOC_ARENA_MAX=2 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config/ config/
COPY backend/ backend/
COPY --from=ui /repo/frontend/dist frontend/dist

# Build the synthetic warehouse at image build time (~40s)
RUN mkdir -p data && cd backend && python -m app.pipeline.build

EXPOSE 8000
CMD cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
