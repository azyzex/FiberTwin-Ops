# build context: repository root (see monitoring/compose.yaml)
FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir fastapi==0.115.12 uvicorn==0.34.2 pyyaml==6.0.2 networkx==3.5
COPY netgen/__init__.py netgen/whatif.py netgen/model.py netgen/
COPY analysis/__init__.py analysis/logic.py analysis/app.py analysis/
EXPOSE 8000
CMD ["uvicorn", "analysis.app:app", "--host", "0.0.0.0", "--port", "8000"]
