"""
Perfect Engine - Interactive Startup Script 👑
==============================================

Easy startup for the beautifully organized Perfect Engine V2.0
"""

import os
import sys
import subprocess
import platform
from pathlib import Path

# Colors for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 70}")
    print(f"  {text}")
    print(f"{'=' * 70}{Colors.END}\n")

def print_success(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.END}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")

def print_info(text):
    print(f"{Colors.CYAN}ℹ️  {text}{Colors.END}")

def check_python_version():
    """Check Python version"""
    print_info("Checking Python version...")
    version = sys.version_info
    if version.major >= 3 and version.minor >= 8:
        print_success(f"Python {version.major}.{version.minor}.{version.micro}")
        return True
    else:
        print_error(f"Python 3.8+ required. Found: {version.major}.{version.minor}.{version.micro}")
        return False

def check_folder_structure():
    """Check if new folder structure exists"""
    print_info("Checking folder structure...")
    
    required_folders = ["app", "llm", "scripts", "tests", "docs", "examples"]
    all_exist = all(Path(folder).exists() for folder in required_folders)
    
    if all_exist:
        print_success("New folder structure detected ✨")
        return True
    else:
        print_warning("Using legacy structure")
        return False

def check_env_file():
    """Check if .env file exists"""
    print_info("Checking .env configuration...")
    
    if Path(".env").exists():
        print_success(".env file found")

        with open(".env", "r") as f:
            content = f.read()
            if "LLM_PROVIDER=litellm" in content:
                print_info("   🤖 Using LiteLLM cloud gateway (DB-driven providers)")

        return True
    else:
        print_warning(".env file not found")
        print_info("Creating .env from template...")

        # Create default .env (litellm gateway; providers configured via API/DB)
        env_content = """# Perfect Engine Configuration
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=arffy_AIbot_engine

# LiteLLM cloud gateway (providers managed via /api/llm-providers, no redeploy).
LLM_PROVIDER=litellm
# GEMINI_API_KEY=
# GROQ_API_KEY=
# OPENROUTER_API_KEY=
# GITHUB_TOKEN=
# OPENAI_API_KEY=

# Server Configuration
HOST=0.0.0.0
PORT=8000
ENVIRONMENT=development
ALLOWED_ORIGINS=*
"""
        with open(".env", "w") as f:
            f.write(env_content)

        print_success(".env file created (LLM_PROVIDER=litellm default)")
        return True

def check_dependencies():
    """Check if dependencies are installed (single requirements file)."""
    print_info("Checking dependencies...")

    try:
        import fastapi
        import uvicorn
        import motor
        import pymongo
        import pydantic
        import httpx
        import openai
        from dotenv import load_dotenv

        print_success("Core dependencies installed (incl. LiteLLM gateway client)")
        return True
    except ImportError as e:
        print_error(f"Missing dependency: {e}")
        print_info("Installing dependencies...")
        
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                check=True
            )
            print_success("Dependencies installed successfully")
            return True
        except subprocess.CalledProcessError:
            print_error("Failed to install dependencies")
            print_info("Please run manually: pip install -r requirements.txt")
            return False

def check_mongodb():
    """Check if MongoDB is accessible"""
    print_info("Checking MongoDB connection...")
    
    try:
        import pymongo
        from dotenv import load_dotenv
        
        load_dotenv()
        mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
        
        client = pymongo.MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
        client.server_info()
        client.close()
        
        print_success("MongoDB connection successful")
        return True
    except Exception as e:
        print_error(f"MongoDB connection failed: {e}")
        print_warning("Please ensure MongoDB is running")
        print_info("Quick start: docker run -d -p 27017:27017 mongo:latest")
        return False

def check_nodejs():
    """Check if Node.js is installed"""
    print_info("Checking Node.js installation...")
    
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            version = result.stdout.strip()
            print_success(f"Node.js installed: {version}")
            return True
        else:
            print_warning("Node.js not found")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print_warning("Node.js not found")
        print_info("Node.js is required for executing node code")
        print_info("Install from: https://nodejs.org/")
        return False

def display_menu():
    """Display startup menu"""
    print(f"\n{Colors.CYAN}{Colors.BOLD}What would you like to do?{Colors.END}\n")
    print("1. Start Backend Server")
    print("2. Run Simple Tests")
    print("3. View System Info")
    print("4. Open API Documentation")
    print("5. Exit")
    print()

