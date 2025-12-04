#!/usr/bin/env python3
"""
CNPJ Data Pipeline - Main entry point

Configurable, efficient processor for CNPJ data files with database abstraction.
Supports multiple databases and intelligent processing strategies.

Usage: python main.py
"""

import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from src.config import Config
from src.database.factory import create_database_adapter
from src.downloader import Downloader
from src.processor import Processor

# Load environment variables
load_dotenv()


def setup_logging(config: Config):
    """Configure logging with enhanced output."""
    log_level = logging.DEBUG if config.debug else logging.INFO

    # Create logs directory
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "cnpj_loader.log"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger(__name__)


def main():
    """Main processing function."""
    # Initialize basic logging first (before config in case config fails)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )
    logger = logging.getLogger(__name__)

    try:
        # Initialize configuration
        config = Config()

        # Setup enhanced logging with config
        logger = setup_logging(config)

        logger.info("=" * 60)
        logger.info("CNPJ Data Pipeline Starting")
        logger.info(f"Database: {config.database_backend.value}")
        logger.info(f"Processing Strategy: {config.processing_strategy.value}")

        # Initialize components to get strategy info
        downloader = Downloader(config)
        processor = Processor(config)
        db = create_database_adapter(config)

        # Log download strategy (now that downloader is initialized)
        logger.info(f"Download Strategy: {downloader.strategy.get_strategy_name()}")
        logger.debug(f"Batch Size: {config.batch_size:,}")
        logger.debug(f"Optimal Chunk Size: {config.optimal_chunk_size:,}")
        logger.info("=" * 60)

        # Connect to database and setup
        logger.info(f"Connecting to {config.database_backend.value}...")
        db.connect()

        # [NOVO] Auto-Migration
        # Garante que as tabelas existam antes de tentar inserir dados
        db.execute_schema("init-db/postgres.sql")

        db.ensure_tracking_table()

        logger.info("Starting CNPJ data processing")

        # Get latest directory
        directories = downloader.get_latest_directories()
        if not directories:
            return

        latest_dir = directories[0]
        logger.info(f"Processing directory: {latest_dir}")

        # Get files in latest directory
        files = downloader.get_directory_files(latest_dir)
        if not files:
            return

        # Get all processed files for this directory
        # Note: it won't check files that might be processed later during same run
        processed_files = db.get_processed_files(latest_dir)
        logger.info(
            f"Found {len(processed_files)} already processed files in {latest_dir}"
        )

        # Organize files by database dependencies
        ordered_files, categorization = downloader.organize_files_by_dependencies(files)

        logger.info("Processing files in dependency order:")
        logger.info(
            f"  Reference tables: {len(categorization['reference_files'])} files"
        )
        for pattern, pattern_files in categorization["data_files"].items():
            if pattern_files:
                logger.info(f"  {pattern}: {len(pattern_files)} files")

        if categorization["unmatched_files"]:
            logger.warning(
                f"  Unmatched files: {len(categorization['unmatched_files'])} files"
            )
            logger.warning(f"    Files: {categorization['unmatched_files']}")

        # Filter out already processed files
        files_to_process = [f for f in ordered_files if f not in processed_files]

        if not files_to_process:
            logger.info("All files already processed")
            return

        logger.info(f"Processing {len(files_to_process)} files...")

        # Process files with parallel download strategy
        total_start = time.time()
        files_processed = 0
        total_rows = 0

        # [MODIFICADO] Processamento Iterativo (Arquivo por Arquivo)
        # Evita o problema de "tudo ou nada" e economiza disco
        for i, filename in enumerate(files_to_process, 1):
            file_start = time.time()
            logger.info(f"🔄 [{i}/{len(files_to_process)}] Starting cycle for: {filename}")

            try:
                # 1. Download e Extração (Individual)
                # Baixa apenas este arquivo específico
                extracted_files = downloader.download_and_extract(latest_dir, filename)
                
                if not extracted_files:
                    logger.warning(f"⚠️ No CSV files extracted from {filename}")
                    continue

                # 2. Processamento e Carga
                for csv_file in extracted_files:
                    try:
                        logger.info(f"  Processing CSV: {csv_file.name}")
                        
                        # Processa (Parse + Transform)
                        result = processor.process_file(csv_file)

                        if result is None:
                            # Caso especial: Arquivos gigantes processados em chunks direto pro banco
                            # O processador já fez a inserção
                            pass
                        else:
                            # Caso padrão: DataFrame em memória
                            df, table_name = result
                            db.bulk_upsert(df, table_name)
                            total_rows += len(df)

                    except Exception as e:
                        logger.error(f"❌ Error processing CSV {csv_file.name}: {e}")
                        # Não re-lançamos a exceção aqui para tentar os outros CSVs do mesmo ZIP
                        # mas marcamos erro para não salvar o checkpoint do ZIP pai se necessário
                        raise e
                    finally:
                        # Limpeza Imediata do CSV descompactado para economizar espaço
                        if csv_file.exists():
                            csv_file.unlink()

                # 3. Checkpoint (Marcação de Sucesso)
                # Se chegou aqui, todos os CSVs do ZIP foram processados
                db.mark_processed(latest_dir, filename)
                files_processed += 1
                
                duration = time.time() - file_start
                logger.info(f"✅ [{i}/{len(files_to_process)}] Completed {filename} in {duration:.1f}s")

            except Exception as e:
                logger.error(f"❌ Failed to process {filename}: {e}")
                if config.debug:
                    logger.exception("Full traceback:")
                # Continua para o próximo arquivo, não aborta o pipeline inteiro
                continue
            
            finally:
                # 4. Limpeza do ZIP original
                # Garante que o arquivo baixado seja removido antes do próximo
                zip_path = Path(config.temp_dir) / filename
                if zip_path.exists() and not config.keep_downloaded_files:
                    zip_path.unlink()

        # Summary
        total_duration = time.time() - total_start
        logger.info("\n" + "=" * 60)
        logger.info("CNPJ Data Loading Complete!")
        logger.info(f"Files processed: {files_processed}")
        logger.info(f"Total rows: {total_rows:,}")
        logger.info(f"Total time: {total_duration:.1f}s")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        if "config" in locals() and config.debug:
            logger.exception("Full traceback:")
        sys.exit(1)
    finally:
        if "db" in locals():
            db.disconnect()


if __name__ == "__main__":
    main()
