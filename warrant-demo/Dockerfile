# The demo runs from the standard library plus the engine's one dependency, so
# this image is deliberately thin — it exists to make the demo runnable on a
# machine with nothing installed, not to model a production deployment.
FROM python:3.12-slim
WORKDIR /app
COPY warrant/requirements.txt /app/warrant-requirements.txt
RUN pip install --no-cache-dir -r /app/warrant-requirements.txt
COPY warrant /app/warrant
COPY warrant-demo /app/warrant-demo
ENV PYTHONPATH=/app/warrant:/app/warrant-demo
WORKDIR /app/warrant-demo
EXPOSE 8140
# --check on start would be a nice guard, but it must not stop the demo coming
# up in front of an audience; the server prints the same warning and serves.
CMD ["python", "server.py", "--port", "8140"]
