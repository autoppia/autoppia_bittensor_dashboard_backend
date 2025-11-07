# Autoppia Leaderboard API

Backend service that powers the Autoppia validator dashboard. It exposes FastAPI endpoints that validators call to register validator rounds, agent runs, evaluations, and aggregate metrics. The service persists data to PostgreSQL (`autoppia_db` locally) and provides UI-ready views consumed by the frontend.

## 📋 Prerequisites

- **Python 3.11 or higher** (Python 3.13+ recommended)
- **PostgreSQL 12+** (required - no SQLite support)
- **Git**

### Windows Additional Requirements

- PowerShell 5.1 or higher (comes with Windows 10+)
- Optional: Windows Terminal for better experience

### Linux Additional Requirements

- `python3-venv` package (usually included with Python)
- `build-essential` for compiling certain dependencies

---

## 🔨 Required Build Tools

**⚠️ IMPORTANT:** These build tools are **required** before installing dependencies. The project requires `bittensor` which needs Rust compiler and C++ build tools to compile.

### Windows Build Tools Installation

#### Step 1: Install Rust

Rust is required to compile the `bittensor` package and its dependencies.

1. Visit: https://rustup.rs/
2. Download and run `rustup-init.exe`
3. Follow the installation prompts (choose default options)
4. **Restart your terminal** after installation
5. Verify installation:
   ```powershell
   rustc --version
   cargo --version
   ```

#### Step 2: Install Visual C++ Build Tools

The Visual C++ Build Tools are required for compiling native Python extensions.

1. Visit: https://visualstudio.microsoft.com/visual-cpp-build-tools/
2. Download **"Build Tools for Visual Studio 2022"**
3. Run the installer
4. In the installer, select **"Desktop development with C++"**
5. Wait for the installation to complete (this may take 10-20 minutes)
6. **Restart your computer** after installation

#### Step 3: Verify Installation

```powershell
# Check if Rust is available
rustc --version
cargo --version

# Check if MSVC is available (should not show an error)
cl.exe
```

### Linux Build Tools Installation

#### Ubuntu/Debian

```bash
# Install build essentials and Rust
sudo apt-get update
sudo apt-get install build-essential pkg-config libssl-dev

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# Verify installation
rustc --version
cargo --version
gcc --version
```

#### Fedora/RHEL/CentOS

```bash
# Install build essentials
sudo dnf groupinstall "Development Tools"
sudo dnf install openssl-devel pkg-config

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# Verify installation
rustc --version
cargo --version
gcc --version
```

#### macOS

```bash
# Install Xcode Command Line Tools
xcode-select --install

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# Verify installation
rustc --version
cargo --version
```

> **⚠️ Important:** After installing these tools, **restart your terminal** or **open a new terminal window** before proceeding with the Python package installation.

---

## 🚀 Installation

### Option A: Linux / macOS

#### 1. Clone the Repository

```bash
git clone <repository-url>
cd dashboard_backend
```

#### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

#### 3. Install Dependencies

```bash
# Upgrade pip
pip install --upgrade pip

# Install all dependencies (including bittensor)
pip install -r requirements.txt
```

> ⏱️ **Note:** This installation may take 10-15 minutes as it compiles Rust-based packages (bittensor). This is normal!

#### 4. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Or create .env manually (see Environment Configuration section)
nano .env
```

#### 5. Run the Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

### Option B: Windows

#### 1. Clone the Repository

```powershell
git clone <repository-url>
cd dashboard_backend
```

#### 2. Create Virtual Environment

```powershell
python -m venv venv
```

#### 3. Activate Virtual Environment

```powershell
# PowerShell
.\venv\Scripts\Activate.ps1

# Command Prompt (cmd)
.\venv\Scripts\activate.bat
```

#### 4. Install Dependencies

```powershell
# Upgrade pip
python -m pip install --upgrade pip