def start_server():
    """Start the backend server"""
    print_header("🚀 Starting Perfect Engine Server")
    
    print_info("Starting server on http://localhost:8000")
    print_info("API Docs: http://localhost:8000/docs")
    print_info("Press Ctrl+C to stop")
    print()
    
    try:
        subprocess.run([sys.executable, "-m", "app.main"], check=True)
    except KeyboardInterrupt:
        print_info("\nServer stopped")
    except subprocess.CalledProcessError as e:
        print_error(f"Server failed: {e}")

def run_simple_tests():
    """Run simple tests"""
    print_header("🧪 Running Simple Tests")
    
    script_path = Path("tests/simple_test.py")
    if not script_path.exists():
        script_path = Path("simple_test.py")  # Legacy location
    
    if script_path.exists():
        try:
            subprocess.run([sys.executable, str(script_path)], check=True)
        except subprocess.CalledProcessError as e:
            print_error(f"Tests failed: {e}")
    else:
        print_error("Test script not found")

def show_system_info():
    """Show system information"""
    print_header("📊 System Information")
    
    print(f"{Colors.CYAN}Python:{Colors.END}")
    print(f"   Version: {sys.version.split()[0]}")
    print(f"   Executable: {sys.executable}")
    
    print(f"\n{Colors.CYAN}Platform:{Colors.END}")
    print(f"   System: {platform.system()}")
    print(f"   Release: {platform.release()}")
    print(f"   Machine: {platform.machine()}")
    
    print(f"\n{Colors.CYAN}Folder Structure:{Colors.END}")
    folders = ["app", "llm", "scripts", "tests", "docs", "examples"]
    for folder in folders:
        exists = "✅" if Path(folder).exists() else "❌"
        print(f"   {exists} {folder}/")
    
    print(f"\n{Colors.CYAN}Environment:{Colors.END}")
    if Path(".env").exists():
        from dotenv import load_dotenv
        load_dotenv()
        print(f"   MongoDB: {os.getenv('MONGODB_URL', 'Not set')}")
        print(f"   Database: {os.getenv('DATABASE_NAME', 'Not set')}")
        print(f"   LLM Provider: {os.getenv('LLM_PROVIDER', 'Not set')}")
        print(f"   Port: {os.getenv('PORT', 'Not set')}")
    else:
        print("   .env file not found")
    
    print(f"\n{Colors.CYAN}Key Files:{Colors.END}")
    files = [
        ("app/main.py", "Main application"),
        ("llm/integration.py", "LLM providers"),
        ("requirements.txt", "Dependencies"),
        (".env", "Configuration")
    ]
    
    for file, desc in files:
        exists = "✅" if Path(file).exists() else "❌"
        print(f"   {exists} {file:25} - {desc}")

def open_api_docs():
    """Open API documentation in browser"""
    print_info("Opening API documentation...")
    import webbrowser
    webbrowser.open("http://localhost:8000/docs")
    print_success("API docs opened in browser")
    print_warning("Make sure the server is running!")

def main():
    """Main function"""
    print_header("👑 Perfect Engine V2.0 - Startup Assistant 👑")
    
    print(f"{Colors.CYAN}LiteLLM cloud gateway — providers via /api/llm-providers{Colors.END}\n")
    
    # Run checks
    print_info("Running system checks...\n")
    
    checks = {
        "Python Version": check_python_version(),
        "Folder Structure": check_folder_structure(),
        "Environment File": check_env_file(),
        "Dependencies": check_dependencies(),
        "MongoDB": check_mongodb(),
        "Node.js": check_nodejs(),
    }
    
    print(f"\n{Colors.CYAN}{Colors.BOLD}System Check Results:{Colors.END}")
    for check, result in checks.items():
        status = "✅" if result else "⚠️ "
        print(f"   {status} {check}")
    
    # Check critical components
    critical_checks = ["Python Version", "Dependencies", "MongoDB"]
    all_critical_passed = all(checks[check] for check in critical_checks)
    
    if not all_critical_passed:
        print_error("\n⚠️  Some critical checks failed!")
        print_info("Please fix the issues above before continuing.")
        return

    # Show menu
    while True:
        display_menu()
        
        try:
            choice = input(f"{Colors.CYAN}Enter your choice (1-5): {Colors.END}").strip()
            
            if choice == "1":
                start_server()
            elif choice == "2":
                run_simple_tests()
            elif choice == "3":
                show_system_info()
            elif choice == "4":
                open_api_docs()
            elif choice == "5":
                print_info("Goodbye! 👋")
                break
            else:
                print_warning("Invalid choice. Please select 1-5.")
        
        except KeyboardInterrupt:
            print_info("\n\nGoodbye! 👋")
            break
        except Exception as e:
            print_error(f"Error: {e}")

if __name__ == "__main__":
    main()
