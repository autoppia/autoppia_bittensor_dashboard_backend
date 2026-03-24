#!/usr/bin/env python3
"""
Script para probar Redis y verificar la contraseña
Migrado desde scripts/bash/test_redis.sh
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Colores ANSI
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
NC = "\033[0m"  # No Color


def print_header(text: str) -> None:
    """Imprime un encabezado."""
    print(f"{text}")
    print()


def print_success(message: str) -> None:
    """Imprime un mensaje de éxito."""
    print(f"   {GREEN}✅{NC} {message}")


def print_error(message: str) -> None:
    """Imprime un mensaje de error."""
    print(f"   {RED}❌{NC} {message}")


def print_warning(message: str) -> None:
    """Imprime un mensaje de advertencia."""
    print(f"   {YELLOW}⚠️{NC} {message}")


def run_command(cmd: list[str], capture_output: bool = True) -> tuple[int, str, str]:
    """Ejecuta un comando y retorna el código de salida, stdout y stderr."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=False,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)


def get_project_root() -> Path:
    """Obtiene la ruta raíz del proyecto."""
    script_dir = Path(__file__).resolve().parent
    return script_dir.parent


def load_env_file(env_path: Path) -> dict[str, str]:
    """Carga variables de entorno desde un archivo .env."""
    env_vars = {}
    if not env_path.exists():
        return env_vars

    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            # Ignorar comentarios y líneas vacías
            if not line or line.startswith("#"):
                continue
            # Parsear KEY=VALUE
            if "=" in line:
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return env_vars


def test_redis_status() -> None:
    """Test 1: Estado de Redis."""
    print("1️⃣  Estado de Redis:")
    code, stdout, _ = run_command(["docker", "compose", "ps", "redis"], capture_output=False)
    print()


def test_redis_logs() -> None:
    """Test 2: Últimos logs."""
    print("2️⃣  Últimos logs:")
    code, stdout, _ = run_command(["docker", "compose", "logs", "--tail=5", "redis"], capture_output=False)
    print()


def _check_redis_connection_no_password() -> tuple[str, bool]:
    """Test 3: Probar conexión SIN contraseña."""
    print("3️⃣  Probando conexión SIN contraseña:")

    code, stdout, stderr = run_command(["docker", "compose", "exec", "-T", "redis", "redis-cli", "ping"])
    output = stdout + stderr

    if "PONG" in output:
        print_success("Redis responde SIN contraseña")
        print_warning("Redis NO tiene contraseña configurada")
        print()
        return "no", True
    elif "NOAUTH" in output or "Authentication required" in output:
        print_error("Redis requiere contraseña")
        print_success("Redis SÍ tiene contraseña configurada")
        print()
        return "yes", True
    else:
        print_warning("No se pudo determinar el estado")
        print()
        return "unknown", False


def get_redis_password() -> Optional[str]:
    """Obtiene la contraseña de Redis desde el archivo .env."""
    project_root = get_project_root()
    env_path = project_root / ".env"

    if not env_path.exists():
        return None

    env_vars = load_env_file(env_path)

    # Obtener el entorno
    environment = env_vars.get("ENVIRONMENT", "local").upper()

    # Buscar REDIS_PASSWORD_<ENVIRONMENT>
    password_var = f"REDIS_PASSWORD_{environment}"
    password = env_vars.get(password_var)

    if not password:
        # Intentar REDIS_PASSWORD sin sufijo
        password = env_vars.get("REDIS_PASSWORD")

    return password


def _check_redis_connection_with_password(password: str) -> bool:
    """Test 4: Probar conexión CON contraseña."""
    print("4️⃣  Probando conexión CON contraseña...")

    if not password:
        print_warning("No hay contraseña disponible")
        print()
        return False

    code, stdout, stderr = run_command(["docker", "compose", "exec", "-T", "redis", "redis-cli", "-a", password, "ping"])
    output = stdout + stderr

    if "PONG" in output:
        print_success("Redis responde con la contraseña correcta")
        print()
        print(f"   📝 Contraseña: {password}")
        print()
        return True
    else:
        print_error("La contraseña no funciona")
        print()
        return False


def _print_connection_info(has_password: str, password: Optional[str]) -> None:
    """Test 5: Información de conexión."""
    print("5️⃣  Información de conexión:")
    print("   Host: localhost (o IP del servidor)")
    print("   Puerto: 6379")
    if has_password == "yes" and password:
        print(f"   Contraseña: {password}")
    else:
        print("   Contraseña: (ninguna)")
    print()


def _check_redis_set_get(has_password: str, password: Optional[str]) -> None:
    """Test 6: Probar SET/GET."""
    print("6️⃣  Probando SET/GET:")

    if has_password == "yes" and password:
        # SET con contraseña
        code1, stdout1, stderr1 = run_command(["docker", "compose", "exec", "-T", "redis", "redis-cli", "-a", password, "SET", "test_key", "test_value"])
        # GET con contraseña
        code2, stdout2, stderr2 = run_command(["docker", "compose", "exec", "-T", "redis", "redis-cli", "-a", password, "GET", "test_key"])
        result = stdout2.strip()
    else:
        # SET sin contraseña
        code1, stdout1, stderr1 = run_command(["docker", "compose", "exec", "-T", "redis", "redis-cli", "SET", "test_key", "test_value"])
        # GET sin contraseña
        code2, stdout2, stderr2 = run_command(["docker", "compose", "exec", "-T", "redis", "redis-cli", "GET", "test_key"])
        result = stdout2.strip()

    if result == "test_value":
        print_success("SET/GET funciona correctamente")
        print(f"   Valor almacenado: {result}")
    else:
        print_warning("Problema con SET/GET")
        print(f"   Resultado obtenido: {result}")
    print()


def main() -> None:
    """Ejecuta todos los tests de Redis."""
    project_root = get_project_root()
    os.chdir(project_root)

    print_header("🔍 Verificando Redis...")

    # Test 1: Estado
    test_redis_status()

    # Test 2: Logs
    test_redis_logs()

    # Test 3: Conexión sin contraseña
    has_password, connection_ok = _check_redis_connection_no_password()

    # Test 4: Conexión con contraseña (si es necesario)
    password = None
    if has_password == "yes":
        password = get_redis_password()
        if password:
            _check_redis_connection_with_password(password)
        else:
            print_warning("No hay contraseña en .env para el entorno actual")
            print()

    # Test 5: Información de conexión
    _print_connection_info(has_password, password)

    # Test 6: SET/GET
    _check_redis_set_get(has_password, password)

    print("✅ Verificación completada")

    if not connection_ok:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