# Install all dependencies (including bittensor)
pip install -r requirements.txt
```

> ⏱️ **Note:** This installation may take 10-15 minutes as it compiles Rust-based packages (bittensor). This is normal!

#### 5. Configure Environment

Create a `.env` file in the project root:

```powershell
# Create .env from the provided content below
notepad .env
```

#### 6. Run the Server

```powershell
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## ⚙️ Environment Configuration

Create a `.env` file in the project root with the following content:

```bash
# ═══════════════════════════════════════════════════════════════
# ENVIRONMENT MODE
# ═══════════════════════════════════════════════════════════════
# Options: local, development, production
ENVIRONMENT=local

# ═══════════════════════════════════════════════════════════════
# APPLICATION SETTINGS
# ═══════════════════════════════════════════════════════════════
APP_NAME=Autoppia Leaderboard API
DEBUG=true

# ═══════════════════════════════════════════════════════════════
# SERVER CONFIGURATION
# ═══════════════════════════════════════════════════════════════
HOST=0.0.0.0
PORT=8000

# ═══════════════════════════════════════════════════════════════
# DATABASE CONFIGURATION - LOCAL
# ═══════════════════════════════════════════════════════════════
POSTGRES_HOST_LOCAL=127.0.0.1
POSTGRES_PORT_LOCAL=5432
POSTGRES_USER_LOCAL=autoppia_user
POSTGRES_PASSWORD_LOCAL=password
POSTGRES_DB_LOCAL=autoppia_db

# ═══════════════════════════════════════════════════════════════
# LOGGING CONFIGURATION
# ═══════════════════════════════════════════════════════════════
LOG_LEVEL=INFO
SQLALCHEMY_LOG_LEVEL=WARNING
BITTENSOR_LOG_LEVEL=WARNING
UVICORN_LOG_LEVEL=INFO
UVICORN_ACCESS_LOG=true

# ═══════════════════════════════════════════════════════════════
# AUTHENTICATION (disabled for local development)
# ═══════════════════════════════════════════════════════════════
AUTH_DISABLED_LOCAL=true
MIN_VALIDATOR_STAKE_LOCAL=0.0

# ═══════════════════════════════════════════════════════════════
# ROUND CONFIGURATION
# ═══════════════════════════════════════════════════════════════
ROUND_SIZE_EPOCHS_LOCAL=0.2
BLOCKS_PER_EPOCH_LOCAL=360
DZ_STARTING_BLOCK_LOCAL=6717750

# ═══════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════
API_CACHE_DISABLED_LOCAL=false
ENABLE_FINAL_ROUND_CACHE=true
ENABLE_CURRENT_ROUND_CACHE=true
```

For complete configuration options, refer to `app/config.py` or see `DEV_SETUP.md` for development environment setup.

---

## 🔧 About Bittensor

The `bittensor` package is **required** for this application. It provides validator authentication and blockchain integration capabilities.

### ✅ Installation Status

If you followed the installation steps above and ran `pip install -r requirements.txt`, bittensor is **already installed**!

### 📦 What Bittensor Provides

- Validator authentication and verification
- Blockchain state management
- Subnet interaction capabilities
- Cryptographic utilities for secure operations

### ⏱️ Installation Time

- **First-time installation**: 10-15 minutes (compiles Rust code)
- **Subsequent installs**: 2-3 minutes (uses cached builds)

### 🔍 Verify Installation

Check if bittensor is installed correctly:

```bash
python -c "import bittensor; print(f'Bittensor {bittensor.__version__} installed successfully!')"
```

### 🛠️ Reinstalling Bittensor

If you need to reinstall bittensor:

```bash
# Uninstall first
pip uninstall bittensor -y

# Reinstall
pip install "bittensor>=6.9.0,<10.0.0"
```

> **Note:** Make sure you have installed the required build tools (Rust and Visual C++ Build Tools for Windows) before installing bittensor.

---

## 📊 Accessing the API

Once the server is running, you can access:

- **API Base**: http://localhost:8000
- **Interactive API Docs (Swagger)**: http://localhost:8000/docs
- **API Documentation (ReDoc)**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

---

## 🗃️ Database Setup

