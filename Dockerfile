FROM python:3.12-slim-bookworm

WORKDIR /app
RUN mkdir /data && chown 10001:10001 /data
COPY accounts.py enhancements.py resolv.py mailer.py reporting.py schema.py server.py sla_calendar.py topology.py triage.py index.html ./
COPY assets/ assets/
COPY migrations/ migrations/
RUN chmod -R a+rX /app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    RESOLV_HOST=0.0.0.0 RESOLV_PORT=8000 \
    RESOLV_DATABASE=/data/resolv.db RESOLV_ATTACHMENTS=/data/evidence
USER 10001:10001
EXPOSE 8000
CMD ["python", "server.py"]
