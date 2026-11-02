# build context: repository root (see monitoring/compose.yaml)
FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir docker==7.1.0 prometheus-client==0.22.1 pyyaml==6.0.2
COPY optics/__init__.py optics/model.py optics/agent.py optics/
EXPOSE 8081 9106
CMD ["python", "-m", "optics.agent"]
