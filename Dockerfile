# Dockerfile
FROM docker.abrha.net/python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# تغییر مخازن دبیان به میرور ایران‌سرور
RUN sed -i 's/deb.debian.org/mirror.iranserver.com/g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's/security.debian.org/mirror.iranserver.com/g' /etc/apt/sources.list.d/debian.sources || true

# نصب Redis و Supervisor
RUN apt-get update -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false && \
    apt-get install -y redis-server supervisor && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# NEW: امتحان چند میرور به ترتیب - اولویت با میرور لیارا (برای بیلد سریع روی لیارا)
RUN pip install --no-cache-dir \
    --trusted-host package-mirror.liara.ir \
    -i https://package-mirror.liara.ir/repository/pypi/simple/ \
    -r requirements.txt || \
    pip install --no-cache-dir \
    --trusted-host pypi.jamko.ir \
    -i https://pypi.jamko.ir/simple/ \
    -r requirements.txt || \
    pip install --no-cache-dir \
    --trusted-host pypi.ir \
    -i https://pypi.ir/simple/ \
    -r requirements.txt || \
    pip install --no-cache-dir \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -i https://pypi.org/simple/ \
    -r requirements.txt

COPY . .
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 8000

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]