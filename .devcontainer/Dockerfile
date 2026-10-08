FROM python:3.12-slim

ARG USER_ID=1000
ARG GROUP_ID=1000

# Create group and user
RUN groupadd -g $GROUP_ID devuser \
    && useradd -m -u $USER_ID -g devuser -s /bin/bash devuser

WORKDIR /workspace

RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    libpq-dev \
    redis-tools \
    postgresql-client \
    zsh \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Fix workspace permissions
RUN chown -R devuser:devuser /workspace

ENV PYTHONPATH=/workspace/src

ENV PATH="/home/devuser/.local/bin:$PATH"

ENV PYTHONUNBUFFERED=1

USER devuser