### PostgreSQL (Required)

#### Linux

```bash
# Install PostgreSQL
sudo apt-get update
sudo apt-get install postgresql postgresql-contrib

# Create database and user
sudo -u postgres psql
CREATE DATABASE autoppia_db;
CREATE USER autoppia_user WITH PASSWORD 'password';
GRANT ALL PRIVILEGES ON DATABASE autoppia_db TO autoppia_user;
\q
```

#### Windows

1. Download PostgreSQL from: https://www.postgresql.org/download/windows/
2. Install and remember your postgres password
3. Open pgAdmin or psql and run:

```sql
CREATE DATABASE autoppia_db;
CREATE USER autoppia_user WITH PASSWORD 'password';
GRANT ALL PRIVILEGES ON DATABASE autoppia_db TO autoppia_user;
```

Update your `.env` file with the PostgreSQL credentials.

---

## 🧪 Running Tests

```bash
# Linux / macOS
source venv/bin/activate
pytest

# Windows
.\venv\Scripts\Activate.ps1
pytest
```

---

## 🛠️ Troubleshooting

### Port 8000 Already in Use

**Linux / macOS:**

```bash
# Find process using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>
```

**Windows:**

```powershell
# Find process using port 8000
netstat -ano | findstr :8000

# Kill the process
Stop-Process -Id <PID> -Force
```

### Module Not Found Errors

Make sure you've activated the virtual environment:

**Linux / macOS:**

```bash
source venv/bin/activate
```

**Windows:**

```powershell
.\venv\Scripts\Activate.ps1
```

### Database Connection Errors

1. Verify PostgreSQL is running:
   - **Linux**: `sudo systemctl status postgresql`
   - **Windows**: Check Services for PostgreSQL service
2. Check your `.env` file has correct database credentials
3. Ensure DATABASE_URL is properly configured in your `.env` file

### Permission Denied (Linux)

```bash
# Make sure you have proper permissions
chmod +x scripts/*.sh
```

### Bittensor Installation Fails (Windows)

If you get errors like `error: failed to run custom build command for 'pyo3-ffi'` or `Rust compiler not found`:

1. **Verify Rust is installed:**

   ```powershell
   rustc --version
   cargo --version
   ```

   If these commands fail, reinstall Rust from https://rustup.rs/

2. **Verify Visual C++ Build Tools are installed:**

   ```powershell
   cl.exe
   ```

   If this fails, reinstall Visual C++ Build Tools and ensure you selected "Desktop development with C++"

3. **Restart your terminal/computer** after installing these tools

4. **Try installing in a new terminal window:**
   ```powershell
   pip install -r requirements.txt
   ```

### Bittensor Installation Fails (Linux)

If you get compilation errors:

```bash
# Make sure you have all required packages
sudo apt-get install build-essential pkg-config libssl-dev

# Verify Rust is available
rustc --version

# If Rust is not found, reload your environment
source $HOME/.cargo/env

# Try installing again
pip install -r requirements.txt
```

### Cargo/Rust Not Found After Installation

**Windows:**

```powershell
# Add Rust to PATH manually
$env:PATH += ";$HOME\.cargo\bin"

# Or restart your terminal/computer
```

**Linux/macOS:**

```bash
# Reload Rust environment
source $HOME/.cargo/env

# Add to shell profile for persistence
echo 'source $HOME/.cargo/env' >> ~/.bashrc  # or ~/.zshrc for zsh
```

---

## 📚 Additional Resources

- **Development Setup**: See `DEV_SETUP.md` for connecting to remote development database
- **Logging**: See `LOGGING.md` and `LOGGING_QUICKSTART.md` for logging configuration
- **IWAP CLI**: See `IWAP_USAGE.md` for database management tools
- **API Configuration**: See `app/config.py` for all available environment variables

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📝 License

See LICENSE file for details.

---

## 🆘 Support

For issues and questions:

- Open an issue on GitHub
- Check existing documentation in the `docs/` folder
- Review configuration examples in the project
