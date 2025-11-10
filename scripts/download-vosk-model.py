#!/usr/bin/env python3
"""
Download Vosk STT Model

Usage:
    python scripts/download-vosk-model.py
    python scripts/download-vosk-model.py --output models/vosk-model/
    python scripts/download-vosk-model.py --force  # Force re-download
"""

import os
import sys
import argparse
import urllib.request
import zipfile
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
    print(f"{Colors.CYAN}{Colors.BOLD}    Vosk STT Model Downloader{Colors.END}")
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


def extract_zip(filepath: Path, extract_to: Path) -> bool:
    """
    Extract ZIP file
    
    Args:
        filepath: ZIP file path
        extract_to: Directory to extract to
    
    Returns:
        True if successful, False otherwise
    """
    try:
        print_info(f"Extracting to: {extract_to}")
        
        with zipfile.ZipFile(filepath, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
        
        print_success("Extraction completed")
        return True
        
    except Exception as e:
        print_error(f"Extraction failed: {e}")
        return False


def verify_model(model_dir: Path) -> bool:
    """
    Verify extracted model directory
    
    Args:
        model_dir: Path to model directory
    
    Returns:
        True if valid, False otherwise
    """
    if not model_dir.exists():
        print_error("Model directory not found after extraction")
        return False
    
    # Check for required files in Vosk model
    required_files = [
        'mfcc.model',
        'am/final.mdl',
        'graph/words.txt',
        'graph/HCLG.fst',
        'graph/disambig_tid.int'
    ]
    
    missing_files = []
    for file_path in required_files:
        full_path = model_dir / file_path
        if not full_path.exists():
            missing_files.append(file_path)
    
    if missing_files:
        print_warning("Some expected files are missing:")
        for f in missing_files:
            print(f"  - {f}")
        return False
    
    return True


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description='Download Vosk STT Model',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python download-vosk-model.py
  python download-vosk-model.py --output models/vosk-model/
  python download-vosk-model.py --force
        '''
    )
    
    parser.add_argument(
        '--output', '-o',
        default='models/vosk-model/',
        help='Output directory for model (default: models/vosk-model/)'
    )
    
    parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='Force re-download even if model exists'
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
    model_dir = project_root / args.output.rstrip('/')
    model_name = 'vosk-model-small-en-us-0.15'
    model_full_path = model_dir / model_name
    
    model_url = f"https://alphacephei.com/vosk/models/{model_name}.zip"
    zip_file = model_dir / f"{model_name}.zip"
    
    print_info("Configuration:")
    print(f"  Model URL: {model_url}")
    print(f"  Extract To: {model_full_path}")
    print(f"  Model Size: ~40 MB (ZIP)")
    print()
    
    # Check if model already exists
    if model_full_path.exists() and not args.force:
        print_warning(f"Model already exists: {model_full_path}")
        print()
        
        response = input("Re-download? (y/n): ").strip().lower()
        if response not in ['y', 'yes']:
            print_success("Using existing model. Done!")
            return 0
        
        print_info("Removing existing model...")
        import shutil
        shutil.rmtree(model_full_path)
    
    # Create directory if needed
    if not model_dir.exists():
        print_info(f"Creating directory: {model_dir}")
        model_dir.mkdir(parents=True, exist_ok=True)
    
    # Download
    print()
    print_info("Downloading Vosk STT model...")
    print_info("This may take several minutes depending on your internet speed...")
    print()
    
    if not download_file(model_url, zip_file):
        print_error("Download failed")
        return 1
    
    # Extract
    print()
    if not extract_zip(zip_file, model_dir):
        print_error("Extraction failed")
        return 1
    
    # Clean up ZIP
    print_info("Cleaning up ZIP file...")
    zip_file.unlink()
    
    # Verify
    if not args.no_verify:
        print_info("Verifying model files...")
        if not verify_model(model_full_path):
            print_warning("Model verification had issues, but may still work")
    
    # Success
    print()
    print_success("Download and extraction completed successfully!")
    print()
    
    dir_size = sum(p.stat().st_size for p in model_full_path.rglob('*'))
    
    print_success("Model Information:")
    print(f"  Path: {model_full_path}")
    print(f"  Size: {format_size(dir_size)}")
    print()
    
    print_success("✨ Vosk STT model is ready to use!")
    print()
    print_info("Next steps:")
    print("  1. Make sure VOSK_MODEL_PATH is set in .env")
    print("  2. Run Vosk server: python -m uvicorn main:app --port 8000")
    print()
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
