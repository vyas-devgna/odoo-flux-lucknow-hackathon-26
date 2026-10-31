FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 DJANGO_DEBUG=0 DJANGO_DB=/data/db.sqlite3
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput
VOLUME /data
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi -b 0.0.0.0:8000 --workers 3 --access-logfile -"]
