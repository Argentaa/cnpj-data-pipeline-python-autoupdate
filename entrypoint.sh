#!/bin/bash
set -e

# 1. Configuração de Ambiente para o Cron
# O Cron roda em um shell limpo, então precisamos exportar as variáveis do Docker
# para um arquivo que o Cron consiga ler (/etc/environment).
printenv | grep -v "no_proxy" >> /etc/environment

echo "🚀 [Entrypoint] Container Iniciado."

# 2. Execução Bootstrap (Síncrona)
# Executa o pipeline imediatamente. O script só avança quando isso terminar.
echo "🔄 [Entrypoint] Iniciando carga inicial do banco de dados..."
python /app/main.py

if [ $? -eq 0 ]; then
    echo "✅ [Entrypoint] Carga inicial concluída com sucesso."
else
    echo "❌ [Entrypoint] Erro na carga inicial. Verifique os logs."
    # Opcional: exit 1 se quiser que o container morra em caso de erro inicial
fi

# 3. Transferência de Controle para o Cron
# Inicia o cron em foreground (-f) para manter o container rodando e aguardar o dia 05.
echo "⏰ [Entrypoint] Iniciando agendador Cron (Próxima execução: Dia 05 às 00:00)..."
exec cron -f