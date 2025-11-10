#!/usr/bin/env python3
"""
Download Silero TTS Model

Usage:
    python scripts/download-silero-model.py
    python scripts/download-silero-model.py --output models/silero_v3_en.pt
    python scripts/download-silero-model.py --force  # Force re-download
"""

import os
import sys
import argparse
import urllib.request
from pathlib import Path
from typing import Optional


class Colors:
    """ANSI color codes"""
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    END = '\033[0m'


def print_header():
    """Print script header"""
    print()
    print(f"{Colors.CYAN}{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.CYAN}{Colors.BOLD}    Silero TTS Model Downloader{Colors.END}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'='*60}{Colors.END}")
    print()


def print_info(msg: str):
    """Print info message"""
    print(f"{Colors.CYAN}ℹ️  {msg}{Colors.END}")


def print_success(msg: str):
    """Print success message"""
    print(f"{Colors.GREEN}✅ {msg}{Colors.END}")


def print_warning(msg: str):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠️  {msg}{Colors.END}")


def print_error(msg: str):
    """Print error message"""
    print(f"{Colors.RED}❌ {msg}{Colors.END}")


def get_project_root() -> Path:
    """Get project root directory"""
    script_dir = Path(__file__).parent
    return script_dir.parent


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def download_file(url: str, filepath: Path, show_progress: bool = True) -> bool:
    """
    Download file from URL
    
    Args:
        url: URL to download from
        filepath: Path to save file
        show_progress: Show download progress
    
    Returns:
        True if successful, False otherwise
    """
    try:
        def download_hook(block_num, block_size, total_size):
            if show_progress and total_size > 0:
                downloaded = block_num * block_size
                percent = min(100, (downloaded / total_size) * 100)
                bar_length = 40
                filled = int(bar_length * downloaded / total_size)
                bar = '█' * filled + '░' * (bar_length - filled)
                sys.stdout.write(f'\r  Progress: [{bar}] {percent:.1f}%')
                sys.stdout.flush()
        
        print_info(f"Downloading from: {url}")
        urllib.request.urlretrieve(url, filepath, download_hook)
        print()  # New line after progress bar
        return True
        
    except Exception as e:
        print_error(f"Download failed: {e}")
        return False


def verify_model(filepath: Path) -> bool:
    """
    Verify downloaded model file
    
    Args:
        filepath: Path to model file
    
    Returns:
        True if valid, False otherwise
    """
    if not filepath.exists():
        print_error("File not found after download")
        return False
    
    file_size = filepath.stat().st_size
    size_mb = file_size / (1024 * 1024)
    
    # Silero model should be around 50MB
    if size_mb < 40:
        print_error(f"File too small: {size_mb:.2f} MB (expected ~50 MB)")
        return False
    
    return True


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description='Download Silero TTS Model',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python download-silero-model.py
  python download-silero-model.py --output models/silero_v3_en.pt
  python download-silero-model.py --force
        '''
    )
    
    parser.add_argument(
        '--output', '-o',
        default='models/silero_v3_en.pt',
        help='Output model path (default: models/silero_v3_en.pt)'
    )
    
    parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='Force re-download even if file exists'
    )
    
    parser.add_argument(
        '--no-verify',
        action='store_true',
        help='Skip model verification'
    )
    
    args = parser.parse_args()
    
    print_header()
    
    # Setup paths
    project_root = get_project_root()
    model_path = project_root / args.output
    model_dir = model_path.parent
    
    model_url = "https://models.silero.ai/models/tts/en/v3_en.pt"
    
    print_info("Configuration:")
    print(f"  Model URL: {model_url}")
    print(f"  Save Path: {model_path}")
    print(f"  Model Size: ~50 MB")
    print()
    
    # Check if model already exists
    if model_path.exists() and not args.force:
        file_size = model_path.stat().st_size
        size_mb = file_size / (1024 * 1024)
        print_warning(f"Model already exists: {size_mb:.2f} MB")
        print()
        
        response = input("Re-download? (y/n): ").strip().lower()
        if response not in ['y', 'yes']:
            print_success("Using existing model. Done!")
            return 0
        
        print_info("Removing existing model...")
        model_path.unlink()
    
    # Create directory if needed
    if not model_dir.exists():
        print_info(f"Creating directory: {model_dir}")
        model_dir.mkdir(parents=True, exist_ok=True)
    
    # Download
    print()
    print_info("Downloading Silero TTS model...")
    print_info("This may take several minutes depending on your internet speed...")
    print()
    
    if not download_file(model_url, model_path):
        print_error("Download failed")
        return 1
    
    # Verify
    if not args.no_verify:
        print_info("Verifying model file...")
        if not verify_model(model_path):
            print_error("Model verification failed")
            return 1
    
    # Success
    print()
    print_success("Download completed successfully!")
    print()
    
    file_size = model_path.stat().st_size
    size_mb = file_size / (1024 * 1024)
    
    print_success("Model Information:")
    print(f"  Path: {model_path}")
    print(f"  Size: {size_mb:.2f} MB ({format_size(file_size)})")
    print()
    
    print_success("✨ Silero TTS model is ready to use!")
    print()
    print_info("Next steps:")
    print("  1. Make sure ENABLE_TTS=true in .env")
    print("  2. Run: python main.py start")
    print()
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
