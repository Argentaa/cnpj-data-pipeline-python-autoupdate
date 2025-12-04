FROM python:3.12-slim

# 1. Instalar dependências do sistema e o CRON (Versão Debian)
RUN apt-get update && apt-get install -y \
    build-essential \
    cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY requirements/ ./requirements/
RUN pip install --no-cache-dir -r requirements/postgres.txt

# Copy the application
COPY src/ ./src/
COPY main.py .
COPY setup.py .
COPY validate.py .

# 2. Copiar scripts de inicialização do banco (Auto-Migration)
COPY init-db/ ./init-db/

# Set environment variables
ENV TEMP_DIR=/app/temp
ENV DATABASE_BACKEND=postgresql
ENV PROCESSING_STRATEGY=auto
ENV BATCH_SIZE=50000
ENV MAX_MEMORY_PERCENT=80
ENV DEBUG=false

# 3. Configurar o Cron Job
# - Cria o arquivo de log para o cron
# - Define o agendamento: 0 0 5 * * (Meia-noite do dia 5)
# - Redireciona logs para /proc/1/fd/1 (Stdout do Docker) para aparecer no EasyPanel
RUN touch /var/log/cron.log && \
    echo "0 0 5 * * root /usr/local/bin/python /app/main.py > /proc/1/fd/1 2>&1" > /etc/cron.d/cnpj-cron && \
    chmod 0644 /etc/cron.d/cnpj-cron && \
    crontab /etc/cron.d/cnpj-cron

# 4. Comando de Inicialização (Truque para Variáveis de Ambiente)
# O Cron por padrão não vê as variáveis do Docker (POSTGRES_HOST, etc).
# O comando abaixo salva as variáveis em /etc/environment antes de iniciar o cron.
CMD printenv > /etc/environment && cron -f
