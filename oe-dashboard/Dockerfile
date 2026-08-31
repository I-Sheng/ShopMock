FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The console reads container status through a narrowly scoped proxy, never the
# socket itself, so it has no reason to run as root.
RUN useradd --system --uid 10001 --no-create-home oe && chown -R oe:oe /app
USER oe

EXPOSE 8000
CMD ["gunicorn", "oe_dashboard.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
