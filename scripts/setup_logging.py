#!/usr/bin/env python3
"""
Script to configure logging settings in .env file
"""
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

ENV_FILE = project_root / ".env"
LOGGING_VARS = {
    "LOG_LEVEL": "INFO",
    "SQLALCHEMY_LOG_LEVEL": "ERROR",
    "BITTENSOR_LOG_LEVEL": "WARNING",
    "UVICORN_LOG_LEVEL": "INFO",
    "UVICORN_ACCESS_LOG": "false",
    "LOG_TO_FILE": "true",
    "LOG_FILE_PATH": "logs/app.log",
    "LOG_REQUEST_BODY": "true",
    "LOG_RESPONSE_BODY": "true",
}


def read_env():
    """Read current .env file"""
    if not ENV_FILE.exists():
        return {}

    env_vars = {}
    with open(ENV_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip()

    return env_vars


def write_env(env_vars):
    """Write .env file"""
    with open(ENV_FILE, "w") as f:
        # Write header
        f.write(
            "# ============================================================================\n"
        )
        f.write("# AUTOPPIA BACKEND CONFIGURATION\n")
        f.write(
            "# ============================================================================\n\n"
        )

        # Group variables
        logging_vars = []
        other_vars = []

        for key, value in env_vars.items():
            if key.startswith("LOG_") or key in [
                "SQLALCHEMY_LOG_LEVEL",
                "BITTENSOR_LOG_LEVEL",
                "UVICORN_LOG_LEVEL",
                "UVICORN_ACCESS_LOG",
            ]:
                logging_vars.append((key, value))
            else:
                other_vars.append((key, value))

        # Write other vars first
        if other_vars:
            f.write("# Database and General Configuration\n")
            f.write(
                "# ----------------------------------------------------------------------------\n"
            )
            for key, value in other_vars:
                f.write(f"{key}={value}\n")
            f.write("\n")

        # Write logging vars
        if logging_vars:
            f.write("# Logging Configuration\n")
            f.write(
                "# ----------------------------------------------------------------------------\n"
            )
            for key, value in logging_vars:
                f.write(f"{key}={value}\n")
            f.write("\n")


def main():
    print("🔧 Configurando sistema de logging...")
    print(f"📁 Archivo .env: {ENV_FILE}")
    print()

    # Read current .env
    env_vars = read_env()

    # Check which logging vars are missing
    missing = []
    existing = []

    for key, default_value in LOGGING_VARS.items():
        if key not in env_vars:
            missing.append(key)
        else:
            existing.append(key)

    if not missing:
        print("✅ Todas las variables de logging ya están configuradas:")
        for key in existing:
            print(f"   - {key}={env_vars[key]}")
        print()
        response = input("¿Quieres resetear a valores recomendados? (s/N): ").lower()
        if response != "s":
            print("✋ No se realizaron cambios")
            return

    # Show what will be added/updated
    print("📝 Variables que se configurarán:")
    for key, value in LOGGING_VARS.items():
        status = "actualizar" if key in env_vars else "añadir"
        current = f" (actual: {env_vars[key]})" if key in env_vars else ""
        print(f"   {status.upper()}: {key}={value}{current}")

    print()
    response = input("¿Continuar? (S/n): ").lower()
    if response == "n":
        print("✋ Operación cancelada")
        return

    # Update env vars
    env_vars.update(LOGGING_VARS)

    # Backup existing .env
    if ENV_FILE.exists():
        backup_file = ENV_FILE.with_suffix(".env.backup")
        import shutil

        shutil.copy(ENV_FILE, backup_file)
        print(f"💾 Backup creado: {backup_file}")

    # Write new .env
    write_env(env_vars)

    print()
    print("✅ Configuración de logging actualizada!")
    print()
    print("📋 Archivos de log que se crearán:")
    print("   - logs/app.log         (logs generales)")
    print("   - logs/requests.log    (requests/responses detallados)")
    print()
    print("🔍 Para ver logs en tiempo real:")
    print("   tail -f logs/app.log")
    print("   tail -f logs/requests.log")
    print()
    print("📚 Más información: ver LOGGING.md")
    print()
    print("🚀 Reinicia el servidor para aplicar cambios:")
    print("   python3 run.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n✋ Operación cancelada por el usuario")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
