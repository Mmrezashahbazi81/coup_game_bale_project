# 1. استفاده از میرور ایرانی داکر (https://docs.parspack.com/reference/mirror/) به جای داکر هاب اصلی
FROM docker.abrha.net/python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 2. تغییر مخازن دبیان (لینوکس) به میرور ایران‌سرور برای دور زدن تحریم apt-get
# نکته: در پایتون 3.11 ساختار فایل‌های سورس دبیان کمی متفاوت است
RUN sed -i 's/deb.debian.org/mirror.iranserver.com/g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's/security.debian.org/mirror.iranserver.com/g' /etc/apt/sources.list.d/debian.sources || true

# نصب Redis و Supervisor از میرور داخلی
RUN apt-get update -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false && apt-get install -y redis-server supervisor && rm -rf /var/lib/apt/lists/*
# RUN apt-get update && apt-get install -y redis-server supervisor && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# 3. استفاده از میرور ایرانی پایتون (pypi.ir) برای نصب پکیج‌ها
RUN pip install --no-cache-dir -i https://pypi.jamko.ir/simple -r requirements.txt

COPY . .
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 8000

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
