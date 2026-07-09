FROM python:3.12-slim

RUN groupadd --gid 1000 sandbox \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin sandbox

WORKDIR /workspace

RUN pip install --no-cache-dir pandas numpy openpyxl requests

USER sandbox:sandbox

CMD ["python", "-c", "import time; time.sleep(3600)"]