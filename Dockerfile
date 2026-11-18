# Recaply's online service (cloud.py): the website, accounts, plans, payments
# and the API the Windows app signs in through. No video work happens here,
# so the image is small and needs no ffmpeg.
FROM python:3.13-slim

WORKDIR /app
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY accounts.py auth.py cloud.py cloud_api.py config.py webutil.py ./
COPY templates ./templates
COPY static ./static

RUN useradd --create-home recaply && mkdir -p /data/downloads && chown -R recaply /data
USER recaply

ENV HOST=0.0.0.0 PORT=8000 RECAPLY_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz',timeout=4)"

CMD ["python", "cloud.py"]
